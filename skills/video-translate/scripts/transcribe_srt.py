#!/usr/bin/env python3
"""生成精确时间戳的 SRT 字幕文件（翻译管线的时间源）。

引擎两级：
- faster（默认）：本地 faster-whisper，词级时间戳，零 API 费；
- api：Groq/OpenAI Whisper API 词级兜底（faster-whisper 装不了时用，
  需要 GROQ_API_KEY 或 OPENAI_API_KEY）。

两条路线共用同一套断句逻辑（local_whisper.merge_words_to_segments，
按「句子 + 停顿」切），保证出的 SRT 断句行为一致。

用法：
    python transcribe_srt.py <音频文件> [--output <SRT路径>] [--engine faster|api]
                             [--language <语言>] [--max-line-ms N] [--pause-ms N]
"""

import argparse
import sys
from pathlib import Path

SCRIPT_DIR = Path(__file__).parent.resolve()
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

import local_whisper  # noqa: E402


def _transcribe_api(audio_path: str, max_line_ms: int, pause_ms: int) -> list[dict]:
    """API 词级兜底：拿 words → 复用同一套断句。"""
    import whisper_api

    segments, words, backend = whisper_api.transcribe_audio_words(Path(audio_path))
    print(f"转写引擎：Whisper API ({backend})", file=sys.stderr)
    if words:
        pseudo_segments = local_whisper.words_from_dicts(words)
        return local_whisper.merge_words_to_segments(
            pseudo_segments, max_line_ms=max_line_ms, pause_ms=pause_ms,
        )
    # 该后端没返回词级时间戳 → 降级 segment 级，字幕对轴精度下降，明示用户
    print(
        "警告：API 未返回词级时间戳，降级为 segment 级 — 烧录字幕的对轴精度会下降。"
        "建议安装本地引擎：pip install faster-whisper",
        file=sys.stderr,
    )
    return segments


def transcribe_to_srt(audio_path: str, output_path: str, engine: str = "faster",
                      model_name: str = "large-v3-turbo",
                      language: str = None, max_line_ms: int = 6000,
                      pause_ms: int = 500):
    """主函数：转写音频并输出 SRT。faster-whisper 缺失时提示 API 路线。"""
    if engine == "api":
        srt_segments = _transcribe_api(audio_path, max_line_ms, pause_ms)
    else:
        segments = local_whisper.transcribe_words(
            audio_path, model_name=model_name, language=language,
        )
        if segments is None:
            print(
                "faster-whisper 不可用。两个选择：\n"
                "  1. pip install faster-whisper（推荐，本地零费用）\n"
                "  2. 配置 GROQ_API_KEY/OPENAI_API_KEY 后加 --engine api",
                file=sys.stderr,
            )
            sys.exit(1)
        srt_segments = local_whisper.merge_words_to_segments(
            segments, max_line_ms=max_line_ms, pause_ms=pause_ms,
        )

    local_whisper.write_srt(srt_segments, output_path)
    print(f"完成！共 {len(srt_segments)} 条字幕 → {output_path}", file=sys.stderr)
    return output_path


def main():
    parser = argparse.ArgumentParser(description="生成精确时间戳 SRT（默认本地 faster-whisper）")
    parser.add_argument("audio", help="音频文件路径")
    parser.add_argument("--output", "-o", help="输出 SRT 路径（默认与音频同名）")
    parser.add_argument("--engine", "-e", default="faster", choices=["faster", "api"],
                        help="转写引擎（默认 faster；api = Groq/OpenAI 词级兜底）")
    parser.add_argument("--model", "-m", default="large-v3-turbo",
                        help="faster-whisper 模型（默认 large-v3-turbo）")
    parser.add_argument("--language", "-l", default=None,
                        help="语言代码（默认自动检测，可指定 en/zh/ja 等）")
    parser.add_argument("--max-line-ms", type=int, default=6000,
                        help="单条字幕最大时长毫秒数（默认 6000）")
    parser.add_argument("--pause-ms", type=int, default=500,
                        help="词间停顿超过此毫秒数则断句（默认 500）")

    args = parser.parse_args()

    audio_path = Path(args.audio)
    if not audio_path.exists():
        print(f"错误：文件不存在 {audio_path}", file=sys.stderr)
        sys.exit(1)

    output_path = args.output or str(audio_path.with_suffix(".srt"))

    transcribe_to_srt(
        str(audio_path),
        output_path,
        engine=args.engine,
        model_name=args.model,
        language=args.language,
        max_line_ms=args.max_line_ms,
        pause_ms=args.pause_ms,
    )


if __name__ == "__main__":
    main()
