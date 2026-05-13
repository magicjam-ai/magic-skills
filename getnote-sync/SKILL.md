---
name: getnote-sync
description: "将 Get笔记 笔记增量或全量同步到 Obsidian；当用户说“笔记同步”“同步 Get笔记”“全量同步”或需要把 Get笔记链接、图片、录音笔记落到 Obsidian Inbox 时使用。"
---

# Get笔记同步

将 Get笔记 的笔记增量同步到 Obsidian `00_Inbox/Get笔记/` 目录。

## 用法

当用户说"笔记同步"或要求同步 Get 笔记时，执行：

```bash
python3 scripts/getnote-sync.py
```

执行命令时，将工作目录设为本 `SKILL.md` 所在目录（即包含 `scripts/getnote-sync.py` 的目录）。脚本内部使用自身路径定位同步状态文件，因此不依赖当前工作目录。

环境变量：
- `GETNOTE_API_KEY`：必填，从 Get笔记开放平台获取；未配置时先要求用户提供或配置，不要把密钥写入 `SKILL.md`。
- `GETNOTE_CLIENT_ID`：可选；未配置时脚本使用默认 CLI client id。
- Robert 本机约定：若 shell 环境变量未设置，脚本会自动尝试读取 `~/.agents/skills/getnote/.local/credentials.env`（本机私有凭证文件，不要提交或发布）。

## 可选参数

- `--full`：强制全量同步（默认增量）
- `--dry-run`：预览模式，不写入文件

## 输出

- 笔记文件：`~/obsidian/00_Inbox/Get笔记/`（Markdown 格式，含 frontmatter）
- frontmatter 字段：`title`、`date`、`source`、`source_type`、`note_id`、`tags`（如有）、`url`（如有）
- **录音笔记文件：`~/obsidian/00_Inbox/音频/`**（note_type 为 `recorder_audio` 的录音转写笔记）
- 图片附件：按 Custom Attachment Location 规则保存在笔记同目录的 `assets/${noteFileName}/file-YYYYMMDDHHmmssSSS.ext`
- 同步状态：`scripts/.getnote_sync_state.json`
- 进度文件：`/tmp/openclaw/getnote-sync-progress.json`

## 特性

- 增量同步：记录上次同步时间戳；默认只从最新列表页开始向旧页翻，遇到上次同步水位即停止，不再每次全量拉取所有笔记信息
- 去重保护：扫描 vault 中已有 `note_id`，已同步、已移动、已编入 wiki 的笔记不会再次落入 Inbox
- 状态修复：如果状态文件落后于 vault 中已有笔记，会自动用本地最新 Get笔记日期修复水位
- **录音笔记路由**：note_type 为 `recorder_audio` 的录音转写笔记自动写入 `00_Inbox/音频/`，其他类型写入 `00_Inbox/Get笔记/`
- 图片下载：`img_text` 和链接正文中的图片会按 `./assets/${noteFileName}/file-YYYYMMDDHHmmssSSS` 规则保存并嵌入 markdown
- 429 限流自动重试
- 并发下载图片（4 workers）
- 音频笔记转写未完成时自动跳过
- `--dry-run` 只预览，不写 Markdown、不下载图片、不更新状态

## 完成后

报告同步了几条笔记。如果出错，报告错误信息。
