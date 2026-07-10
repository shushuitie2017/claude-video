"""双语 SRT → 双语 ASS 转换：解析、时间戳换算、字号表、字体脚本判定。"""
from __future__ import annotations

import sys

import bilingual_ass
from bilingual_ass import (
    _detect_script,
    build_ass,
    parse_srt,
    pick_sizes,
    srt_time_to_ass,
)

BILINGUAL_SRT = """1
00:00:19,239 --> 00:00:21,239
大家好吗
Hello everyone, how are you?

2
00:00:21,500 --> 00:00:23,000
这是第二条
This is the second one
"""


class TestParseSrt:
    def test_parses_two_line_blocks(self):
        items = parse_srt(BILINGUAL_SRT)
        assert len(items) == 2
        start, end, lines = items[0]
        assert start == "00:00:19,239"
        assert lines == ["大家好吗", "Hello everyone, how are you?"]

    def test_parses_single_line_block(self):
        items = parse_srt("1\n00:00:00,000 --> 00:00:01,000\n只有中文\n")
        assert items[0][2] == ["只有中文"]

    def test_skips_malformed_blocks(self):
        items = parse_srt("1\nnot-a-timestamp\n文本\n\n2\n00:00:01,000 --> 00:00:02,000\nok\n")
        assert len(items) == 1

    def test_accepts_dot_millis(self):
        items = parse_srt("1\n00:00:01.500 --> 00:00:02.000\nok\n")
        assert len(items) == 1


class TestSrtTimeToAss:
    def test_basic(self):
        assert srt_time_to_ass("00:00:19,239") == "0:00:19.24"

    def test_rounds_up_and_clamps_centiseconds(self):
        # 996ms → cs 100 → 钳到 99，不进位破坏秒字段
        assert srt_time_to_ass("00:00:01,996") == "0:00:01.99"

    def test_hours_lose_leading_zero(self):
        assert srt_time_to_ass("01:02:03,000") == "1:02:03.00"


class TestPickSizes:
    def test_default_720(self):
        assert pick_sizes(720) == (22, 13)

    def test_default_1080(self):
        assert pick_sizes(1080) == (20, 12)

    def test_none_height_falls_back_720(self):
        assert pick_sizes(None) == (22, 13)

    def test_override_derives_english_by_ratio(self):
        cn, en = pick_sizes(1080, cn_override=24)
        assert cn == 24
        assert en == round(24 / 1.7)

    def test_english_floor(self):
        _, en = pick_sizes(None, cn_override=10)
        assert en >= 8


class TestBuildAss:
    def test_bilingual_line_uses_inline_fs(self):
        items = parse_srt(BILINGUAL_SRT)
        ass = build_ass(items, cn_size=20, en_size=12, font="TestFont", marginv=16)
        assert "大家好吗\\N{\\fs12}Hello everyone, how are you?" in ass
        assert "Style: Default,TestFont,20," in ass

    def test_single_line_has_no_inline_fs(self):
        items = parse_srt("1\n00:00:00,000 --> 00:00:01,000\n只有中文\n")
        ass = build_ass(items, cn_size=20, en_size=12, font="TestFont")
        dialogue = [l for l in ass.splitlines() if l.startswith("Dialogue")][0]
        assert "\\fs" not in dialogue


class TestDetectScript:
    def test_cjk(self):
        assert _detect_script("大家好 hello") == "cjk"

    def test_kana_wins_over_cjk(self):
        assert _detect_script("日本語のテキスト") == "kana"

    def test_hangul(self):
        assert _detect_script("안녕하세요") == "hangul"

    def test_arabic(self):
        assert _detect_script("مرحبا") == "arabic"

    def test_latin(self):
        assert _detect_script("hello only") == "latin"


class TestPlatformFonts:
    def test_bilingual_chinese_first_line_wins(self, monkeypatch):
        """双语（中上日下）必须按首行中文选字体——日文字体缺简体字形会出方块。"""
        items = parse_srt("1\n00:00:00,000 --> 00:00:01,000\n中文在上\nこんにちは日本語\n")
        monkeypatch.setattr(sys, "platform", "win32")
        assert bilingual_ass.pick_font_for_items(items) == "Microsoft YaHei"

    def test_pure_kana_first_line_follows_platform(self, monkeypatch):
        items = parse_srt("1\n00:00:00,000 --> 00:00:01,000\nこんにちは\n")
        monkeypatch.setattr(sys, "platform", "win32")
        assert bilingual_ass.pick_font_for_items(items) == "Yu Gothic"
        monkeypatch.setattr(sys, "platform", "darwin")
        assert bilingual_ass.pick_font_for_items(items) == "Hiragino Sans"

    def test_cjk_falls_back_to_platform_default(self, monkeypatch):
        items = parse_srt("1\n00:00:00,000 --> 00:00:01,000\n纯中文\nEnglish below\n")
        monkeypatch.setattr(sys, "platform", "win32")
        assert bilingual_ass.pick_font_for_items(items) == "Microsoft YaHei"
