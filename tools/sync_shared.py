#!/usr/bin/env python3
"""shared/ → skills/*/scripts/ 单向同步。

shared/ 是共享脚本的唯一可编辑源；每个 skill 的 scripts/ 里放副本，
保证 skill 目录自包含（npx skills add / 手工复制单个 skill 都能独立工作）。

用法：
    python tools/sync_shared.py           # 复制（覆盖副本）
    python tools/sync_shared.py --check   # 只比对，漂移时非零退出（给测试/CI 用）

改共享代码的正确姿势：改 shared/ 下的源 → 跑本脚本 → 提交。
直接改 skill 里的副本会被 --check（tests/test_sync.py）拦下。
"""
from __future__ import annotations

import hashlib
import shutil
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SHARED_DIR = REPO_ROOT / "shared"
TARGET_DIRS = [
    REPO_ROOT / "skills" / "watch" / "scripts",
    REPO_ROOT / "skills" / "video-translate" / "scripts",
]
SHARED_FILES = ["config.py", "download.py", "whisper_api.py", "local_whisper.py", "setup.py"]


def _digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def check() -> int:
    drift = []
    for name in SHARED_FILES:
        src = SHARED_DIR / name
        if not src.exists():
            drift.append(f"MISSING SOURCE: {src}")
            continue
        for target_dir in TARGET_DIRS:
            dst = target_dir / name
            if not dst.exists():
                drift.append(f"MISSING COPY: {dst}")
            elif _digest(dst) != _digest(src):
                drift.append(f"DRIFT: {dst} != {src}")
    if drift:
        for line in drift:
            print(line, file=sys.stderr)
        print(
            "\n副本与 shared/ 源不一致。改共享代码请改 shared/ 下的源，"
            "然后运行: python tools/sync_shared.py",
            file=sys.stderr,
        )
        return 1
    print("sync check OK: all copies match shared/")
    return 0


def sync() -> int:
    for name in SHARED_FILES:
        src = SHARED_DIR / name
        if not src.exists():
            print(f"ERROR: source missing: {src}", file=sys.stderr)
            return 1
        for target_dir in TARGET_DIRS:
            target_dir.mkdir(parents=True, exist_ok=True)
            dst = target_dir / name
            shutil.copyfile(src, dst)
            print(f"synced {name} -> {dst.relative_to(REPO_ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(check() if "--check" in sys.argv else sync())
