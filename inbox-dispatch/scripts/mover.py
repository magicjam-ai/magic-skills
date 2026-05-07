#!/usr/bin/env python3
"""
inbox-dispatch mover: execute a reviewed dispatch plan.

Safety policy:
  - Default is DRY-RUN.
  - Actual moves require --execute after the plan has passed the required
    mechanical + LLM review workflow in SKILL.md.
  - When moving Get笔记 Markdown, move/copy referenced images into the note's
    Custom Attachment Location folder: ./assets/${noteFileName}/

用法：
  python3 scripts/mover.py plan.json             # 预览，不实际移动
  python3 scripts/mover.py plan.json --execute   # plan 验收通过后执行移动
"""

import os
import sys
import json
import shutil
import argparse
import re
from pathlib import Path

from queue_config import VAULT, resolve_queue_destination, queue_by_name, pending_root_rel

GETNOTE_ASSET_DIR = VAULT / "00_Inbox" / "Get笔记" / "_assets" / "Get笔记"
SPECIAL_CHARS = set('#^[]|*\\<>:?/')


def parse_args():
    p = argparse.ArgumentParser(description="执行 inbox dispatch plan；默认只预览")
    p.add_argument("plan_file", help="dispatch plan JSON 文件路径")
    p.add_argument("--execute", action="store_true", help="实际移动文件。仅在 plan 经过机械校验和大模型验收后使用")
    p.add_argument("--dry-run", action="store_true", help="兼容旧用法；显式预览，不实际移动")
    return p.parse_args()


def sanitize_note_file_name(stem):
    """Mirror Custom Attachment Location specialCharactersReplacement ('-')."""
    return ''.join('-' if ch in SPECIAL_CHARS else ch for ch in stem).strip() or "untitled"


def resolve_destination(item):
    """Resolve plan destination dynamically.

    Preferred plan formats:
      - destination: vault-relative directory path
      - destination: queue name, e.g. "高考"
      - destination_queue/category_name: queue name when destination is omitted
    """
    dest_rel = (item.get("destination") or "").strip().strip("/")
    queue_name = (item.get("destination_queue") or item.get("category_name") or "").strip()

    queues = queue_by_name()
    if dest_rel in queues:
        dest_rel = queues[dest_rel]["destination"]
    elif not dest_rel and queue_name:
        dest_rel = resolve_queue_destination(queue_name)

    if not dest_rel:
        return None, None
    if Path(dest_rel).is_absolute():
        return None, dest_rel
    return VAULT / dest_rel, dest_rel


def process_getnote_attachments(md_path):
    """Move/copy Get笔记 images next to the moved note and rewrite embeds.

    Returns: (rewritten_link_count, copied_count, missing_count, central_sources)
    """
    try:
        text = md_path.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return 0, 0, 0, set()

    note_folder_name = sanitize_note_file_name(md_path.stem)
    local_asset_dir = md_path.parent / "assets" / note_folder_name
    central_sources = set()
    copied_count = 0
    missing_count = 0

    def ensure_local(filename):
        nonlocal copied_count, missing_count
        filename = Path(filename.strip()).name
        local_asset_dir.mkdir(parents=True, exist_ok=True)
        dest = local_asset_dir / filename
        src = GETNOTE_ASSET_DIR / filename
        if dest.exists():
            return filename
        if src.exists():
            shutil.copy2(src, dest)
            central_sources.add(src)
            copied_count += 1
        else:
            missing_count += 1
        return filename

    rewritten = 0

    # Original Get笔记 relative markdown links: ![](_assets/Get笔记/x.jpg)
    def repl_relative(m):
        nonlocal rewritten
        filename = ensure_local(m.group(2))
        rewritten += 1
        return f'![[assets/{note_folder_name}/{filename}]]'

    text = re.sub(r'!\[([^\]]*)\]\(_assets/Get笔记/([^\)]+)\)', repl_relative, text)

    # Previous/absolute central wikilinks, if any: ![[00_Inbox/Get笔记/_assets/Get笔记/x.jpg]]
    def repl_central_wikilink(m):
        nonlocal rewritten
        filename = ensure_local(m.group(1))
        rewritten += 1
        return f'![[assets/{note_folder_name}/{filename}]]'

    text = re.sub(r'!\[\[00_Inbox/Get笔记/_assets/Get笔记/([^\]]+)\]\]', repl_central_wikilink, text)

    if rewritten:
        md_path.write_text(text, encoding="utf-8")
    return rewritten, copied_count, missing_count, central_sources


def cleanup_unreferenced_central_assets(central_sources):
    """Remove central Get笔记 assets only when no Markdown still references them via the old central path."""
    removed = 0
    if not central_sources:
        return removed

    md_files = [p for p in VAULT.rglob("*.md") if ".obsidian" not in p.parts]
    for src in sorted(central_sources):
        if not src.exists():
            continue
        name = src.name
        rel_markdown = f"_assets/Get笔记/{name}"
        central_wikilink = f"00_Inbox/Get笔记/_assets/Get笔记/{name}"
        still_referenced = False
        for md in md_files:
            try:
                t = md.read_text(encoding="utf-8", errors="ignore")
            except OSError:
                continue
            if rel_markdown in t or central_wikilink in t:
                still_referenced = True
                break
        if not still_referenced:
            src.unlink()
            removed += 1
    return removed


def execute():
    args = parse_args()
    if args.execute and args.dry_run:
        print("❌ --execute 与 --dry-run 不能同时使用", file=sys.stderr)
        sys.exit(2)

    plan_path = Path(args.plan_file).expanduser()
    dry = not args.execute

    if not plan_path.exists():
        print(f"❌ Plan 文件不存在: {plan_path}", file=sys.stderr)
        sys.exit(1)

    plan = json.loads(plan_path.read_text(encoding="utf-8"))
    dispatches = plan.get("dispatches", [])

    if not dispatches:
        print("Plan 中无 dispatch 条目，退出。")
        return

    moved = 0
    skipped_conflict = 0
    skipped_missing = 0
    skipped_invalid = 0
    total_rewritten = 0
    total_copied = 0
    total_missing_assets = 0
    central_sources_for_cleanup = set()

    for item in dispatches:
        source_rel = item.get("source")
        dest_dir, dest_rel = resolve_destination(item)
        if not source_rel or not dest_dir:
            print(f"⚠️  跳过无效条目（缺 source/destination 或 destination_queue）: {item}")
            skipped_invalid += 1
            continue

        src = VAULT / source_rel
        dest = dest_dir / src.name

        if not src.exists():
            print(f"⚠️  源文件不存在: {source_rel}")
            skipped_missing += 1
            continue

        if dest.exists():
            print(f"⏭️  跳过（目标已存在）: {source_rel} -> {dest_rel}")
            skipped_conflict += 1
            continue

        category = item.get("category_id", "")
        confidence = item.get("confidence", "")
        suffix = ""
        if category or confidence:
            suffix = f" [{category} confidence={confidence}]"

        if dry:
            print(f"[DRY-RUN] 将移动: {source_rel} -> {dest_rel}{suffix}")
        else:
            dest_dir.mkdir(parents=True, exist_ok=True)
            shutil.move(str(src), str(dest))
            rewritten, copied, missing_assets, central_sources = process_getnote_attachments(dest)
            total_rewritten += rewritten
            total_copied += copied
            total_missing_assets += missing_assets
            central_sources_for_cleanup.update(central_sources)
            extra_parts = []
            if rewritten:
                extra_parts.append(f"图片链接改写 {rewritten} 处")
            if copied:
                extra_parts.append(f"图片迁移 {copied} 个")
            if missing_assets:
                extra_parts.append(f"缺失图片 {missing_assets} 个")
            extra = "；" + "，".join(extra_parts) if extra_parts else ""
            print(f"✅ 已移动: {source_rel} -> {dest_rel}{suffix}{extra}")

        moved += 1

    removed_central = 0
    if not dry:
        removed_central = cleanup_unreferenced_central_assets(central_sources_for_cleanup)

    verb = "将移动" if dry else "已移动"
    print("\n--- 结果 ---")
    print(f"模式: {'DRY-RUN（未移动）' if dry else 'EXECUTE（已执行）'}")
    print(f"{verb}: {moved} 条")
    if not dry:
        print(f"图片链接改写: {total_rewritten} 处")
        print(f"图片迁移到笔记本地 assets: {total_copied} 个")
        print(f"源目录缺失图片: {total_missing_assets} 个")
        print(f"已清理无旧引用的中央图片: {removed_central} 个")
    if skipped_conflict:
        print(f"跳过（目标已存在）: {skipped_conflict} 条")
    if skipped_missing:
        print(f"跳过（源文件消失）: {skipped_missing} 条")
    if skipped_invalid:
        print(f"跳过（plan 条目无效）: {skipped_invalid} 条")

    if dry:
        print(f"\n当前待处理根目录: {pending_root_rel()}")
        print("如 plan 已完成机械校验和大模型验收且满足执行阈值，再运行：")
        print(f"python3 scripts/mover.py {plan_path} --execute")


if __name__ == "__main__":
    execute()
