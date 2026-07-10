#!/usr/bin/env python3
"""SessionStart hook — 一行状态提示，让用户知道 video-skills 配到什么程度。

就绪时静默（避免每次会话刷屏）；缺东西时指向安装器。跨平台 Python 实现
（原 bash 版在 Windows 上不可用）。
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

CONFIG_FILE = Path.home() / ".config" / "video-skills" / ".env"


def read_key(name: str) -> str | None:
    value = os.environ.get(name)
    if value and value.strip():
        return value.strip()
    if not CONFIG_FILE.exists():
        return None
    try:
        for line in CONFIG_FILE.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, raw = line.partition("=")
            if key.strip() != name:
                continue
            raw = raw.strip()
            if len(raw) >= 2 and raw[0] in ('"', "'") and raw[-1] == raw[0]:
                raw = raw[1:-1]
            return raw or None
    except OSError:
        return None
    return None


def main() -> int:
    # POSIX 下警告泄露权限的密钥文件（Windows 用 ACL，stat 位无意义）
    if os.name != "nt" and CONFIG_FILE.exists():
        try:
            mode = CONFIG_FILE.stat().st_mode
            if mode & 0o077:
                print(f"video-skills: WARNING — {CONFIG_FILE} 对其他用户可读。修复: chmod 600 {CONFIG_FILE}")
        except OSError:
            pass

    # 复用共享的 WinGet 感知定位（hook 独立于 skill 安装目录，就近 import）
    plugin_root = Path(__file__).resolve().parents[2]
    scripts = plugin_root / "skills" / "watch" / "scripts"
    if str(scripts) not in sys.path:
        sys.path.insert(0, str(scripts))
    try:
        from config import find_binary
    except ImportError:
        import shutil
        find_binary = shutil.which

    has_ffmpeg = find_binary("ffmpeg") is not None
    has_ytdlp = find_binary("yt-dlp") is not None

    try:
        import faster_whisper  # noqa: F401
        has_local = True
    except ImportError:
        has_local = False

    has_key = bool(read_key("GROQ_API_KEY") or read_key("OPENAI_API_KEY"))
    setup_complete = read_key("SETUP_COMPLETE") == "true"

    # 全配好 → 静默
    if setup_complete and has_ffmpeg and has_ytdlp:
        return 0

    if not has_ffmpeg or not has_ytdlp:
        print(
            "video-skills: 需要 ffmpeg + yt-dlp。跑一次 "
            "`python <skill目录>/scripts/setup.py` 自动安装并生成配置。"
        )
    elif not has_key and not has_local:
        print(
            "video-skills: 有原生字幕的视频已可用。补一个转录兜底可解锁无字幕视频："
            "`pip install faster-whisper`（免费本地）或在 ~/.config/video-skills/.env "
            "配 GROQ_API_KEY / OPENAI_API_KEY。"
        )
    else:
        print("video-skills: ready.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
