#!/usr/bin/env python3
"""
inbox-dispatch mover: 执行 dispatch plan（将文件从 inbox 移到目标目录）。

用法：
  python3 mover.py plan.json               # 执行移动
  python3 mover.py plan.json --dry-run     # 预览，不实际移动
"""

import os, sys, json, shutil, argparse
from pathlib import Path
from datetime import datetime

VAULT = Path(os.path.expanduser("~/obsidian"))


def parse_args():
    p = argparse.ArgumentParser(description="执行 inbox dispatch plan")
    p.add_argument("plan_file", help="dispatch plan JSON 文件路径")
    p.add_argument("--dry-run", action="store_true", help="预览模式，不实际移动文件")
    return p.parse_args()


def execute():
    args = parse_args()
    plan_path = Path(args.plan_file)
    dry = args.dry_run

    if not plan_path.exists():
        print(f"❌ Plan 文件不存在: {plan_path}", file=sys.stderr)
        sys.exit(1)

    plan = json.loads(plan_path.read_text(encoding="utf-8"))
    dispatches = plan.get("dispatches", [])

    if not dispatches:
        print("Plan 中无 dispatch 条目，退出。")
        return

    moved, skipped_conflict, skipped_missing = 0, 0, 0

    for item in dispatches:
        src = VAULT / item["source"]
        dest_dir = VAULT / item["destination"]
        dest = dest_dir / src.name

        if not src.exists():
            print(f"⚠️  源文件不存在: {item['source']}")
            skipped_missing += 1
            continue

        if dest.exists():
            print(f"⏭️  跳过（目标已存在）: {item['source']} -> {item['destination']}")
            skipped_conflict += 1
            continue

        if dry:
            print(f"[DRY-RUN] 将移动: {item['source']} -> {item['destination']}")
        else:
            dest_dir.mkdir(parents=True, exist_ok=True)
            shutil.move(str(src), str(dest))
            print(f"✅ 已移动: {item['source']} -> {item['destination']}")

        moved += 1

    verb = "将移动" if dry else "已移动"
    print(f"\n--- 结果 ---")
    print(f"{verb}: {moved} 条")
    if skipped_conflict:
        print(f"跳过（目标已存在）: {skipped_conflict} 条")
    if skipped_missing:
        print(f"跳过（源文件消失）: {skipped_missing} 条")


if __name__ == "__main__":
    execute()
