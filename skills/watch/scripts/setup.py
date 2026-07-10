#!/usr/bin/env python3
"""Setup / preflight for video-skills (/watch and /video-translate share this).

Modes:
  setup.py --check      Silent preflight. Exit 0 if ready, 2/3/4 on failure.
  setup.py --json       Machine-readable status for Claude to parse.
  setup.py              Installer. Auto-installs deps, scaffolds config, marks SETUP_COMPLETE.

Design:
- Silent on success: --check exits 0 with no output when everything's ready so
  the skills don't spam "setup is complete" on every turn.
- Idempotent: re-running the installer is safe — it never clobbers existing
  keys and only appends missing ones.
- SETUP_COMPLETE=true in ~/.config/video-skills/.env tells us the user has been
  through a successful installer run at least once.
- Never sudo. Auto-install via brew on macOS and winget on Windows. On Linux,
  print exact commands.
- Never write an API key to disk automatically — only scaffold placeholders.
"""
from __future__ import annotations

import json
import os
import platform
import subprocess
import sys
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))
from config import (  # noqa: E402
    CONFIG_DIR,
    CONFIG_FILE,
    TRANSLATE_CONFIG_FILE,
    find_binary,
    get_config,
)


REQUIRED_BINARIES = ["ffmpeg", "ffprobe", "yt-dlp"]
ENV_TEMPLATE = """# video-skills API configuration
#
# Whisper API fallback — used only when yt-dlp cannot get captions AND the
# free local engine (pip install faster-whisper) is not installed.
#
# Groq is preferred: it runs whisper-large-v3 at a fraction of OpenAI's price
# and is faster in practice. OpenAI is the compatible fallback.
#
# Get a Groq key:  https://console.groq.com/keys
# Get an OpenAI key:  https://platform.openai.com/api-keys
#
# Leave both blank to skip the API tier — /watch still works, and videos
# without captions can use local faster-whisper (or come back frames-only).

GROQ_API_KEY=
OPENAI_API_KEY=

# Default watch behavior (the /watch first-run wizard sets this for you).
# Allowed values: transcript | efficient | balanced | token-burner
# Keep the value on its own line with no trailing comment.
# WATCH_DETAIL=balanced
"""

TRANSLATE_TEMPLATE = {
    "output_dir": "~/Videos/video-skills-output",
    "settings": {
        "watermark_enabled": False,
        "watermark_text": "",
        "watermark_opacity": 0.28,
        "watermark_duration": 4,
        "watermark_fade": 0.5,
        "watermark_count": 3,
        "watermark_position": "top-left",
        "watermark_fontsize": 44,
        "subtitle_type": "zh",
    },
}


def _check_binaries() -> list[str]:
    return [b for b in REQUIRED_BINARIES if not find_binary(b)]


def _has_faster_whisper() -> bool:
    # 测试用开关：让 keyless 场景不被开发机上已装的 faster-whisper 干扰
    if os.environ.get("VIDEO_SKILLS_DISABLE_LOCAL_WHISPER"):
        return False
    try:
        import faster_whisper  # noqa: F401
        return True
    except ImportError:
        return False


_PERM_WARNED: set[str] = set()


def _check_file_permissions(path: Path) -> None:
    """Warn to stderr (once per path per process) if a secrets file is
    world/group readable. POSIX only — Windows uses ACLs, stat bits mean nothing."""
    if os.name == "nt":
        return
    key = str(path)
    if key in _PERM_WARNED:
        return
    try:
        mode = path.stat().st_mode
        if mode & 0o044:
            _PERM_WARNED.add(key)
            sys.stderr.write(
                f"[video-skills] WARNING: {path} is readable by other users. "
                f"Run: chmod 600 {path}\n"
            )
            sys.stderr.flush()
    except OSError:
        pass


def _read_env_key(name: str) -> str | None:
    value = os.environ.get(name)
    if value and value.strip():
        return value.strip()
    if not CONFIG_FILE.exists():
        return None
    _check_file_permissions(CONFIG_FILE)
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


def _have_api_key() -> tuple[bool, str | None]:
    if _read_env_key("GROQ_API_KEY"):
        return True, "groq"
    if _read_env_key("OPENAI_API_KEY"):
        return True, "openai"
    return False, None


def is_first_run() -> bool:
    """True if the installer hasn't completed successfully yet."""
    return _read_env_key("SETUP_COMPLETE") != "true"


def _scaffold_env() -> bool:
    """Create ~/.config/video-skills/.env with placeholders if missing."""
    if CONFIG_FILE.exists():
        return False
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    CONFIG_FILE.write_text(ENV_TEMPLATE, encoding="utf-8")
    try:
        CONFIG_FILE.chmod(0o600)
    except OSError:
        pass
    return True


def _scaffold_translate_config() -> bool:
    """Create ~/.config/video-skills/translate.json from template if missing."""
    if TRANSLATE_CONFIG_FILE.exists():
        return False
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    TRANSLATE_CONFIG_FILE.write_text(
        json.dumps(TRANSLATE_TEMPLATE, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return True


def _write_setup_complete() -> None:
    """Idempotently append SETUP_COMPLETE=true to .env."""
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    if CONFIG_FILE.exists():
        existing = CONFIG_FILE.read_text(encoding="utf-8")
        for line in existing.splitlines():
            if line.strip().startswith("SETUP_COMPLETE="):
                return
        if existing and not existing.endswith("\n"):
            existing += "\n"
        CONFIG_FILE.write_text(existing + "SETUP_COMPLETE=true\n", encoding="utf-8")
    else:
        CONFIG_FILE.write_text(ENV_TEMPLATE + "\nSETUP_COMPLETE=true\n", encoding="utf-8")
    try:
        CONFIG_FILE.chmod(0o600)
    except OSError:
        pass


def _pkg_names(missing: list[str]) -> list[str]:
    pkgs: list[str] = []
    for bin_name in missing:
        if bin_name in ("ffmpeg", "ffprobe"):
            if "ffmpeg" not in pkgs:
                pkgs.append("ffmpeg")
        elif bin_name not in pkgs:
            pkgs.append(bin_name)
    return pkgs


def _install_macos(missing: list[str]) -> tuple[bool, str]:
    if find_binary("brew") is None:
        return False, (
            "Homebrew is not installed. Install it from https://brew.sh, then re-run setup. "
            "Or install manually: `brew install " + " ".join(_pkg_names(missing)) + "`"
        )
    pkgs = _pkg_names(missing)
    if not pkgs:
        return True, "nothing to install"
    cmd = ["brew", "install", *pkgs]
    print(f"[setup] running: {' '.join(cmd)}", file=sys.stderr)
    result = subprocess.run(cmd)
    if result.returncode != 0:
        return False, f"brew install failed with exit code {result.returncode}"
    return True, f"installed via brew: {', '.join(pkgs)}"


WINGET_IDS = {"ffmpeg": "Gyan.FFmpeg", "yt-dlp": "yt-dlp.yt-dlp"}


def _install_windows(missing: list[str]) -> tuple[bool, str]:
    """Try winget auto-install; fall back to printing the exact commands."""
    winget = find_binary("winget")
    pkgs = _pkg_names(missing)
    if winget is None:
        cmds = "; ".join(f"winget install {WINGET_IDS.get(p, p)}" for p in pkgs)
        return False, f"winget not found. Install manually: {cmds}"
    installed = []
    for pkg in pkgs:
        pkg_id = WINGET_IDS.get(pkg, pkg)
        cmd = [
            winget, "install", "--id", pkg_id, "-e",
            "--accept-source-agreements", "--accept-package-agreements",
        ]
        print(f"[setup] running: winget install --id {pkg_id}", file=sys.stderr)
        result = subprocess.run(cmd)
        if result.returncode != 0:
            return False, (
                f"winget install {pkg_id} failed (exit {result.returncode}). "
                f"Install manually: winget install {pkg_id}"
            )
        installed.append(pkg)
    return True, f"installed via winget: {', '.join(installed)}"


def _install_hint_linux(missing: list[str]) -> str:
    pkgs = _pkg_names(missing)
    hints = []
    if "ffmpeg" in pkgs:
        hints.append("apt: `sudo apt install ffmpeg` or dnf: `sudo dnf install ffmpeg`")
    if "yt-dlp" in pkgs:
        hints.append("`pipx install yt-dlp` (recommended) or `pip install --user yt-dlp`")
    return "\n  ".join(hints) if hints else "nothing to install"


def _status() -> dict:
    """Structured preflight snapshot.

    `status` describes the *ideal* state (a transcription fallback is
    encouraged), so a keyless install without faster-whisper still reports
    `needs_key` on the very first run — that's the agent's cue to encourage
    one. `can_proceed` is the operational gate: the skills can run as long as
    the binaries are present AND the user has a transcription fallback
    (local or API key) or already finished setup (consciously opting out).
    """
    missing = _check_binaries()
    has_key, backend = _have_api_key()
    has_local = _has_faster_whisper()
    setup_complete = not is_first_run()

    if not missing and (has_key or has_local):
        status = "ready"
    elif missing and not (has_key or has_local):
        status = "needs_install_and_key"
    elif missing:
        status = "needs_install"
    else:
        status = "needs_key"

    can_proceed = (not missing) and (has_key or has_local or setup_complete)

    cfg = get_config()
    return {
        "status": status,
        "can_proceed": can_proceed,
        "first_run": not setup_complete,
        "setup_complete": setup_complete,
        "missing_binaries": missing,
        "whisper_backend": backend,
        "has_api_key": has_key,
        "has_local_whisper": has_local,
        "config_file": str(CONFIG_FILE),
        "translate_config_file": str(TRANSLATE_CONFIG_FILE),
        "translate_config_exists": TRANSLATE_CONFIG_FILE.exists(),
        "watch_detail": cfg["detail"],
        "platform": platform.system(),
    }


def cmd_check() -> int:
    """Silent-on-success preflight.

    Exit 0 with no output when the skills can run. A keyless user who already
    finished setup counts as ready — transcription fallback is encouraged, not
    required — so they are never nagged on follow-up calls.

    On a blocking state, print one actionable line to stderr:
      2 → binaries missing
      3 → genuine first run with no transcription fallback (encourage one)
      4 → both missing
    """
    s = _status()
    if s["can_proceed"]:
        return 0

    parts = []
    if s["missing_binaries"]:
        parts.append(f"missing binaries: {', '.join(s['missing_binaries'])}")
    if not s["has_api_key"] and not s["has_local_whisper"] and not s["setup_complete"]:
        parts.append(
            "no transcription fallback (pip install faster-whisper, "
            "or set GROQ_API_KEY / OPENAI_API_KEY)"
        )
    installer = Path(__file__).resolve()
    sys.stderr.write(
        f"[video-skills] setup incomplete ({'; '.join(parts)}). "
        f"Run: python {installer}\n"
    )
    sys.stderr.flush()

    if s["missing_binaries"] and not s["has_api_key"] and not s["has_local_whisper"]:
        return 4
    if s["missing_binaries"]:
        return 2
    return 3


def cmd_json() -> int:
    json.dump(_status(), sys.stdout, indent=2)
    sys.stdout.write("\n")
    return 0


def cmd_install() -> int:
    missing = _check_binaries()
    installed_deps = False
    if missing:
        system = platform.system()
        if system == "Darwin":
            ok, msg = _install_macos(missing)
        elif system == "Windows":
            ok, msg = _install_windows(missing)
        elif system == "Linux":
            print("[setup] dependencies missing on Linux — please install:", file=sys.stderr)
            print("  " + _install_hint_linux(missing), file=sys.stderr)
            return 2
        else:
            print(f"[setup] unsupported platform ({system}) for auto-install. Install manually:", file=sys.stderr)
            print(f"  missing: {', '.join(missing)}", file=sys.stderr)
            return 2
        print(f"[setup] {msg}", file=sys.stderr)
        if not ok:
            return 2
        still_missing = _check_binaries()
        if still_missing:
            print(
                f"[setup] still missing after install: {', '.join(still_missing)} "
                "(a new terminal may be needed for PATH refresh)",
                file=sys.stderr,
            )
            return 2
        installed_deps = True

    created = _scaffold_env()
    if created:
        print(f"[setup] created config: {CONFIG_FILE}")
    else:
        print(f"[setup] config exists: {CONFIG_FILE}")

    created_translate = _scaffold_translate_config()
    if created_translate:
        print(f"[setup] created translate config: {TRANSLATE_CONFIG_FILE}")
        print("  (edit output_dir there before using /video-translate)")

    has_key, backend = _have_api_key()
    has_local = _has_faster_whisper()
    if has_key or has_local:
        _write_setup_complete()
        fallback = f"whisper API ({backend})" if has_key else "local faster-whisper"
        print(f"[setup] ready. transcription fallback: {fallback}")
        if installed_deps:
            print("[setup] installed dependencies; video-skills is fully set up.")
        return 0

    print("")
    print("[setup] one step left: add a transcription fallback (pick one).")
    print("")
    print("  Free, local (recommended, needed for /video-translate subtitles):")
    print("    pip install faster-whisper")
    print("")
    print(f"  Or an API key — edit {CONFIG_FILE} and set either:")
    print("    GROQ_API_KEY=...    (preferred — cheaper, faster; console.groq.com/keys)")
    print("    OPENAI_API_KEY=...  (fallback; platform.openai.com/api-keys)")
    print("")
    print("  Without either, /watch still works but videos without captions come back frames-only.")
    return 3


def main() -> int:
    if len(sys.argv) > 1:
        arg = sys.argv[1]
        if arg == "--check":
            return cmd_check()
        if arg == "--json":
            return cmd_json()
    return cmd_install()


if __name__ == "__main__":
    raise SystemExit(main())
