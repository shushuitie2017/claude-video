#!/usr/bin/env python3
"""video-skills 一键安装：把两个 skill 复制到 ~/.claude/skills/ 并脚手架配置。

用法：
    python install.py            # 安装/更新两个 skill + 生成配置 + 依赖检查
    python install.py --check    # 只做依赖检查

单一跨平台脚本（PowerShell / Git Bash / macOS / Linux 行为一致）。
"""
from __future__ import annotations

import shutil
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(REPO_ROOT / "shared"))

from config import CONFIG_DIR, find_binary, install_hint  # noqa: E402

SKILLS = ["watch", "video-translate"]
TARGET_ROOT = Path.home() / ".claude" / "skills"


def check_deps() -> bool:
    ok = True
    for name in ("ffmpeg", "ffprobe", "yt-dlp"):
        found = find_binary(name)
        if found:
            print(f"  [ok] {name}: {found}")
        else:
            ok = False
            print(f"  [缺] {name} — 安装：{install_hint(name)}")
    try:
        import faster_whisper  # noqa: F401
        print("  [ok] faster-whisper（本地转录引擎）")
    except ImportError:
        print("  [可选] faster-whisper 未装 — 无字幕视频转录/翻译管线需要：pip install faster-whisper")
    return ok


def install() -> int:
    # 保证副本与 shared/ 一致（从 git 克隆安装时通常已一致，防手改漂移）
    sync = REPO_ROOT / "tools" / "sync_shared.py"
    if sync.exists():
        import subprocess
        subprocess.run([sys.executable, str(sync)], capture_output=True)

    TARGET_ROOT.mkdir(parents=True, exist_ok=True)
    for skill in SKILLS:
        src = REPO_ROOT / "skills" / skill
        dst = TARGET_ROOT / skill
        if dst.exists():
            backup = TARGET_ROOT / f"{skill}.bak"
            if backup.exists():
                shutil.rmtree(backup)
            dst.rename(backup)
            print(f"[install] 已备份旧版：{backup}")
        shutil.copytree(src, dst)
        print(f"[install] {skill} → {dst}")

    # 脚手架配置（存在不覆盖）
    setup = TARGET_ROOT / "watch" / "scripts" / "setup.py"
    import subprocess
    subprocess.run([sys.executable, str(setup)])

    print()
    print("[install] 依赖检查：")
    check_deps()
    print()
    print("完成。翻译管线使用前请编辑输出目录：")
    print(f"  {CONFIG_DIR / 'translate.json'}  →  output_dir 填绝对路径")
    print("然后在 Claude Code 里直接：/watch <视频URL>  或  /video-translate <视频URL>")
    return 0


if __name__ == "__main__":
    if "--check" in sys.argv:
        raise SystemExit(0 if check_deps() else 1)
    raise SystemExit(install())
