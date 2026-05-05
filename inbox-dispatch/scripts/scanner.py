#!/usr/bin/env python3
"""
inbox-dispatch scanner: read-only scan of 00_Inbox metadata.

用法：
  python3 scanner.py                      # 全量扫描
  python3 scanner.py --since-days 7       # 仅扫描最近 N 天的文件

输出 JSON lines（每行一个 JSON 对象）到 stdout。
"""

import os, sys, json, argparse, re, time
from datetime import datetime
from pathlib import Path

VAULT = Path(os.path.expanduser("~/obsidian"))
INBOX = VAULT / "00_Inbox"
SKIP_DIRS = {"_dispatch_logs"}
PREVIEW_LEN = 200


def parse_args():
    p = argparse.ArgumentParser(description="扫描 00_Inbox 下的 .md 文件元数据")
    p.add_argument("--since-days", type=int, default=None,
                   help="仅扫描最近 N 天修改过的文件")
    return p.parse_args()


def strip_frontmatter(text):
    """返回 YAML frontmatter 之后的正文；无 frontmatter 时返回原文。"""
    m = re.match(r'^---\s*\n(.*?\n)?---\s*\n', text, re.DOTALL)
    return text[m.end():] if m else text


def extract_title(text, filename):
    """从 frontmatter title: 字段提取标题，否则返回去掉扩展名的文件名。"""
    m = re.search(r'^title:\s*(.+?)\s*$', text, re.MULTILINE)
    return m.group(1).strip().strip('"').strip("'") if m else Path(filename).stem


def extract_tags(text):
    """从 frontmatter tags: 字段提取标签。"""
    m = re.search(r'^tags:\s*(.+?)\s*$', text, re.MULTILINE)
    return m.group(1).strip() if m else ""


def source_dir_name(fpath):
    """返回 inbox 下的第一层子目录名，顶层文件返回空字符串。"""
    try:
        rel = fpath.relative_to(INBOX)
        parts = rel.parts
        return parts[0] if len(parts) > 1 else ""
    except ValueError:
        return ""


def scan():
    args = parse_args()

    if not INBOX.is_dir():
        print(json.dumps({"error": f"Inbox 目录不存在: {INBOX}"},
                         ensure_ascii=False), file=sys.stderr)
        sys.exit(1)

    for root, dirs, files in os.walk(INBOX):
        dirs[:] = sorted(d for d in dirs
                         if not d.startswith(".") and d not in SKIP_DIRS)

        for fname in sorted(files):
            if not fname.endswith(".md"):
                continue
            if fname == "分拣规则.md":
                continue

            fpath = Path(root) / fname
            try:
                st = fpath.stat()
            except OSError:
                continue

            if args.since_days is not None:
                cutoff = time.time() - args.since_days * 86400
                if st.st_mtime < cutoff:
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
                "body_preview": body[:PREVIEW_LEN].replace("\n", " ").strip(),
            }
            print(json.dumps(record, ensure_ascii=False))


def _iso(ts):
    return datetime.fromtimestamp(ts).strftime("%Y-%m-%dT%H:%M:%S")


if __name__ == "__main__":
    scan()
