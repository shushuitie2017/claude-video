"""翻译管线时间戳正确性核心：merge_words_to_segments 的切分与清洗逻辑。

纯单元测试，零网络零模型——手工构造词对象喂给切分器。
"""
from __future__ import annotations

import local_whisper
from local_whisper import (
    _PseudoWord,
    format_timestamp,
    merge_words_to_segments,
    words_from_dicts,
)


class FakeSeg:
    def __init__(self, words=None, start=0.0, end=0.0, text=""):
        self.words = words or []
        self.start = words[0].start if words else start
        self.end = words[-1].end if words else end
        self.text = "".join(w.word for w in words) if words else text


def W(start, end, word):
    return _PseudoWord(start, end, word)


def _words(*triples):
    return [W(*t) for t in triples]


class TestSentenceCut:
    def test_sentence_end_punctuation_cuts(self):
        words = _words(
            (0.0, 0.5, " Hello"), (0.5, 1.0, " world."),
            (1.1, 1.6, " Next"), (1.6, 2.0, " one."),
        )
        out = merge_words_to_segments([FakeSeg(words)])
        assert len(out) == 2
        assert out[0]["text"] == "Hello world."
        assert out[1]["text"] == "Next one."

    def test_segment_times_hug_speech(self):
        """起止时间 = 首词开口到末词收尾。"""
        words = _words((2.0, 2.4, " Hi"), (2.4, 3.1, " there."))
        out = merge_words_to_segments([FakeSeg(words)])
        assert out[0]["start"] == 2.0
        assert out[0]["end"] == 3.1


class TestPauseCut:
    def test_big_pause_cuts(self):
        # 无标点，但词间停顿 600ms >= 500ms 阈值 → 切
        words = _words(
            (0.0, 0.5, " okay"), (0.5, 1.0, " so"),
            (1.6, 2.1, " anyway"), (2.1, 2.6, " yes"),
        )
        out = merge_words_to_segments([FakeSeg(words)])
        assert len(out) == 2
        assert out[0]["text"] == "okay so"

    def test_small_pause_does_not_cut(self):
        words = _words((0.0, 0.5, " a"), (0.7, 1.2, " b"))
        out = merge_words_to_segments([FakeSeg(words)])
        assert len(out) == 1


class TestLongLineFallback:
    def test_too_long_soft_cuts_at_secondary_punctuation(self):
        # 超过 max_chars，中途有逗号 → 在逗号处软切
        words = [W(i * 0.3, i * 0.3 + 0.25, f" w{i:02d}" + ("," if i == 5 else ""))
                 for i in range(20)]
        out = merge_words_to_segments([FakeSeg(words)], max_chars=40)
        assert len(out) >= 2
        assert out[0]["text"].endswith(",")

    def test_too_long_without_punctuation_cuts_at_biggest_pause(self):
        words = []
        t = 0.0
        for i in range(20):
            words.append(W(t, t + 0.25, f" w{i:02d}"))
            t += 0.3 if i != 12 else 0.7  # 内部最大停顿在第 12 词后
        out = merge_words_to_segments([FakeSeg(words)], max_chars=40, pause_ms=999999)
        assert len(out) >= 2


class TestPostprocess:
    def test_drops_invalid_timestamps(self):
        out = local_whisper._postprocess([
            {"start": 1.0, "end": 0.5, "text": "bad"},
            {"start": 1.0, "end": 2.0, "text": "good"},
        ])
        assert [s["text"] for s in out] == ["good"]

    def test_collapses_whisper_repeats(self):
        out = local_whisper._postprocess([
            {"start": 0.0, "end": 1.0, "text": "same line"},
            {"start": 1.0, "end": 2.0, "text": "same line"},
        ])
        assert len(out) == 1
        assert out[0]["end"] == 2.0

    def test_merges_tiny_fragments(self):
        out = local_whisper._postprocess([
            {"start": 0.0, "end": 2.0, "text": "main sentence"},
            {"start": 2.0, "end": 2.2, "text": "uh"},
        ])
        assert len(out) == 1
        assert out[0]["text"].endswith("uh")

    def test_fixes_overlaps(self):
        out = local_whisper._postprocess([
            {"start": 0.0, "end": 2.0, "text": "a"},
            {"start": 1.5, "end": 3.0, "text": "b"},
        ])
        assert out[1]["start"] >= out[0]["end"]
        assert out[1]["end"] > out[1]["start"]


class TestFormatTimestamp:
    def test_zero(self):
        assert format_timestamp(0.0) == "00:00:00,000"

    def test_full_fields(self):
        assert format_timestamp(3661.5) == "01:01:01,500"

    def test_millis(self):
        assert format_timestamp(1.234) == "00:00:01,234"


class TestWordsFromDicts:
    def test_round_trip_through_merge(self):
        """API 词级兜底路线：dict words → 同一切分器 → 与本地路线同形状输出。"""
        dicts = [
            {"start": 0.0, "end": 0.5, "word": " Hello"},
            {"start": 0.5, "end": 1.0, "word": " world."},
            {"start": 1.8, "end": 2.2, "word": " Bye."},
        ]
        segs = words_from_dicts(dicts)
        out = merge_words_to_segments(segs)
        assert [s["text"] for s in out] == ["Hello world.", "Bye."]

    def test_empty_input(self):
        assert words_from_dicts([]) == []
