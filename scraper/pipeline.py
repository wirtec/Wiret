"""Orchestrates: feed -> resolve -> full article -> news.json"""
from __future__ import annotations

import logging
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any, Dict, List, Optional

from . import extractor, resolver, rss, storage
from .config import Settings, settings as default_settings
from .rss import FeedItem, utc_now_iso

log = logging.getLogger(__name__)


def _build_record(item: FeedItem, conf: Settings) -> Dict[str, Any]:
    """Resolve a feed item and scrape its full article."""
    time.sleep(conf.delay)

    record: Dict[str, Any] = {
        "id": item.id,
        # --- from RSS ---
        "title": item.title,
        "header": item.header,
        "published_at": item.published_at,
        "published_raw": item.published_raw,
        "google_news_url": item.google_news_url,
        "description": item.description_text,
        "description_html": item.description_html,
        "description_links": item.description_links,
        "scraped_at": utc_now_iso(),
    }

    target = resolver.resolve(item.google_news_url)
    if not target:
        record.update({"url": None, "status": "unresolved", "error": "cannot_resolve_google_url"})
        return record
    record["url"] = target

    article = extractor.fetch_article(target)
    record.update(
        {
            "final_url": article.final_url,
            "source": article.site or item.header,
            "article_title": article.title,
            "heading": article.heading or item.title,
            "authors": article.authors,
            "article_published_at": article.published_at,
            "article_modified_at": article.modified_at,
            "summary": article.summary or item.description_text,
            "content": article.text,
            "content_html": article.html,
            "word_count": article.word_count,
            "char_count": article.char_count,
            "lead_image": article.lead_image,
            "images": [img.__dict__ for img in article.images],
            "image_count": len(article.images),
            "extractor": article.extractor,
        }
    )
    if article.error:
        record["status"] = "partial"
        record["error"] = article.error
    else:
        record["status"] = "ok"
        record.pop("error", None)
    return record


def run(conf: Optional[Settings] = None) -> Dict[str, Any]:
    """Execute one full scrape cycle. Returns a small run summary."""
    conf = conf or default_settings
    started = time.time()

    items = rss.fetch_feed(conf.feed_url)
    if not items:
        log.error("no feed items - aborting")
        return {"ok": False, "reason": "empty_feed", "articles": 0}

    existing, _meta = storage.load(conf.output_file)
    known = {
        str(record.get("google_news_url") or record.get("url") or record.get("id"))
        for record in existing
        if record.get("status") == "ok"
    }

    todo: List[FeedItem] = []
    for item in items:
        if not conf.refresh_existing and item.google_news_url in known:
            continue
        todo.append(item)

    log.info("%s feed items, %s to scrape (%s skipped)", len(items), len(todo), len(items) - len(todo))

    fresh: List[Dict[str, Any]] = []
    if todo:
        with ThreadPoolExecutor(max_workers=max(1, conf.workers)) as pool:
            futures = {pool.submit(_build_record, item, conf): item for item in todo}
            for future in as_completed(futures):
                item = futures[future]
                try:
                    record = future.result()
                except Exception as exc:  # keep the run alive
                    log.exception("scrape crashed for %s", item.title[:70])
                    record = {
                        "id": item.id,
                        "title": item.title,
                        "header": item.header,
                        "published_at": item.published_at,
                        "google_news_url": item.google_news_url,
                        "description": item.description_text,
                        "status": "error",
                        "error": f"{type(exc).__name__}: {exc}",
                        "scraped_at": utc_now_iso(),
                    }
                fresh.append(record)
                log.info(
                    "[%s] %s (%s words) - %s",
                    record.get("status"),
                    (record.get("title") or "")[:70],
                    record.get("word_count", 0),
                    record.get("source") or record.get("header") or "",
                )

    merged, added, updated = storage.merge(existing, fresh, max_stored=conf.max_stored)
    ok_count = sum(1 for record in merged if record.get("status") == "ok")

    payload = {
        "feed_url": conf.feed_url,
        "generated_at": utc_now_iso(),
        "count": len(merged),
        "stats": {
            "feed_items": len(items),
            "scraped_now": len(fresh),
            "added": added,
            "updated": updated,
            "ok": ok_count,
            "failed": len(merged) - ok_count,
            "duration_seconds": round(time.time() - started, 2),
        },
        "articles": merged,
    }
    storage.save(conf.output_file, payload)

    log.info(
        "done: %s articles in %s (+%s new, %s updated) in %.1fs",
        len(merged), conf.output_file, added, updated, time.time() - started,
    )
    return {"ok": True, "articles": len(merged), "added": added, "updated": updated}
