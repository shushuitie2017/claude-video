#!/usr/bin/env python3
"""Groq / OpenAI Whisper API 客户端（纯 stdlib，无需 pip install SDK）。

策略：提取音频（单声道 16kHz mp3，体积极小）→ 上传到配了 key 的 API。
返回的 segment 形状与 transcribe.parse_vtt 一致（{start, end, text}），
下游管线（filter_range / format_transcript）不关心转录来自哪里。

新增词级时间戳（granularity="word"）：请求 timestamp_granularities[]=word，
返回 {start, end, word} 列表，供翻译管线的断句逻辑
（local_whisper.merge_words_to_segments）复用——保证 API 兜底路线与本地
faster-whisper 路线出的 SRT 断句行为一致。
"""
from __future__ import annotations

import io
import json
import math
import mimetypes
import os
import ssl
import subprocess
import sys
import time
import urllib.error
import uuid
from pathlib import Path
from urllib.request import Request, urlopen

SCRIPT_DIR = Path(__file__).parent.resolve()
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from config import CONFIG_FILE, require_binary  # noqa: E402


GROQ_ENDPOINT = "https://api.groq.com/openai/v1/audio/transcriptions"
GROQ_MODEL = "whisper-large-v3"

OPENAI_ENDPOINT = "https://api.openai.com/v1/audio/transcriptions"
OPENAI_MODEL = "whisper-1"

# Groq 免费档与 OpenAI whisper-1 都限 25 MB 上传。留出 multipart 框架开销的余量。
MAX_UPLOAD_BYTES = 24 * 1024 * 1024


def plan_chunks(
    total_seconds: float,
    total_bytes: int,
    max_bytes: int = MAX_UPLOAD_BYTES,
) -> list[tuple[float, float]]:
    """把时长切成若干 (offset, duration) 连续块，每块字节数 < max_bytes。

    恒定码率单声道 mp3 的体积与时长线性相关，均匀时间切分即得均匀体积。
    已在限内时返回单个全长块。
    """
    if total_bytes <= max_bytes or total_seconds <= 0:
        return [(0.0, total_seconds)]

    n = math.ceil(total_bytes / max_bytes)
    chunk = total_seconds / n
    plan: list[tuple[float, float]] = []
    for i in range(n):
        offset = i * chunk
        # 最后一块吸收舍入余量，保证时长严格加和。
        duration = (total_seconds - offset) if i == n - 1 else chunk
        plan.append((round(offset, 3), round(duration, 3)))
    return plan


def load_api_key(preferred: str | None = None) -> tuple[str, str] | tuple[None, None]:
    """返回 (backend, api_key)。优先 Groq，回落 OpenAI。

    preferred 传 "groq" 或 "openai" 时只考虑该后端的 key。
    """
    def _from_env(name: str) -> str | None:
        value = os.environ.get(name)
        return value.strip() if value else None

    def _from_dotenv(path: Path, name: str) -> str | None:
        if not path.exists():
            return None
        try:
            for line in path.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, _, value = line.partition("=")
                if key.strip() != name:
                    continue
                value = value.strip()
                if len(value) >= 2 and value[0] in ('"', "'") and value[-1] == value[0]:
                    value = value[1:-1]
                return value or None
        except OSError:
            return None
        return None

    dotenv_paths = [
        CONFIG_FILE,
        Path.cwd() / ".env",
    ]

    candidates = (("GROQ_API_KEY", "groq"), ("OPENAI_API_KEY", "openai"))
    if preferred is not None:
        candidates = tuple(c for c in candidates if c[1] == preferred)

    for key_name, backend in candidates:
        value = _from_env(key_name)
        if not value:
            for candidate in dotenv_paths:
                value = _from_dotenv(candidate, key_name)
                if value:
                    break
        if value:
            return backend, value

    return None, None


def extract_audio(video_path: str, out_path: Path) -> Path:
    """提取单声道 16kHz 64kbps mp3 — 约 480 kB/分钟，任何 Whisper 限制都装得下。"""
    ffmpeg = require_binary("ffmpeg")

    out_path.parent.mkdir(parents=True, exist_ok=True)
    cmd = [
        ffmpeg,
        "-hide_banner",
        "-loglevel", "error",
        "-y",
        "-i", str(Path(video_path).resolve()),
        "-vn",
        "-acodec", "libmp3lame",
        "-ar", "16000",
        "-ac", "1",
        "-b:a", "64k",
        str(out_path.resolve()),
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise SystemExit(f"ffmpeg audio extraction failed: {result.stderr.strip()}")
    if not out_path.exists() or out_path.stat().st_size == 0:
        raise SystemExit("ffmpeg produced no audio — video may have no audio track")
    return out_path


def audio_duration(audio_path: Path) -> float:
    """经 ffprobe 返回音频时长（秒）。"""
    ffprobe = require_binary("ffprobe")

    result = subprocess.run(
        [
            ffprobe,
            "-v", "quiet",
            "-print_format", "json",
            "-show_format",
            str(audio_path.resolve()),
        ],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise SystemExit(f"ffprobe failed: {result.stderr.strip()}")
    fmt = json.loads(result.stdout or "{}").get("format", {})
    return float(fmt.get("duration") or 0.0)


def split_audio(
    full_audio: Path,
    work_dir: Path,
    plan: list[tuple[float, float]],
) -> list[tuple[Path, float]]:
    """按 plan 把音频切成块文件，返回 (path, offset) 对。

    用流拷贝（-c copy）避免重编码；mp3 帧边界的误差对转录足够小。
    """
    ffmpeg = require_binary("ffmpeg")

    work_dir.mkdir(parents=True, exist_ok=True)
    chunks: list[tuple[Path, float]] = []
    for index, (offset, duration) in enumerate(plan):
        out_path = work_dir / f"chunk_{index:03d}.mp3"
        cmd = [
            ffmpeg,
            "-hide_banner",
            "-loglevel", "error",
            "-y",
            "-ss", f"{offset:.3f}",
            "-i", str(full_audio.resolve()),
            "-t", f"{duration:.3f}",
            "-c", "copy",
            str(out_path.resolve()),
        ]
        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.returncode != 0 or not out_path.exists() or out_path.stat().st_size == 0:
            raise SystemExit(
                f"ffmpeg failed to split audio chunk {index + 1}: {result.stderr.strip()}"
            )
        chunks.append((out_path, offset))
    return chunks


def _build_multipart(fields: list[tuple[str, str]], file_path: Path) -> tuple[bytes, str]:
    """手工组装 Whisper API 接受的 multipart/form-data 请求体。

    fields 用 (name, value) 对的列表以支持重复字段名
    （timestamp_granularities[] 需要重复出现）。上传体小而可预测——
    手工组装让我们保持纯 stdlib，不必拉 requests/groq/openai SDK。
    """
    boundary = f"----VideoSkillsBoundary{uuid.uuid4().hex}"
    eol = b"\r\n"
    buf = io.BytesIO()

    for name, value in fields:
        buf.write(f"--{boundary}".encode()); buf.write(eol)
        buf.write(f'Content-Disposition: form-data; name="{name}"'.encode()); buf.write(eol)
        buf.write(eol)
        buf.write(str(value).encode()); buf.write(eol)

    mimetype = mimetypes.guess_type(file_path.name)[0] or "application/octet-stream"
    buf.write(f"--{boundary}".encode()); buf.write(eol)
    buf.write(
        f'Content-Disposition: form-data; name="file"; filename="{file_path.name}"'.encode()
    )
    buf.write(eol)
    buf.write(f"Content-Type: {mimetype}".encode()); buf.write(eol)
    buf.write(eol)
    buf.write(file_path.read_bytes())
    buf.write(eol)
    buf.write(f"--{boundary}--".encode()); buf.write(eol)

    return buf.getvalue(), boundary


MAX_ATTEMPTS = 4       # 首发 + 3 次重试
MAX_429_RETRIES = 2
RETRY_BASE_DELAY = 2.0


def _post_whisper(
    endpoint: str,
    api_key: str,
    model: str,
    audio_path: Path,
    granularity: str = "segment",
) -> dict:
    fields: list[tuple[str, str]] = [
        ("model", model),
        ("response_format", "verbose_json"),
        ("temperature", "0"),
    ]
    if granularity == "word":
        # 词级 + 段级都要：词级供断句，段级供整体校验
        fields.append(("timestamp_granularities[]", "word"))
        fields.append(("timestamp_granularities[]", "segment"))
    body, boundary = _build_multipart(fields, audio_path)
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": f"multipart/form-data; boundary={boundary}",
        # Groq 在 Cloudflare 后面——默认的 `Python-urllib/3.x` UA 会在鉴权前
        # 触发 WAF 规则 1010（403）。任何非默认 UA 都能过；我们如实标识。
        "User-Agent": "video-skills/1.0 (+claude-code; python-urllib)",
    }

    context = ssl.create_default_context()
    rate_limit_hits = 0
    last_exc: Exception | None = None
    last_detail = ""

    for attempt in range(MAX_ATTEMPTS):
        request = Request(endpoint, data=body, headers=headers, method="POST")
        try:
            with urlopen(request, timeout=300, context=context) as response:
                payload = response.read().decode("utf-8", errors="replace")
        except urllib.error.HTTPError as exc:
            detail = _read_error_body(exc)
            last_exc, last_detail = exc, detail

            # 429 以外的 4xx 是客户端错误——重试无益。
            if 400 <= exc.code < 500 and exc.code != 429:
                raise SystemExit(f"Whisper request failed: {exc}{detail}")

            if exc.code == 429:
                rate_limit_hits += 1
                if rate_limit_hits >= MAX_429_RETRIES:
                    raise SystemExit(f"Whisper request failed: {exc}{detail}")
                delay = _retry_after(exc) or RETRY_BASE_DELAY * (2 ** attempt) + 1
            else:
                delay = RETRY_BASE_DELAY * (2 ** attempt)

            if attempt < MAX_ATTEMPTS - 1:
                print(
                    f"[video-skills] whisper HTTP {exc.code} — retrying in {delay:.1f}s "
                    f"(attempt {attempt + 2}/{MAX_ATTEMPTS})",
                    file=sys.stderr,
                )
                time.sleep(delay)
            continue
        except (urllib.error.URLError, TimeoutError, ConnectionResetError, OSError) as exc:
            last_exc, last_detail = exc, ""
            if attempt < MAX_ATTEMPTS - 1:
                delay = RETRY_BASE_DELAY * (attempt + 1)
                print(
                    f"[video-skills] whisper network error ({type(exc).__name__}: {exc}) — "
                    f"retrying in {delay:.1f}s (attempt {attempt + 2}/{MAX_ATTEMPTS})",
                    file=sys.stderr,
                )
                time.sleep(delay)
            continue

        try:
            return json.loads(payload)
        except json.JSONDecodeError as exc:
            raise SystemExit(f"Whisper returned non-JSON response: {exc}: {payload[:200]}")

    raise SystemExit(
        f"Whisper request failed after {MAX_ATTEMPTS} attempts: {last_exc}{last_detail}"
    )


def _read_error_body(exc: urllib.error.HTTPError) -> str:
    try:
        body = exc.read()
    except Exception:
        return ""
    if not body:
        return ""
    try:
        return f" — {body.decode('utf-8', errors='replace')[:400]}"
    except Exception:
        return ""


def _retry_after(exc: urllib.error.HTTPError) -> float | None:
    header = exc.headers.get("Retry-After") if getattr(exc, "headers", None) else None
    if not header:
        return None
    try:
        return float(header)
    except ValueError:
        return None


def shift_segments(segments: list[dict], offset_seconds: float) -> list[dict]:
    """返回 start/end 平移 offset_seconds 后的 segments 副本。

    每块独立转录，Whisper 返回块内 0 起点时间戳；按块偏移平移后拼回源时间轴。
    """
    if offset_seconds == 0:
        return segments
    return [
        {
            "start": round(seg["start"] + offset_seconds, 2),
            "end": round(seg["end"] + offset_seconds, 2),
            "text": seg["text"],
        }
        for seg in segments
    ]


def shift_words(words: list[dict], offset_seconds: float) -> list[dict]:
    """词级版本的 shift_segments。"""
    if offset_seconds == 0:
        return words
    return [
        {
            "start": round(w["start"] + offset_seconds, 3),
            "end": round(w["end"] + offset_seconds, 3),
            "word": w["word"],
        }
        for w in words
    ]


def _segments_from_response(data: dict) -> list[dict]:
    """把 Whisper verbose_json 转成 {start, end, text} 段格式。"""
    out: list[dict] = []
    for seg in data.get("segments") or []:
        text = (seg.get("text") or "").strip()
        if not text:
            continue
        out.append({
            "start": round(float(seg.get("start") or 0.0), 2),
            "end": round(float(seg.get("end") or 0.0), 2),
            "text": text,
        })

    if not out:
        full = (data.get("text") or "").strip()
        if full:
            out.append({"start": 0.0, "end": 0.0, "text": full})

    return out


def _words_from_response(data: dict) -> list[dict]:
    """把 verbose_json 的 words 转成 {start, end, word} 列表（可能为空）。"""
    out: list[dict] = []
    for w in data.get("words") or []:
        word = w.get("word") or ""
        if not word:
            continue
        out.append({
            "start": float(w.get("start") or 0.0),
            "end": float(w.get("end") or 0.0),
            "word": word,
        })
    return out


def transcribe_chunks(
    chunks: list[tuple[Path, float]],
    transcribe_one,
) -> list[dict]:
    """逐块转录、按块偏移平移、拼接。

    单块在自身重试后仍失败 → 记录并跳过，不让一个坏块废掉整个转录。
    只有全部块都失败才抛错。
    """
    segments: list[dict] = []
    failures = 0
    for index, (path, offset) in enumerate(chunks):
        try:
            chunk_segments = transcribe_one(path)
        except SystemExit as exc:
            failures += 1
            print(
                f"[video-skills] chunk {index + 1}/{len(chunks)} failed — skipping ({exc})",
                file=sys.stderr,
            )
            continue
        segments.extend(shift_segments(chunk_segments, offset))
        print(
            f"[video-skills] chunk {index + 1}/{len(chunks)} → {len(chunk_segments)} segments",
            file=sys.stderr,
        )

    if failures == len(chunks):
        raise SystemExit("Whisper failed on every audio chunk")
    return segments


def _transcribe_file(backend: str, api_key: str, audio_path: Path) -> list[dict]:
    """上传单个音频文件，返回其 0 起点 segments。"""
    if backend == "groq":
        response = _post_whisper(GROQ_ENDPOINT, api_key, GROQ_MODEL, audio_path)
    elif backend == "openai":
        response = _post_whisper(OPENAI_ENDPOINT, api_key, OPENAI_MODEL, audio_path)
    else:
        raise SystemExit(f"Unknown whisper backend: {backend}")
    return _segments_from_response(response)


def _transcribe_file_words(backend: str, api_key: str, audio_path: Path) -> dict:
    """上传单个音频文件（词级），返回 {"segments": [...], "words": [...]}。"""
    if backend == "groq":
        response = _post_whisper(GROQ_ENDPOINT, api_key, GROQ_MODEL, audio_path, granularity="word")
    elif backend == "openai":
        response = _post_whisper(OPENAI_ENDPOINT, api_key, OPENAI_MODEL, audio_path, granularity="word")
    else:
        raise SystemExit(f"Unknown whisper backend: {backend}")
    return {
        "segments": _segments_from_response(response),
        "words": _words_from_response(response),
    }


def transcribe_video(
    video_path: str,
    audio_out: Path,
    backend: str | None = None,
    api_key: str | None = None,
) -> tuple[list[dict], str]:
    """完整流程：提音频 → 上传 → 解析 segments。

    返回 (segments, backend_used)。任何失败抛 SystemExit。
    """
    if backend is None or api_key is None:
        detected_backend, detected_key = load_api_key()
        backend = backend or detected_backend
        api_key = api_key or detected_key

    if not backend or not api_key:
        raise SystemExit(
            "No Whisper API key available. Set GROQ_API_KEY (preferred) or OPENAI_API_KEY "
            f"in the environment or in {CONFIG_FILE}."
        )

    print(f"[video-skills] extracting audio for Whisper ({backend})…", file=sys.stderr)
    audio_path = extract_audio(video_path, audio_out)
    audio_bytes = audio_path.stat().st_size

    def transcribe_one(path: Path) -> list[dict]:
        return _transcribe_file(backend, api_key, path)

    if audio_bytes <= MAX_UPLOAD_BYTES:
        print(
            f"[video-skills] audio: {audio_bytes / 1024:.0f} kB — uploading to {backend} Whisper…",
            file=sys.stderr,
        )
        segments = transcribe_one(audio_path)
    else:
        duration = audio_duration(audio_path)
        plan = plan_chunks(duration, audio_bytes, MAX_UPLOAD_BYTES)
        print(
            f"[video-skills] audio: {audio_bytes / (1024 * 1024):.0f} MB exceeds "
            f"{MAX_UPLOAD_BYTES // (1024 * 1024)} MB — splitting into {len(plan)} chunks…",
            file=sys.stderr,
        )
        chunks = split_audio(audio_path, audio_out.parent / "chunks", plan)
        segments = transcribe_chunks(chunks, transcribe_one)

    if not segments:
        raise SystemExit("Whisper returned no transcript segments")

    print(f"[video-skills] transcribed {len(segments)} segments via {backend}", file=sys.stderr)
    return segments, backend


def transcribe_audio_words(
    audio_path: Path,
    backend: str | None = None,
    api_key: str | None = None,
) -> tuple[list[dict], list[dict], str]:
    """词级转录一个已就绪的音频文件（翻译管线的 API 兜底路线）。

    返回 (segments, words, backend_used)。words 为空说明该后端/模型不支持
    词级时间戳——调用方应降级到 segment 级并向用户明示字幕精度下降。
    超过 25 MB 自动分块，词时间戳按块偏移平移。
    """
    if backend is None or api_key is None:
        detected_backend, detected_key = load_api_key()
        backend = backend or detected_backend
        api_key = api_key or detected_key

    if not backend or not api_key:
        raise SystemExit(
            "No Whisper API key available. Set GROQ_API_KEY (preferred) or OPENAI_API_KEY "
            f"in the environment or in {CONFIG_FILE}."
        )

    audio_path = Path(audio_path)
    audio_bytes = audio_path.stat().st_size

    if audio_bytes <= MAX_UPLOAD_BYTES:
        result = _transcribe_file_words(backend, api_key, audio_path)
        return result["segments"], result["words"], backend

    duration = audio_duration(audio_path)
    plan = plan_chunks(duration, audio_bytes, MAX_UPLOAD_BYTES)
    print(
        f"[video-skills] audio exceeds upload cap — splitting into {len(plan)} chunks…",
        file=sys.stderr,
    )
    chunks = split_audio(audio_path, audio_path.parent / "chunks", plan)

    all_segments: list[dict] = []
    all_words: list[dict] = []
    failures = 0
    for index, (path, offset) in enumerate(chunks):
        try:
            result = _transcribe_file_words(backend, api_key, path)
        except SystemExit as exc:
            failures += 1
            print(
                f"[video-skills] chunk {index + 1}/{len(chunks)} failed — skipping ({exc})",
                file=sys.stderr,
            )
            continue
        all_segments.extend(shift_segments(result["segments"], offset))
        all_words.extend(shift_words(result["words"], offset))
    if failures == len(chunks):
        raise SystemExit("Whisper failed on every audio chunk")
    return all_segments, all_words, backend


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("usage: whisper_api.py <video-path> [<audio-out.mp3>] [--backend groq|openai]", file=sys.stderr)
        raise SystemExit(2)

    video = sys.argv[1]
    audio_out = Path(sys.argv[2]) if len(sys.argv) > 2 and not sys.argv[2].startswith("--") else Path("audio.mp3")
    backend_override = None
    if "--backend" in sys.argv:
        backend_override = sys.argv[sys.argv.index("--backend") + 1]

    segments, backend = transcribe_video(video, audio_out, backend=backend_override)
    print(json.dumps({"backend": backend, "segments": segments}, ensure_ascii=False, indent=2))
