#!/usr/bin/env python3
"""video-skills 共享配置层。

统一配置目录 ~/.config/video-skills/：
  .env            敏感项 + 简单偏好（GROQ_API_KEY / OPENAI_API_KEY / WATCH_DETAIL / SETUP_COMPLETE）
  translate.json  翻译管线结构化配置（output_dir / settings.watermark_* / settings.subtitle_type）

同时提供跨平台的二进制定位 find_binary()（Windows 下额外扫 WinGet 安装路径）。
"""
from __future__ import annotations

import glob
import json
import os
import shutil
import sys
from pathlib import Path

# Windows 控制台/管道默认 GBK，与脚本的中文 UTF-8 输出互啃 —— 统一强制 UTF-8
for _stream in (sys.stdout, sys.stderr):
    if _stream is not None and hasattr(_stream, "reconfigure"):
        try:
            _stream.reconfigure(encoding="utf-8")
        except (OSError, ValueError):
            pass


CONFIG_DIR = Path.home() / ".config" / "video-skills"
CONFIG_FILE = CONFIG_DIR / ".env"
TRANSLATE_CONFIG_FILE = CONFIG_DIR / "translate.json"

DEFAULT_DETAIL = "balanced"

DETAILS = {"transcript", "efficient", "balanced", "token-burner"}


# ---------------------------------------------------------------------------
# 二进制定位（跨平台，Windows WinGet 感知）
# ---------------------------------------------------------------------------

_BINARY_CACHE: dict[str, str | None] = {}


def find_binary(name: str) -> str | None:
    """定位可执行文件，返回绝对路径；找不到返回 None。

    顺序：PATH（shutil.which）→ Windows WinGet Links 目录 →
    WinGet Packages 下的 ffmpeg/ffprobe bin 目录（WinGet 装完当前会话
    PATH 未刷新时兜底）。结果按进程缓存。
    """
    if name in _BINARY_CACHE:
        return _BINARY_CACHE[name]

    found = shutil.which(name)
    if not found and os.name == "nt":
        localappdata = os.environ.get("LOCALAPPDATA", "")
        if localappdata:
            link = Path(localappdata) / "Microsoft" / "WinGet" / "Links" / f"{name}.exe"
            if link.exists():
                found = str(link)
        if not found and localappdata and name in ("ffmpeg", "ffprobe"):
            pattern = os.path.join(
                localappdata, "Microsoft", "WinGet", "Packages",
                "Gyan.FFmpeg*", "**", "bin", f"{name}.exe",
            )
            matches = glob.glob(pattern, recursive=True)
            if matches:
                found = matches[0]

    _BINARY_CACHE[name] = found
    return found


def install_hint(name: str) -> str:
    """给定缺失的二进制名，返回当前平台的安装命令提示。"""
    pkg = "ffmpeg" if name in ("ffmpeg", "ffprobe") else name
    if sys.platform == "darwin":
        return f"brew install {pkg}"
    if os.name == "nt":
        if pkg == "ffmpeg":
            return "winget install Gyan.FFmpeg"
        return f"winget install yt-dlp.yt-dlp 或 pip install --user {pkg}"
    if pkg == "ffmpeg":
        return "sudo apt install ffmpeg（或 sudo dnf install ffmpeg）"
    return f"pipx install {pkg}（或 pip install --user {pkg}）"


def require_binary(name: str) -> str:
    """定位可执行文件；找不到时带安装提示退出。"""
    found = find_binary(name)
    if not found:
        raise SystemExit(f"{name} 未安装。安装方法：{install_hint(name)}")
    return found


# ---------------------------------------------------------------------------
# .env 解析（watch 偏好 + API key）
# ---------------------------------------------------------------------------

def read_env_file(path: Path | None = None) -> dict[str, str]:
    if path is None:
        path = CONFIG_FILE
    values: dict[str, str] = {}
    if not path.exists():
        return values
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return values
    for line in lines:
        raw = line.strip()
        if not raw or raw.startswith("#") or "=" not in raw:
            continue
        key, _, value = raw.partition("=")
        value = value.strip()
        if len(value) >= 2 and value[0] in ('"', "'") and value[-1] == value[0]:
            value = value[1:-1]
        else:
            # 剥掉未加引号值后面的行内注释（前面带空白的 '#'）。
            # 否则 `WATCH_DETAIL=balanced  # note` 会解析成 "balanced  # note"
            # 校验失败后静默回落默认值。引号内 / API key 里的 '#' 不受影响。
            for i, ch in enumerate(value):
                if ch == "#" and i > 0 and value[i - 1] in " \t":
                    value = value[:i].rstrip()
                    break
        values[key.strip()] = value
    return values


def get_config() -> dict[str, object]:
    file_values = read_env_file()

    detail = (
        os.environ.get("WATCH_DETAIL")
        or file_values.get("WATCH_DETAIL")
        or DEFAULT_DETAIL
    )
    if detail not in DETAILS:
        detail = DEFAULT_DETAIL

    return {
        "detail": detail,
        "config_file": str(CONFIG_FILE),
    }


def frame_cap(detail: str) -> int | None:
    if detail == "efficient":
        return 50
    if detail == "balanced":
        return 100
    if detail == "token-burner":
        return None
    if detail == "transcript":
        return None
    return 100


# ---------------------------------------------------------------------------
# translate.json（翻译管线配置）
# ---------------------------------------------------------------------------

DEFAULT_TRANSLATE_SETTINGS = {
    "watermark_enabled": False,
    "watermark_text": "",
    "watermark_opacity": 0.28,
    "watermark_duration": 4,
    "watermark_fade": 0.5,
    "watermark_count": 3,
    "watermark_position": "top-left",
    "watermark_fontsize": 44,
    "subtitle_type": "zh",
}


def load_translate_config(path: Path | None = None) -> dict:
    """读取翻译管线配置。output_dir 必须是绝对路径（或以 ~ 开头）。

    返回 {"output_dir": Path, "settings": dict}。配置缺失/无效时 SystemExit，
    提示信息可直接转述给用户。
    """
    if path is None:
        path = TRANSLATE_CONFIG_FILE
    if not path.exists():
        raise SystemExit(
            f"翻译配置缺失：{path} 不存在。"
            f"请先运行安装器（python install.py 或 setup.py）生成，"
            f"再把 output_dir 设为一个绝对路径。"
        )
    try:
        cfg = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise SystemExit(f"翻译配置解析失败：{path}：{exc}")

    raw = str(cfg.get("output_dir", "")).strip()
    if not raw:
        raise SystemExit(f"翻译配置无效：{path} 缺少 output_dir（必须是绝对路径或以 ~ 开头）")
    root = Path(os.path.expanduser(raw)).resolve()
    if not root.is_absolute():
        raise SystemExit(f"翻译配置无效：output_dir 必须是绝对路径或以 ~ 开头（当前：{raw}）")

    settings = dict(DEFAULT_TRANSLATE_SETTINGS)
    settings.update(cfg.get("settings") or {})
    return {"output_dir": root, "settings": settings}


def ensure_output_root(root: Path) -> Path:
    """在输出根下创建 tmp/（中间产物）与 data/（最终产物）。"""
    (root / "tmp").mkdir(parents=True, exist_ok=True)
    (root / "data").mkdir(parents=True, exist_ok=True)
    return root


if __name__ == "__main__":
    # 快速自检：打印二进制定位与配置状态
    status = {
        "ffmpeg": find_binary("ffmpeg"),
        "ffprobe": find_binary("ffprobe"),
        "yt-dlp": find_binary("yt-dlp"),
        "config_file": str(CONFIG_FILE),
        "config_exists": CONFIG_FILE.exists(),
        "translate_config": str(TRANSLATE_CONFIG_FILE),
        "translate_config_exists": TRANSLATE_CONFIG_FILE.exists(),
    }
    print(json.dumps(status, ensure_ascii=False, indent=2))
