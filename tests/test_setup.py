"""setup.py preflight: --json fields, keyless gating, local-whisper fallback."""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

SETUP = Path(__file__).resolve().parent.parent / "skills" / "watch" / "scripts" / "setup.py"


def _run(args, *, home=None, extra_env=None, allow_local=False):
    env = dict(os.environ)
    env.pop("WATCH_DETAIL", None)
    # Don't let a real key in the developer's shell env leak into the test.
    env.pop("GROQ_API_KEY", None)
    env.pop("OPENAI_API_KEY", None)
    env.pop("SETUP_COMPLETE", None)
    if not allow_local:
        # 开发机可能装了 faster-whisper——默认屏蔽，让 keyless 断言成立
        env["VIDEO_SKILLS_DISABLE_LOCAL_WHISPER"] = "1"
    if home is not None:
        env["HOME"] = str(home)
        env["USERPROFILE"] = str(home)  # Windows
    if extra_env:
        env.update(extra_env)
    return subprocess.run(
        [sys.executable, str(SETUP), *args],
        capture_output=True, text=True, env=env,
    )


def _write_env(home: Path, body: str) -> None:
    cfg = home / ".config" / "video-skills"
    cfg.mkdir(parents=True, exist_ok=True)
    f = cfg / ".env"
    f.write_text(body, encoding="utf-8")
    if os.name != "nt":
        f.chmod(0o600)


def test_json_reports_watch_detail(tmp_path):
    proc = _run(["--json"], home=tmp_path)
    assert proc.returncode == 0, proc.stderr
    data = json.loads(proc.stdout)
    assert data["watch_detail"] == "balanced"


def test_keyless_completed_setup_proceeds_silently(tmp_path):
    """A user who finished setup without a key must NOT be nagged forever."""
    _write_env(tmp_path, "GROQ_API_KEY=\nOPENAI_API_KEY=\nSETUP_COMPLETE=true\n")
    chk = _run(["--check"], home=tmp_path)
    assert chk.returncode == 0, f"keyless-complete should pass --check; got {chk.returncode}: {chk.stderr}"
    assert chk.stdout == "" and chk.stderr == ""

    js = json.loads(_run(["--json"], home=tmp_path).stdout)
    assert js["can_proceed"] is True
    assert js["first_run"] is False
    assert js["setup_complete"] is True
    # status still encourages a fallback even though we can proceed
    assert js["status"] == "needs_key"


def test_keyless_first_run_is_encouraged(tmp_path):
    """Genuine first run with no transcription fallback: --check exits 3."""
    _write_env(tmp_path, "GROQ_API_KEY=\nOPENAI_API_KEY=\n")
    chk = _run(["--check"], home=tmp_path)
    assert chk.returncode == 3, chk.stderr

    js = json.loads(_run(["--json"], home=tmp_path).stdout)
    assert js["can_proceed"] is False
    assert js["first_run"] is True


def test_key_present_is_ready(tmp_path):
    _write_env(tmp_path, "GROQ_API_KEY=sk-test-abc\n")
    chk = _run(["--check"], home=tmp_path)
    assert chk.returncode == 0, chk.stderr

    js = json.loads(_run(["--json"], home=tmp_path).stdout)
    assert js["status"] == "ready"
    assert js["can_proceed"] is True
    assert js["whisper_backend"] == "groq"


def test_local_whisper_counts_as_fallback(tmp_path):
    """本地 faster-whisper 在场时，keyless 首跑也应 can_proceed（三级策略第二级）。"""
    try:
        import faster_whisper  # noqa: F401
    except ImportError:
        import pytest
        pytest.skip("faster-whisper not installed in this environment")
    _write_env(tmp_path, "GROQ_API_KEY=\nOPENAI_API_KEY=\n")
    js = json.loads(_run(["--json"], home=tmp_path, allow_local=True).stdout)
    assert js["has_local_whisper"] is True
    assert js["status"] == "ready"
    assert js["can_proceed"] is True
