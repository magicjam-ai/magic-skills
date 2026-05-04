---
name: inbox-dispatch
description: "将 Obsidian 00_Inbox 下的 Markdown 笔记按关键词规则分拣到对应目录。Use when the user asks to dispatch, organize, sort, or clean up their inbox notes, especially phrases like “分拣 inbox”, “整理 inbox”, or “inbox dispatch”."
---

# inbox-dispatch

将 `00_Inbox/` 下的笔记按关键词分拣到对应目录。

## 用法

当用户说"分拣 inbox"、"整理 inbox"、"inbox dispatch"时，执行：

```bash
python3 scripts/dispatch.py
```

执行命令时，将工作目录设为本 `SKILL.md` 所在目录（即包含 `scripts/dispatch.py` 的目录）。

## 分拣规则

规则定义在 `scripts/dispatch_rules.json`：

```json
{
  "obsidian": "10_思考/待处理/obsidian",
  "高考": "10_思考/待处理/高考"
}
```

- 优先匹配文件名（大小写不敏感）
- 再匹配正文内容
- 目标目录已存在同名文件时跳过

## 可选参数

- `--dry-run`：预览模式，显示会移动哪些文件，但不实际移动
- `--since-days=N`：仅扫描最近 N 天修改过的文件

## 示例

```bash
# 预览 Obsidian 相关分拣
python3 scripts/dispatch.py --dry-run

# 仅处理最近 3 天修改的文件
python3 scripts/dispatch.py --since-days=3
```

## 完成后

报告移动了多少条笔记。如需新增规则，修改 `dispatch_rules.json` 即可。
