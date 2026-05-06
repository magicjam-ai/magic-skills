#!/usr/bin/env python3
"""
inbox-dispatch scanner: read-only scan of 00_Inbox metadata.

用法：
  python3 scripts/scanner.py --since-days 30 --limit 50
  python3 scripts/scanner.py --all --oldest-first

输出 JSON lines（每行一个 JSON 对象）到 stdout。
"""

import os
import sys
import json
import argparse
import re
import time
from datetime import datetime
from pathlib import Path

VAULT = Path(os.path.expanduser("~/obsidian"))
INBOX = VAULT / "00_Inbox"

# 只扫描 Markdown 信息源；跳过日志、附件/素材类目录。
SKIP_DIRS = {
    "_dispatch_logs",
    "_assets",
    "图片",
    "素材",
    "视频",
    "论文",
}
SKIP_FILES = {"分拣规则.md"}
PREVIEW_LEN = 800


def parse_args():
    p = argparse.ArgumentParser(description="扫描 00_Inbox 下的 .md 文件元数据（只读）")
    p.add_argument("--since-days", type=int, default=30,
                   help="仅扫描最近 N 天修改过的文件；默认 30。用 --all 扫描全部")
    p.add_argument("--all", action="store_true", help="扫描全部 .md 文件，忽略 --since-days")
    p.add_argument("--limit", type=int, default=50,
                   help="最多输出 N 条；默认 50。传 0 表示不限制")
    p.add_argument("--oldest-first", action="store_true", help="按修改时间从旧到新输出；默认从新到旧")
    return p.parse_args()


def strip_frontmatter(text):
    """返回 YAML frontmatter 之后的正文；无 frontmatter 时返回原文。"""
    m = re.match(r'^---\s*\n(.*?\n)?---\s*\n', text, re.DOTALL)
    return text[m.end():] if m else text


def extract_frontmatter(text):
    m = re.match(r'^---\s*\n(.*?)\n---\s*\n', text, re.DOTALL)
    return m.group(1) if m else ""


def extract_title(text, filename):
    """从 frontmatter title: 字段或第一个 H1 提取标题，否则返回去掉扩展名的文件名。"""
    fm = extract_frontmatter(text)
    m = re.search(r'^title:\s*(.+?)\s*$', fm, re.MULTILINE)
    if m:
        return m.group(1).strip().strip('"').strip("'")
    body = strip_frontmatter(text)
    h = re.search(r'^#\s+(.+?)\s*$', body, re.MULTILINE)
    return h.group(1).strip() if h else Path(filename).stem


def extract_tags(text):
    """从 frontmatter tags 字段提取简要标签文本。"""
    fm = extract_frontmatter(text)
    m = re.search(r'^tags:\s*(.+?)\s*$', fm, re.MULTILINE)
    if m:
        return m.group(1).strip()
    block = re.search(r'^tags:\s*\n((?:\s+-\s*.+\n?)+)', fm, re.MULTILINE)
    if block:
        return ", ".join(re.sub(r'^\s+-\s*', '', line).strip() for line in block.group(1).splitlines())
    return ""


def extract_headings(body, max_items=6):
    headings = []
    for line in body.splitlines():
        m = re.match(r'^(#{1,3})\s+(.+?)\s*$', line)
        if m:
            headings.append(m.group(2).strip())
            if len(headings) >= max_items:
                break
    return headings


def source_dir_name(fpath):
    """返回 inbox 下的第一层子目录名，顶层文件返回空字符串。"""
    try:
        rel = fpath.relative_to(INBOX)
        parts = rel.parts
        return parts[0] if len(parts) > 1 else ""
    except ValueError:
        return ""


def should_skip_dir(dirname):
    return dirname.startswith(".") or dirname in SKIP_DIRS


def collect_records(args):
    records = []
    if not INBOX.is_dir():
        print(json.dumps({"error": f"Inbox 目录不存在: {INBOX}"}, ensure_ascii=False), file=sys.stderr)
        sys.exit(1)

    cutoff = None
    if not args.all and args.since_days is not None:
        cutoff = time.time() - args.since_days * 86400

    for root, dirs, files in os.walk(INBOX):
        dirs[:] = sorted(d for d in dirs if not should_skip_dir(d))

        for fname in sorted(files):
            if not fname.endswith(".md") or fname in SKIP_FILES:
                continue
            fpath = Path(root) / fname
            try:
                st = fpath.stat()
            except OSError:
                continue

            if cutoff is not None and st.st_mtime < cutoff:
                continue

            try:
                raw = fpath.read_text(encoding="utf-8", errors="ignore")
            except OSError:
                continue

            body = strip_frontmatter(raw)
            record = {
                "path": str(fpath.relative_to(VAULT)),
                "filename": fname,
                "mtime": st.st_mtime,
                "mtime_iso": _iso(st.st_mtime),
                "source_dir": source_dir_name(fpath),
                "title": extract_title(raw, fname),
                "tags": extract_tags(raw),
                "headings": extract_headings(body),
                "body_preview": body[:PREVIEW_LEN].replace("\n", " ").strip(),
            }
            records.append(record)

    records.sort(key=lambda r: (r["mtime"], r["path"]), reverse=not args.oldest_first)
    if args.limit and args.limit > 0:
        records = records[:args.limit]
    return records


def scan():
    args = parse_args()
    for record in collect_records(args):
        print(json.dumps(record, ensure_ascii=False))


def _iso(ts):
    return datetime.fromtimestamp(ts).strftime("%Y-%m-%dT%H:%M:%S")


if __name__ == "__main__":
    scan()
