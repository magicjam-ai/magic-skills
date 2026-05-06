---
name: inbox-dispatch
description: '将 Obsidian 00_Inbox 下的 Markdown 笔记按 Robert 当前 10_思考/待处理 队列做语义预分拣，并生成可审核 dispatch plan。Use when the user asks to dispatch, organize, sort, or clean up their inbox notes, especially phrases like “分拣 inbox”, “整理 inbox”, or “inbox dispatch”.'
---

# inbox-dispatch

将 `00_Inbox/` 下的 Markdown 笔记按 Robert 当前工作流做**语义预分拣**，输出可审核的 dispatch plan。

**核心原则**：不使用单个关键词全文匹配直接移动；必须 plan / dry-run first，给出分类、理由、置信度，Robert 确认后才执行移动。

---

## 动态队列体系

队列目录不写死在 skill 文档里。每次分拣前必须动态读取当前队列：

```bash
cd /Users/robertma/.agents/skills/inbox-dispatch
python3 scripts/list_queues.py --format markdown
```

配置来源：

1. 环境变量 `INBOX_DISPATCH_PENDING_ROOT`（最高优先级）
2. `scripts/dispatch_rules.json` 的 `pending_root`
3. 默认值：`10_思考/待处理`

`10_思考/待处理` 下的**第一层子目录**是当前可用队列。`scripts/dispatch_rules.json` 只保存队列说明、include/exclude 锚点、fallback/trash 队列名；它不是队列目录清单。Robert 新增/删除子目录后，`list_queues.py` 会自动反映。

低置信度默认进入 `fallback_queue`（通常为 `待判定`）；低价值/营销/重复内容进入 `trash_queue`（通常为 `淘汰候选`）。如果这些队列目录不存在，先报告，不要擅自创建或移动。

`include` / `exclude` 只是辅助锚点；分类必须以语义判断为准。未配置说明的新目录也可以被使用，但理由里要说明是按目录名和实际语义判断。

---

## 强制安全规则

1. **未经 Robert 确认，不执行实际移动。**
2. **默认只生成 plan 和 dry-run。**
3. **不要使用单个关键词全文匹配决定分类。** 文件名、标题、小标题的权重大于正文偶然关键词。
4. **低置信度进入 `fallback_queue`。** 不要强行归入主题队列。
5. **疑似重复、浅层、营销进入 `trash_queue`。** 不要直接删除。
6. **单批建议不超过 50 篇。** 大批量 Inbox 积压要分批处理。
7. **跳过附件/素材类目录。** scanner 默认跳过 `_dispatch_logs`、`图片`、`素材`、`视频`、`论文`、`_assets`。
8. **移动 Get笔记 Markdown 时必须同步处理图片。** `mover.py --execute` 会把 `![](_assets/Get笔记/xxx.jpg)` 引用到的图片复制到笔记同目录的 `assets/${noteFileName}/`，再把链接改写为 `![[assets/${noteFileName}/xxx.jpg]]`；确认没有旧引用后才清理 `00_Inbox/Get笔记/_assets/Get笔记/` 中的中央图片。

---

## 标准流程

### Step 1：读取当前队列与分类说明

```bash
cd /Users/robertma/.agents/skills/inbox-dispatch
python3 scripts/list_queues.py --format json
```

重点读取：

- `pending_root`
- `fallback_queue`
- `trash_queue`
- 每个队列的 `name`、`destination`、`description`、`include`、`exclude`

### Step 2：只读扫描 Inbox

```bash
python3 scripts/scanner.py --since-days 30 --limit 50
```

常用参数：

```bash
python3 scripts/scanner.py --since-days 7 --limit 50
python3 scripts/scanner.py --all --limit 50
python3 scripts/scanner.py --all --oldest-first --limit 50
python3 scripts/scanner.py --since-days 30 --limit 0
```

scanner 输出 JSON lines，每行一个文件，字段包括：

| 字段 | 含义 |
|---|---|
| `path` | vault 相对路径，如 `00_Inbox/Get笔记/xxx.md` |
| `filename` | 文件名 |
| `mtime_iso` | 修改时间 |
| `source_dir` | Inbox 下第一层来源目录 |
| `title` | frontmatter title / H1 / 文件名 |
| `tags` | frontmatter tags 摘要 |
| `headings` | 前几个 H1-H3 标题 |
| `body_preview` | 正文前 800 字 |

### Step 3：语义分类

对每个文件综合判断：

1. `filename` / `title`
2. `headings`
3. `body_preview`
4. `source_dir`
5. `tags`
6. 当前队列说明和目录名
7. 必要时读取完整文件再判断

每个文件输出：

- `category_id`：优先用队列配置里的 `id`；未配置时可用目录名或 slug
- `category_name`：必须等于某个当前队列名
- `destination`：使用 `list_queues.py` 返回的目标目录
- `confidence`: `high` / `medium` / `low`
- `reason`: 一句话说明

分类准则：

- `high`：标题和摘要都明确指向同一队列。
- `medium`：大体可判断，但有边界重叠；仍可放入主题队列，但报告里标出。
- `low`：主题不清或多类难分，放入 `fallback_queue`。
- 低价值/重复/浅层营销：放入 `trash_queue`，等待 Robert 最终确认。

### Step 4：生成 dispatch plan

写到：

```text
00_Inbox/_dispatch_logs/plan_YYYY-MM-DD_HHMM.json
```

Plan 格式：

```json
{
  "created": "2026-05-06T22:00:00+08:00",
  "mode": "review_required",
  "pending_root": "10_思考/待处理",
  "queues_source": "scripts/list_queues.py",
  "files_scanned": 50,
  "dispatches": [
    {
      "source": "00_Inbox/Get笔记/example.md",
      "destination": "10_思考/待处理/个人第二大脑",
      "category_id": "personal-second-brain",
      "category_name": "个人第二大脑",
      "confidence": "high",
      "reason": "标题和摘要都围绕 llm-wiki 与个人知识管理。"
    }
  ],
  "unclassified": []
}
```

说明：

- `destination` 是目录，不含文件名；必须来自当前队列清单。
- 也可用 `destination_queue` / `category_name` 让 `mover.py` 动态解析队列，但推荐仍写入完整 `destination`，方便审阅。
- 不要删除文件；淘汰只移动到 `trash_queue` 等待确认。

### Step 5：先 dry-run 给 Robert 审核

```bash
python3 scripts/mover.py ~/obsidian/00_Inbox/_dispatch_logs/plan_YYYY-MM-DD_HHMM.json --dry-run
```

报告：

- 当前 `pending_root`
- 扫描了多少篇
- 建议移动多少篇
- 每个队列多少篇
- `fallback_queue` 多少篇
- `trash_queue` 多少篇
- 典型边界案例和低置信项

### Step 6：Robert 确认后才执行

```bash
python3 scripts/mover.py ~/obsidian/00_Inbox/_dispatch_logs/plan_YYYY-MM-DD_HHMM.json --execute
```

执行后报告：

- 已移动多少条
- 跳过多少条（目标已存在 / 源文件消失 / plan 无效）
- 图片迁移/缺失统计
- 是否需要对某个队列做去重、价值判断或编入 `60_wiki/<主题>/`

---

## 常见用户意图映射

| 用户说法 | 动作 |
|---|---|
| “分拣 inbox” | 读取当前队列，扫描最近 30 天 50 篇，生成 plan，dry-run 报告 |
| “整理 inbox 最近一周” | `scanner.py --since-days 7 --limit 50` |
| “全量看看” | `scanner.py --all --limit 50`，仍分批 |
| “从最旧的开始” | `scanner.py --all --oldest-first --limit 50` |
| “执行这个 plan” | 先确认用户已经审核；然后 `mover.py plan --execute` |

---

## 完成后

报告使用 Obsidian wikilink，例如：

- 目标队列：`[[10_思考/待处理/个人第二大脑]]`
- plan 文件：`[[00_Inbox/_dispatch_logs/plan_YYYY-MM-DD_HHMM.json]]`

不要在报告中输出大量完整原文，只列标题、分类、理由和置信度。
