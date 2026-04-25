#!/usr/bin/env bash
# llm-wiki lint scanner — programmatic first-pass.
# Usage:  scripts/lint.sh <wiki-root>
#
# Emits a plain-text report on stdout. The skill agent is expected to read this
# and turn it into a severity-ranked summary for the user. This script does NOT
# modify the wiki — read-only scan.

set -u

WIKI="${1:-}"
if [[ -z "$WIKI" || ! -d "$WIKI" ]]; then
  echo "usage: lint.sh <wiki-root>" >&2
  exit 2
fi

cd "$WIKI" || exit 2

# Collect wiki pages (exclude raw/, _archive/)
mapfile -t PAGES < <(find entities concepts comparisons queries -type f -name '*.md' 2>/dev/null | sort)

# Collect meta pages at the wiki root (schema.md, index.md, log.md, and any other
# user-authored meta files). These are valid wikilink targets but are NOT subject
# to the type-page checks (frontmatter, orphan, size, etc.).
mapfile -t META_PAGES < <(find . -maxdepth 1 -type f -name '*.md' 2>/dev/null | sort)

echo "=== llm-wiki lint: $WIKI ==="
echo "wiki pages found: ${#PAGES[@]}"
echo

# ---------- 1. Frontmatter check ----------
echo "--- [1] frontmatter missing or incomplete ---"
REQUIRED_FIELDS=(title created updated type tags sources)
for f in "${PAGES[@]}"; do
  # Extract frontmatter block (between first two ---)
  fm=$(awk '/^---$/{c++; if(c==2) exit; next} c==1' "$f")
  if [[ -z "$fm" ]]; then
    echo "  NO_FRONTMATTER: $f"
    continue
  fi
  for k in "${REQUIRED_FIELDS[@]}"; do
    if ! grep -q "^$k:" <<<"$fm"; then
      echo "  MISSING[$k]: $f"
    fi
  done
done
echo

# ---------- 2. Wikilinks: collect outbound, detect broken ----------
echo "--- [2] broken wikilinks ---"
# Map every page slug (basename without .md) to its file.
# Both type pages and meta pages (schema/index/log) are valid wikilink targets.
declare -A PAGE_BY_SLUG
for f in "${PAGES[@]}" "${META_PAGES[@]}"; do
  slug=$(basename "$f" .md)
  PAGE_BY_SLUG[$slug]="$f"
done

declare -A INBOUND_COUNT
for f in "${PAGES[@]}"; do
  # grep wikilinks — handle [[page]] and [[page|alt]]
  while IFS= read -r target; do
    [[ -z "$target" ]] && continue
    # strip alt text
    target="${target%%|*}"
    # strip heading anchor
    target="${target%%#*}"
    # allow folder prefix — keep just the last component for slug matching
    base=$(basename "$target")
    if [[ -z "${PAGE_BY_SLUG[$base]:-}" ]]; then
      echo "  BROKEN: $f -> [[$target]]"
    else
      INBOUND_COUNT[$base]=$((${INBOUND_COUNT[$base]:-0} + 1))
    fi
  done < <(grep -oE '\[\[[^]]+\]\]' "$f" | sed -E 's/^\[\[|\]\]$//g')
done
echo

# ---------- 3. Orphan pages (no inbound wikilinks) ----------
# queries/ are leaf pages by design — reported separately below.
echo "--- [3] orphan pages (0 inbound, excluding queries/) ---"
for f in "${PAGES[@]}"; do
  [[ "$f" == queries/* ]] && continue
  slug=$(basename "$f" .md)
  if [[ -z "${INBOUND_COUNT[$slug]:-}" ]]; then
    echo "  ORPHAN: $f"
  fi
done
echo

echo "--- [3b] filed queries without inbound back-links ---"
echo "  (queries are leaves by design; adding a back-link from a relevant"
echo "   concept/entity page makes the wiki graph more discoverable)"
for f in "${PAGES[@]}"; do
  [[ "$f" != queries/* ]] && continue
  slug=$(basename "$f" .md)
  if [[ -z "${INBOUND_COUNT[$slug]:-}" ]]; then
    echo "  NO_BACKLINK: $f"
  fi
done
echo

# ---------- 4. Index completeness ----------
echo "--- [4] pages missing from index.md ---"
if [[ -f index.md ]]; then
  index_content=$(cat index.md)
  for f in "${PAGES[@]}"; do
    slug=$(basename "$f" .md)
    if ! grep -qE "\[\[([^]]+/)?${slug}(\||\]\])" <<<"$index_content"; then
      echo "  NOT_INDEXED: $f"
    fi
  done
else
  echo "  MISSING: index.md not found"
fi
echo

# ---------- 5. Minimum 2 outbound wikilinks ----------
echo "--- [5] pages with <2 outbound wikilinks ---"
for f in "${PAGES[@]}"; do
  n=$(grep -oE '\[\[[^]]+\]\]' "$f" | wc -l)
  if (( n < 2 )); then
    echo "  LOW_LINKS[$n]: $f"
  fi
done
echo

# ---------- 6. Page size ----------
echo "--- [6] pages over 200 lines (split candidates) ---"
for f in "${PAGES[@]}"; do
  lines=$(wc -l < "$f")
  if (( lines > 200 )); then
    echo "  LARGE[$lines]: $f"
  fi
done
echo

# ---------- 7. Raw sha256 drift ----------
echo "--- [7] raw source sha256 drift ---"
if [[ -d raw ]]; then
  while IFS= read -r f; do
    declared=$(awk '/^---$/{c++; if(c==2) exit; next} c==1 && /^sha256:/ {print $2}' "$f")
    [[ -z "$declared" ]] && continue
    # compute sha256 over body (everything after the 2nd ---)
    actual=$(awk '/^---$/{c++; next} c>=2' "$f" | sha256sum | awk '{print $1}')
    if [[ "$declared" != "$actual" ]]; then
      echo "  DRIFT: $f"
      echo "    declared: $declared"
      echo "    actual:   $actual"
    fi
  done < <(find raw -type f -name '*.md')
fi
echo

# ---------- 8. Log size ----------
echo "--- [8] log rotation ---"
if [[ -f log.md ]]; then
  entries=$(grep -cE '^## \[' log.md)
  echo "  log.md entries: $entries"
  if (( entries > 500 )); then
    echo "  ROTATE_NEEDED: log.md has $entries entries (>500)"
  fi
fi
echo

# ---------- 9. Contested / low-confidence ----------
echo "--- [9] contested / low-confidence pages ---"
for f in "${PAGES[@]}"; do
  fm=$(awk '/^---$/{c++; if(c==2) exit; next} c==1' "$f")
  if grep -qE '^contested:\s*true' <<<"$fm"; then
    echo "  CONTESTED: $f"
  fi
  if grep -qE '^confidence:\s*low' <<<"$fm"; then
    echo "  LOW_CONFIDENCE: $f"
  fi
done
echo

# ---------- 10. Tag taxonomy check ----------
echo "--- [10] tags not in SCHEMA taxonomy ---"
if [[ -f SCHEMA.md ]]; then
  # crude: collect words under a "## Tag Taxonomy" section
  taxonomy=$(awk '/^## Tag Taxonomy/{flag=1; next} /^## /{flag=0} flag' SCHEMA.md \
             | grep -oE '[a-z][a-z0-9_-]+' | sort -u)
  for f in "${PAGES[@]}"; do
    tags_line=$(awk '/^---$/{c++; if(c==2) exit; next} c==1 && /^tags:/' "$f")
    # extract tags inside [ ... ]
    page_tags=$(grep -oE '\[[^]]*\]' <<<"$tags_line" | tr -d '[]' | tr ',' '\n' | awk '{$1=$1; print}')
    while IFS= read -r t; do
      [[ -z "$t" ]] && continue
      if ! grep -qxF "$t" <<<"$taxonomy"; then
        echo "  UNKNOWN_TAG[$t]: $f"
      fi
    done <<<"$page_tags"
  done
fi
echo

echo "=== lint done ==="
