#!/usr/bin/env python3
"""
inbox-dispatch: 扫描 00_Inbox/ 下所有笔记，按关键词分拣到对应子目录

用法：
  python3 dispatch.py                      # 全量扫描并分拣
  python3 dispatch.py --dry-run            # 预览，不实际移动文件
  python3 dispatch.py --since-days 3       # 仅扫描最近 N 天的修改文件

分拣规则（dispatch_rules.json）：
  {
    "obsidian": "20_思考/待处理/obsidian",
    "AI": "20_思考/待处理/AI",
    "高考": "20_思考/待处理/高考"
  }

规则按 key 优先级顺序匹配，优先匹配 filename，再匹配正文内容。
"""

import os, re, json, sys, shutil
from datetime import datetime, timedelta

VAULT = os.path.expanduser("~/obsidian")
INBOX = os.path.join(VAULT, "00_Inbox")
RULES_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "dispatch_rules.json")
DRY_RUN = "--dry-run" in sys.argv
SINCE_DAYS = None

for arg in sys.argv[1:]:
    if arg.startswith("--since-days="):
        SINCE_DAYS = int(arg.split("=")[1])

# 加载分拣规则
if os.path.exists(RULES_FILE):
    with open(RULES_FILE, encoding="utf-8") as f:
        RULES = json.load(f)
else:
    RULES = {}
    print(f"⚠️  未找到规则文件: {RULES_FILE}，脚本不会移动任何文件。")

def is_recent(path, days):
    if days is None:
        return True
    mtime = os.path.getmtime(path)
    return (datetime.now().timestamp() - mtime) < days * 86400

def match_rule(filename, content):
    """返回匹配的规则 key，未匹配返回 None"""
    for key, dest in RULES.items():
        if key.lower() in filename.lower():
            return key, dest
    for key, dest in RULES.items():
        if key.lower() in content.lower():
            return key, dest
    return None, None

def dispatch():
    moved = []
    skipped_no_rule = []
    skipped_recent = []

    for root, dirs, files in os.walk(INBOX):
        dirs[:] = [d for d in dirs if not d.startswith(".")]  # 跳过隐藏目录

        for fname in files:
            if not fname.endswith(".md"):
                continue
            fpath = os.path.join(root, fname)

            if not is_recent(fpath, SINCE_DAYS):
                skipped_recent.append(fname)
                continue

            with open(fpath, encoding="utf-8", errors="ignore") as f:
                content = f.read()

            key, dest = match_rule(fname, content)
            if key is None:
                skipped_no_rule.append(fname)
                continue

            dest_dir = os.path.join(VAULT, dest)
            os.makedirs(dest_dir, exist_ok=True)
            dest_path = os.path.join(dest_dir, fname)

            if os.path.exists(dest_path):
                # 目标已有同名文件，跳过
                print(f"⏭️  跳过（已存在）: {fname} -> {dest}")
                continue

            if DRY_RUN:
                print(f"[DRY-RUN] 移动: {fname} -> {dest}")
            else:
                shutil.move(fpath, dest_path)
                print(f"✅ 移动: {fname} -> {dest}")

            moved.append((fname, key, dest))

    print(f"\n--- 结果 ---")
    print(f"移动: {len(moved)} 条")
    if moved:
        for fname, key, dest in moved:
            print(f"  [{key}] {fname} -> {dest}")
    print(f"无匹配规则: {len(skipped_no_rule)} 条")
    print(f"非近期文件（跳过）: {len(skipped_recent)} 条")

    return moved

if __name__ == "__main__":
    dispatch()
