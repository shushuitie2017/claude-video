#!/usr/bin/env python3
"""跨平台中文字体解析。

两类消费者，需求不同：
- 字幕（subtitles/ass 滤镜，libass）：用**字体名**（FontName），libass 经
  系统字体索引查找。
- 水印（drawtext 滤镜）：必须用**字体文件路径**（fontfile）。macOS 的
  fontconfig 索引不到苹方，按名查找会回退 Verdana（不支持中文）→ 方块；
  所以 drawtext 一律走 fontfile 绝对路径。macOS 水印用圆体（Yuanti SC），
  index 0 即 SC 简体，不会出方块（PingFang.ttc 的 index 0 是 HK 变体）。
"""
from __future__ import annotations

import glob
import subprocess
import sys
from pathlib import Path

# Windows 管道默认 GBK，与中文输出互啃 —— 统一强制 UTF-8
for _stream in (sys.stdout, sys.stderr):
    if _stream is not None and hasattr(_stream, "reconfigure"):
        try:
            _stream.reconfigure(encoding="utf-8")
        except (OSError, ValueError):
            pass


def subtitle_font_name() -> str:
    """字幕 FontName（libass 名称查找）。"""
    if sys.platform == "win32":
        return "Microsoft YaHei"
    if sys.platform == "darwin":
        return "PingFang SC"
    return "Noto Sans CJK SC"


# 按文字脚本选字体（bilingual_ass 用）：默认中文字体只覆盖中文 + 拉丁，
# 韩语 / 阿拉伯语 / 日语假名各需对应系统字体，否则烧出来是方块。
# 这些字体都自带拉丁字形，英文行同字体即可。
def script_font_table() -> dict[str, str]:
    if sys.platform == "win32":
        return {
            "hangul": "Malgun Gothic",
            "arabic": "Segoe UI",
            "kana": "Yu Gothic",
        }
    if sys.platform == "darwin":
        return {
            "hangul": "Apple SD Gothic Neo",
            "arabic": "Geeza Pro",
            "kana": "Hiragino Sans",
        }
    return {
        "hangul": "Noto Sans CJK KR",
        "arabic": "Noto Sans Arabic",
        "kana": "Noto Sans CJK JP",
    }


def watermark_fontfile() -> str | None:
    """水印 drawtext 的 fontfile 绝对路径；找不到返回 None（调用方跳过水印或报错）。"""
    if sys.platform == "win32":
        windir = Path("C:/Windows/Fonts")
        for name in ("msyh.ttc", "msyhbd.ttc", "simhei.ttf", "msyh.ttf"):
            candidate = windir / name
            if candidate.exists():
                return str(candidate)
        return None
    if sys.platform == "darwin":
        for pattern in (
            "/System/Library/AssetsV2/**/Yuanti.ttc",
            "/System/Library/AssetsV2/**/PingFang.ttc",
        ):
            matches = glob.glob(pattern, recursive=True)
            if matches:
                return matches[0]
        fallback = Path("/System/Library/Fonts/STHeiti Medium.ttc")
        return str(fallback) if fallback.exists() else None
    # Linux: fc-match 查 Noto CJK
    try:
        result = subprocess.run(
            ["fc-match", "-f", "%{file}", "Noto Sans CJK SC"],
            capture_output=True, text=True, timeout=10,
        )
        path = result.stdout.strip()
        if path and Path(path).exists():
            return path
    except (OSError, subprocess.TimeoutExpired):
        pass
    return None


if __name__ == "__main__":
    print(f"subtitle FontName : {subtitle_font_name()}")
    print(f"watermark fontfile: {watermark_fontfile()}")
    print(f"script fonts      : {script_font_table()}")
