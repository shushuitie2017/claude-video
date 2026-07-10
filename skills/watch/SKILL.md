---
name: watch
version: "1.0.0"
description: 看视频（URL 或本地文件）。yt-dlp 下载、ffmpeg 智能抽帧（场景感知/预算制/去重）、三级转录（原生字幕 → 本地 faster-whisper → Whisper API），把帧+带时间戳的转录交给 Claude 回答关于视频内容的任何问题。触发：用户贴视频链接提问、"看看这个视频""这个视频讲了什么""总结这个视频"、/watch。
argument-hint: "<视频URL或路径> [问题]"
allowed-tools: Bash, Read, AskUserQuestion
license: MIT
user-invocable: true
---

# /watch — 让 Claude 看视频

你没有视频输入能力；本 skill 给你一个。Python 脚本先抓字幕、按需下载视频、把帧抽成 JPEG（场景感知；`efficient` 档走快速关键帧）、拿到带时间戳的转录（原生字幕优先，其次本地 faster-whisper，最后 Whisper API），然后打印帧路径清单。你逐一 `Read` 帧路径看到画面，结合转录回答用户。

## 解析 `SKILL_DIR`（执行任何命令前先做）

下面所有 `python ...` 命令跑的都是 `SKILL_DIR/scripts/` 下的内置脚本。把 `SKILL_DIR` 设为**你刚 Read 的这份 SKILL.md 所在目录的绝对路径**——harness 在 Read 结果里告诉过你。脚本永远是本文件的直接兄弟目录（`SKILL_DIR/scripts/watch.py`），任何安装布局都如此：

```
Read ~/.claude/skills/watch/SKILL.md   → SKILL_DIR=~/.claude/skills/watch
Read ~/.codex/skills/watch/SKILL.md    → SKILL_DIR=~/.codex/skills/watch
```

后续命令里的 `${SKILL_DIR}` 一律替换为该字面路径。这在所有 harness（Claude Code、Codex、Cursor、Gemini CLI……）上通用，不依赖任何 harness 专属环境变量。每轮开始先守卫一次：

```bash
SKILL_DIR="<你 Read 的 SKILL.md 所在目录绝对路径>"
if [ ! -f "$SKILL_DIR/scripts/watch.py" ]; then
  echo "ERROR: scripts/watch.py not found under SKILL_DIR=$SKILL_DIR" >&2
  exit 1
fi
```

## 步骤 0 — 环境预检（每次 /watch 都跑，就绪时静默）

**Python 解释器：** 本 skill 所有命令按 **Windows 写法**用 `python`。macOS/Linux 上换成 `python3`（Windows 的 `python3` 是 Microsoft Store 占位符，跑不了脚本）。

会话内首次调用 /watch 时用结构化预检：

```bash
python "${SKILL_DIR}/scripts/setup.py" --json
```

按两个字段分支：

- **`can_proceed: true` 且 `first_run: false`** → 已配置好（用户可能有意跳过转录兜底——允许）。直接进步骤 1，不做任何评论。
- **`first_run: true`** → 真正的首次配置。按序做：
  1. `missing_binaries` 非空 → 先跑安装器（macOS brew / Windows winget 自动装，Linux 打印命令），确认二进制到位。**不许跳过这步直接问偏好。**
  2. 需要时再跑一次安装器，让它脚手架 `~/.config/video-skills/.env`（文件已存在时不会覆盖）。
  3. 鼓励配置转录兜底（`pip install faster-whisper` 免费本地档优先；或 Groq/OpenAI API key），并问下面的观看偏好，把选择写进 `.env` 后设 `SETUP_COMPLETE=true`。
- **`can_proceed: false` 且 `first_run: false`** → 之前配好但环境退化（如换系统后二进制丢了）。跑安装器补救后继续，不重新问偏好。

缺转录兜底是**鼓励补齐，不是硬阻塞**：首跑且二进制齐时 `status` 会是 `needs_key`——那是提示你鼓励用户补一个，不是不让跑。

同会话的后续 /watch 用静默检查（<100ms，exit 0 无输出直接继续，**不要向用户播报"已配置完成"**）：

```bash
python "${SKILL_DIR}/scripts/setup.py" --check
```

非零退出按表处理：

| Exit | 含义 | 动作 |
|------|------|------|
| `2` | 缺二进制（ffmpeg / ffprobe / yt-dlp） | 跑安装器 |
| `3` | 真首跑且无任何转录兜底 | 跑安装器脚手架配置，鼓励 faster-whisper 或 API key（用户可拒绝——用 `--no-whisper` 继续） |
| `4` | 都缺 | 跑安装器，再鼓励兜底 |

安装器幂等，随时可重跑：

```bash
python "${SKILL_DIR}/scripts/setup.py"
```

**API key 补写：** 用户愿意配 API key 时，用 `AskUserQuestion` 问是 Groq（推荐——便宜快）还是 OpenAI，然后把 `GROQ_API_KEY=...` 或 `OPENAI_API_KEY=...` 写进 `~/.config/video-skills/.env`。用户不想配就用 `--no-whisper` 继续，并告知无字幕视频只有帧。

**首跑观看偏好：** 安装器脚手架 `.env` 后，用 `AskUserQuestion` 问一个问题——默认精度档。按从轻到重的顺序给选项，`balanced` 标注（推荐）且**不要把推荐项挪到第一位**：

- `transcript` — 不抽帧，只要转录（有字幕时连视频都不下）。
- `efficient` — 快速关键帧（上限 50）。
- `balanced`（推荐）— 场景感知抽帧（上限 100，默认）。
- `token-burner` — 场景感知不设上限（最高保真，token 大户）。

把答案写入 `.env`，值单独一行**不带行尾注释**（`# 注释` 会破坏解析）：

```bash
WATCH_DETAIL=balanced
```

依赖、兜底选择、偏好都处理完后写 `SETUP_COMPLETE=true`。`SETUP_COMPLETE=true` 后不再问偏好。

## 适用场景

- 用户贴视频 URL 提问（YouTube、B站、Vimeo、X、TikTok、Twitch 剪辑……大多数 yt-dlp 支持的站点）。
- 用户指向本地视频文件（`.mp4`、`.mov`、`.mkv`、`.webm` 等）提问。
- 用户输入 `/watch <url或路径> [问题]`。

## 推荐限制

- **最佳精度：10 分钟以内的视频。** 帧覆盖密度与时长成反比。
- **全局采样上限 2 fps。** 任何预算或 `--fps` 都不会超过。
- **帧数上限由精度档决定**（`.env` 的 `WATCH_DETAIL` 或 `--detail`）：`transcript` 0 帧 / `efficient` 50 / `balanced` 100 / `token-burner` 不限（超 250 帧打软警告）。`--max-frames N` 可覆盖。
- **全片帧预算按时长分档**：≤30s 约 12-30 帧；30s-1min 约 40；1-3min 约 60；3-10min 约 80；>10min 顶到档位上限、稀疏铺开（打警告）。
- 用户给长视频时，考虑先问要不要聚焦某一段，别烧 token 做稀疏全扫。

## 调用方法

**步骤 1 — 解析输入。** 把视频来源（URL 或路径）和用户问题分开。例：`/watch https://youtu.be/abc 这是什么语言？` → source = URL，question = 这是什么语言？

**步骤 2 — 跑脚本。** 来源原样传入，除正常引号外不要自己做 shell 转义：

```bash
python "${SKILL_DIR}/scripts/watch.py" "<source>"
```

可选旗标：
- `--detail transcript|efficient|balanced|token-burner` — 精度/速度拨盘。
- `--start T` / `--end T` — 聚焦一段（`SS`、`MM:SS`、`HH:MM:SS`）。设了任一，fps 自动加密（见下）。
- `--timestamps T1,T2,…` — 在指定绝对时间点强制抓帧。读完转录后用它捕捉演讲者明说"看这里"的时刻（见「转录线索帧」）。
- `--max-frames N` — 收紧帧预算。
- `--resolution W` — 帧宽（默认 512；只在用户要读屏幕文字时升到 1024）。
- `--fps F` — 覆盖自动 fps（仍钳制 2 fps）。
- `--out-dir DIR` — 指定工作目录（默认自动建临时目录）。
- `--whisper groq|openai` — 强制指定 API 后端（跳过本地档）。
- `--no-local-whisper` — 跳过本地 faster-whisper，直接走 API。
- `--no-whisper` — 完全禁用转录兜底（无字幕时只有帧）。
- `--browser chrome|firefox|edge|none` — 下载被风控时从哪个浏览器读 cookies 重试（默认 chrome）。
- `--no-dedup` — 保留近似重复帧（默认丢弃静止画面/长驻幻灯片的重复帧，把预算留给不同内容；报告的 Frames 行会注明丢了几张）。

### 聚焦一段（更密采样）

用户问具体时刻——"2 分钟那里发生了什么？""看 0:45 到 1:00""开头 10 秒"——就传 `--start`/`--end`。聚焦预算更密（仍受 2 fps 与档位上限约束）：≤5s → 2 fps；5-15s → 2 fps（至多 30 帧）；15-30s → ~2 fps（60）；30-60s → ~1.3 fps（80）；60-180s → ~0.6 fps（100 封顶）。

聚焦模式适合：用户点名的任何时刻/区间；>10 分钟视频里问具体部分；全扫后细节不够的重扫。转录自动过滤到同一区间，帧时间戳始终是源视频绝对时间。

```bash
# 1 分钟视频的最后 10 秒
python "${SKILL_DIR}/scripts/watch.py" video.mp4 --start 50 --end 60
# 聚焦 2:15 → 2:45
python "${SKILL_DIR}/scripts/watch.py" "$URL" --start 2:15 --end 2:45 --fps 2
```

**步骤 3 — Read 脚本列出的每一个帧路径。** Read 工具直接把 JPEG 渲染成图像。一条消息里并行 Read 全部帧，按时间序一起看。每帧带 `t=MM:SS` 绝对时间戳，可与转录对齐。

**步骤 4 — 回答用户。** 你手里有两路证据：**帧**（每个时间点屏幕上是什么）+ **转录**（每个时间点说了什么；报告头部标注来源：`captions` / `whisper (local)` / `whisper (groq)` / `whisper (openai)`）。用户问了具体问题就直接回答并引用时间戳；没问就总结视频——结构、关键时刻、值得注意的画面、口述内容。`transcript` 档同样要**总结**，不要把全文转录贴进对话；用户明确要原文才给。

**步骤 5 — 清理。** 脚本末尾打印工作目录。用户不会追问这个视频就删掉；可能追问就留着。

## 转录线索帧

视觉选帧（场景/关键帧）会漏掉演讲者明确指向屏幕的时刻——"look here""注意看这里"——因为指着一页幻灯片讲话往往是*低*视觉变化。`--timestamps` 让你在那些时刻强制抓帧。**由你**读转录来判断哪些时刻值得抓（修辞性的"你看，重点是…"要忽略——这是判断题，不是正则能做的）：

1. 先跑一次拿到带时间戳的转录（任何档位都行）。
2. 扫描指示性话术，记下时间点。
3. 带 `--timestamps 4:32,7:10,9:55` 重跑；URL 来源的第二跑请指向工作目录里**已下载的本地文件**，避免重复下载。

线索帧默认叠加在档位选帧之上、优先占用帧预算（不会被均匀采样挤掉）；配合 `--start/--end` 时窗口外的线索点会被丢弃并在报告注明；`--detail transcript --timestamps …` 则只出线索帧。

## 转录（三级策略）

1. **原生字幕（免费，最快）**：yt-dlp 拉平台的手动/自动字幕（英文与中文优先）。
2. **本地 faster-whisper（免费，无需 key）**：`pip install faster-whisper` 即启用；无字幕时自动使用，词级精度。
3. **Whisper API 兜底**：本地引擎也没有时，提取音频（约 0.5 MB/分钟）上传到配了 key 的 API——Groq `whisper-large-v3`（推荐：更便宜更快，console.groq.com/keys）或 OpenAI `whisper-1`（platform.openai.com/api-keys）。key 存 `~/.config/video-skills/.env`。超过 25 MB 的音频自动分块转录。

## 失败处理

- **预检失败** → 跑 `python "${SKILL_DIR}/scripts/setup.py"`（macOS brew / Windows winget 自动装，脚手架配置文件）。API key 用 `AskUserQuestion` 问用户后写入 `.env`。
- **没有转录** → 字幕缺失且两级 Whisper 都不可用/失败。脚本会打印指引。带帧继续并告知用户。
- **长视频警告** → 在回答里承认覆盖稀疏，主动提出用 `--start/--end` 聚焦重扫，而不是重复稀疏全扫。
- **下载失败** → yt-dlp 报错进 stderr。脚本已自动用浏览器 cookies 重试过一次；仍失败时换 `--browser firefox`/`--browser edge` 或建议用户配代理。确认是需登录/地区锁的视频就直说，不要反复重试。
- **Whisper API 请求失败** → 错误在 stderr（多半是 key 无效或限流）。分块转录下个别块失败只损失局部；Groq 失败可 `--whisper openai` 换后端重试（反之亦然）。

## Token 效率

本 skill 的 token 大头是帧。数量级：80 帧 512px 宽约 50-80k 图像 token；转录很便宜（10 分钟视频几千 token）；`--resolution 1024` 让每帧 token 约翻四倍，确有必要才用。同会话已看过的视频，用户追问时**不要重跑脚本**——帧和转录都在你的上下文里，直接回答。

## 安全与权限

**本 skill 做什么：**
- 本地跑 `yt-dlp` 下载视频/拉字幕（公开数据；请求直达 URL 指向的站点；被风控时可从本机浏览器读 cookies 重试，cookies 不出本机）
- 本地跑 `ffmpeg`/`ffprobe` 抽帧、提取音频
- 本地跑 faster-whisper 转录（若已安装；数据不出本机）
- 仅当走 API 档时，把提取的音频发给 Groq（`api.groq.com`）或 OpenAI（`api.openai.com`）的转录接口
- 把视频、帧、音频、转录写到系统临时目录下的工作目录（或 `--out-dir`）
- 读写 `~/.config/video-skills/.env`（POSIX 下 0600）存 API key 与 `SETUP_COMPLETE` 标记；兜底也读当前目录 `.env`

**本 skill 不做什么：**
- 不把视频本体上传给任何 API——只有提取的音频、且只在需要 API 转录时才出网
- 不登录、不发帖、不动平台账号（cookies 只读、只给 yt-dlp 本地使用）
- 不跨供应商共享 key（Groq key 只发 api.groq.com，OpenAI key 只发 api.openai.com）
- 不把 key 写进 stdout/stderr/输出文件
- 除工作目录与 `~/.config/video-skills/` 外不持久化任何东西——步骤 5 记得清理

**内置脚本：** `scripts/watch.py`（入口）、`scripts/download.py`（yt-dlp 包装+cookies 兜底）、`scripts/frames.py`（抽帧引擎）、`scripts/transcribe.py`（VTT 解析）、`scripts/local_whisper.py`（本地转录）、`scripts/whisper_api.py`（Groq/OpenAI 客户端）、`scripts/setup.py`（预检+安装器）、`scripts/config.py`（共享配置）

首次使用前可审查脚本确认行为。
