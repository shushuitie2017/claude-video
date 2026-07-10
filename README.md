<div align="center">

# video-skills

**让 Claude 看懂视频、翻译视频。**

粘一个链接，Claude 就能回答"这视频讲了什么"；再一句话，外语视频变成带中文字幕的视频 + 中文文稿。

[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
[![Platform](https://img.shields.io/badge/platform-Windows%20%7C%20macOS%20%7C%20Linux-lightgrey)](#安装)
[![Python](https://img.shields.io/badge/python-3.9%2B%20stdlib-green)](#依赖)
[![Tests](https://img.shields.io/badge/tests-133%20passed-brightgreen)](#开发)

</div>

---

## 30 秒看效果

```
你：  /watch https://youtu.be/xxxx 这个新功能到底解决了什么问题？
Claude：（下载→抽帧→拿转录→逐帧看完）
       这条视频演示的是……在 02:14 的画面里可以看到……结论是……

你：  /video-translate https://youtu.be/xxxx 双语
Claude：（转写→翻译润色→烧录中大英小双语字幕→截帧自检）
       已输出：xxx-中文字幕.mp4 + xxx-原文.md + xxx-中文.md
```

## 两个 skill，各管一件事

| Skill | 一句话 | 产出 |
|-------|--------|------|
| **/watch** | 让 Claude "看"视频回答任何问题 | 时间戳引用的分析回答 |
| **/video-translate** | 外语视频 → 中文字幕视频 + 文稿 | 烧好字幕的 mp4 + Markdown |

底座共享：yt-dlp 下载（带浏览器 cookies 自动兜底，治 YouTube 风控与 B 站登录墙）+ 三级转录（**原生字幕 → 本地 faster-whisper → Whisper API**，能免费就免费）。

## 特性

- **智能抽帧**：场景感知选帧 + 按时长预算 + 感知去重（静止画面不浪费 token），四档精度拨盘 `transcript / efficient / balanced / token-burner`
- **词级时间戳字幕**：本地 faster-whisper 词级转写，按「句子+停顿」断句——字幕不抢跑、不挤条，说完正好换条
- **人类级润色**：ASR 纠错、去口头语、断句 ≤12 字、专有名词保留原文、静音检测裁超长挂屏
- **双语字幕**：中文大 / 英文小（1.7:1 实测反差），真 ASS 烧录（SRT inline 字号会被 ffmpeg 剥离——踩过）
- **间歇水印**（可选）：按时长自动排布，渐隐渐出
- **Windows 一等公民**：WinGet 路径自动探测、微软雅黑字体链、filter 路径转义全部脚本内处理；macOS/Linux 同样开箱即用
- **零 pip 强依赖**：核心纯 stdlib；faster-whisper 是可选增强（没有就走字幕/API）

## 安装

```bash
git clone https://github.com/shushuitie2017/video-skills.git
cd video-skills
python install.py
```

或作为 Claude Code 插件：

```
/plugin marketplace add shushuitie2017/video-skills
```

外部工具（`install.py` 会检查并给出对应平台命令）：

```bash
# Windows
winget install Gyan.FFmpeg ; winget install yt-dlp.yt-dlp
# macOS
brew install ffmpeg yt-dlp
# 推荐：免费本地转录引擎（翻译管线必备其一）
pip install faster-whisper
```

## 配置

一处配置目录 `~/.config/video-skills/`：

| 文件 | 作用 |
|------|------|
| `.env` | 可选的 Whisper API key（`GROQ_API_KEY` 优先 / `OPENAI_API_KEY`）+ /watch 默认精度档 |
| `translate.json` | 翻译管线输出目录（**必填绝对路径**）+ 水印开关与参数 |

不配任何 API key 也能用：有字幕的视频全免费；无字幕的靠本地 faster-whisper（也免费）。

## 支持的视频来源

YouTube、Bilibili、X/Twitter、TikTok、Vimeo、Twitch 剪辑等 yt-dlp 支持的数百个站点，以及本地文件（mp4/mkv/webm/mov…）。被风控时自动读浏览器 cookies 重试（Chrome 默认，可换 Firefox/Edge）。

## 诚实边界

- 最佳体验是 10 分钟内的视频；更长的建议聚焦片段（`--start/--end`）或分段处理
- 直播流不支持，需等视频完结
- 需登录/地区锁定的内容，cookies 兜底也救不了的就是救不了
- 翻译质量取决于当前对话里的模型——这套 skill 负责把转写、断句、时间轴、烧录做到位

## 开发

```bash
python -m pytest -q          # 133 个测试，零网络（ffmpeg 合成测试视频）
python tools/sync_shared.py  # 改了 shared/ 后同步到两个 skill（--check 验漂移）
```

共享脚本唯一源在 `shared/`，两个 skill 的 `scripts/` 里是同步副本（保证 skill 目录自包含、可单独安装）；`tests/test_sync.py` 拦截手改副本导致的漂移。

## License

[MIT](LICENSE)
