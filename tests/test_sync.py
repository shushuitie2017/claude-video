"""shared/ 副本一致性红线：改了副本忘改源（或反之）直接红。"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

SYNC = Path(__file__).resolve().parent.parent / "tools" / "sync_shared.py"


def test_copies_match_shared_source():
    proc = subprocess.run(
        [sys.executable, str(SYNC), "--check"],
        capture_output=True, text=True, encoding="utf-8",
    )
    assert proc.returncode == 0, (
        f"shared copies drifted:\n{proc.stderr}\n"
        "改共享代码请改 shared/ 下的源，然后运行 python tools/sync_shared.py"
    )
