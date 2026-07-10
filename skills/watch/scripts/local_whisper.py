#!/usr/bin/env python3
"""本地 Whisper 引擎层（faster-whisper）+ 词级时间戳断句逻辑。

merge_words_to_segments 是翻译管线的时间戳正确性核心：按「句子 + 停顿」
切分字幕，让每条字幕尽量对应一个完整句子，起止时间 = 该句首词开口到
末词收尾（精确对齐语音，说完正好换条）。多次实战踩坑后定型，勿改。

两条转录路线共用这套切分逻辑：
- 本地 faster-whisper（word_timestamps=True）
- Whisper API 词级兜底（whisper_api.transcribe_audio_words → words_from_dicts）
"""
from __future__ import annotations

import sys

# Windows 管道默认 GBK，与中文 stderr 输出互啃 —— 统一强制 UTF-8
for _stream in (sys.stdout, sys.stderr):
    if _stream is not None and hasattr(_stream, "reconfigure"):
        try:
            _stream.reconfigure(encoding="utf-8")
        except (OSError, ValueError):
            pass


def format_timestamp(seconds: float) -> str:
    """秒数转 SRT 时间戳格式 HH:MM:SS,mmm"""
    h = int(seconds // 3600)
    m = int((seconds % 3600) // 60)
    s = int(seconds % 60)
    ms = int((seconds - int(seconds)) * 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


# 句末标点（强边界：一句话结束）
SENTENCE_END = ".?!。？！…"
# 次级标点（长句兜底时的软切点）
SOFT_BREAK = ",;:，；：、"


class _PseudoWord:
    """没有词级时间戳的 segment 退化成单个伪 word"""
    def __init__(self, start, end, text):
        self.start = start
        self.end = end
        self.word = text


class _PseudoSegment:
    """把 {start, end, word} dict 列表包装成带 .words 的 segment 对象，
    让 API 词级兜底路线能复用 merge_words_to_segments。"""
    def __init__(self, words):
        self.words = words
        self.start = words[0].start if words else 0.0
        self.end = words[-1].end if words else 0.0
        self.text = "".join(w.word for w in words)


def words_from_dicts(words: list[dict]) -> list:
    """{start, end, word} dict 列表 → 可喂 merge_words_to_segments 的
    segment 对象列表（单个 segment 含全部词）。"""
    pseudo = [_PseudoWord(w["start"], w["end"], w["word"]) for w in words]
    if not pseudo:
        return []
    return [_PseudoSegment(pseudo)]


def _flush(words):
    """把一串 word 收成一个字幕段；空文本返回 None"""
    text = "".join(w.word for w in words).strip()
    if not text:
        return None
    return {"start": words[0].start, "end": words[-1].end, "text": text}


def _find_soft_cut(words):
    """从后往前找最靠后的次级标点位置（返回该词索引），找不到返回 None"""
    for j in range(len(words) - 1, -1, -1):
        t = words[j].word.strip()
        if t and t[-1] in SOFT_BREAK:
            return j
    return None


def _find_pause_cut(words, min_gap=0.2):
    """长句无标点时的兜底：在句子后 2/3 段内找最大词间停顿处切（返回停顿前的词索引）。
    避免切出过短的头，也保证切点落在换气停顿而非词中间。找不到返回 None。"""
    best_gap, best_j = min_gap, None
    start = max(1, len(words) // 3)
    for j in range(start, len(words) - 1):
        gap = words[j + 1].start - words[j].end
        if gap > best_gap:
            best_gap, best_j = gap, j
    return best_j


def _postprocess(result):
    """清洗：丢弃无效/重复段、合并碎片、消除时间重叠"""
    cleaned = []
    for item in result:
        if item["end"] <= item["start"]:
            continue  # 丢弃无效时间戳（end <= start）
        if cleaned and item["text"] == cleaned[-1]["text"]:
            # 与前一段文本完全重复 → 延长前段，丢弃重复（Whisper 复读 artifact）
            cleaned[-1]["end"] = max(cleaned[-1]["end"], item["end"])
            continue
        cleaned.append(item)

    # 合并过短碎片（< 400ms 且少于 3 词）到前一段
    merged = []
    for item in cleaned:
        duration = item["end"] - item["start"]
        word_count = len(item["text"].split())
        if merged and duration < 0.4 and word_count < 3:
            merged[-1]["end"] = item["end"]
            merged[-1]["text"] += " " + item["text"]
        else:
            merged.append(item)

    # 消除相邻时间重叠（下一段 start 不早于上一段 end）
    for k in range(1, len(merged)):
        if merged[k]["start"] < merged[k - 1]["end"]:
            merged[k]["start"] = merged[k - 1]["end"]
        if merged[k]["end"] <= merged[k]["start"]:
            merged[k]["end"] = merged[k]["start"] + 0.3

    return merged


def merge_words_to_segments(segments, max_line_ms=6000, pause_ms=500, max_chars=80):
    """
    按「句子 + 停顿」切分字幕，让每条字幕尽量对应一个完整句子，
    起止时间 = 该句首词开口到末词收尾（精确对齐语音，说完正好换条）。

    切分信号（优先级从高到低）：
    1. 句末标点（. ? ! 。？！）→ 一句结束，立即切
    2. 词间停顿 >= pause_ms → 说话人换气/停顿，切
    3. 兜底：累计时长 >= max_line_ms 或字符数 >= max_chars
       → 在最近的次级标点处软切，没有则强制切（避免一条过长一行放不下）

    旧逻辑（照搬 Whisper segment）会把好几句挤一条、半句甩到下一条、
    超长时按时间在词中间硬剁；新逻辑以人话的句子为单位。
    """
    # 1) 把所有 segment 的 word 拉平成一个连续列表
    flat = []
    for seg in segments:
        words = seg.words if seg.words else []
        if not words:
            flat.append(_PseudoWord(seg.start, seg.end, seg.text))
        else:
            flat.extend(words)

    if not flat:
        return []

    # 2) 遍历 word，按句子边界 / 停顿 / 长度兜底切分
    result = []
    cur = []
    n = len(flat)
    for i, w in enumerate(flat):
        cur.append(w)
        wtext = w.word.strip()
        cur_text = "".join(x.word for x in cur).strip()
        cur_dur_ms = (w.end - cur[0].start) * 1000

        gap_ms = ((flat[i + 1].start - w.end) * 1000) if i + 1 < n else 0

        end_sentence = bool(wtext) and wtext[-1] in SENTENCE_END
        big_pause = gap_ms >= pause_ms
        too_long = cur_dur_ms >= max_line_ms or len(cur_text) >= max_chars

        if end_sentence or big_pause:
            seg_obj = _flush(cur)
            if seg_obj:
                result.append(seg_obj)
            cur = []
        elif too_long:
            cut = _find_soft_cut(cur)
            if cut is None or cut >= len(cur) - 1:
                cut = _find_pause_cut(cur)  # 无次级标点 → 退而求其次，在内部最大停顿处切
            if cut is not None and cut < len(cur) - 1:
                head, cur = cur[:cut + 1], cur[cut + 1:]
                seg_obj = _flush(head)
                if seg_obj:
                    result.append(seg_obj)
            else:
                seg_obj = _flush(cur)
                if seg_obj:
                    result.append(seg_obj)
                cur = []

    if cur:
        seg_obj = _flush(cur)
        if seg_obj:
            result.append(seg_obj)

    # 3) 清洗后处理
    return _postprocess(result)


def is_available() -> bool:
    """faster-whisper 是否可导入（不加载模型，秒回）。"""
    try:
        import faster_whisper  # noqa: F401
        return True
    except ImportError:
        return False


def transcribe_words(audio_path: str, model_name: str = "large-v3-turbo",
                     language: str | None = None):
    """faster-whisper 词级转写（CPU int8），返回 segment 对象列表。

    引擎缺失时返回 None（调用方决定降级到 API 还是报错）。
    """
    try:
        from faster_whisper import WhisperModel
    except ImportError:
        print("错误：faster-whisper 未安装，请运行：pip install faster-whisper",
              file=sys.stderr)
        return None

    print(f"转写引擎：faster-whisper (CPU), 模型：{model_name}", file=sys.stderr)
    model = WhisperModel(model_name, device="cpu", compute_type="int8")

    print(f"转写中：{audio_path}", file=sys.stderr)
    transcribe_kwargs = {"word_timestamps": True}
    if language:
        transcribe_kwargs["language"] = language

    segments_iter, info = model.transcribe(audio_path, **transcribe_kwargs)
    print(f"检测语言：{info.language}（置信度 {info.language_probability:.0%}）", file=sys.stderr)

    return list(segments_iter)


def transcribe_segments(audio_path: str, language: str | None = None,
                        model_name: str = "large-v3-turbo") -> list[dict] | None:
    """本地转写 → {start, end, text} dict 列表（与 transcribe.parse_vtt 同形状）。

    watch 管线的三级转写第二级用这个：拿到与 captions 一致形状的段落，
    直接喂 filter_range / format_transcript。引擎缺失返回 None。
    """
    segments = transcribe_words(audio_path, model_name=model_name, language=language)
    if segments is None:
        return None
    merged = merge_words_to_segments(segments)
    return [
        {"start": round(s["start"], 2), "end": round(s["end"], 2), "text": s["text"]}
        for s in merged
    ]


def write_srt(segments: list[dict], output_path: str) -> str:
    """把 {start, end, text} 段列表写成 SRT 文件。"""
    with open(output_path, "w", encoding="utf-8") as f:
        for i, seg in enumerate(segments, 1):
            f.write(f"{i}\n")
            f.write(f"{format_timestamp(seg['start'])} --> {format_timestamp(seg['end'])}\n")
            f.write(f"{seg['text']}\n\n")
    return output_path
