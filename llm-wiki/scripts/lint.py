#!/usr/bin/env python3
"""
llm-wiki lint scanner — cross-OS Python rewrite.
Usage:  python3 scripts/lint.py <wiki-root>

Emits a plain-text report on stdout. The skill agent reads this
and turns it into a severity-ranked summary. This script does NOT
modify the wiki — read-only scan.
"""

import sys, os, re, hashlib
from pathlib import Path
from datetime import datetime, timezone, timedelta
from collections import defaultdict

# ---------------------------------------------------------------------------
# Setup
# ---------------------------------------------------------------------------

if len(sys.argv) < 2:
    print("usage: lint.py <wiki-root>", file=sys.stderr)
    sys.exit(2)

WIKI = Path(sys.argv[1])
if not WIKI.is_dir():
    print(f"error: {WIKI} is not a directory", file=sys.stderr)
    sys.exit(2)

CST = timezone(timedelta(hours=8))
TODAY = datetime.now(CST).strftime("%Y-%m-%d")
STALE_DAYS = 90

CONTENT_DIRS = ["entities", "concepts", "comparisons", "queries"]
RAW_DIRS = ["raw/articles", "raw/papers", "raw/transcripts"]

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def rel(p):
    """Path relative to wiki root."""
    try:
        return str(p.relative_to(WIKI))
    except ValueError:
        return str(p)


def read_file(p):
    try:
        return p.read_text(encoding="utf-8")
    except Exception:
        return ""


def parse_frontmatter(text):
    """Return dict of YAML frontmatter key-value pairs (flat)."""
    m = re.match(r'^---\s*\n(.*?)\n---', text, re.DOTALL)
    if not m:
        return {}
    fm = {}
    for line in m.group(1).split("\n"):
        line = line.strip()
        if line.startswith("- ") or not line:
            continue
        if ":" in line:
            k, v = line.split(":", 1)
            fm[k.strip()] = v.strip()
    return fm


def get_pages():
    """Collect all wiki content pages (exclude raw/, _archive/)."""
    pages = []
    for d in CONTENT_DIRS:
        dd = WIKI / d
        if dd.is_dir():
            pages.extend(sorted(dd.glob("*.md")))
    return pages


def get_meta_pages():
    """Collect meta pages at wiki root (SCHEMA.md, index.md, log.md)."""
    return sorted(WIKI.glob("*.md"))


def get_raw_files():
    """Collect all raw source files."""
    files = []
    for d in RAW_DIRS:
        dd = WIKI / d
        if dd.is_dir():
            files.extend(sorted(dd.glob("*.md")))
    return files


def extract_wikilinks(text):
    """Extract wikilink targets from text. Returns list of target slugs."""
    links = re.findall(r'\[\[([^\]]+)\]\]', text)
    targets = []
    for link in links:
        # Strip alt text: [[page|alt]] → page
        target = link.split("|")[0].split("#")[0].strip()
        if target:
            targets.append(target)
    return targets


def slug_of(path):
    """Basename without .md extension."""
    return path.stem


def file_sha256_body(filepath):
    """Compute SHA-256 over the body (everything after the 2nd ---)."""
    text = read_file(filepath)
    parts = text.split("---", 2)
    if len(parts) >= 3:
        body = parts[2]
    else:
        body = text
    return hashlib.sha256(body.encode("utf-8")).hexdigest()


# ---------------------------------------------------------------------------
# Main lint
# ---------------------------------------------------------------------------

def main():
    pages = get_pages()
    meta_pages = get_meta_pages()
    raw_files = get_raw_files()
    all_slugs = {}  # slug → filepath (includes meta pages)

    for p in pages + meta_pages:
        all_slugs[slug_of(p)] = p

    print(f"=== llm-wiki lint: {WIKI} ===")
    print(f"wiki pages found: {len(pages)}")
    print()

    # Track inbound links for orphan detection
    inbound = defaultdict(int)

    # Pre-read all page contents and frontmatters
    page_data = {}
    for p in pages:
        text = read_file(p)
        fm = parse_frontmatter(text)
        links = extract_wikilinks(text)
        page_data[p] = {"text": text, "fm": fm, "links": links}

    # ------------------------------------------------------------------
    # [1] Frontmatter check
    # ------------------------------------------------------------------
    print("--- [1] frontmatter missing or incomplete ---")
    REQUIRED = ["title", "created", "updated", "type", "tags", "sources"]
    issues_1 = 0
    for p in pages:
        fm = page_data[p]["fm"]
        if not fm:
            print(f"  NO_FRONTMATTER: {rel(p)}")
            issues_1 += 1
            continue
        for k in REQUIRED:
            if k not in fm:
                print(f"  MISSING[{k}]: {rel(p)}")
                issues_1 += 1
    if issues_1 == 0:
        print("  ✅ all pages have complete frontmatter")
    print()

    # ------------------------------------------------------------------
    # [2] Broken wikilinks
    # ------------------------------------------------------------------
    print("--- [2] broken wikilinks ---")
    issues_2 = 0
    for p in pages:
        for target in page_data[p]["links"]:
            # Try full match first, then basename
            base = Path(target).stem if "/" in target else target
            if base not in all_slugs:
                print(f"  BROKEN: {rel(p)} -> [[{target}]]")
                issues_2 += 1
            else:
                inbound[base] += 1
    if issues_2 == 0:
        print("  ✅ no broken wikilinks")
    print()

    # ------------------------------------------------------------------
    # [3] Orphan pages (no inbound links)
    # ------------------------------------------------------------------
    print("--- [3] orphan pages (0 inbound, excluding queries/) ---")
    issues_3 = 0
    for p in pages:
        if str(p).endswith("/queries") or "/queries/" in str(p):
            continue
        slug = slug_of(p)
        if inbound.get(slug, 0) == 0:
            print(f"  ORPHAN: {rel(p)}")
            issues_3 += 1
    if issues_3 == 0:
        print("  ✅ no orphan pages")
    print()

    # [3b] Queries without backlinks
    print("--- [3b] queries without inbound back-links ---")
    issues_3b = 0
    for p in pages:
        if "/queries/" not in str(p):
            continue
        slug = slug_of(p)
        if inbound.get(slug, 0) == 0:
            print(f"  NO_BACKLINK: {rel(p)}")
            issues_3b += 1
    if issues_3b == 0:
        print("  ✅ all queries have backlinks (or no queries exist)")
    print()

    # ------------------------------------------------------------------
    # [4] Index completeness
    # ------------------------------------------------------------------
    print("--- [4] pages missing from index.md ---")
    index_path = WIKI / "index.md"
    if index_path.exists():
        index_text = read_file(index_path)
        issues_4 = 0
        for p in pages:
            slug = slug_of(p)
            # Match [[slug]] or [[path/slug]] or [[slug|alt]]
            pattern = rf'\[\[[^\]]*{re.escape(slug)}[^\]]*\]\]'
            if not re.search(pattern, index_text):
                print(f"  NOT_INDEXED: {rel(p)}")
                issues_4 += 1
        if issues_4 == 0:
            print("  ✅ all pages indexed")
    else:
        print("  MISSING: index.md not found")
    print()

    # ------------------------------------------------------------------
    # [5] Minimum 2 outbound wikilinks
    # ------------------------------------------------------------------
    print("--- [5] pages with <2 outbound wikilinks ---")
    issues_5 = 0
    for p in pages:
        n = len(page_data[p]["links"])
        if n < 2:
            print(f"  LOW_LINKS[{n}]: {rel(p)}")
            issues_5 += 1
    if issues_5 == 0:
        print("  ✅ all pages have ≥2 outbound links")
    print()

    # ------------------------------------------------------------------
    # [6] Page size (>200 lines)
    # ------------------------------------------------------------------
    print("--- [6] pages over 200 lines (split candidates) ---")
    issues_6 = 0
    for p in pages:
        lines = page_data[p]["text"].count("\n") + 1
        if lines > 200:
            print(f"  LARGE[{lines}]: {rel(p)}")
            issues_6 += 1
    if issues_6 == 0:
        print("  ✅ all pages under 200 lines")
    print()

    # ------------------------------------------------------------------
    # [7] Source consistency (files referenced in frontmatter exist)
    # ------------------------------------------------------------------
    print("--- [7] source file consistency ---")
    # Read .wiki-config to understand external sources
    config_path = WIKI / ".wiki-config"
    external_sources = []
    if config_path.exists():
        config_text = read_file(config_path)
        for line in config_text.split("\n"):
            line = line.strip()
            if line.startswith("- ") and not line.startswith("#"):
                external_sources.append(line[2:].strip())

    issues_7_missing = 0
    issues_7_inbox = 0
    sources_in_raw = 0
    sources_in_project = 0

    for p in pages:
        text = page_data[p]["text"]
        # Extract source paths from frontmatter
        for m in re.findall(r'^\s*-\s*(.+\.md)$', text, re.MULTILINE):
            src = m.strip()
            # Skip false positives
            if src.startswith("**") or src.startswith("#"):
                continue

            # Resolve path
            if src.startswith("raw/"):
                full = WIKI / src
            else:
                # Vault-root relative paths: try multiple ancestors
                # e.g. wiki at "40_知识/wiki" → vault root is 2 levels up
                full = None
                candidate = WIKI
                for _ in range(5):
                    candidate = candidate.parent
                    probe = candidate / src
                    if probe.exists():
                        full = probe
                        break
                if full is None:
                    # Default: try 2 levels up (most common nesting)
                    full = WIKI.parent.parent / src

            if full.exists():
                if src.startswith("raw/"):
                    sources_in_raw += 1
                elif src.startswith("30_项目/"):
                    sources_in_project += 1
                elif src.startswith("00_Inbox/"):
                    print(f"  IN_INBOX: {rel(p)} references {src}")
                    issues_7_inbox += 1
            else:
                # Check if it's an external source declared in .wiki-config
                is_external = any(ext in src for ext in external_sources)
                if not is_external:
                    print(f"  MISSING_SRC: {rel(p)} references {src}")
                    issues_7_missing += 1

    # Orphan raw files (in raw/ but not referenced)
    referenced_raw = set()
    for p in pages:
        text = page_data[p]["text"]
        for m in re.findall(r'^\s*-\s*raw/articles/(.+\.md)$', text, re.MULTILINE):
            referenced_raw.add(m.strip())
    orphan_raw = 0
    for rf in raw_files:
        if rf.name not in referenced_raw:
            print(f"  ORPHAN_RAW: {rel(rf)}")
            orphan_raw += 1

    print(f"  stats: {sources_in_raw} in raw/, {sources_in_project} in project/, "
          f"{issues_7_inbox} in inbox, {issues_7_missing} missing, {orphan_raw} orphan raw files")
    if issues_7_missing == 0 and issues_7_inbox == 0 and orphan_raw == 0:
        print("  ✅ all sources consistent")
    print()

    # ------------------------------------------------------------------
    # [8] Raw sha256 drift
    # ------------------------------------------------------------------
    print("--- [8] raw source sha256 drift ---")
    issues_8 = 0
    for rf in raw_files:
        text = read_file(rf)
        fm = parse_frontmatter(text)
        declared = fm.get("sha256", "")
        if not declared:
            continue
        actual = file_sha256_body(rf)
        if declared != actual:
            print(f"  DRIFT: {rel(rf)}")
            print(f"    declared: {declared}")
            print(f"    actual:   {actual}")
            issues_8 += 1
    if issues_8 == 0:
        print("  ✅ no sha256 drift")
    print()

    # ------------------------------------------------------------------
    # [9] Log rotation
    # ------------------------------------------------------------------
    print("--- [9] log rotation ---")
    log_path = WIKI / "log.md"
    if log_path.exists():
        log_text = read_file(log_path)
        entries = len(re.findall(r'^## \[', log_text, re.MULTILINE))
        print(f"  log.md entries: {entries}")
        if entries > 500:
            print(f"  ROTATE_NEEDED: log.md has {entries} entries (>500)")
    else:
        print("  MISSING: log.md not found")
    print()

    # ------------------------------------------------------------------
    # [10] Contested / low-confidence
    # ------------------------------------------------------------------
    print("--- [10] contested / low-confidence pages ---")
    for p in pages:
        fm = page_data[p]["fm"]
        confidence = fm.get("confidence", "")
        contested = fm.get("contested", "")
        if contested.lower() == "true":
            print(f"  CONTESTED: {rel(p)}")
        if confidence == "low":
            print(f"  LOW_CONFIDENCE: {rel(p)}")
    print()

    # ------------------------------------------------------------------
    # [11] Stale content (updated > 90 days ago)
    # ------------------------------------------------------------------
    print(f"--- [11] stale content (not updated in {STALE_DAYS} days) ---")
    issues_11 = 0
    try:
        today = datetime.now(CST).date()
    except Exception:
        today = datetime.now(timezone.utc).date()
    for p in pages:
        fm = page_data[p]["fm"]
        updated = fm.get("updated", "")
        if not updated:
            continue
        try:
            upd_date = datetime.strptime(updated[:10], "%Y-%m-%d").date()
            age = (today - upd_date).days
            if age > STALE_DAYS:
                print(f"  STALE[{age}d]: {rel(p)} (last updated {updated})")
                issues_11 += 1
        except ValueError:
            pass
    if issues_11 == 0:
        print("  ✅ no stale pages")
    print()

    # ------------------------------------------------------------------
    # [12] Tag taxonomy check
    # ------------------------------------------------------------------
    print("--- [12] tags not in SCHEMA taxonomy ---")
    schema_path = WIKI / "SCHEMA.md"
    if schema_path.exists():
        schema_text = read_file(schema_path)
        # Extract tags from taxonomy section
        taxonomy = set(re.findall(r'-\s+`?([a-z][a-z0-9_-]+)`?', schema_text))
        # Also add tags from lines like "knowledge-graph," etc.
        # Extract tags from comma-separated lists or inline text
        taxonomy.update(re.findall(r'(?:^|[\s,])([a-z][a-z0-9_-]+)(?=[\s,]|$)', schema_text))

        issues_12 = 0
        for p in pages:
            fm = page_data[p]["fm"]
            tags_str = fm.get("tags", "")
            # Parse tags: [tag1, tag2, ...] or just tag1 tag2
            tags = re.findall(r'[\w-]+', tags_str)
            for t in tags:
                if t not in taxonomy:
                    print(f"  UNKNOWN_TAG[{t}]: {rel(p)}")
                    issues_12 += 1
        if issues_12 == 0:
            print("  ✅ all tags in taxonomy")
    else:
        print("  MISSING: SCHEMA.md not found")
    print()

    # ------------------------------------------------------------------
    # Summary
    # ------------------------------------------------------------------
    total_issues = (issues_1 + issues_2 + issues_3 + issues_3b +
                    issues_5 + issues_6 + issues_7_missing + issues_7_inbox +
                    orphan_raw + issues_8 + issues_11 + issues_12)
    print(f"=== lint done: {total_issues} issues found ===")


if __name__ == "__main__":
    main()
