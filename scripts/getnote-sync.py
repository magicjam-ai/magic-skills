#!/usr/bin/env python3
"""
Get笔记增量同步脚本 v4（支持图片附件）
- 增量策略：记录「上次同步到的最新笔记时间」，下次只同步该时间之后的笔记
- API 总是从 since_id=0 全量拉取（在本地过滤时间戳）
- img_text 类型笔记：下载图片附件到本地，嵌入 markdown
- 支持 --full 强制全量
- 支持 --dry-run 预览
"""

import json, urllib.request, urllib.error, re, os, sys, time, hashlib
import concurrent.futures
from datetime import datetime, timezone, timedelta

API_KEY = os.environ.get("GETNOTE_API_KEY", "")
CLIENT_ID = os.environ.get("GETNOTE_CLIENT_ID", "cli_a1b2c3d4e5f6789012345678abcdef90")
BASE_URL = "https://openapi.biji.com"
OUT_DIR = os.path.expanduser("~/obsidian/00_Inbox/Get笔记")
AUDIO_OUT_DIR = os.path.expanduser("~/obsidian/00_Inbox/音频")
ASSETS_DIR = os.path.join(OUT_DIR, "_assets", "Get笔记")
STATE_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".getnote_sync_state.json")
PROGRESS_FILE = "/tmp/openclaw/getnote-sync-progress.json"
# llm-wiki 编译后的源文件存放目录（已消费的笔记不重新下载）
WIKI_RAW_DIR = os.path.expanduser("~/obsidian/40_知识/wiki/raw/articles")

CST = timezone(timedelta(hours=8))

DRY_RUN = "--dry-run" in sys.argv
FULL_SYNC = "--full" in sys.argv
MAX_WORKERS = 4  # 并发下载图片

# 图片 URL 缓存（避免同一笔记重复下载）
_url_cache = {}

# 已消费笔记 ID 缓存（避免扫描目录多次）
_consumed_ids = None


def is_note_consumed(note_id_short):
    """检查笔记是否已被 llm-wiki 消费（移入 wiki/raw/articles/）"""
    global _consumed_ids
    if _consumed_ids is None:
        _consumed_ids = set()
        if os.path.isdir(WIKI_RAW_DIR):
            for f in os.listdir(WIKI_RAW_DIR):
                if f.endswith(".md"):
                    _consumed_ids.add(f)
    # 文件名格式：2026-04-27 Title (note_id[:8]).md → 检查 note_id 片段
    return any(note_id_short in f for f in _consumed_ids)


def log(msg):
    print(msg)
    sys.stdout.flush()


def write_progress(status, synced=0, total=0, error=""):
    os.makedirs(os.path.dirname(PROGRESS_FILE), exist_ok=True)
    with open(PROGRESS_FILE, "w") as f:
        json.dump({
            "status": status,
            "synced": synced,
            "total": total,
            "error": error,
            "updated_at": datetime.now(CST).isoformat(),
        }, f)


def load_state():
    if os.path.exists(STATE_FILE):
        with open(STATE_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return None


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
    safe = re.sub(
        r'"(id|note_id|next_cursor|parent_id|follow_id|live_id)"\s*:\s*(\d+)',
        r'"\1":"\2"',
        text
    )
    return json.loads(safe)


def fetch_all_notes():
    all_notes = []
    cursor = "0"
    while True:
        log(f"  [批次] 拉取中... cursor={cursor[:16]}...")
        try:
            data = api_get("/open/api/v1/resource/note/list", f"since_id={cursor}")
        except Exception as e:
            log(f"  ⚠️  拉取失败: {e}，2秒后重试...")
            time.sleep(2)
            continue
        d = data.get("data") or {}
        batch = d.get("notes", [])
        if not batch:
            break
        all_notes.extend(batch)
        log(f"  [批次] +{len(batch)} 条，累计 {len(all_notes)} 条...")
        if not d.get("has_more"):
            break
        cursor = d.get("next_cursor", "")
        if not cursor:
            break
    all_notes.sort(key=lambda n: n.get("created_at", ""))
    return all_notes


def fetch_detail_with_retry(note_id, retries=3, delay=5):
    for attempt in range(retries):
        try:
            data = api_get("/open/api/v1/resource/note/detail", f"id={note_id}", timeout=15)
            return (data.get("data") or {}).get("note", {})
        except urllib.error.HTTPError as e:
            if e.code == 429 and attempt < retries - 1:
                wait = delay * (attempt + 1)
                log(f"    429 限流，等待 {wait}s（{attempt+1}/{retries}）...")
                time.sleep(wait)
            else:
                raise
        except Exception as e:
            if attempt < retries - 1:
                log(f"    详情失败 {note_id[:16]}...: {e}，重试中...")
                time.sleep(2)
            else:
                raise


def download_image(url, note_id, idx, retries=3):
    """下载图片，返回本地保存路径（相对路径）"""
    global _url_cache
    cache_key = f"{note_id}:{idx}"
    if cache_key in _url_cache:
        return _url_cache[cache_key]

    os.makedirs(ASSETS_DIR, exist_ok=True)

    # 从 URL 提取扩展名
    if ".jpeg" in url or ".jpg" in url:
        ext = ".jpg"
    elif ".png" in url:
        ext = ".png"
    elif ".gif" in url:
        ext = ".gif"
    elif ".webp" in url:
        ext = ".webp"
    else:
        ext = ".jpg"

    local_filename = f"{note_id[:12]}_{idx}{ext}"
    local_path = os.path.join(ASSETS_DIR, local_filename)
    rel_path = os.path.join("_assets", "Get笔记", local_filename)

    if os.path.exists(local_path):
        _url_cache[cache_key] = rel_path
        return rel_path

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
            log(f"    📷 下载图片: {local_filename} ({len(data)//1024}KB)")
            return rel_path
        except Exception as e:
            if attempt < retries - 1:
                time.sleep(2)
            else:
                log(f"    ⚠️  图片下载失败: {e}")
                return None
    return None


def safe_filename(title, max_len=40):
    # Obsidian 禁用字符: \ / : * ? " < > | # ^ [ ]
    return (title[:max_len]
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
        .replace("]", "］"))


def parse_ts(ts_str):
    if not ts_str:
        return None
    for fmt in ("%Y-%m-%dT%H:%M:%S%z", "%Y-%m-%dT%H:%M:%S",
                "%Y-%m-%d %H:%M:%S%z", "%Y-%m-%d %H:%M:%S"):
        try:
            return datetime.strptime(ts_str[:26], fmt).timestamp()
        except Exception:
            pass
    try:
        clean = ts_str[:19]
        return datetime.strptime(clean, "%Y-%m-%dT%H:%M:%S").timestamp()
    except Exception:
        return None


def extract_image_urls(text):
    """从 markdown 文本中提取所有图片 URL"""
    if not text:
        return []
    import re
    # 匹配 ![...](url) 和 <img src="url">
    urls = re.findall(r'!\[.*?\]\((.*?)\)', text)
    urls.extend(re.findall(r'<img[^>]+src=["\'](.*?)["\']', text))
    return urls


def replace_images_in_text(text, url_to_local_path):
    """将文本中的图片 URL 替换为本地路径"""
    import re
    # 替换 ![...](url) — 2个捕获组: (prefix+url) 和 (url)
    def replacer_md(m):
        full = m.group(0)        # 完整匹配 ![...](url)
        url = m.group(1)        # URL
        local = url_to_local_path.get(url, url)
        # 重新构建: ![](local)
        return f'![]({local})'
    text = re.sub(r'!\[.*?\]\((.*?)\)', replacer_md, text)

    # 替换 <img src="url"> — 1个捕获组: (url)
    def replacer_img(m):
        prefix = m.group(0)[:m.start(1)-m.start(0)]  # <img ... src="
        url = m.group(1)
        suffix = m.group(0)[m.end(1)-m.start(0):]    # "
        local = url_to_local_path.get(url, url)
        return f'{prefix}{local}{suffix}'
    text = re.sub(r'<img[^>]+src=["\'](.*?)["\']', replacer_img, text)
    return text


def download_and_replace_images(text, note_id):
    """下载文本中的所有图片，返回替换后的文本和图片路径列表"""
    urls = extract_image_urls(text)
    if not urls:
        return text, []

    url_to_path = {}
    paths = []
    for idx, url in enumerate(urls):
        path = download_image(url, note_id, idx)
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
    note_id = note.get("note_id", note.get("id", ""))
    web_page = note.get("web_page", {})
    web_url = web_page.get("url", "")
    ref_content = note.get("ref_content", "") or ""

    # 处理 tags：可能是字符串列表，也可能是 dict 列表（Get笔记 API 返回的是 dict 列表）
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

    lines = [f"---", f'title: "{title}"', f"date: {created}",
             f"source: Get笔记", f"source_type: {note_type}", f'note_id: "{note_id}"']
    if tag_str:
        lines.append(f"tags: {tag_str}")
    if web_url:
        lines.append(f"url: {web_url}")
    lines.append(f"---")
    lines.append(f"# {title}")
    lines.append("")

    # 链接笔记：优先使用 web_page.content（原文），而非 AI 总结（content）
    # AI 总结放在正文前
    if note_type == "link" and web_page.get("content"):
        body_content = web_page["content"]
        if content and content.strip():
            ai_summary_block = f"{content.strip()}\n\n---\n\n"
        else:
            ai_summary_block = ""
    elif ref_content:
        body_content = ref_content
        ai_summary_block = ""
    else:
        body_content = content
        ai_summary_block = ""

    if body_content:
        lines.append(ai_summary_block + body_content)

    # 嵌入图片（link 类型已在 body_content 中通过 download_and_replace_images 处理过，无需重复追加）
    if image_paths and note_type != "link":
        for p in image_paths:
            if p:
                lines.append(f"")
                lines.append(f"![]({p})")

    if web_url and note_type == "link":
        lines.append(f"\n🔗 [查看原文]({web_url})")

    return "\n".join(lines)


def write_note(note, out_dir):
    note_id = note.get("note_id", note.get("id", ""))
    created = note.get("created_at", "")[:10]

    # 检查笔记是否已被 llm-wiki 消费（移入 wiki/raw/articles/）
    if note_id and is_note_consumed(note_id[:8]):
        return None, f"⏭️  跳过（已编入 wiki/raw/articles/）：{note.get('title', '')[:30]}"

    # 音频笔记转写未完成检查
    if note.get("note_type") == "recorder_audio":
        content = note.get("content", "") or ""
        if len(content.strip()) < 50:
            title = note.get("title", "无标题")
            return None, f"⏳ 跳过（转写生成中）：{title}"

    # 处理图片附件（img_text 类型）
    image_paths = []
    if note.get("note_type") == "img_text" and note.get("attachments"):
        attachments = note.get("attachments", [])
        for idx, att in enumerate(attachments):
            if att.get("type") == "image" and att.get("url"):
                p = download_image(att["url"], note_id, idx)
                if p:
                    image_paths.append(p)

    # 链接笔记：下载 web_page.content 中的图片并替换 URL
    if note.get("note_type") == "link":
        web_page = note.get("web_page", {})
        if web_page.get("content"):
            content_with_local_images, downloaded_paths = download_and_replace_images(
                web_page["content"], note_id
            )
            if downloaded_paths:
                image_paths.extend(downloaded_paths)
                # 将替换后的内容写回 note，避免 note_to_md 再次从 web_page.content 拿 URL
                note = dict(note)
                note["web_page"] = dict(web_page)
                note["web_page"]["content"] = content_with_local_images

    try:
        md = note_to_md(note, image_paths)
        title = safe_filename(note.get("title", "无标题"))
        filename = f"{created} {title} ({note_id[:8]}).md"
        filepath = os.path.join(out_dir, filename)
        with open(filepath, "w", encoding="utf-8") as f:
            f.write(md)
        ts = parse_ts(note.get("created_at", ""))
        if ts:
            os.utime(filepath, (ts, ts))
        return True, filename
    except Exception as e:
        return False, str(e)


def main():
    global _url_cache, _consumed_ids
    _consumed_ids = None  # 重置已消费笔记缓存
    os.makedirs(OUT_DIR, exist_ok=True)
    os.makedirs(AUDIO_OUT_DIR, exist_ok=True)
    os.makedirs(ASSETS_DIR, exist_ok=True)
    write_progress("running", synced=0, total=0)

    state = load_state()
    skipped_ids = set()
    if state and state.get("skipped_notes"):
        skipped_ids = set(state["skipped_notes"])

    # 记录本次同步开始时间，用于 cutoff（只在有笔记同步成功时才用）
    sync_start_time = datetime.now(CST).isoformat()

    if FULL_SYNC:
        cutoff = None
        log("🔄 全量同步模式（--full）")
    elif state and state.get("last_synced_at"):
        cutoff = state["last_synced_at"]
        total_prev = state.get("total_synced", 0)
        log(f"📡 增量同步模式，cutoff={cutoff}（上次共同步 {total_prev} 条）")
        if skipped_ids:
            log(f"🔄 待重试的跳过笔记：{len(skipped_ids)} 条")
    else:
        cutoff = None
        log("📡 首次同步，自动全量同步")

    log("\n开始拉取所有笔记（since_id=0，全量）...")
    write_progress("fetching_list", synced=0, total=0)
    all_notes = fetch_all_notes()
    log(f"📋 API 返回共 {len(all_notes)} 条笔记")

    if cutoff:
        # 过滤出 cutoff 之后新建的笔记
        filtered = [n for n in all_notes if n.get("created_at", "") > cutoff[:19]]
        # 同时无条件加入之前跳过的笔记（转写可能已完成，需要重试）
        # 注意：这些笔记的 created_at 可能早于 cutoff，必须强制加入，否则永远漏掉
        if skipped_ids:
            retry_notes = [n for n in all_notes if n.get("note_id", n.get("id", "")) in skipped_ids]
            existing_ids = {n.get("note_id", n.get("id", "")) for n in filtered}
            for n in retry_notes:
                nid = n.get("note_id", n.get("id", ""))
                if nid not in existing_ids:
                    filtered.append(n)
                    existing_ids.add(nid)

        # 音频笔记特殊处理：即使不在 skipped_ids 里，也检查转写状态
        # 原因：语音录音可能从设备延迟同步，上次同步时 API 还没有这条笔记
        # 策略：如果本地没有对应文件，强制拉详情检查转写是否完成
        audio_notes_in_api = [n for n in all_notes if n.get("note_type") == "recorder_audio"]
        existing_note_ids = {n.get("note_id", n.get("id", "")) for n in filtered}
        for n in audio_notes_in_api:
            nid = n.get("note_id", n.get("id", ""))
            if nid not in existing_note_ids:
                # 检查本地是否已有这条笔记
                created = n.get("created_at", "")[:10]
                title = n.get("title", "无标题")[:40]
                filename = f"{created} {title} ({nid[:8]}).md"
                if not os.path.exists(os.path.join(OUT_DIR, filename)) and not os.path.exists(os.path.join(AUDIO_OUT_DIR, filename)):
                    filtered.append(n)
                    existing_note_ids.add(nid)
        log(f"🔍 本地过滤（>{cutoff[:19]}）：{len(filtered)} 条需要同步" + (f"（含 {len(skipped_ids)} 条重试）" if skipped_ids else ""))
    else:
        filtered = all_notes
        log(f"🔍 全量模式：{len(filtered)} 条全部同步")

    if not filtered:
        log("没有新笔记需要同步 ✅")
        write_progress("done", synced=0, total=0)
        if not DRY_RUN:
            # 无新笔记时保留原 cutoff，不更新
            if state:
                save_state(state)
        return

    total = len(filtered)
    synced_ok = 0
    consumed_this_run = 0  # 本轮因已编入 wiki 而跳过的笔记数
    # 追踪本次成功同步的笔记中最新那条的 created_at
    new_max_created = None

    log(f"\n开始获取详情（并发数={MAX_WORKERS}）...")
    write_progress("fetching_details", synced=0, total=total)

    executor = concurrent.futures.ThreadPoolExecutor(max_workers=MAX_WORKERS)
    futures = {}
    for i, note in enumerate(filtered):
        note_id = note.get("note_id", note.get("id", ""))
        log(f"  [{i+1}/{total}] 提交: {note_id[:16]}... {note.get('title','')[:25]}")
        future = executor.submit(fetch_detail_with_retry, note_id)
        futures[future] = (i, note)

    completed = 0
    for future in concurrent.futures.as_completed(futures):
        i, note = futures[future]
        completed += 1
        try:
            detail = future.result()
            if detail:
                note = detail
            else:
                log(f"  [{completed}/{total}] ⚠️  详情为空")
        except Exception as e:
            log(f"  [{completed}/{total}] ⚠️  详情失败: {e}")

        ok, result = write_note(note, AUDIO_OUT_DIR if note.get("note_type") in ("recorder_audio", "audio") else OUT_DIR)
        if ok is None:
            # 区分：已编入 wiki 跳过 vs 转写未完成跳过
            nid = note.get("note_id", note.get("id", ""))
            if "已编入 wiki" in result:
                consumed_this_run += 1
                log(f"  [{completed}/{total}] {result}")
            else:
                skipped_ids.discard(nid)  # 会被 still_skipped 重新收集
                log(f"  [{completed}/{total}] {result}")
        elif ok:
            synced_ok += 1
            note_created = note.get("created_at", "")
            if note_created:
                if new_max_created is None or note_created > new_max_created:
                    new_max_created = note_created
        else:
            log(f"  [{completed}/{total}] ⚠️  写入失败: {result}")

        write_progress("writing", synced=synced_ok, total=total)
        if completed % 5 == 0 or completed == total:
            log(f"  进度 {completed}/{total}，成功 {synced_ok} 条...")

    executor.shutdown(wait=True)

    # 收集本轮仍被跳过的笔记 ID
    still_skipped = []
    for note in filtered:
        nid = note.get("note_id", note.get("id", ""))
        content = note.get("content", "") or ""
        if note.get("note_type") == "recorder_audio" and len(content.strip()) < 50:
            still_skipped.append(nid)

    if not DRY_RUN:
        # 有新笔记同步成功时：用成功笔记里最新那条的 created_at 作为下次 cutoff
        # 原因：created_at 是服务器时间，音频笔记的 created_at 可能是录音开始时间（远早于同步时间）
        # 用它做 cutoff 保证了"这次同步结束时 API 里最新笔记之后"的笔记下次都会被拉进来
        # 无新笔记时：保留原 cutoff，下次再试（可能还在延迟同步中）
        if synced_ok > 0 and new_max_created:
            new_last_synced = new_max_created
        else:
            new_last_synced = state.get("last_synced_at") if state else None
        new_state = {
            "last_synced_at": new_last_synced,
            "total_synced": (state.get("total_synced", 0) if state else 0) + synced_ok,
            "skipped_notes": still_skipped,
            "consumed_notes": (state.get("consumed_notes", []) if state else []),
            "consumed_total": (state.get("consumed_total", 0) if state else 0) + consumed_this_run,
        }
        # 追加本轮被消费的笔记 ID
        if consumed_this_run > 0:
            for note in filtered:
                nid = note.get("note_id", note.get("id", ""))
                if nid and is_note_consumed(nid[:8]):
                    if nid not in new_state["consumed_notes"]:
                        new_state["consumed_notes"].append(nid)
        save_state(new_state)
        consumed_total = new_state.get("consumed_total", 0)
        log(f"\n✅ 同步完成：{synced_ok} 条笔记" + (f"，{consumed_this_run} 条已编入 wiki 跳过" if consumed_this_run > 0 else ""))
        log(f"状态已更新：last_synced_at={new_last_synced}，累计 {new_state['total_synced']} 条，已消费 {consumed_total} 条")
    else:
        log(f"\n[dry-run] 本次模拟同步 {synced_ok} 条")

    write_progress("done", synced=synced_ok, total=total)


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        write_progress("error", error=str(e))
        raise
