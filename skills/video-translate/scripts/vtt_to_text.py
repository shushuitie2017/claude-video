#!/usr/bin/env python3
"""VTT/SRT 字幕 → 纯文本（每行一句，去序号/时间戳/标签/滚动重复）。

只出文档场景用：清洗后的文本比原始 VTT 省约一半 token。

用法：python vtt_to_text.py <字幕文件> [--output <txt路径>]（默认打印到 stdout）
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

TS_RE = re.compile(r"\d{2}:\d{2}(:\d{2})?[.,]\d{3}\s+-->")
TAG_RE = re.compile(r"<[^>]+>")
INDEX_RE = re.compile(r"^\d+$")


def clean_lines(text: str) -> list[str]:
    out: list[str] = []
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line == "WEBVTT":
            continue
        if line.startswith(("NOTE", "STYLE", "Kind:", "Language:")):
            continue
        if TS_RE.search(line) or INDEX_RE.match(line):
            continue
        line = TAG_RE.sub("", line).strip()
        if not line:
            continue
        # 折叠 YouTube 自动字幕的滚动重复（相邻同句/前缀延长）
        if out and (line == out[-1] or line.startswith(out[-1] + " ")):
            out[-1] = line
            continue
        out.append(line)
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description="VTT/SRT → 纯文本清洗")
    ap.add_argument("subtitle", help="VTT 或 SRT 文件路径")
    ap.add_argument("--output", "-o", default=None, help="输出 txt 路径（默认 stdout）")
    args = ap.parse_args()

    path = Path(args.subtitle)
    if not path.exists():
        print(f"错误：文件不存在 {path}", file=sys.stderr)
        return 1

    lines = clean_lines(path.read_text(encoding="utf-8", errors="ignore"))
    body = "\n".join(lines) + "\n"
    if args.output:
        Path(args.output).write_text(body, encoding="utf-8")
        print(f"完成！{len(lines)} 行 → {args.output}", file=sys.stderr)
    else:
        sys.stdout.write(body)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
