"""burn.py：filter 路径转义、水印时间表、命令组装（dry-run 集成断言）。"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

import burn
from burn import (
    build_subtitle_filter,
    escape_drawtext_text,
    escape_filter_path,
    watermark_schedule,
)

BURN = Path(__file__).resolve().parent.parent / "skills" / "video-translate" / "scripts" / "burn.py"


class TestEscapeFilterPath:
    @pytest.mark.skipif(os.name != "nt", reason="Windows drive-letter escaping")
    def test_windows_drive_path(self):
        out = escape_filter_path(Path(r"C:\Users\a\sub.srt"))
        assert out == "C\\:/Users/a/sub.srt"

    def test_no_backslashes_survive_as_separators(self):
        out = escape_filter_path(Path("C:/a/b.srt") if os.name == "nt" else Path("/a/b.srt"))
        assert "\\" not in out.replace("\\:", "")


class TestEscapeDrawtextText:
    def test_escapes_specials(self):
        assert escape_drawtext_text("a:b,c'd") == "a\\:b\\,c\\'d"

    def test_plain_text_untouched(self):
        assert escape_drawtext_text("蓝猫BlueCat") == "蓝猫BlueCat"


class TestWatermarkSchedule:
    def test_short_video_three_times(self):
        sched = watermark_schedule(300.0)  # 5 分钟
        assert len(sched) == 3
        assert all(dwell == 4.0 for _, dwell in sched)

    def test_medium_video_five_times(self):
        sched = watermark_schedule(2400.0)  # 40 分钟
        assert len(sched) == 5
        assert all(dwell == 5.0 for _, dwell in sched)

    def test_long_video_every_15_minutes(self):
        sched = watermark_schedule(8400.0)  # 140 分钟
        assert len(sched) == max(5, int(8400 // 900))
        assert all(dwell == 10.0 for _, dwell in sched)

    def test_first_early_last_before_end(self):
        duration = 600.0
        sched = watermark_schedule(duration)
        first_start, _ = sched[0]
        last_start, last_dwell = sched[-1]
        assert first_start <= 12.0
        assert last_start + last_dwell <= duration

    def test_zero_duration_empty(self):
        assert watermark_schedule(0.0) == []


class TestBuildSubtitleFilter:
    def test_srt_uses_subtitles_with_force_style(self, tmp_path):
        srt = tmp_path / "a.srt"
        srt.write_text("x", encoding="utf-8")
        f = build_subtitle_filter(srt, "Microsoft YaHei", 20, 30)
        assert f.startswith("subtitles='")
        assert "force_style='FontName=Microsoft YaHei,Bold=1,FontSize=20," in f
        assert "MarginV=30" in f

    def test_ass_uses_ass_filter_without_style_override(self, tmp_path):
        ass = tmp_path / "a.ass"
        ass.write_text("x", encoding="utf-8")
        f = build_subtitle_filter(ass, "Microsoft YaHei", 20, 30)
        assert f.startswith("ass='")
        assert "force_style" not in f


def _dry_run(clip: Path, *args: str) -> list[str]:
    proc = subprocess.run(
        [sys.executable, str(BURN), str(clip), "--dry-run", *args],
        capture_output=True, text=True, encoding="utf-8",
    )
    assert proc.returncode == 0, proc.stderr
    return json.loads(proc.stdout)


class TestDryRunCommand:
    def test_forces_aac_never_copy(self, cut_clip: Path, tmp_path):
        srt = tmp_path / "s.srt"
        srt.write_text("1\n00:00:00,000 --> 00:00:01,000\n测试\n", encoding="utf-8")
        cmd = _dry_run(cut_clip, "--subs", str(srt))
        assert cmd[cmd.index("-c:a") + 1] == "aac"
        assert cmd[cmd.index("-b:a") + 1] == "128k"

    def test_watermark_adds_drawtext_per_slot(self, cut_clip: Path, tmp_path):
        srt = tmp_path / "s.srt"
        srt.write_text("1\n00:00:00,000 --> 00:00:01,000\n测试\n", encoding="utf-8")
        cmd = _dry_run(cut_clip, "--subs", str(srt), "--watermark-text", "@测试")
        vf = cmd[cmd.index("-vf") + 1]
        # 合成短片只有几秒 → 首尾时点重合，排布收敛为 1 次（长视频排布见
        # TestWatermarkSchedule 的纯单元断言）
        assert vf.count("drawtext=") >= 1
        assert "fontfile=" in vf
        assert "alpha=" in vf

    def test_avoid_hardsub_presets(self, cut_clip: Path, tmp_path):
        srt = tmp_path / "s.srt"
        srt.write_text("1\n00:00:00,000 --> 00:00:01,000\n测试\n", encoding="utf-8")
        cmd = _dry_run(cut_clip, "--subs", str(srt), "--avoid-hardsub", "double")
        vf = cmd[cmd.index("-vf") + 1]
        assert "MarginV=60" in vf
        assert "FontSize=19" in vf

    def test_explicit_margin_beats_preset(self, cut_clip: Path, tmp_path):
        srt = tmp_path / "s.srt"
        srt.write_text("1\n00:00:00,000 --> 00:00:01,000\n测试\n", encoding="utf-8")
        cmd = _dry_run(cut_clip, "--subs", str(srt), "--avoid-hardsub", "double", "--margin-v", "48")
        vf = cmd[cmd.index("-vf") + 1]
        assert "MarginV=48" in vf
