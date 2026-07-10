#!/usr/bin/env python3
"""字幕 + 水印烧录命令组装器。

把「手拼 60 行 ffmpeg 命令」变成一行脚本调用，集中处理实战踩过的坑：
- Windows 路径在 filter 参数里必须转义（C:\\a\\b.srt → 'C\\:/a/b.srt'），
  手拼十拼九错；
- 双语走 `ass=` 滤镜（SRT inline 字号会被剥离），单语走 `subtitles=` +
  force_style；按 --subs 扩展名自动选；
- 音频强制转 AAC（-c:a aac -b:a 128k），禁止 -c:a copy——yt-dlp 下的
  Opus 音轨会导致 X/微信/微博上传失败；
- 水印 drawtext 必须用 fontfile= 文件路径（macOS fontconfig 索引不到苹方，
  font= 名称查找会回退 Verdana 出方块）；水印时间点按视频时长自动分布；
- 字幕 + 水印在同一条 -vf 里一次编码完成，避免二次画质损失。

用 --dry-run 先打印最终命令供审查，再真跑。
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

SCRIPT_DIR = Path(__file__).parent.resolve()
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from config import require_binary  # noqa: E402
from fonts import subtitle_font_name, watermark_fontfile  # noqa: E402


DEFAULT_FONT_SIZE = 20
DEFAULT_MARGIN_V = 30
# 原视频底部已有硬字幕时的避让档（相对 PlayResY=288 的相对值，非像素）
AVOID_PRESETS = {
    "single": {"margin_v": 42, "font_size": 19},
    "double": {"margin_v": 60, "font_size": 19},
}


def probe(video: Path) -> dict:
    """ffprobe 取时长 + 分辨率。"""
    ffprobe = require_binary("ffprobe")
    result = subprocess.run(
        [
            ffprobe, "-v", "quiet", "-print_format", "json",
            "-show_format", "-show_streams", str(video.resolve()),
        ],
        capture_output=True, text=True,
    )
    if result.returncode != 0:
        raise SystemExit(f"ffprobe failed: {result.stderr.strip()}")
    data = json.loads(result.stdout or "{}")
    duration = float((data.get("format") or {}).get("duration") or 0.0)
    width = height = None
    for stream in data.get("streams") or []:
        if stream.get("codec_type") == "video":
            width, height = stream.get("width"), stream.get("height")
            break
    return {"duration": duration, "width": width, "height": height}


def escape_filter_path(path: Path) -> str:
    """ffmpeg filter 参数里的文件路径转义（Windows 最大坑）。

    C:\\a\\b.srt → C\\:/a/b.srt：反斜杠换正斜杠 + 冒号转义。调用方再包单引号。
    """
    s = str(path.resolve()).replace("\\", "/")
    s = s.replace(":", "\\:")
    return s


def escape_drawtext_text(text: str) -> str:
    """drawtext text= 值的转义：反斜杠、单引号、冒号、逗号、分号、百分号。"""
    out = []
    for ch in text:
        if ch in ("\\", "'", ":", ",", ";", "%"):
            out.append("\\" + ch)
        else:
            out.append(ch)
    return "".join(out)


def watermark_schedule(duration: float, count_min: int = 3, dwell: float = 4.0) -> list[tuple[float, float]]:
    """按视频时长算水印出现的 (start, dwell) 列表。

    <30 分钟 → 3 次 4 秒；30-60 分钟 → 5 次 5 秒；
    >60 分钟 → 每 15 分钟 1 次（最少 5 次）10 秒。
    第一次固定在开头（~12s），最后一次固定在结尾前（~20s），中间均匀分布。
    """
    if duration <= 0:
        return []
    if duration > 3600:
        count, dwell = max(5, int(duration // 900)), 10.0
    elif duration > 1800:
        count, dwell = 5, 5.0
    else:
        count, dwell = max(3, count_min), dwell

    first = min(12.0, max(1.0, duration * 0.05))
    last = max(first, duration - 20.0 - dwell)
    if count == 1 or last <= first:
        return [(first, dwell)]
    step = (last - first) / (count - 1)
    return [(round(first + i * step, 1), dwell) for i in range(count)]


def build_drawtext(
    text: str,
    fontfile: str,
    schedule: list[tuple[float, float]],
    opacity: float = 0.28,
    fade: float = 0.5,
    fontsize: int = 44,
    x: int = 40,
    y: int = 35,
) -> list[str]:
    """每个水印出现时段一个 drawtext 滤镜（渐入渐出靠 alpha 表达式）。"""
    escaped_text = escape_drawtext_text(text)
    escaped_font = escape_filter_path(Path(fontfile))
    filters = []
    for start, dwell in schedule:
        end = start + dwell
        alpha = (
            f"{opacity}*min(min((t-{start})/{fade}\\,1)\\,({end}-t)/{fade})"
        )
        filters.append(
            f"drawtext=text='{escaped_text}'"
            f":fontfile='{escaped_font}'"
            f":fontsize={fontsize}:fontcolor=white"
            f":alpha='{alpha}'"
            f":x={x}:y={y}"
            f":enable='between(t\\,{start}\\,{end})'"
        )
    return filters


def build_subtitle_filter(
    subs: Path,
    font_name: str,
    font_size: int,
    margin_v: int,
) -> str:
    escaped = escape_filter_path(subs)
    if subs.suffix.lower() == ".ass":
        # 双语 ASS：字号/边距在 ASS 文件里（bilingual_ass.py 已写好），不再覆盖
        return f"ass='{escaped}'"
    force_style = (
        f"FontName={font_name},Bold=1,FontSize={font_size},"
        f"PrimaryColour=&H00FFFFFF,OutlineColour=&H40000000,"
        f"Outline=1,Shadow=0,MarginV={margin_v}"
    )
    return f"subtitles='{escaped}':force_style='{force_style}'"


def main() -> int:
    ap = argparse.ArgumentParser(description="烧录字幕（+可选水印）到视频，一次编码完成")
    ap.add_argument("video", help="输入视频路径")
    ap.add_argument("--subs", required=True, help="字幕路径（.srt 走 subtitles+force_style；.ass 走 ass= 滤镜）")
    ap.add_argument("--output", "-o", default=None, help="输出路径（默认 <视频名>-中文字幕.mp4，同目录）")
    ap.add_argument("--font-name", default=None, help="字幕字体名（默认按平台选：雅黑/苹方/Noto）")
    ap.add_argument("--font-size", type=int, default=None, help=f"字幕字号（默认 {DEFAULT_FONT_SIZE}）")
    ap.add_argument("--margin-v", type=int, default=None,
                    help=f"字幕底部边距（默认 {DEFAULT_MARGIN_V}；注意是相对 PlayResY=288 的相对值，非像素）")
    ap.add_argument("--avoid-hardsub", choices=["single", "double"], default=None,
                    help="原视频底部已有硬字幕时的避让档：single（单行）/ double（两行或动态变行）")
    ap.add_argument("--watermark-text", default=None, help="水印文字（不传 = 不加水印）")
    ap.add_argument("--watermark-opacity", type=float, default=0.28)
    ap.add_argument("--watermark-fade", type=float, default=0.5)
    ap.add_argument("--watermark-fontsize", type=int, default=44)
    ap.add_argument("--dry-run", action="store_true", help="只打印最终 ffmpeg 命令，不执行")
    args = ap.parse_args()

    video = Path(args.video).expanduser()
    subs = Path(args.subs).expanduser()
    if not video.exists():
        raise SystemExit(f"视频不存在：{video}")
    if not subs.exists():
        raise SystemExit(f"字幕不存在：{subs}")

    meta = probe(video)

    font_size = args.font_size if args.font_size is not None else DEFAULT_FONT_SIZE
    margin_v = args.margin_v if args.margin_v is not None else DEFAULT_MARGIN_V
    if args.avoid_hardsub:
        preset = AVOID_PRESETS[args.avoid_hardsub]
        if args.font_size is None:
            font_size = preset["font_size"]
        if args.margin_v is None:
            margin_v = preset["margin_v"]

    font_name = args.font_name or subtitle_font_name()

    vf_parts = [build_subtitle_filter(subs, font_name, font_size, margin_v)]

    if args.watermark_text:
        fontfile = watermark_fontfile()
        if fontfile is None:
            raise SystemExit(
                "找不到可用的水印中文字体文件（drawtext 需要 fontfile 路径）。"
                "跳过水印请去掉 --watermark-text。"
            )
        schedule = watermark_schedule(meta["duration"])
        vf_parts.extend(build_drawtext(
            args.watermark_text, fontfile, schedule,
            opacity=args.watermark_opacity,
            fade=args.watermark_fade,
            fontsize=args.watermark_fontsize,
        ))

    output = Path(args.output).expanduser() if args.output else video.with_name(f"{video.stem}-中文字幕.mp4")

    ffmpeg = require_binary("ffmpeg")
    cmd = [
        ffmpeg, "-y",
        "-i", str(video.resolve()),
        "-vf", ",".join(vf_parts),
        # 音频必转 AAC：yt-dlp 常给 Opus 音轨，X/微信/微博不认，禁 -c:a copy
        "-c:a", "aac", "-b:a", "128k",
        str(output.resolve()),
    ]

    if args.dry_run:
        print("[burn] dry-run。分辨率 "
              f"{meta['width']}x{meta['height']}，时长 {meta['duration']:.1f}s。命令：", file=sys.stderr)
        print(json.dumps(cmd, ensure_ascii=False, indent=2))
        return 0

    print(f"[burn] 烧录中（{meta['width']}x{meta['height']}，{meta['duration']:.1f}s）→ {output}", file=sys.stderr)
    result = subprocess.run(cmd)
    if result.returncode != 0:
        raise SystemExit(f"ffmpeg 烧录失败（exit {result.returncode}）。可用 --dry-run 检查命令。")
    print(f"[burn] 完成：{output}", file=sys.stderr)
    print(str(output))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
