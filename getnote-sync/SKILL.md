---
name: getnote-sync
description: 将 Get笔记 的笔记增量同步到 Obsidian `00_Inbox/Get笔记/` 目录。
---

# Get笔记同步

将 Get笔记 的笔记增量同步到 Obsidian `00_Inbox/Get笔记/` 目录。

> ⚠️ 2026-04-23 更新：增量同步 cutoff 语义和音频笔记兜底逻辑已修复，之前的同步可能漏掉过录音延迟同步的音频笔记，建议手动跑一次全量同步确认无遗漏。

## 用法

当用户说"笔记同步"或要求同步 Get 笔记时，执行：

```bash
export $(grep -v '^#' ~/.hermes/env | xargs) && python3 scripts/getnote-sync.py
```

如果怀疑有遗漏的音频笔记（尤其是录音时间早于上次同步时间的），跑一次全量同步确认：

```bash
export $(grep -v '^#' ~/.hermes/env | xargs) && python3 scripts/getnote-sync.py --full
```

## 可选参数

- `--full`：强制全量同步（默认增量）
- `--dry-run`：预览模式，不写入文件

## 输出

- 笔记文件：`~/obsidian/00_Inbox/Get笔记/`（Markdown 格式，含 frontmatter）
- 图片附件：`~/obsidian/00_Inbox/Get笔记/_assets/Get笔记/`
- 同步状态：`.getnote_sync_state.json`
- 进度文件：`/tmp/openclaw/getnote-sync-progress.json`

## 内容同步策略

| 笔记类型 | content 字段 | 同步到笔记的正文 |
|---|---|---|
| link（链接笔记） | AI 总结 | **web_page.content（原文全文）** ✅ |
| recorder_audio（录音笔记） | 转写文本 | 转写文本（原文） |
| img_text（图片笔记） | 图片描述 | 图片描述 |
| text（纯文字笔记） | 笔记正文 | 笔记正文 |

> 📝 2026-04-24 更新：链接笔记现在同步原文全文（web_page.content），而非 AI 生成的总结。同时自动下载原文中的图片（OSS URL 会过期，下载后替换为本地路径）。

- **增量同步**：以"本次成功同步笔记中最新那条的 `created_at`"作为下次 cutoff；无新笔记同步时保持原 cutoff 不变
- **零新笔记时保持 cutoff 不变**：空同步不更新 `last_synced_at`，避免窗口漂移
- **图片下载**：`img_text` 类型笔记自动下载图片附件并嵌入 markdown
- **429 限流自动重试**
- **并发下载图片**（4 workers）
- **音频笔记兜底**：每次同步检查 API 里所有 `recorder_audio` 类型，若本地 .md 不存在则强制拉详情——解决录音从设备延迟同步、API 延迟出现的问题
- **音频笔记跳过逻辑**：转写未完成（content 长度 < 50）时自动跳过；由兜底逻辑在后续同步中重试

## 关键实现细节

### cutoff 语义（重要，踩过坑）

| 方案 | 问题 |
|---|---|
| cutoff = 同步开始时间 | 同步进行中新建的笔记下次会被漏掉 |
| cutoff = 同步结束时间 | 同步进行中新建的笔记下次会被漏掉（end_time 已跳过） |
| **cutoff = 成功同步笔记里最新那条的 created_at** | ✅ 正确：只拉本次同步前已存在的笔记，之后新建的下次都能看到 |
| 无新笔记时保持原 cutoff | ✅ 正确：避免空跑后窗口漂移 |

过滤时统一用 `cutoff[:19]` 去掉 `+08:00` 时区后缀再字符串比较。

### 音频笔记异步流程

```
设备录音 → 设备同步到 Get笔记（延迟不确定）→ 服务器转写（延迟不确定）→ API 出现该笔记
```

三个阶段的漏笔记场景：

1. **录音未到**：API 还没有这条笔记，任何增量逻辑都拉不到 → 全量拉取时发现
2. **录音到但转写未完成**：content 长度 < 50，被跳过；下次同步由兜底逻辑重试
3. **转写完成但 created_at 早于 cutoff**：由"音频笔记兜底"逻辑处理

**兜底策略**：每次同步都检查 API 里所有 `recorder_audio` 类型笔记的本地文件是否存在，不存在则强制拉详情并写入。

### 曾出现的 bug（已修复）

1. 直接用带时区的 cutoff 字符串与 `created_at` 比较 → Python 3 字符串比较时 `+08:00` 后缀导致 `>` 判断错误
2. 空同步时也更新时间戳 → 多次空跑后 cutoff 窗口漂移，漏掉在两次空跑之间出现的笔记
3. skipped_ids 里的笔记去重逻辑有误 → 重复写入或漏掉
4. `replace_images_in_text` 正则分组写错 → `re.sub` 用了两个捕获组但 `replacer` 里用了 `group(3)`，导致 IndexError；修复后用单个捕获组 `!\[.*?\]\((.*?)\)` 配合闭包函数重建完整 markdown 图片语法

## 完成后

报告同步了几条笔记。如果出错，报告错误信息。
