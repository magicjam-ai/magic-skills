#!/usr/bin/env python3
"""
Get笔记增量同步脚本 v5（真正增量 + 去重 + 安全 dry-run）

默认策略：
- 启动时扫描 Obsidian vault 中已有的 Get笔记 note_id，避免已同步/已移动/已编入 wiki 的笔记再次落入 Inbox。
- 增量同步只从 note/list 的最新页（since_id=0）开始向旧页翻，遇到上次同步水位后停止；不再每次拉完整列表。
- 状态文件落后于 vault 时，自动用 vault 中已有笔记的最大 date 修复 last_synced_at。
- --full 仅在显式指定时全量扫描列表，但仍按 note_id 跳过已有笔记，不覆盖本地文件。
- --dry-run 不写文件、不下载图片、不更新状态。
"""

import concurrent.futures
import glob
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone, timedelta


def load_getnote_local_credentials():
    """Load credentials from the Get笔记 skill local env file when shell env is unset."""
    candidates = [
        os.path.expanduser("~/.agents/skills/getnote/.local/credentials.env"),
        os.path.expanduser("~/.codex/skills/getnote/.local/credentials.env"),
        os.path.expanduser("~/.Codex/skills/getnote/.local/credentials.env"),
    ]
    for path in candidates:
        if not os.path.exists(path):
            continue
        with open(path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, value = line.split("=", 1)
                key = key.strip()
                value = value.strip().strip('"').strip("'")
                if key.startswith("GETNOTE_") and key not in os.environ:
                    os.environ[key] = value
        break


load_getnote_local_credentials()

API_KEY = os.environ.get("GETNOTE_API_KEY", "")
CLIENT_ID = os.environ.get("GETNOTE_CLIENT_ID", "cli_a1b2c3d4e5f6789012345678abcdef90")
BASE_URL = "https://openapi.biji.com"

VAULT_DIR = os.path.expanduser("~/obsidian")
OUT_DIR = os.path.join(VAULT_DIR, "00_Inbox", "Get笔记")
AUDIO_OUT_DIR = os.path.join(VAULT_DIR, "00_Inbox", "音频")
ATTACHMENTS_DIR_NAME = "assets"
STATE_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".getnote_sync_state.json")
PROGRESS_FILE = "/tmp/openclaw/getnote-sync-progress.json"

# 录音/音频类笔记统一进入 00_Inbox/音频/
AUDIO_NOTE_TYPES = {
    "audio",
    "meeting",
    "local_audio",
    "internal_record",
    "class_audio",
    "recorder_audio",
    "recorder_flash_audio",
}

# 标准 vault 顶级目录；扫描这些目录里的 frontmatter note_id，避免已移动笔记被重复导入。
VAULT_SCAN_TOP_DIRS = [
    "00_Inbox",
    "10_思考",
    "20_研究",
    "30_项目",
    "50_个人",
    "60_wiki",
    "70_写作",
    "80_高考",
    "99_归档",
]

# 明确识别 wiki raw/articles，兼容旧路径，避免旧脚本路径写错后反复重拉已消费内容。
LEGACY_WIKI_RAW_DIRS = [
    os.path.join(VAULT_DIR, "60_wiki", "wiki", "raw", "articles"),
    os.path.join(VAULT_DIR, "40_知识", "wiki", "raw", "articles"),
]

CST = timezone(timedelta(hours=8))

DRY_RUN = "--dry-run" in sys.argv
FULL_SYNC = "--full" in sys.argv
MAX_WORKERS = 4

# 图片 URL 缓存（避免同一笔记重复下载）
_url_cache = {}
_last_attachment_ms = 0


def log(msg):
    print(msg)
    sys.stdout.flush()


def now_iso():
    return datetime.now(CST).isoformat()


def write_progress(status, synced=0, total=0, error=""):
    os.makedirs(os.path.dirname(PROGRESS_FILE), exist_ok=True)
    with open(PROGRESS_FILE, "w", encoding="utf-8") as f:
        json.dump({
            "status": status,
            "synced": synced,
            "total": total,
            "error": error,
            "updated_at": now_iso(),
        }, f, ensure_ascii=False)


def load_state():
    if os.path.exists(STATE_FILE):
        with open(STATE_FILE, "r", encoding="utf-8") as f:
            try:
                return json.load(f)
            except json.JSONDecodeError:
                return {}
    return {}


def save_state(state):
    tmp = STATE_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)
    os.replace(tmp, STATE_FILE)


def api_get(path, params="", timeout=30):
    url = f"{BASE_URL}{path}?{params}" if params else f"{BASE_URL}{path}"
    req = urllib.request.Request(url, headers={
        "Authorization": API_KEY,
        "X-Client-ID": CLIENT_ID,
    })
    resp = urllib.request.urlopen(req, timeout=timeout)
    text = resp.read().decode()
    # Get笔记 ID 是 int64，统一在 JSON decode 前字符串化，避免精度问题。
    safe = re.sub(
        r'"(id|note_id|next_cursor|parent_id|follow_id|live_id)"\s*:\s*(\d+)',
        r'"\1":"\2"',
        text,
    )
    return json.loads(safe)


def fetch_note_list_page(cursor):
    data = api_get("/open/api/v1/resource/note/list", f"since_id={cursor}")
    d = data.get("data") or {}
    return d.get("notes", []) or [], d


def fetch_detail_with_retry(note_id, retries=3, delay=5):
    for attempt in range(retries):
        try:
            data = api_get("/open/api/v1/resource/note/detail", f"id={note_id}", timeout=15)
            return (data.get("data") or {}).get("note", {})
        except urllib.error.HTTPError as e:
            if e.code in (429, 10202) and attempt < retries - 1:
                wait = delay * (attempt + 1)
                log(f"    429/限流，等待 {wait}s（{attempt+1}/{retries}）...")
                time.sleep(wait)
            else:
                raise
        except Exception as e:
            if attempt < retries - 1:
                log(f"    详情失败 {str(note_id)[:16]}...: {e}，重试中...")
                time.sleep(2)
            else:
                raise


def parse_ts(ts_str):
    if not ts_str:
        return None
    s = str(ts_str).strip()
    # 兼容 2026-05-11 22:39:59、2026-05-11T22:39:59+08:00 等格式。
    variants = [s, s.replace("Z", "+00:00")]
    if "T" in s and len(s) >= 19:
        variants.append(s[:19])
    if " " in s and len(s) >= 19:
        variants.append(s[:19])
    for item in variants:
        try:
            return datetime.fromisoformat(item).timestamp()
        except Exception:
            pass
    for fmt in ("%Y-%m-%d %H:%M:%S%z", "%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S%z", "%Y-%m-%dT%H:%M:%S"):
        try:
            return datetime.strptime(s[:19] if "%z" not in fmt else s, fmt).timestamp()
        except Exception:
            pass
    return None


def ts_after(a, b):
    if not b:
        return bool(a)
    av = parse_ts(a)
    bv = parse_ts(b)
    if av is None or bv is None:
        return str(a or "") > str(b or "")
    return av > bv


def ts_equal(a, b):
    if not a or not b:
        return False
    av = parse_ts(a)
    bv = parse_ts(b)
    if av is None or bv is None:
        return str(a or "")[:19] == str(b or "")[:19]
    return abs(av - bv) < 0.001


def max_ts(a, b):
    if not a:
        return b
    if not b:
        return a
    return a if ts_after(a, b) else b


def yaml_scalar(value):
    value = str(value).strip()
    if (value.startswith('"') and value.endswith('"')) or (value.startswith("'") and value.endswith("'")):
        return value[1:-1]
    return value


def read_frontmatter(path):
    try:
        with open(path, "r", encoding="utf-8") as f:
            head = f.read(8192)
    except Exception:
        return {}
    if not head.startswith("---"):
        return {}
    end = head.find("\n---", 3)
    if end == -1:
        return {}
    block = head[3:end]
    fm = {}
    for line in block.splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        if ":" not in line:
            continue
        key, value = line.split(":", 1)
        fm[key.strip()] = yaml_scalar(value)
    return fm


def is_under(path, root):
    try:
        return os.path.commonpath([os.path.abspath(path), os.path.abspath(root)]) == os.path.abspath(root)
    except Exception:
        return False


def wiki_raw_dirs():
    dirs = set(LEGACY_WIKI_RAW_DIRS)
    for pattern in (
        os.path.join(VAULT_DIR, "60_wiki", "*", "raw", "articles"),
        os.path.join(VAULT_DIR, "60_wiki", "*", "raw"),
    ):
        dirs.update(glob.glob(pattern))
    return [d for d in sorted(dirs) if os.path.isdir(d)]


def is_consumed_path(path):
    normalized = os.path.abspath(path)
    for d in wiki_raw_dirs():
        if is_under(normalized, d):
            return True
    return False


def iter_vault_markdown_files():
    skip_dir_names = {".obsidian", ".claude", ".claudian", ".git", "node_modules", "__pycache__", "_assets"}
    for top in VAULT_SCAN_TOP_DIRS:
        root = os.path.join(VAULT_DIR, top)
        if not os.path.isdir(root):
            continue
        for dirpath, dirnames, filenames in os.walk(root):
            dirnames[:] = [d for d in dirnames if d not in skip_dir_names and not d.startswith(".")]
            for name in filenames:
                if name.endswith(".md"):
                    yield os.path.join(dirpath, name)


def build_note_index():
    """扫描 vault 中已有 Get笔记 note_id，返回 note_id -> record。"""
    index = {}
    for path in iter_vault_markdown_files():
        fm = read_frontmatter(path)
        note_id = str(fm.get("note_id", "")).strip()
        if not note_id:
            continue
        # 优先识别 Get笔记；如果 source 缺失但位于 Get笔记/音频/wiki raw，也纳入防重。
        source = str(fm.get("source", "")).strip()
        looks_getnote = (
            source == "Get笔记"
            or is_under(path, OUT_DIR)
            or is_under(path, AUDIO_OUT_DIR)
            or is_consumed_path(path)
        )
        if not looks_getnote:
            continue
        rec = index.setdefault(note_id, {
            "paths": [],
            "active_paths": [],
            "consumed_paths": [],
            "created_at": "",
        })
        rec["paths"].append(path)
        if is_consumed_path(path):
            rec["consumed_paths"].append(path)
        else:
            rec["active_paths"].append(path)
        created = str(fm.get("date", "")).strip()
        if created:
            rec["created_at"] = max_ts(rec.get("created_at"), created)
    return index


def note_exists(note_id, note_index):
    return bool(note_id and str(note_id) in note_index and note_index[str(note_id)].get("paths"))


def note_is_consumed(note_id, note_index):
    return bool(note_id and str(note_id) in note_index and note_index[str(note_id)].get("consumed_paths"))


def local_max_created_at(note_index):
    latest = ""
    for rec in note_index.values():
        latest = max_ts(latest, rec.get("created_at", ""))
    return latest or None


def reconcile_state_with_vault(state, note_index):
    """如果状态水位落后于 vault 中已有笔记，自动抬高水位，避免重复同步。"""
    state = dict(state or {})
    local_latest = local_max_created_at(note_index)
    current = state.get("last_synced_at")
    changed = False
    if local_latest and (not current or ts_after(local_latest, current)):
        state["last_synced_at"] = local_latest
        state["reconciled_from_vault_at"] = now_iso()
        state["reconciled_from_vault_note_count"] = len(note_index)
        changed = True
    return state, changed, local_latest


def note_stem_from_path(note_filepath):
    return os.path.splitext(os.path.basename(note_filepath))[0]


def attachment_dir_for_note_path(note_filepath):
    """Custom Attachment Location: ./assets/${noteFileName}."""
    return os.path.join(
        os.path.dirname(note_filepath),
        ATTACHMENTS_DIR_NAME,
        note_stem_from_path(note_filepath),
    )


def attachment_rel_dir_for_note_path(note_filepath):
    """Relative markdown path from the note to its attachment directory."""
    return os.path.join(ATTACHMENTS_DIR_NAME, note_stem_from_path(note_filepath))


def next_attachment_basename(ext):
    """Custom Attachment Location filename: file-YYYYMMDDHHmmssSSS."""
    global _last_attachment_ms
    current_ms = time.time_ns() // 1_000_000
    if current_ms <= _last_attachment_ms:
        current_ms = _last_attachment_ms + 1
    _last_attachment_ms = current_ms
    dt = datetime.fromtimestamp(current_ms / 1000, CST)
    return f"file-{dt.strftime('%Y%m%d%H%M%S')}{current_ms % 1000:03d}{ext}"


def image_ext_from_url(url):
    lower = url.lower()
    if ".jpeg" in lower or ".jpg" in lower:
        return ".jpg"
    if ".png" in lower:
        return ".png"
    if ".gif" in lower:
        return ".gif"
    if ".webp" in lower:
        return ".webp"
    return ".jpg"


def download_image(url, note_filepath, retries=3):
    """下载图片，返回相对当前笔记的本地路径：assets/${noteFileName}/file-*.ext。"""
    global _url_cache
    cache_key = f"{note_filepath}:{url}"
    if cache_key in _url_cache:
        return _url_cache[cache_key]

    attachment_dir = attachment_dir_for_note_path(note_filepath)
    rel_dir = attachment_rel_dir_for_note_path(note_filepath)
    os.makedirs(attachment_dir, exist_ok=True)

    ext = image_ext_from_url(url)
    local_filename = next_attachment_basename(ext)
    local_path = os.path.join(attachment_dir, local_filename)
    while os.path.exists(local_path):
        local_filename = next_attachment_basename(ext)
        local_path = os.path.join(attachment_dir, local_filename)
    rel_path = os.path.join(rel_dir, local_filename)

    for attempt in range(retries):
        try:
            req = urllib.request.Request(url, headers={
                "Authorization": API_KEY,
                "X-Client-ID": CLIENT_ID,
            })
            with urllib.request.urlopen(req, timeout=30) as resp:
                data = resp.read()
            with open(local_path, "wb") as f:
                f.write(data)
            _url_cache[cache_key] = rel_path
            log(f"    📷 下载图片: {rel_path} ({len(data)//1024}KB)")
            return rel_path
        except Exception as e:
            if attempt < retries - 1:
                time.sleep(2)
            else:
                log(f"    ⚠️  图片下载失败: {e}")
                return None
    return None


def safe_filename(title, max_len=48):
    # Obsidian 禁用字符: \ / : * ? " < > | # ^ [ ]
    return (str(title or "无标题")[:max_len]
        .replace("\\", "＼")
        .replace("/", "／")
        .replace(":", "：")
        .replace("*", "＊")
        .replace("?", "？")
        .replace("\"", "＂")
        .replace("<", "＜")
        .replace(">", "＞")
        .replace("|", "｜")
        .replace("#", "＃")
        .replace("^", "＾")
        .replace("[", "［")
        .replace("]", "］")
        .strip()) or "无标题"


def filename_for_note(note):
    note_id = str(note.get("note_id", note.get("id", ""))).strip()
    created = str(note.get("created_at", ""))[:10] or datetime.now(CST).strftime("%Y-%m-%d")
    title = safe_filename(note.get("title", "无标题"))
    # v5 起使用完整 note_id，避免旧版前 8 位在同一天多条笔记中大量碰撞。
    suffix = note_id if note_id else hashlib_fallback(f"{created}-{title}")
    return f"{created} {title} ({suffix}).md"


def hashlib_fallback(text):
    import hashlib
    return hashlib.sha1(text.encode("utf-8")).hexdigest()[:12]


def extract_image_urls(text):
    """从 markdown/html 文本中提取所有图片 URL。"""
    if not text:
        return []
    urls = re.findall(r'!\[.*?\]\((.*?)\)', text)
    urls.extend(re.findall(r'<img[^>]+src=["\'](.*?)["\']', text))
    # 保持顺序去重
    seen = set()
    result = []
    for u in urls:
        if u not in seen:
            seen.add(u)
            result.append(u)
    return result


def replace_images_in_text(text, url_to_local_path):
    """将文本中的图片 URL 替换为本地路径。"""
    def replacer_md(m):
        url = m.group(1)
        local = url_to_local_path.get(url, url)
        if local != url:
            return f'![](<{local}>)'
        return f'![]({local})'
    text = re.sub(r'!\[.*?\]\((.*?)\)', replacer_md, text)

    def replacer_img(m):
        full = m.group(0)
        url = m.group(1)
        local = url_to_local_path.get(url, url)
        return full.replace(url, local, 1)
    text = re.sub(r'<img[^>]+src=["\'](.*?)["\']', replacer_img, text)
    return text


def download_and_replace_images(text, note_filepath):
    """下载文本中的所有图片，返回替换后的文本和图片路径列表。"""
    urls = extract_image_urls(text)
    if not urls:
        return text, []

    url_to_path = {}
    paths = []
    for url in urls:
        path = download_image(url, note_filepath)
        if path:
            url_to_path[url] = path
            paths.append(path)

    if url_to_path:
        text = replace_images_in_text(text, url_to_path)
    return text, paths


def note_to_md(note, image_paths=None):
    title = note.get("title", "无标题")
    note_type = note.get("note_type", "")
    created = note.get("created_at", "")
    tags = note.get("tags", [])
    content = note.get("content", "")
    note_id = str(note.get("note_id", note.get("id", ""))).strip()
    web_page = note.get("web_page", {}) or {}
    web_url = web_page.get("url", "")
    ref_content = note.get("ref_content", "") or ""

    if tags:
        processed = []
        for t in tags:
            if isinstance(t, dict):
                processed.append(t.get("name", ""))
            else:
                processed.append(str(t))
        tag_str = " ".join(f"#{name}" for name in processed if name)
    else:
        tag_str = ""

    # 注意：沿用旧 frontmatter 结构，避免破坏现有查询。
    safe_title = str(title).replace('"', '\\"')
    lines = [
        "---",
        f'title: "{safe_title}"',
        f"date: {created}",
        "source: Get笔记",
        f"source_type: {note_type}",
        f'note_id: "{note_id}"',
    ]
    if tag_str:
        lines.append(f"tags: {tag_str}")
    if web_url:
        lines.append(f"url: {web_url}")
    lines.append("---")
    lines.append(f"# {title}")
    lines.append("")

    # 链接笔记：优先使用 web_page.content（原文），AI 总结放在正文前。
    if note_type == "link" and web_page.get("content"):
        body_content = web_page["content"]
        if content and str(content).strip():
            ai_summary_block = f"{str(content).strip()}\n\n---\n\n"
        else:
            ai_summary_block = ""
    elif ref_content:
        body_content = ref_content
        ai_summary_block = ""
    else:
        body_content = content
        ai_summary_block = ""

    if body_content:
        lines.append(ai_summary_block + str(body_content))

    if image_paths and note_type != "link":
        for p in image_paths:
            if p:
                lines.append("")
                lines.append(f"![](<{p}>)")

    if web_url and note_type == "link":
        lines.append(f"\n🔗 [查看原文]({web_url})")

    return "\n".join(lines)


def out_dir_for_note(note):
    return AUDIO_OUT_DIR if note.get("note_type") in AUDIO_NOTE_TYPES else OUT_DIR


def write_note(note, out_dir, note_index):
    """写入单条笔记。

    返回 (status, message)：
    - written / dry_run
    - existing / consumed / pending
    - error
    """
    note_id = str(note.get("note_id", note.get("id", ""))).strip()
    title = note.get("title", "无标题")

    # 黑名单：用户明确排除的笔记
    excluded_ids = getattr(write_note, "_excluded_ids", set())
    if note_id and note_id in excluded_ids:
        return "excluded", f"⏭️  跳过（黑名单）：{title}"

    if note_id and note_is_consumed(note_id, note_index):
        path = note_index[note_id]["consumed_paths"][0]
        return "consumed", f"⏭️  跳过（已编入 wiki/raw/articles）：{os.path.relpath(path, VAULT_DIR)}"

    if note_id and note_exists(note_id, note_index):
        path = note_index[note_id]["paths"][0]
        return "existing", f"⏭️  跳过（vault 已存在同 note_id）：{os.path.relpath(path, VAULT_DIR)}"

    # 音频笔记转写未完成检查。
    if note.get("note_type") in AUDIO_NOTE_TYPES:
        content = note.get("content", "") or ""
        if len(str(content).strip()) < 50:
            return "pending", f"⏳ 跳过（转写生成中）：{title}"

    filename = filename_for_note(note)
    filepath = os.path.join(out_dir, filename)

    if DRY_RUN:
        return "dry_run", f"[dry-run] 将写入：{os.path.relpath(filepath, VAULT_DIR)}"

    # 二次防重：即使 index 漏扫，也不要覆盖同名文件。
    if os.path.exists(filepath):
        return "existing", f"⏭️  跳过（目标文件已存在）：{os.path.relpath(filepath, VAULT_DIR)}"

    image_paths = []
    try:
        # 处理图片附件（img_text 类型）。
        if note.get("note_type") == "img_text" and note.get("attachments"):
            attachments = note.get("attachments", []) or []
            for idx, att in enumerate(attachments):
                if att.get("type") == "image" and (att.get("original_url") or att.get("url")):
                    p = download_image(att.get("original_url") or att.get("url"), filepath)
                    if p:
                        image_paths.append(p)

        # 链接笔记：下载 web_page.content 中的图片并替换 URL。
        if note.get("note_type") == "link":
            web_page = note.get("web_page", {}) or {}
            if web_page.get("content"):
                content_with_local_images, downloaded_paths = download_and_replace_images(
                    web_page["content"], filepath
                )
                if downloaded_paths:
                    image_paths.extend(downloaded_paths)
                    note = dict(note)
                    note["web_page"] = dict(web_page)
                    note["web_page"]["content"] = content_with_local_images

        os.makedirs(out_dir, exist_ok=True)
        md = note_to_md(note, image_paths)
        with open(filepath, "w", encoding="utf-8") as f:
            f.write(md)
        ts = parse_ts(note.get("created_at", ""))
        if ts:
            os.utime(filepath, (ts, ts))
        # 更新本轮内存索引，防止同一轮重复写入同 note_id。
        if note_id:
            note_index[note_id] = {
                "paths": [filepath],
                "active_paths": [filepath],
                "consumed_paths": [],
                "created_at": note.get("created_at", ""),
            }
        return "written", filename
    except Exception as e:
        return "error", str(e)


def add_unique_note(result, seen_ids, note, reason):
    note_id = str(note.get("note_id", note.get("id", ""))).strip()
    if not note_id or note_id in seen_ids:
        return False
    item = dict(note)
    item["_sync_reason"] = reason
    result.append(item)
    seen_ids.add(note_id)
    return True


def fetch_notes_for_sync(cutoff, note_index, retry_ids=None, full=False):
    """获取需要同步的列表项。

    默认从 since_id=0 开始翻页，遇到 cutoff（水位）所在页后停止。
    retry_ids 会直接按详情接口重试，不强迫列表全量扫描。
    """
    retry_ids = {str(x) for x in (retry_ids or []) if x}
    result = []
    seen_ids = set()
    stats = {
        "pages_fetched": 0,
        "list_notes_seen": 0,
        "api_total": None,
        "stopped_at_cutoff": False,
        "retried_ids": 0,
    }

    # 旧的 skipped_notes 直接走详情接口；若 vault 已经存在，则清理掉，不再重试。
    for note_id in sorted(retry_ids):
        if note_exists(note_id, note_index):
            continue
        add_unique_note(result, seen_ids, {"note_id": note_id, "id": note_id, "_retry_only": True}, "retry")
        stats["retried_ids"] += 1

    cursor = "0"
    while True:
        log(f"  [列表] 拉取 cursor={str(cursor)[:16]}...")
        try:
            batch, data = fetch_note_list_page(cursor)
        except Exception as e:
            log(f"  ⚠️  列表拉取失败: {e}，2秒后重试...")
            time.sleep(2)
            batch, data = fetch_note_list_page(cursor)

        stats["pages_fetched"] += 1
        stats["list_notes_seen"] += len(batch)
        stats["api_total"] = data.get("total")
        log(f"  [列表] +{len(batch)} 条（累计检查 {stats['list_notes_seen']} / API total={stats['api_total']}）")

        if not batch:
            break

        reached_cutoff_on_page = False
        for note in batch:
            note_id = str(note.get("note_id", note.get("id", ""))).strip()
            created = note.get("created_at", "")

            if cutoff and not ts_after(created, cutoff):
                reached_cutoff_on_page = True

            if note_id and note_exists(note_id, note_index):
                continue

            # 对 cutoff 同秒但本地缺失的 note_id 也纳入处理，避免同一秒多条笔记被水位遗漏。
            if full or not cutoff or ts_after(created, cutoff) or ts_equal(created, cutoff):
                add_unique_note(result, seen_ids, note, "new" if not full else "full-missing")
                continue

            # 音频类笔记可能列表出现较早但转写稍后完成；只要它出现在本次增量检查窗口且本地没有，就重试详情。
            if note.get("note_type") in AUDIO_NOTE_TYPES:
                add_unique_note(result, seen_ids, note, "audio-delayed")

        if not data.get("has_more"):
            break
        if not full and cutoff and reached_cutoff_on_page:
            stats["stopped_at_cutoff"] = True
            break
        cursor = str(data.get("next_cursor") or "")
        if not cursor:
            break

    # 保持旧版写入顺序：旧到新。retry-only 没有 created_at，会排在前面。
    result.sort(key=lambda n: n.get("created_at", ""))
    return result, stats


def state_with_run_metadata(state, note_index, stats, synced_ok, still_skipped, new_max_created, sync_start_time):
    state = dict(state or {})
    previous_cutoff = state.get("last_synced_at")
    state["last_synced_at"] = max_ts(previous_cutoff, new_max_created) if new_max_created else previous_cutoff
    state["total_synced"] = int(state.get("total_synced", 0) or 0) + synced_ok
    state["skipped_notes"] = sorted(still_skipped)
    consumed = sorted([nid for nid, rec in note_index.items() if rec.get("consumed_paths")])
    state["consumed_notes"] = consumed
    state["consumed_total"] = len(consumed)
    state["known_note_ids_in_vault"] = len(note_index)
    state["last_run_at"] = sync_start_time
    state["last_run"] = stats
    return state


def main():
    global _url_cache
    _url_cache = {}

    if not API_KEY:
        raise RuntimeError("未配置 GETNOTE_API_KEY，请先配置 Get笔记 API Key。")

    os.makedirs(OUT_DIR, exist_ok=True)
    os.makedirs(AUDIO_OUT_DIR, exist_ok=True)
    write_progress("running", synced=0, total=0)

    sync_start_time = now_iso()
    original_state = load_state()
    note_index = build_note_index()
    state, reconciled, local_latest = reconcile_state_with_vault(original_state, note_index)
    skipped_ids = set(str(x) for x in (state.get("skipped_notes") or []) if x)
    excluded_ids = set(str(x) for x in (state.get("excluded_notes") or []) if x)
    cutoff = None if FULL_SYNC else state.get("last_synced_at")

    if FULL_SYNC:
        log("📡 显式全量扫描模式：会检查所有列表页，但仍按 note_id 跳过 vault 已存在笔记")
    elif cutoff:
        log(f"📡 增量同步模式，cutoff={cutoff}")
        if reconciled:
            log(f"🧭 状态水位已按 vault 自动修复：local_latest={local_latest}（已有 {len(note_index)} 个 Get笔记 note_id）")
        if skipped_ids:
            log(f"🔄 待重试的跳过笔记：{len(skipped_ids)} 条")
    else:
        log("📡 首次同步：未找到状态水位，将执行一次全量扫描")

    log("\n开始增量拉取笔记列表...")
    write_progress("fetching_list", synced=0, total=0)
    notes_to_sync, stats = fetch_notes_for_sync(cutoff, note_index, retry_ids=skipped_ids, full=FULL_SYNC or not cutoff)
    log(
        f"🔍 列表检查完成：拉取 {stats['pages_fetched']} 页 / 检查 {stats['list_notes_seen']} 条"
        + ("，已在 cutoff 页停止" if stats.get("stopped_at_cutoff") else "")
        + f"；需要处理 {len(notes_to_sync)} 条"
    )

    if not notes_to_sync:
        log("没有新笔记需要同步 ✅")
        write_progress("done", synced=0, total=0)
        if not DRY_RUN:
            # 即使没有新笔记，也保存自动修复后的状态水位，避免下次重复检查旧窗口。
            new_state = state_with_run_metadata(state, note_index, stats, 0, set(), None, sync_start_time)
            save_state(new_state)
            if reconciled:
                log(f"状态已修复并保存：last_synced_at={new_state.get('last_synced_at')}")
        return

    total = len(notes_to_sync)
    written_ok = 0
    dry_run_ok = 0
    skipped_existing = 0
    skipped_consumed = 0
    still_skipped = set()
    new_max_created = None

    # 注入黑名单到 write_note
    write_note._excluded_ids = excluded_ids

    log(f"\n开始获取详情（并发数={MAX_WORKERS}）...")
    write_progress("fetching_details", synced=0, total=total)

    with concurrent.futures.ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
        futures = {}
        for i, note in enumerate(notes_to_sync):
            note_id = str(note.get("note_id", note.get("id", ""))).strip()
            reason = note.get("_sync_reason", "")
            log(f"  [{i+1}/{total}] 提交详情: {note_id[:20]} {reason} {note.get('title','')[:25]}")
            futures[executor.submit(fetch_detail_with_retry, note_id)] = (i, note)

        completed = 0
        for future in concurrent.futures.as_completed(futures):
            i, list_note = futures[future]
            completed += 1
            note_id = str(list_note.get("note_id", list_note.get("id", ""))).strip()
            try:
                detail = future.result()
                note = detail or list_note
                if not detail:
                    log(f"  [{completed}/{total}] ⚠️  详情为空: {note_id}")
            except Exception as e:
                log(f"  [{completed}/{total}] ⚠️  详情失败: {note_id} {e}")
                if note_id:
                    still_skipped.add(note_id)
                write_progress("writing", synced=written_ok + dry_run_ok, total=total)
                continue

            status, message = write_note(note, out_dir_for_note(note), note_index)
            actual_note_id = str(note.get("note_id", note.get("id", note_id))).strip()

            if status == "written":
                written_ok += 1
                new_max_created = max_ts(new_max_created, note.get("created_at", ""))
                log(f"  [{completed}/{total}] ✅ {message}")
            elif status == "dry_run":
                dry_run_ok += 1
                new_max_created = max_ts(new_max_created, note.get("created_at", ""))
                log(f"  [{completed}/{total}] {message}")
            elif status == "existing":
                skipped_existing += 1
                log(f"  [{completed}/{total}] {message}")
            elif status == "excluded":
                log(f"  [{completed}/{total}] {message}")
            elif status == "consumed":
                skipped_consumed += 1
                log(f"  [{completed}/{total}] {message}")
            elif status == "pending":
                if actual_note_id:
                    still_skipped.add(actual_note_id)
                log(f"  [{completed}/{total}] {message}")
            else:
                if actual_note_id:
                    still_skipped.add(actual_note_id)
                log(f"  [{completed}/{total}] ⚠️  写入失败: {message}")

            write_progress("writing", synced=written_ok + dry_run_ok, total=total)
            if completed % 5 == 0 or completed == total:
                log(f"  进度 {completed}/{total}，写入 {written_ok} 条" + (f"，dry-run {dry_run_ok} 条" if DRY_RUN else ""))

    if not DRY_RUN:
        new_state = state_with_run_metadata(
            state,
            note_index,
            stats,
            written_ok,
            still_skipped,
            new_max_created,
            sync_start_time,
        )
        save_state(new_state)
        log(
            f"\n✅ 同步完成：写入 {written_ok} 条"
            + (f"，已存在跳过 {skipped_existing} 条" if skipped_existing else "")
            + (f"，已编入 wiki 跳过 {skipped_consumed} 条" if skipped_consumed else "")
            + (f"，待重试 {len(still_skipped)} 条" if still_skipped else "")
        )
        log(f"状态已更新：last_synced_at={new_state.get('last_synced_at')}，累计写入 {new_state.get('total_synced')} 条")
    else:
        log(
            f"\n[dry-run] 预览完成：将写入 {dry_run_ok} 条"
            + (f"，已存在会跳过 {skipped_existing} 条" if skipped_existing else "")
            + (f"，已编入 wiki 会跳过 {skipped_consumed} 条" if skipped_consumed else "")
            + (f"，待重试 {len(still_skipped)} 条" if still_skipped else "")
        )

    write_progress("done", synced=written_ok + dry_run_ok, total=total)


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        write_progress("error", error=str(e))
        raise
