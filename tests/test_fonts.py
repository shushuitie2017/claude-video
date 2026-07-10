"""fonts.py：三平台字体名解析与水印 fontfile 兜底链。"""
from __future__ import annotations

import os
import sys

import fonts


class TestSubtitleFontName:
    def test_windows(self, monkeypatch):
        monkeypatch.setattr(sys, "platform", "win32")
        assert fonts.subtitle_font_name() == "Microsoft YaHei"

    def test_macos(self, monkeypatch):
        monkeypatch.setattr(sys, "platform", "darwin")
        assert fonts.subtitle_font_name() == "PingFang SC"

    def test_linux(self, monkeypatch):
        monkeypatch.setattr(sys, "platform", "linux")
        assert fonts.subtitle_font_name() == "Noto Sans CJK SC"


class TestScriptFontTable:
    def test_covers_three_scripts_everywhere(self, monkeypatch):
        for platform in ("win32", "darwin", "linux"):
            monkeypatch.setattr(sys, "platform", platform)
            table = fonts.script_font_table()
            assert set(table) == {"hangul", "arabic", "kana"}


class TestWatermarkFontfile:
    def test_windows_returns_existing_file(self):
        if os.name != "nt":
            import pytest
            pytest.skip("needs real C:/Windows/Fonts")
        path = fonts.watermark_fontfile()
        assert path is not None
        assert os.path.exists(path)
