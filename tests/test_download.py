"""yt-dlp argv construction for download.py.

Regression guards:
- ``--sub-langs all`` makes yt-dlp fetch YouTube's hundreds of auto-translated
  caption tracks, which can take minutes. The request must stay bounded to the
  configured English+Chinese pattern.
- Cookie fallback: when the first attempt produces nothing, the retry must
  append ``--cookies-from-browser``; with browser=None there is no retry.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

SCRIPTS_DIR = Path(__file__).resolve().parent.parent / "skills" / "watch" / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))

import download  # noqa: E402

URL = "https://www.youtube.com/watch?v=rlOpbu3Enkw"


def _capture_argv(monkeypatch: pytest.MonkeyPatch) -> list[list[str]]:
    """Stub subprocess.run inside download.py and record every argv."""
    calls: list[list[str]] = []

    class _Result:
        returncode = 0
        stdout = ""
        stderr = ""

    def fake_run(cmd, *args, **kwargs):
        calls.append(list(cmd))
        return _Result()

    monkeypatch.setattr(download.subprocess, "run", fake_run)
    return calls


def _sub_langs(argv: list[str]) -> str:
    idx = argv.index("--sub-langs")
    return argv[idx + 1]


def _assert_bounded_langs(langs: str) -> None:
    tokens = langs.split(",")
    assert "all" not in tokens, f"sub-langs must not request all languages, got {langs!r}"
    assert all(
        t.startswith("en") or t.startswith("zh") for t in tokens
    ), f"sub-langs must stay English/Chinese-bounded, got {langs!r}"


def test_fetch_captions_requests_bounded_langs(monkeypatch, tmp_path):
    calls = _capture_argv(monkeypatch)
    download.fetch_captions(URL, tmp_path / "download", browser=None)
    _assert_bounded_langs(_sub_langs(calls[0]))


def test_download_url_requests_bounded_langs(monkeypatch, tmp_path):
    calls = _capture_argv(monkeypatch)
    # _pick_video returns None with no real file, which raises SystemExit after
    # the yt-dlp argv is already built — that's all we need to inspect.
    with pytest.raises(SystemExit):
        download.download_url(URL, tmp_path / "download", browser=None)
    _assert_bounded_langs(_sub_langs(calls[0]))


def test_cookie_fallback_retries_with_browser(monkeypatch, tmp_path):
    calls = _capture_argv(monkeypatch)
    with pytest.raises(SystemExit):
        download.download_url(URL, tmp_path / "download", browser="firefox")
    assert len(calls) == 2, "first attempt failed → must retry once with cookies"
    assert "--cookies-from-browser" not in calls[0]
    idx = calls[1].index("--cookies-from-browser")
    assert calls[1][idx + 1] == "firefox"
    assert calls[1][-1] == URL, "retry must keep the URL as the final arg after --"


def test_no_cookie_retry_when_browser_disabled(monkeypatch, tmp_path):
    calls = _capture_argv(monkeypatch)
    with pytest.raises(SystemExit):
        download.download_url(URL, tmp_path / "download", browser=None)
    assert len(calls) == 1


def test_no_cookie_retry_when_first_attempt_succeeds(monkeypatch, tmp_path):
    calls = _capture_argv(monkeypatch)
    # 伪造首跑产物：info.json 落盘 = fetch_captions 视为成功
    out_dir = tmp_path / "download"
    out_dir.mkdir(parents=True)
    (out_dir / "video.info.json").write_text("{}", encoding="utf-8")
    download.fetch_captions(URL, out_dir, browser="chrome")
    assert len(calls) == 1


def test_is_url_rejects_flags_and_paths():
    assert download.is_url("https://youtu.be/x")
    assert not download.is_url("-rf")
    assert not download.is_url(r"C:\videos\a.mp4")
    assert not download.is_url("/home/user/a.mp4")
