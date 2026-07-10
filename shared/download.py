#!/usr/bin/env python3
"""yt-dlp 下载器：下载视频/音频、抓取字幕（VTT），或解析本地文件路径。

合并了两套实战经验：
- 字幕优先（手动字幕 → 自动字幕），VTT 格式供 transcribe.parse_vtt 解析，
  无需 Whisper 就能拿到转录；
- 浏览器 cookies 自动兜底：YouTube 对无 cookies 的 yt-dlp 常返回
  403/SABR/PO-token 风控。首次尝试不带 cookies（更快），失败后自动追加
  --cookies-from-browser 重试。B 站的 AI 字幕/高清晰度需登录，同样被
  cookies 兜底覆盖。
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path
from urllib.parse import urlparse

SCRIPT_DIR = Path(__file__).parent.resolve()
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from config import require_binary  # noqa: E402


VIDEO_EXTS = {".mp4", ".mkv", ".webm", ".mov", ".m4v", ".avi", ".flv", ".wmv"}

# 字幕语言优先级（yt-dlp --sub-langs 语法）。中英双收：原文是英文时拿英文字幕，
# B 站等中文源拿中文字幕。
DEFAULT_SUB_LANGS = "en.*,zh.*,zh-Hans,zh-CN"


def is_url(source: str) -> bool:
    if source.startswith("-"):
        return False
    parsed = urlparse(source)
    return parsed.scheme in ("http", "https") and bool(parsed.netloc)


def resolve_local(path: str) -> dict:
    p = Path(path).expanduser().resolve()
    if not p.exists():
        raise SystemExit(f"File not found: {p}")
    if p.suffix.lower() not in VIDEO_EXTS:
        print(
            f"[video-skills] warning: {p.suffix} is not a known video extension, proceeding anyway",
            file=sys.stderr,
        )
    return {
        "video_path": str(p),
        "subtitle_path": None,
        "info": {"title": p.name, "url": str(p)},
        "downloaded": False,
    }


def _pick_subtitle(out_dir: Path, langs: str = DEFAULT_SUB_LANGS) -> Path | None:
    """在下载目录里挑最优字幕：按 langs 的语言顺序优先，否则取第一个。"""
    candidates = sorted(out_dir.glob("video*.vtt"))
    if not candidates:
        return None
    for lang in (token.strip().rstrip(".*") for token in langs.split(",")):
        if not lang:
            continue
        preferred = [c for c in candidates if f".{lang}" in c.name]
        if preferred:
            return preferred[0]
    return candidates[0]


def _pick_video(out_dir: Path) -> Path | None:
    for ext in (".mp4", ".mkv", ".webm", ".mov", ".m4a", ".mp3", ".opus"):
        for candidate in out_dir.glob(f"video*{ext}"):
            return candidate
    for candidate in out_dir.glob("video.*"):
        if candidate.suffix.lower() in VIDEO_EXTS:
            return candidate
    return None


def _run_with_cookie_fallback(
    cmd: list[str],
    url: str,
    browser: str | None,
    succeeded,
) -> subprocess.CompletedProcess:
    """先无 cookies 跑一次；判定失败后追加 --cookies-from-browser 重试。

    succeeded 是无参回调：检查产物是否落盘（yt-dlp 带 --ignore-errors 时
    退出码不可靠，以产物为准）。browser 为 None 时不做重试。
    """
    result = subprocess.run(cmd, stdout=sys.stderr, stderr=sys.stderr)
    if succeeded() or browser is None:
        return result
    print(
        f"[video-skills] 首次尝试未拿到产物 — 用 {browser} 浏览器 cookies 重试…",
        file=sys.stderr,
    )
    retry_cmd = cmd[:-2] + ["--cookies-from-browser", browser, "--", url]
    return subprocess.run(retry_cmd, stdout=sys.stderr, stderr=sys.stderr)


def fetch_captions(
    url: str,
    out_dir: Path,
    langs: str = DEFAULT_SUB_LANGS,
    browser: str | None = "chrome",
    proxy: str | None = None,
) -> dict:
    """只抓元数据 + 最优 VTT 字幕，不下载视频。"""
    ytdlp = require_binary("yt-dlp")

    out_dir.mkdir(parents=True, exist_ok=True)
    output_template = str(out_dir / "video.%(ext)s")
    cmd = [
        ytdlp,
        "--skip-download",
        "--write-info-json",
        "--write-subs",
        "--write-auto-subs",
        "--sub-langs", langs,
        "--sub-format", "vtt",
        "--convert-subs", "vtt",
        "--no-playlist",
        "--ignore-errors",
    ]
    if proxy:
        cmd += ["--proxy", proxy]
    cmd += ["-o", output_template, "--", url]

    # 元数据 info.json 落盘 = 没被风控挡住（字幕可以合法地不存在）
    _run_with_cookie_fallback(
        cmd, url, browser,
        succeeded=lambda: (out_dir / "video.info.json").exists(),
    )
    subtitle = _pick_subtitle(out_dir, langs)
    info = _read_info(out_dir / "video.info.json", url)
    return {
        "video_path": None,
        "subtitle_path": str(subtitle) if subtitle else None,
        "info": info or {"url": url},
        "downloaded": False,
    }


def _read_info(info_path: Path, url: str) -> dict:
    info: dict = {}
    if info_path.exists():
        try:
            raw = json.loads(info_path.read_text(encoding="utf-8"))
            info = {
                "title": raw.get("title"),
                "uploader": raw.get("uploader") or raw.get("channel"),
                "duration": raw.get("duration"),
                "url": raw.get("webpage_url") or url,
            }
        except Exception as exc:
            print(f"[video-skills] info.json parse failed: {exc}", file=sys.stderr)
            info = {"url": url}
    return info


def download_url(
    url: str,
    out_dir: Path,
    audio_only: bool = False,
    langs: str = DEFAULT_SUB_LANGS,
    browser: str | None = "chrome",
    proxy: str | None = None,
    fetch_subs: bool = True,
) -> dict:
    ytdlp = require_binary("yt-dlp")

    out_dir.mkdir(parents=True, exist_ok=True)
    output_template = str(out_dir / "video.%(ext)s")

    fmt = "ba/bestaudio" if audio_only else "bv*[height<=720]+ba/b[height<=720]/bv+ba/b"
    cmd = [
        ytdlp,
        "-N", "8",
        "-f", fmt,
        "--merge-output-format", "mp4",
        "--write-info-json",
    ]
    if fetch_subs:
        cmd += [
            "--write-subs",
            "--write-auto-subs",
            "--sub-langs", langs,
            "--sub-format", "vtt",
            "--convert-subs", "vtt",
        ]
    cmd += [
        "--no-playlist",
        "--ignore-errors",
        "--restrict-filenames",
    ]
    if proxy:
        cmd += ["--proxy", proxy]
    cmd += ["-o", output_template, "--", url]

    # yt-dlp 在字幕子请求失败（如 429）时可能非零退出，但视频本身下好了。
    # 以"视频文件落盘"为成功判据；没落盘再走 cookies 重试。
    result = _run_with_cookie_fallback(
        cmd, url, browser,
        succeeded=lambda: _pick_video(out_dir) is not None,
    )
    video = _pick_video(out_dir)
    if video is None:
        raise SystemExit(
            f"yt-dlp did not produce a video file in {out_dir} (exit {result.returncode})。"
            f"若是登录/地区限制视频，可试 --browser firefox/edge 换浏览器 cookies，"
            f"或 --proxy 走代理。"
        )

    subtitle = _pick_subtitle(out_dir, langs)
    info = _read_info(out_dir / "video.info.json", url)

    return {
        "video_path": str(video),
        "subtitle_path": str(subtitle) if subtitle else None,
        "info": info or {"url": url},
        "downloaded": True,
    }


def download(
    source: str,
    out_dir: Path,
    audio_only: bool = False,
    langs: str = DEFAULT_SUB_LANGS,
    browser: str | None = "chrome",
    proxy: str | None = None,
) -> dict:
    if is_url(source):
        return download_url(
            source, out_dir,
            audio_only=audio_only, langs=langs, browser=browser, proxy=proxy,
        )
    return resolve_local(source)


def main() -> int:
    ap = argparse.ArgumentParser(
        description="下载视频/音频或抓字幕（yt-dlp 包装，含浏览器 cookies 兜底）",
    )
    ap.add_argument("source", help="视频 URL 或本地文件路径")
    ap.add_argument("out_dir", help="输出目录")
    ap.add_argument("--audio", action="store_true", help="只下载音频")
    ap.add_argument("--captions-only", action="store_true", help="只抓字幕+元数据，不下载视频")
    ap.add_argument("--langs", default=DEFAULT_SUB_LANGS, help=f"字幕语言优先级（默认 {DEFAULT_SUB_LANGS}）")
    ap.add_argument("--browser", default="chrome", help="失败后从哪个浏览器读 cookies（默认 chrome；传 none 禁用）")
    ap.add_argument("--proxy", default=None, help="代理，例如 http://127.0.0.1:7890")
    args = ap.parse_args()

    browser = None if args.browser in ("none", "") else args.browser
    out_dir = Path(args.out_dir).expanduser()

    if args.captions_only and is_url(args.source):
        result = fetch_captions(args.source, out_dir, langs=args.langs, browser=browser, proxy=args.proxy)
    else:
        result = download(
            args.source, out_dir,
            audio_only=args.audio, langs=args.langs, browser=browser, proxy=args.proxy,
        )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
