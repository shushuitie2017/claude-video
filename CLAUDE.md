# video-skills

让 Claude 看视频、翻译视频的双技能套件（Claude Code / Agent Skills 兼容）。

## 结构

- `skills/watch/` — 看视频回答问题 skill（自包含：SKILL.md + scripts/）
- `skills/video-translate/` — 翻译烧字幕出文稿 skill（自包含）
- `shared/` — **共享脚本唯一可编辑源**（config/download/whisper_api/local_whisper/setup）
- `tools/sync_shared.py` — shared/ → 两个 skill 的 scripts/ 单向同步；`--check` 验漂移
- `tests/` — pytest 套件（ffmpeg 合成测试视频，零网络）
- `hooks/` — Claude Code SessionStart 状态提示（仅插件安装方式生效）
- `install.py` — 复制 skills 到 ~/.claude/skills/ + 脚手架配置

## 铁律

- **改共享代码只改 `shared/` 下的源**，然后跑 `python tools/sync_shared.py`。直接改 skill 里的副本会被 `tests/test_sync.py` 拦下。
- skill 目录必须自包含（SKILL.md 与 scripts/ 是兄弟）——这是 `npx skills add` / 手工复制单个 skill 能独立工作的前提，别把脚本挪回仓库根。
- SKILL.md 里的路径解析走「Read 到的 SKILL.md 所在目录 = SKILL_DIR」，不要引入 `${CLAUDE_SKILL_DIR}` 等 harness 专属变量。
- 命令以 Windows 为一等公民：文档写 `python`（macOS/Linux 换 `python3`）；二进制定位统一走 `config.find_binary()`（WinGet 感知）；脚本顶部强制 UTF-8 stdio。
- 密钥只进 `~/.config/video-skills/.env`，绝不进仓库。

## 常用命令

```bash
python -m pytest -q              # 全量测试（需 ffmpeg；133 个）
python tools/sync_shared.py      # 同步共享脚本
python install.py                # 本机安装两个 skill
```

## 配置

`~/.config/video-skills/.env`（API key + WATCH_DETAIL + SETUP_COMPLETE）；
`~/.config/video-skills/translate.json`（output_dir 绝对路径 + 水印设置）。
