---
name: inbox-dispatch
description: "将 Obsidian 00_Inbox 下的 Markdown 笔记按语义分类路由到目标目录。Use when the user asks to dispatch, organize, sort, or clean up their inbox notes, especially phrases like "分拣 inbox", "整理 inbox", or "inbox dispatch"."
---

# inbox-dispatch

将 `00_Inbox/` 下的笔记**语义分类**并路由到对应目录。

**分工**：`scanner.py` 负责只读扫描；Claude Code 负责语义分类；`mover.py` 负责执行移动。

---

## 触发条件

当用户说"分拣 inbox"、"整理 inbox"、"inbox dispatch"时触发。

---

## 步骤

### 第 1 步：读取分类规则

Read `scripts/dispatch_rules.json`，理解所有分类的定义、描述和目标目录。

规则格式（数组，每项一个分类）：

```json
{
  "id": "obsidian",
  "name": "Obsidian 使用",
  "description": "关于 Obsidian 笔记应用的使用技巧、插件推荐、配置方法、主题定制等",
  "destination": "10_思考/待处理/obsidian",
  "hints": ["obsidian", "插件", "dataview", "模板"]
}
```

- `description` 是核心语义指导——用它判断内容属于哪个分类
- `hints` 是辅助锚词，非决定性
- 用户可在对话中临时指定新分类，不需要提前写进 rules

---

### 第 2 步：扫描 Inbox

执行 scanner 获取文件列表（工作目录设为本 SKILL.md 所在目录）：

```bash
python3 scripts/scanner.py --since-days 30
```

- `--since-days N`：仅扫描最近 N 天修改的文件（增量分拣推荐用 7-30）
- 不加参数：扫描全部文件
- 输出为 JSON lines，每行一个文件

读取 stdout，解析每行的 JSON 对象。每个对象包含：

| 字段 | 含义 |
|------|------|
| `path` | 相对 vault 路径（`00_Inbox/Cubox/article.md`） |
| `filename` | 文件名 |
| `mtime_iso` | ISO 8601 修改时间 |
| `source_dir` | inbox 下的直接父目录（`Clippings`/`Cubox`/`Get笔记` 等） |
| `title` | frontmatter 标题或文件名 |
| `tags` | frontmatter 标签 |
| `body_preview` | 正文前 200 字 |

---

### 第 3 步：语义分类

对扫描到的每个文件，综合以下信号做语义判断：

- **filename / title**：最直观的分类线索
- **body_preview**：正文前 200 字，通常足够判断主题
- **source_dir**：来源目录有倾向性（如 `Cubox` 多为网文剪藏，`Get笔记` 多为读书笔记）
- **tags**：frontmatter 标签

**分类原则**：

1. 按 `description` 的语义做判断，不依赖单个关键词匹配
2. 一个文件只归入一个分类（有冲突时选最匹配的）
3. 不确定时，用 `Read` 工具读完整内容再判断
4. 无法确定分类的文件**留在 inbox**，不强分
5. 大量文件时分批处理（每批 50-80 个），逐批输出分类结果

---

### 第 4 步：生成 Dispatch Plan

将分类结果写为 JSON 文件：

```
~/obsidian/00_Inbox/_dispatch_logs/plan_YYYY-MM-DD.json
```

Plan 格式：

```json
{
  "created": "2026-05-05T10:00:00",
  "files_scanned": 42,
  "dispatches": [
    {
      "source": "00_Inbox/Cubox/some-article.md",
      "destination": "10_思考/待处理/AI",
      "category_id": "ai-agent",
      "reason": "讨论 coding agent 架构设计"
    }
  ]
}
```

- `source`：相对 vault 路径
- `destination`：相对 vault 路径（不含文件名）
- `reason`：简短说明分类理由（方便用户审核）

---

### 第 5 步：确认并执行

向用户展示 plan 摘要：

- 每个分类各多少文件
- 总移动数
- 未分类（留在 inbox）数

**用户确认后**执行（工作目录设为本 SKILL.md 所在目录）：

```bash
# 实际执行
python3 scripts/mover.py ~/obsidian/00_Inbox/_dispatch_logs/plan_YYYY-MM-DD.json

# 或预览（不实际移动）
python3 scripts/mover.py ~/obsidian/00_Inbox/_dispatch_logs/plan_YYYY-MM-DD.json --dry-run
```

⚠️ **未经用户确认不要执行 mover.py**。

---

### 第 6 步：报告

执行完成后报告：

- 已移动多少条笔记
- 跳过多少条（目标已存在 / 源文件消失）
- 未分类多少条（留在 inbox）

---

## 可选参数

- 用户说"只看最近的"或"最近 N 天"：给 scanner 加 `--since-days N`
- 用户说"先看看"或"预览"：给 mover 加 `--dry-run`
- 用户指定新分类：临时添加到分类规则中，不需要修改 JSON 文件

## 完成后

报告分拣结果。如需新增分类规则，修改 `scripts/dispatch_rules.json` 即可。
