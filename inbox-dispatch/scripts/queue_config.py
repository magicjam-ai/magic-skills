#!/usr/bin/env python3
"""Shared queue configuration for inbox-dispatch.

Queue directories are discovered dynamically from the configured pending root.
Metadata in dispatch_rules.json is only descriptive; the filesystem is the
source of truth for which queues currently exist.
"""

import json
import os
import re
from pathlib import Path

VAULT = Path(os.environ.get("OBSIDIAN_VAULT", "~/obsidian")).expanduser()
SCRIPT_DIR = Path(__file__).resolve().parent
RULES_FILE = SCRIPT_DIR / "dispatch_rules.json"
DEFAULT_PENDING_ROOT = "10_思考/待处理"
DEFAULT_FALLBACK_QUEUE = "待判定"
DEFAULT_TRASH_QUEUE = "淘汰候选"
SKIP_QUEUE_DIRS = {"assets", "_assets", ".obsidian"}


def _read_rules():
    if not RULES_FILE.exists():
        return {}
    data = json.loads(RULES_FILE.read_text(encoding="utf-8"))
    # Backward compatibility: old rules file was a list of queue metadata with hard-coded destinations.
    if isinstance(data, list):
        return {"queue_metadata": data}
    if isinstance(data, dict):
        return data
    return {}


def _slugify(name):
    slug = re.sub(r"[^0-9A-Za-z]+", "-", name).strip("-").lower()
    return slug or name


def pending_root_rel():
    rules = _read_rules()
    return (
        os.environ.get("INBOX_DISPATCH_PENDING_ROOT")
        or rules.get("pending_root")
        or DEFAULT_PENDING_ROOT
    ).strip().strip("/")


def pending_root_path():
    return VAULT / pending_root_rel()


def _metadata_by_name():
    rules = _read_rules()
    result = {}
    for item in rules.get("queue_metadata", []):
        if not isinstance(item, dict) or not item.get("name"):
            continue
        meta = dict(item)
        meta.pop("destination", None)  # destination is derived dynamically from pending_root/name
        result[meta["name"]] = meta
    return result


def fallback_queue_name():
    rules = _read_rules()
    return rules.get("fallback_queue") or DEFAULT_FALLBACK_QUEUE


def trash_queue_name():
    rules = _read_rules()
    return rules.get("trash_queue") or DEFAULT_TRASH_QUEUE


def discover_queues():
    root = pending_root_path()
    metadata = _metadata_by_name()
    queues = []
    if not root.is_dir():
        return queues
    for child in sorted(p for p in root.iterdir() if p.is_dir()):
        name = child.name
        if name.startswith(".") or name.startswith("_") or name in SKIP_QUEUE_DIRS:
            continue
        meta = metadata.get(name, {})
        queue = {
            "id": meta.get("id") or _slugify(name),
            "name": name,
            "destination": str(child.relative_to(VAULT)),
            "description": meta.get("description") or f"{name} 队列。未配置说明时，按目录名和实际内容语义判断。",
            "include": meta.get("include", []),
            "exclude": meta.get("exclude", []),
            "configured": name in metadata,
        }
        queues.append(queue)
    return queues


def queue_by_name():
    return {q["name"]: q for q in discover_queues()}


def resolve_queue_destination(queue_name):
    queues = queue_by_name()
    if queue_name in queues:
        return queues[queue_name]["destination"]
    return str((pending_root_path() / queue_name).relative_to(VAULT))


def config_summary():
    return {
        "vault": str(VAULT),
        "pending_root": pending_root_rel(),
        "fallback_queue": fallback_queue_name(),
        "trash_queue": trash_queue_name(),
        "rules_file": str(RULES_FILE),
        "queues": discover_queues(),
    }
