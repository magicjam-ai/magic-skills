#!/usr/bin/env python3
"""Validate inbox-dispatch plan structure against current dynamic queues.

This is a mechanical validator. It does not replace the required LLM semantic
review, but it catches unsafe execution issues before mover.py --execute.
"""

import argparse
import json
from pathlib import Path

from queue_config import VAULT, discover_queues, pending_root_rel, resolve_queue_destination

VALID_CONFIDENCE = {"high", "medium", "low"}


def parse_args():
    p = argparse.ArgumentParser(description="验证 inbox-dispatch plan 的结构与当前队列一致性")
    p.add_argument("plan_file", help="dispatch plan JSON 文件路径")
    p.add_argument("--format", choices=("json", "markdown"), default="json")
    return p.parse_args()


def load_plan(path):
    plan_path = Path(path).expanduser()
    if not plan_path.exists():
        raise SystemExit(f"Plan 文件不存在: {plan_path}")
    return plan_path, json.loads(plan_path.read_text(encoding="utf-8"))


def destination_for(item, queues_by_name, queues_by_dest):
    dest = (item.get("destination") or "").strip().strip("/")
    queue_name = (item.get("destination_queue") or item.get("category_name") or "").strip()

    if dest in queues_by_name:
        q = queues_by_name[dest]
        return q["destination"], q["name"]
    if dest in queues_by_dest:
        q = queues_by_dest[dest]
        return q["destination"], q["name"]
    if not dest and queue_name:
        resolved = resolve_queue_destination(queue_name)
        q = queues_by_name.get(queue_name)
        return resolved, q["name"] if q else queue_name
    return dest, queue_name


def validate(plan_path, plan):
    queues = discover_queues()
    queues_by_name = {q["name"]: q for q in queues}
    queues_by_dest = {q["destination"]: q for q in queues}
    valid_destinations = set(queues_by_dest)

    errors = []
    warnings = []
    dispatches = plan.get("dispatches", [])
    if not isinstance(dispatches, list):
        errors.append({"index": None, "code": "dispatches_not_list", "message": "plan.dispatches 不是数组"})
        dispatches = []
    if not queues:
        errors.append({"index": None, "code": "no_queues", "message": f"当前 pending_root 无可用队列: {pending_root_rel()}"})

    seen_sources = set()
    for i, item in enumerate(dispatches):
        if not isinstance(item, dict):
            errors.append({"index": i, "code": "dispatch_not_object", "message": "dispatch 条目不是对象"})
            continue

        source = (item.get("source") or "").strip()
        if not source:
            errors.append({"index": i, "code": "missing_source", "message": "缺 source"})
        elif source in seen_sources:
            warnings.append({"index": i, "source": source, "code": "duplicate_source", "message": "同一 source 在 plan 中重复出现"})
        else:
            seen_sources.add(source)

        if source:
            src = VAULT / source
            if not src.exists():
                errors.append({"index": i, "source": source, "code": "source_missing", "message": "源文件不存在"})
            elif src.suffix.lower() != ".md":
                warnings.append({"index": i, "source": source, "code": "source_not_md", "message": "源文件不是 .md"})

        dest, resolved_queue = destination_for(item, queues_by_name, queues_by_dest)
        if not dest:
            errors.append({"index": i, "source": source, "code": "missing_destination", "message": "缺 destination 或 destination_queue/category_name"})
        elif dest not in valid_destinations:
            errors.append({
                "index": i,
                "source": source,
                "destination": dest,
                "code": "destination_not_current_queue",
                "message": "目标目录不在当前动态队列清单中",
            })

        category_name = (item.get("category_name") or item.get("destination_queue") or "").strip()
        if category_name and category_name not in queues_by_name:
            errors.append({"index": i, "source": source, "category_name": category_name, "code": "category_not_current_queue", "message": "category_name 不是当前队列名"})
        if category_name and dest in queues_by_dest and category_name != queues_by_dest[dest]["name"]:
            errors.append({
                "index": i,
                "source": source,
                "destination": dest,
                "category_name": category_name,
                "code": "category_destination_mismatch",
                "message": "category_name 与 destination 指向的队列不一致",
            })

        confidence = (item.get("confidence") or "").strip()
        if confidence and confidence not in VALID_CONFIDENCE:
            warnings.append({"index": i, "source": source, "confidence": confidence, "code": "invalid_confidence", "message": "confidence 建议为 high/medium/low"})
        if not confidence:
            warnings.append({"index": i, "source": source, "code": "missing_confidence", "message": "缺 confidence"})
        if not (item.get("reason") or "").strip():
            warnings.append({"index": i, "source": source, "code": "missing_reason", "message": "缺 reason"})

        if source and dest:
            src_name = Path(source).name
            target = VAULT / dest / src_name
            if target.exists():
                errors.append({
                    "index": i,
                    "source": source,
                    "destination": dest,
                    "target": str(target.relative_to(VAULT)),
                    "code": "target_exists",
                    "message": "目标文件已存在，执行时会跳过或冲突",
                })

    return {
        "plan_file": str(plan_path),
        "pending_root": pending_root_rel(),
        "queue_count": len(queues),
        "queues": [q["name"] for q in queues],
        "dispatch_count": len(dispatches),
        "error_count": len(errors),
        "warning_count": len(warnings),
        "auto_execute_allowed_by_mechanical_checks": len(errors) == 0,
        "errors": errors,
        "warnings": warnings,
    }


def print_markdown(result):
    print(f"pending_root: `{result['pending_root']}`")
    print(f"dispatches: {result['dispatch_count']}")
    print(f"mechanical_errors: {result['error_count']}")
    print(f"warnings: {result['warning_count']}")
    if result["errors"]:
        print("\n## Errors")
        for e in result["errors"]:
            print(f"- [{e.get('index')}] `{e.get('code')}` {e.get('source', '')} {e.get('message')}")
    if result["warnings"]:
        print("\n## Warnings")
        for w in result["warnings"][:50]:
            print(f"- [{w.get('index')}] `{w.get('code')}` {w.get('source', '')} {w.get('message')}")


def main():
    args = parse_args()
    plan_path, plan = load_plan(args.plan_file)
    result = validate(plan_path, plan)
    if args.format == "json":
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print_markdown(result)


if __name__ == "__main__":
    main()
