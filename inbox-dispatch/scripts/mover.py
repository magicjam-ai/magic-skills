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
import subprocess
import filecmp
import time
from datetime import datetime
from pathlib import Path

from queue_config import VAULT, resolve_queue_destination, queue_by_name, pending_root_rel

GETNOTE_ASSET_DIR = VAULT / "00_Inbox" / "Get笔记" / "_assets" / "Get笔记"
LOCAL_ASSETS_DIR_NAME = "assets"
SPECIAL_CHARS = set('#^[]|*\\<>:?/')
_last_attachment_ms = 0


def parse_args():
    p = argparse.ArgumentParser(description="执行 inbox dispatch plan；默认只预览")
    p.add_argument("plan_file", help="dispatch plan JSON 文件路径")
    p.add_argument("--execute", action="store_true", help="实际移动文件。仅在 plan 经过机械校验和大模型验收后使用")
    p.add_argument("--dry-run", action="store_true", help="兼容旧用法；显式预览，不实际移动")
    return p.parse_args()


def sanitize_note_file_name(stem):
    """Mirror Custom Attachment Location specialCharactersReplacement ('-')."""
    return ''.join('-' if ch in SPECIAL_CHARS else ch for ch in stem).strip() or "untitled"


def next_attachment_basename(ext):
    """Mirror Custom Attachment Location generatedAttachmentFileName: file-YYYYMMDDHHmmssSSS."""
    global _last_attachment_ms
    current_ms = time.time_ns() // 1_000_000
    if current_ms <= _last_attachment_ms:
        current_ms = _last_attachment_ms + 1
    _last_attachment_ms = current_ms
    dt = datetime.fromtimestamp(current_ms / 1000)
    return f"file-{dt.strftime('%Y%m%d%H%M%S')}{current_ms % 1000:03d}{ext or '.jpg'}"


def custom_attachment_folder_name(stem):
    """Folder used for newly created attachments under ./assets/${noteFileName}.

    The plugin config has specialCharactersReplacement='-'. Existing notes may
    already contain unsanitized folder names from getnote-sync, so local-asset
    moves are driven primarily by actual links. This helper is used when we need
    to create a folder while converting old central Get笔记 assets.
    """
    return sanitize_note_file_name(stem)


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


def capture_file_times(path):
    """Capture timestamps before moving a note.

    Robert reads Inbox/待处理 notes sorted newest-to-oldest by creation time.
    Moving by copy/delete, or rewriting Get笔记 image links after a move, can
    make notes look newly-created/modified and destroy that reading order.

    On macOS, ``st_birthtime`` is the Finder/Obsidian-visible creation time.
    Python can restore atime/mtime directly; birthtime needs ``SetFile``.
    """
    st = path.stat()
    return {
        "atime_ns": st.st_atime_ns,
        "mtime_ns": st.st_mtime_ns,
        "birthtime": getattr(st, "st_birthtime", None),
    }


def _setfile_date(ts):
    return datetime.fromtimestamp(ts).strftime("%m/%d/%Y %H:%M:%S")


def restore_file_times(path, times):
    """Best-effort restore of creation and modified/access times.

    ``ctime`` (inode status-change time) cannot be preserved on POSIX and will
    still change when a file is renamed or metadata is updated. We preserve the
    user-facing macOS birthtime when possible, and always restore mtime/atime.

    Returns a list of warning strings.
    """
    warnings = []

    birthtime = times.get("birthtime")
    if birthtime is not None and sys.platform == "darwin":
        try:
            subprocess.run(
                ["SetFile", "-d", _setfile_date(birthtime), str(path)],
                check=True,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
                text=True,
            )
        except Exception as e:
            warnings.append(f"创建时间恢复失败: {path.name}: {e}")

    try:
        os.utime(path, ns=(times["atime_ns"], times["mtime_ns"]))
    except Exception as e:
        warnings.append(f"修改时间恢复失败: {path.name}: {e}")

    return warnings


def move_note_preserving_file_times(src, dest):
    """Move a Markdown note while preserving user-facing timestamps.

    Prefer ``os.rename`` over ``shutil.move`` so same-volume moves preserve the
    original inode and macOS birthtime. If a cross-device move ever occurs, use
    copy2+unlink and then restore timestamps explicitly.
    """
    times = capture_file_times(src)
    try:
        os.rename(src, dest)
    except OSError as e:
        if getattr(e, "errno", None) != 18:  # EXDEV: cross-device link
            raise
        shutil.copy2(src, dest)
        restore_file_times(dest, times)
        src.unlink()
    return times


def local_asset_refs_from_text(text):
    """Return vault-note-relative attachment paths beginning with assets/.

    Supports the formats produced by getnote-sync and Obsidian:
      - ![](<assets/Note/file-...jpg>)
      - ![](assets/Note/file-...jpg)
      - ![[assets/Note/file-...jpg]]
      - <img src="assets/Note/file-...jpg">
    """
    refs = []
    patterns = [
        r'!\[[^\]]*\]\(<(assets/[^>]+)>\)',
        r'!\[[^\]]*\]\((assets/[^\)\n]+)\)',
        r'!\[\[(assets/[^|\]]+)(?:\|[^\]]*)?\]\]',
        r'<img[^>]+src=["\'](assets/[^"\']+)["\']',
    ]
    seen = set()
    for pattern in patterns:
        for match in re.finditer(pattern, text):
            rel = match.group(1).strip()
            if rel and rel not in seen:
                seen.add(rel)
                refs.append(rel)
    return refs


def remove_empty_dirs_up_to(path, stop):
    """Remove empty directories from path upward until stop (exclusive)."""
    path = Path(path)
    stop = Path(stop)
    while path != stop and path != path.parent:
        try:
            path.rmdir()
        except OSError:
            break
        path = path.parent


def move_asset_file(src_file, dest_file):
    """Move one attachment file, avoiding overwrites.

    Returns: (final_dest_file, moved_count, renamed_bool)
    """
    dest_file.parent.mkdir(parents=True, exist_ok=True)
    if dest_file.exists():
        try:
            if src_file.exists() and filecmp.cmp(src_file, dest_file, shallow=False):
                src_file.unlink()
                return dest_file, 0, False
        except OSError:
            pass
        # Collision with different content: keep Custom Attachment Location naming
        # and ask the caller to rewrite links to the new relative path.
        candidate = dest_file.parent / next_attachment_basename(dest_file.suffix)
        while candidate.exists():
            candidate = dest_file.parent / next_attachment_basename(dest_file.suffix)
        dest_file = candidate
        renamed = True
    else:
        renamed = False
    shutil.move(str(src_file), str(dest_file))
    return dest_file, 1, renamed


def process_local_attachments(src_md_path, dest_md_path):
    """Move attachments already stored beside the source note.

    This is required because mover.py uses filesystem rename instead of Obsidian
    APIs, so the Custom Attachment Location plugin is not invoked automatically.

    Returns: (rewritten_link_count, moved_count, missing_count)
    """
    try:
        text = dest_md_path.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return 0, 0, 0

    src_assets_root = src_md_path.parent / LOCAL_ASSETS_DIR_NAME
    dest_assets_root = dest_md_path.parent / LOCAL_ASSETS_DIR_NAME
    refs = local_asset_refs_from_text(text)
    moved = 0
    missing = 0
    rewrite_map = {}
    processed_sources = set()

    def move_by_rel(rel, count_missing=True):
        nonlocal moved, missing
        rel_path = Path(rel)
        src_file = src_md_path.parent / rel_path
        dest_file = dest_md_path.parent / rel_path
        if src_file.exists() and src_file.is_file():
            final_dest, moved_count, renamed = move_asset_file(src_file, dest_file)
            moved += moved_count
            processed_sources.add(src_file)
            if renamed:
                rewrite_map[rel] = str(final_dest.relative_to(dest_md_path.parent))
            return
        if dest_file.exists():
            return
        if count_missing:
            missing += 1

    for rel in refs:
        # Only manage note-local assets; leave external URLs and unrelated paths alone.
        if rel == LOCAL_ASSETS_DIR_NAME or not rel.startswith(f"{LOCAL_ASSETS_DIR_NAME}/"):
            continue
        move_by_rel(rel, count_missing=True)

    # Also move the conventional note attachment folder, including unreferenced
    # files that may still belong to the note. Use both actual and sanitized
    # noteFileName variants for compatibility with getnote-sync and the plugin.
    candidate_names = [src_md_path.stem, custom_attachment_folder_name(src_md_path.stem)]
    for folder_name in dict.fromkeys(candidate_names):
        src_dir = src_assets_root / folder_name
        if not src_dir.is_dir():
            continue
        for src_file in list(src_dir.rglob("*")):
            if not src_file.is_file() or src_file in processed_sources:
                continue
            rel = str(src_file.relative_to(src_md_path.parent))
            move_by_rel(rel, count_missing=False)

    if rewrite_map:
        for old, new in rewrite_map.items():
            text = text.replace(old, new)
        dest_md_path.write_text(text, encoding="utf-8")

    if src_assets_root.exists():
        # Clean up empty source attachment folders left after moving files.
        for folder in sorted([p for p in src_assets_root.rglob("*") if p.is_dir()], reverse=True):
            remove_empty_dirs_up_to(folder, src_assets_root.parent)
        remove_empty_dirs_up_to(src_assets_root, src_assets_root.parent)

    return len(rewrite_map), moved, missing


def process_getnote_attachments(md_path):
    """Move/copy Get笔记 images next to the moved note and rewrite embeds.

    Returns: (rewritten_link_count, copied_count, missing_count, central_sources)
    """
    try:
        text = md_path.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return 0, 0, 0, set()

    note_folder_name = custom_attachment_folder_name(md_path.stem)
    local_asset_dir = md_path.parent / "assets" / note_folder_name
    central_sources = set()
    copied_count = 0
    missing_count = 0

    def ensure_local(filename):
        nonlocal copied_count, missing_count
        filename = Path(filename.strip()).name
        local_asset_dir.mkdir(parents=True, exist_ok=True)
        src = GETNOTE_ASSET_DIR / filename
        if src.exists():
            dest = local_asset_dir / next_attachment_basename(src.suffix)
            while dest.exists():
                dest = local_asset_dir / next_attachment_basename(src.suffix)
            shutil.copy2(src, dest)
            central_sources.add(src)
            copied_count += 1
            return f"assets/{note_folder_name}/{dest.name}"
        else:
            missing_count += 1
            return None

    rewritten = 0

    # Original Get笔记 relative markdown links: ![](_assets/Get笔记/x.jpg)
    def repl_relative(m):
        nonlocal rewritten
        rel = ensure_local(m.group(2))
        if not rel:
            return m.group(0)
        rewritten += 1
        return f'![{m.group(1)}](<{rel}>)'

    text = re.sub(r'!\[([^\]]*)\]\(_assets/Get笔记/([^\)]+)\)', repl_relative, text)

    # Previous/absolute central wikilinks, if any: ![[00_Inbox/Get笔记/_assets/Get笔记/x.jpg]]
    def repl_central_wikilink(m):
        nonlocal rewritten
        rel = ensure_local(m.group(1))
        if not rel:
            return m.group(0)
        rewritten += 1
        return f'![](<{rel}>)'

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
            try:
                src.unlink()
                removed += 1
            except FileNotFoundError:
                # Another cleanup path may have removed the same central asset already.
                continue
    return removed


def collect_getnote_central_sources(md_path):
    """Collect central Get笔记 image files referenced by a note before deletion."""
    try:
        text = md_path.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return set()

    sources = set()
    for m in re.finditer(r'!\[[^\]]*\]\(_assets/Get笔记/([^\)]+)\)', text):
        filename = Path(m.group(1).strip()).name
        sources.add(GETNOTE_ASSET_DIR / filename)
    for m in re.finditer(r'!\[\[00_Inbox/Get笔记/_assets/Get笔记/([^\]]+)\]\]', text):
        filename = Path(m.group(1).strip()).name
        sources.add(GETNOTE_ASSET_DIR / filename)
    return sources


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
    deleted_duplicate_sources = 0
    skipped_missing = 0
    skipped_invalid = 0
    total_rewritten = 0
    total_copied = 0
    total_missing_assets = 0
    total_time_restored = 0
    time_restore_warnings = []
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
            if dry:
                print(f"[DRY-RUN] 目标已存在，将删除 00_Inbox 重复源笔记: {source_rel} -> {dest_rel}")
            else:
                central_sources_for_cleanup.update(collect_getnote_central_sources(src))
                src.unlink()
                print(f"🗑️  已删除重复源笔记（目标已存在）: {source_rel} -> {dest_rel}")
            deleted_duplicate_sources += 1
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
            original_times = move_note_preserving_file_times(src, dest)
            local_rewritten, local_moved, local_missing_assets = process_local_attachments(src, dest)
            central_rewritten, central_copied, central_missing_assets, central_sources = process_getnote_attachments(dest)
            time_restore_warnings.extend(restore_file_times(dest, original_times))
            total_time_restored += 1
            total_rewritten += local_rewritten + central_rewritten
            total_copied += local_moved + central_copied
            total_missing_assets += local_missing_assets + central_missing_assets
            central_sources_for_cleanup.update(central_sources)
            extra_parts = []
            if local_rewritten + central_rewritten:
                extra_parts.append(f"图片链接改写 {local_rewritten + central_rewritten} 处")
            if local_moved + central_copied:
                extra_parts.append(f"图片迁移 {local_moved + central_copied} 个")
            if local_missing_assets + central_missing_assets:
                extra_parts.append(f"缺失图片 {local_missing_assets + central_missing_assets} 个")
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
        print(f"已恢复笔记创建/修改时间: {total_time_restored} 条")
        if time_restore_warnings:
            print(f"时间恢复警告: {len(time_restore_warnings)} 条")
            for warning in time_restore_warnings[:5]:
                print(f"  - {warning}")
        print(f"已清理无旧引用的中央图片: {removed_central} 个")
    if deleted_duplicate_sources:
        print(f"{'将删除' if dry else '已删除'}重复源笔记（目标已存在）: {deleted_duplicate_sources} 条")
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
