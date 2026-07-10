---
name: video-translate
version: "1.0.0"
description: 视频转写与翻译：外语视频 → 带中文字幕的视频 + 中文文稿。完整管线 = 下载 → 本地 Whisper 词级转写 → 翻译润色 → 烧录字幕（可选双语中大英小/水印）→ 出 Markdown。当用户说"翻译视频""翻译这个视频""配字幕""加字幕""视频转写""转文字""视频转markdown""出文稿"时使用；说"翻译/配字幕"必须执行完整管线烧出视频，不能只出文档。
argument-hint: "<视频URL或路径> [中文|双语]"
allowed-tools: Bash, Read, AskUserQuestion, Write
license: MIT
user-invocable: true
---

# /video-translate — 视频翻译与转写

## 能力与意图识别（必须严格遵守）

- **转写出文档**：抓字幕或音频转写 → 输出 Markdown 文档（有字幕优先，快且免费）。
- **翻译视频**：转写 → 翻译润色 → 烧录中文字幕 → 输出带字幕的视频文件 + Markdown 文档。

判定规则：
- 用户说"转写""转文字""出文档""转 markdown" → 只生成 Markdown 文档（走「只出文档」流程）。
- 用户说"翻译""翻译视频""配字幕""加字幕" → **必须执行完整翻译管线**（提取音频 → Whisper 转写 SRT → 翻译润色 → 烧录字幕到视频 → 同时生成 Markdown），**不能只输出文档就结束**。
- 输入是 URL → 先下载再处理；本地文件路径 → 直接处理。

**Python 解释器：** 本 skill 所有命令按 **Windows 写法**用 `python`。macOS/Linux 换成 `python3`。

## 解析 `SKILL_DIR`（执行任何命令前先做）

把 `SKILL_DIR` 设为**你刚 Read 的这份 SKILL.md 所在目录的绝对路径**，脚本永远在 `SKILL_DIR/scripts/` 下。先守卫：

```bash
SKILL_DIR="<你 Read 的 SKILL.md 所在目录绝对路径>"
if [ ! -f "$SKILL_DIR/scripts/transcribe_srt.py" ]; then
  echo "ERROR: scripts not found under SKILL_DIR=$SKILL_DIR" >&2
  exit 1
fi
```

## 步骤 -1：解析输出根（⛔ 强制，先于一切）

执行任何命令/写任何文件前，必须先读配置拿 `output_dir` 作为 `<输出根>`：

```bash
python -c "import sys; sys.path.insert(0, r'${SKILL_DIR}/scripts'); import config; c = config.load_translate_config(); config.ensure_output_root(c['output_dir']); print(c['output_dir'])"
```

- 配置文件在 `~/.config/video-skills/translate.json`（首次可跑 `python "${SKILL_DIR}/scripts/setup.py"` 生成模板）。
- `output_dir` 必须是绝对路径或以 `~` 开头。缺失/无效时上面的命令会报错——**立即停止**，提示用户编辑该文件填入绝对路径，不要自动填默认值继续。
- 中间产物一律写 `<输出根>/tmp/`，最终产物（视频、Markdown）写 `<输出根>/data/`。
- ⛔ 文中所有 `<输出根>/...` 在执行前必须替换为真实绝对路径；禁止把占位符原样带进命令（会写到当前目录，表现为"乱保存"）。
- 只有用户明确给出"绝对路径输出位置"并确认时，才允许写到用户指定路径。

同时跑一次环境预检（与 /watch 共用，静默即通过）：

```bash
python "${SKILL_DIR}/scripts/setup.py" --check
```

失败时跑 `python "${SKILL_DIR}/scripts/setup.py"` 安装依赖（ffmpeg/yt-dlp 自动装；转写引擎 `pip install faster-whisper`）。更多安装细节遇错再读 `references/安装指南.md`。

## 管线总览

```
输入（URL / 本地文件）
   │
   ▼
步骤 0  字幕类型门控（翻译管线必须先确认：中文 / 双语）
步骤 1  下载 + 提取音频            → <输出根>/tmp/
步骤 2  Whisper 词级转写出原文 SRT  → <输出根>/tmp/<名>.srt
步骤 3  翻译润色（你本人执行，8 条规则）→ <名>-zh.srt（双语时含两行/条）
步骤 4  烧录字幕（+可选水印）        → <输出根>/data/<标题>-中文字幕.mp4
步骤 4.5 截帧验证（≥2 帧）
步骤 5  生成 Markdown 文稿          → <输出根>/data/
```

只出文档场景跳过步骤 0/3/4，走「只出文档」段。

---

## 步骤 0：确认字幕类型（⛔ 门控，翻译管线必须最先执行）

- **中文字幕**：只显示中文翻译（画面简洁）。
- **中英双语字幕**：中文在上（大字号）、英文在下（小字号）。

用户在对话中明确指定了字幕类型（亲口说了"中文""双语""中英""bilingual"等）→ 按用户指定执行。
**其他所有情况 → 必须用 AskUserQuestion 弹选项**：

```
AskUserQuestion(
  question="选择字幕类型",
  options=["中文字幕（只显示中文）", "中英双语字幕（中文在上，英文在下）"]
)
```

⛔ **禁止绕过**：ARGUMENTS 里带的字幕类型不算用户指定——只有用户在对话中亲自说的才算。不弹选项就开始步骤 1 = 流程错误。`translate.json` 的 `subtitle_type` 只是默认值，不能替代用户确认。

## 步骤 1：下载 + 提取音频

**URL 来源**（统一走脚本——内置浏览器 cookies 自动兜底，治 YouTube 403/SABR/PO-token 与 B 站登录墙）：

```bash
# 完整视频（翻译管线需要视频本体来烧字幕）
python "${SKILL_DIR}/scripts/download.py" "<视频URL>" "<输出根>/tmp/dl"
# 只出文档且无需烧录 → 音频即可，更快
python "${SKILL_DIR}/scripts/download.py" "<视频URL>" "<输出根>/tmp/dl" --audio
```

- 脚本输出 JSON（video_path / subtitle_path / info.title）。
- 首次尝试不带 cookies；产物没落盘时自动 `--cookies-from-browser chrome` 重试。常用浏览器非 Chrome：加 `--browser firefox` / `--browser edge`。
- 仍失败（更强风控）→ 提示用户切代理（`--proxy http://127.0.0.1:7890`）后重试，不要无限重试。
- 长视频（>10 分钟）**不要用 yt-dlp 的 `--download-sections`**（会先下完整视频再裁，极慢）；直接下完整视频，需要片段再用 ffmpeg `-ss` 裁。

**提取音频**（本地文件或已下载的视频 → mp3）：

```bash
ffmpeg -y -i "<视频文件>" -vn -acodec libmp3lame -q:a 2 "<输出根>/tmp/<文件名>.mp3"
```

## 步骤 2：Whisper 词级转写出原文 SRT

⛔ **铁律：烧字幕的原文 SRT 只能由 Whisper 词级转写生成，禁止用平台自动字幕（yt-dlp --write-auto-subs）当时间源。** 原因：YouTube 自动字幕是滚动叠加格式（每条包含前一条文本），无法提取干净逐句文本+精确时间戳，烧出来必然不同步。词级时间戳是唯一可靠时间源。

```bash
python "${SKILL_DIR}/scripts/transcribe_srt.py" "<输出根>/tmp/<文件名>.mp3" \
  --output "<输出根>/tmp/<文件名>.srt"
# 语种误判 / 多语言混轨时强制指定：加 --language en（或 zh/ja/…）
```

- 默认引擎 = 本地 faster-whisper（词级时间戳，零 API 费，首次运行自动从 HuggingFace 下模型）。
- faster-whisper 装不上 → 配 `GROQ_API_KEY`/`OPENAI_API_KEY` 后加 `--engine api`（API 词级兜底，与本地路线共用同一套断句逻辑）。API 未返回词级时间戳时脚本会降级 segment 级并在 stderr 明示"字幕精度下降"——把这一点转告用户。
- ⛔ **不要在对话里现写转写 Python 代码**——脚本按「句子+停顿」切（segment 作基础 / 超 6s 才按词拆 / <400ms 才合并），绕开脚本自己写 greedy 切分会把 A 句尾巴+B 句开头挤进同一帧（实战踩坑根治）。
- `--max-line-ms` 控制单条最大时长（默认 6000ms），`--pause-ms` 控制断句停顿阈值（默认 500ms）。

## 步骤 3：翻译润色（你本人执行——这是 LLM 工作，没有脚本）

读入步骤 2 的原文 SRT，逐条翻译润色，输出润色后的 SRT 到 `<输出根>/tmp/<文件名>-zh.srt`。你的角色：**专业字幕文案编辑**，擅长中文写作、口语还原与字幕排版规范。

### 润色 8 条规则（核心）

1. **纠正 ASR 识别错误**：同音/近音字、专有名词拼写（如 Claude 被听成 cloud、MCP 被听成 NCP）、数字/缩写/单位。可参考视频画面辅助判断。
2. **标点与书面化**：先补全标点把口语转书面（保留说话人语气），**但最终输出的字幕文本去掉所有标点符号**（逗号句号问号感叹号全去）；英文和中文之间加一个英文空格（如：`这是 Google 的产品`）。
3. **去冗余**：删无信息量的口头语、重复、语气词（"那个""呃""就是说"），保证语义连续不丢关键信息。宁可略冗余，不可丢意思。
4. **时间戳对齐（严格）**：原始时间戳严格保留，不得随意偏移。**绝不手动提前 start time**（会让字幕跑在说话人前面）。**超长挂屏裁剪**：一条时间跨度 >6 秒且文本较短 = Whisper 把语音后的静音/BGM 算进了同一段，用 ffmpeg 静音检测精确定位语音结束点，不要公式估算：
   ```bash
   ffmpeg -i <音频> -ss <start> -to <end> -af silencedetect=n=-30dB:d=0.5 -f null - 2>&1 | grep silence_start
   ```
   取第一个 `silence_start`（加回 start 偏移）作为裁剪后的 end；整段都有声就不裁。一条 >8 秒且文本长可拆成多条，时间戳在原范围内合理分配。**禁止合并相邻条目，只允许拆分**；拆分后重新连续编号；禁止时间戳重叠。
5. **断句与分段**：核心原则**可拆不可合**。一条原文对应一条译文，超 12 字按语义断点拆两条（时间戳按比例分配）。以单行为主，每行 ≤12 个中文字符（含空格和英文），在语气停顿处断开，不把完整短语拆到两条里。宁可多拆短条，不在屏幕上堆多行。
6. **专有名词**：人名/地名/公司/产品保留原文（首字母大写）；技术术语保留英文（API、GPU、AI、ASR）；广为人知的品牌不翻（Google、iPhone、YouTube）；生僻术语首次出现可附简短中文释义。
7. **语言风格**：符合当代年轻人用语习惯，网络用语/俚语翻得自然接地气，避免翻译腔（"这是非常令人兴奋的"→"这真的很酷"）。旁白用简洁书面语，对话/歌词保留口语感。
8. **歌词**：用 ♪ 包裹（`♪ 歌词 ♪`），重意境押韵不逐字直译，与旁白交替时保证观众能区分。

### 输出格式（严格 SRT）

```
1
00:00:00,000 --> 00:00:03,500
这是第一条字幕内容

2
00:00:03,500 --> 00:00:07,200
这是第二条字幕内容
```

序号从 1 连续；时间戳 `HH:MM:SS,mmm`（逗号分毫秒）；文本无标点；中英间有空格；条间空行；文件末尾空行。

### 双语模式（用户步骤 0 选了双语时）

产出「双语 SRT」：每条**两行**——中文在上、英文（原文）在下，**时间戳与原文 SRT 一句对一句完全对齐**（不重新断句、不合并；原文怎么切中文就怎么对）。中文行照常去标点、规范术语；英文行保留原文（仅修 ASR 识别错误）：

```
1
00:00:19,239 --> 00:00:21,239
大家好吗
Hello everyone, how are you?
```

### 长视频（>10 分钟）分段并行

字幕条数多时**分段并行翻译**：每段约 500 条，developer 并行子任务翻译，翻完合并重新编号。合并后全局校验：时间戳无重叠无跳跃、序号连续、语义连贯、格式合规。

⛔ **管线不停顿**：润色完成后立即执行步骤 4（烧录），不输出中间报告、不等用户确认。整条管线只在最终产物（视频 + Markdown）全部完成后报告一次。

## 步骤 4：烧录字幕（+可选水印）

**统一走 `burn.py`**——不要手拼 ffmpeg 命令。脚本集中处理了：Windows 路径在 filter 里的转义（`C:\a.srt` → `'C\:/a.srt'`，手拼十拼九错）、音频强制转 AAC（yt-dlp 的 Opus 音轨会让 X/微信/微博上传失败，⛔ 禁 `-c:a copy`）、水印 fontfile 解析与时间点分布、字幕+水印同一条 `-vf` 一次编码（避免二次画质损失）。

**中文单语**（SRT 直烧）：

```bash
python "${SKILL_DIR}/scripts/burn.py" "<视频文件>" \
  --subs "<输出根>/tmp/<文件名>-zh.srt" \
  --output "<输出根>/data/<视频标题>-中文字幕.mp4"
```

**中英双语**（先转 ASS 再烧——⛔ 必须走 ASS）：

```bash
python "${SKILL_DIR}/scripts/bilingual_ass.py" "<输出根>/tmp/<文件名>-zh.srt" \
  --output "<输出根>/tmp/<文件名>-zh.ass" [--cn-size N] [--height 视频高度]
python "${SKILL_DIR}/scripts/burn.py" "<视频文件>" \
  --subs "<输出根>/tmp/<文件名>-zh.ass" \
  --output "<输出根>/data/<视频标题>-中文字幕.mp4"
```

- ⛔ **为什么双语必须 ASS**：`subtitles` 滤镜的 force_style FontSize 对整条统一，做不到一条内中文大英文小；SRT 里的 inline `{\fsN}` 会被 ffmpeg 的 srt 解码器剥离（实测三档字号烧出 md5 全同）。只有真 ASS 喂 libass 才能一条内 `\N` 换行 + inline 切字号。`bilingual_ass.py` 就是干这个的，**不要手写 ASS**。
- 字号：用户在对话里明确给了中文字号（如"字号 24"）→ 传 `--cn-size 24`，英文自动按 中文/1.7 算；没给 → 脚本按分辨率选默认。中文︰英文 ≈ **1.7** 是实测干净的反差比例。
- ⛔ **ASS FontSize/MarginV 是相对 PlayResY=288 的相对值，不是像素**。libass 会按视频分辨率自动缩放，不要按分辨率线性放大字号（1080p 的 17 乘 2 到 4K 会占满半屏）；1080p 里 MarginV=N 实际 ≈ N×3.75px 距底（MarginV=110 ≈ 画面正中，不是 110px），别按像素直觉调。

**水印**（默认关闭；`translate.json` 的 `settings.watermark_enabled: true` 或用户要求时开）：

```bash
python "${SKILL_DIR}/scripts/burn.py" "<视频文件>" --subs <字幕> \
  --watermark-text "<水印文字>" --output <输出>
```

时间点按时长自动分布（<30min 3 次 4s / 30-60min 5 次 5s / >60min 每 15 分钟一次 10s；首尾固定），透明度 0.28、0.5s 渐隐渐出、左上角。参数可用 `--watermark-opacity/--watermark-fontsize` 等覆盖；对话中指定的参数优先于配置文件。

**原视频底部已有硬字幕时的避让**：新字幕需上移，不能遮挡原字幕：

```bash
# 单行硬字幕
python "${SKILL_DIR}/scripts/burn.py" <视频> --subs <字幕> --avoid-hardsub single ...
# 两行或动态变行（一句 1 行一句 2 行）→ 按两行避让；1 行段中文稍高是可接受代价
python "${SKILL_DIR}/scripts/burn.py" <视频> --subs <字幕> --avoid-hardsub double ...
```

避让档位（single=MarginV 42/FontSize 19，double=MarginV 60/FontSize 19）是经验值，截帧验证不过时用 `--margin-v` 微调 ±3-5 重烧。

拿不准最终命令时先 `--dry-run` 打印命令审查再真跑。

## 步骤 4.5：截帧验证（⛔ 必做，≥2 帧）

烧录后必须抽帧用 Read 查看确认：字幕完整显示、不遮挡原有硬字幕、双语字号反差明显、中文无方块（字体缺失）：

```bash
ffmpeg -y -i "<输出视频>" -ss <时间点1> -vframes 1 "<输出根>/tmp/check1.jpg"
ffmpeg -y -i "<输出视频>" -ss <时间点2> -vframes 1 "<输出根>/tmp/check2.jpg"
```

有避让需求时两帧要分别选原字幕 1 行和 2 行的时刻，两帧都不遮挡才算过。**只看一帧就交付 = 踩过的坑。** 有问题调 `--margin-v`/`--font-size` 重烧再验。

## 步骤 5：生成 Markdown 文稿

通用规则（所有场景）：
- 读取转写文本（SRT 去序号和时间戳行，或 `vtt_to_text.py` 清洗后的文本），**严禁增删改原文**，只做标点、合句、分段。必须覆盖全部内容，不得遗漏。
- **中文内容**：合并短句、规范标点、按语义分段 → 输出 1 个文件 `<输出根>/data/<视频标题>-中文.md`。⛔ 中文内容严禁使用翻译规则。
- **非中文内容**：先按原文语言生成 `<输出根>/data/<视频标题>-原文.md`（同样不得增删改），再读 `references/深度文稿规则.md` 按其规则创作 `<输出根>/data/<视频标题>-中文.md`（深度文章体，非逐句直译）。
- Front matter 仅含 `title`、`source_url`。

## 只出文档场景（用户只要转写/文稿，不烧字幕）

1. **有字幕的视频优先抓字幕**（免费最快；⛔ 仅限出文档——烧字幕禁用此路线，见步骤 2 铁律）：
   ```bash
   python "${SKILL_DIR}/scripts/download.py" "<视频URL>" "<输出根>/tmp/dl" --captions-only
   python "${SKILL_DIR}/scripts/vtt_to_text.py" "<输出根>/tmp/dl/<字幕>.vtt" --output "<输出根>/tmp/cleaned.txt"
   ```
   清洗后的纯文本省约一半 token。读 `cleaned.txt` 按步骤 5 出文档。
2. **无字幕** → 下音频（`--audio`）→ `transcribe_srt.py` 转写 → SRT 去序号时间戳得纯文本 → 按步骤 5 出文档。

## 临时文件与清理

- 中间产物（音频、SRT、ASS、截帧）都在 `<输出根>/tmp/`，处理完可清理；最终产物在 `<输出根>/data/` 不受影响。
- 转写 SRT 可保留用于调试；用户可随时手动清空 tmp/。

## 错误处理

| 症状 | 处理 |
|------|------|
| 下载失败（403/风控/登录墙） | 脚本已自动 cookies 重试；仍失败换 `--browser firefox/edge`，或建议用户配代理；确认是地区锁/付费内容就直说 |
| faster-whisper 未安装 | `pip install faster-whisper`；装不了走 `--engine api`（需 API key） |
| 转写语种误判 | 加 `--language en`（或对应语种）强制重转 |
| 烧录后中文是方块 | 字体缺失——`burn.py` 默认按平台选（Windows 雅黑/macOS 苹方/Linux Noto CJK），Linux 需 `sudo apt install fonts-noto-cjk`；也可 `--font-name` 指定 |
| 字幕遮挡原硬字幕 | `--avoid-hardsub single|double`，仍不行 `--margin-v` ±3-5 微调重烧再截帧验证 |
| 磁盘空间不足 | 清理 `<输出根>/tmp/` |
| 依赖类报错 | 读 `references/安装指南.md` 按指引装好后回到主流程 |

依赖检查原则：**直接执行主流程，遇错再查**——不要预先逐项检查依赖浪费时间，默认环境已配好（步骤 -1 的 `--check` 已覆盖基础项）。
