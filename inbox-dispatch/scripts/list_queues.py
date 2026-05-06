#!/usr/bin/env python3
"""List current inbox-dispatch queues discovered from the configured pending root."""

import argparse
import json
from queue_config import config_summary


def parse_args():
    p = argparse.ArgumentParser(description="列出 inbox-dispatch 当前可用队列")
    p.add_argument("--format", choices=("json", "markdown"), default="json")
    return p.parse_args()


def main():
    args = parse_args()
    summary = config_summary()
    if args.format == "json":
        print(json.dumps(summary, ensure_ascii=False, indent=2))
        return
    print(f"pending_root: `{summary['pending_root']}`")
    print(f"fallback_queue: `{summary['fallback_queue']}`")
    print(f"trash_queue: `{summary['trash_queue']}`")
    print("\n| 队列 | 目标目录 | 说明 |")
    print("|---|---|---|")
    for q in summary["queues"]:
        mark = "" if q["configured"] else "（未配置说明）"
        print(f"| `{q['name']}` | `{q['destination']}` | {q['description']}{mark} |")


if __name__ == "__main__":
    main()
