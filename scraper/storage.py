"""Read/merge/write `news.json` atomically."""
from __future__ import annotations

import json
import logging
import os
import tempfile
from typing import Any, Dict, List, Tuple

log = logging.getLogger(__name__)


def load(path: str) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    """Return (articles, meta) from an existing news.json (empty when missing)."""
    if not os.path.exists(path):
        return [], {}
    try:
        with open(path, "r", encoding="utf-8") as handle:
            data = json.load(handle)
    except (json.JSONDecodeError, OSError) as exc:
        log.warning("cannot read %s (%s) - starting fresh", path, exc)
        return [], {}

    if isinstance(data, list):  # tolerate a bare list
        return [item for item in data if isinstance(item, dict)], {}
    if isinstance(data, dict):
        articles = data.get("articles") or data.get("items") or []
        meta = {k: v for k, v in data.items() if k not in ("articles", "items")}
        return [item for item in articles if isinstance(item, dict)], meta
    return [], {}


def _key(item: Dict[str, Any]) -> str:
    """De-duplication key: canonical article url, else google url, else id."""
    for field in ("url", "google_news_url", "id"):
        value = item.get(field)
        if isinstance(value, str) and value:
            return value.split("#")[0].rstrip("/")
    return json.dumps(item, sort_keys=True)[:120]


def _sort_key(item: Dict[str, Any]) -> str:
    return str(item.get("published_at") or item.get("scraped_at") or "")


def merge(
    existing: List[Dict[str, Any]],
    fresh: List[Dict[str, Any]],
    *,
    max_stored: int = 0,
) -> Tuple[List[Dict[str, Any]], int, int]:
    """Merge fresh articles into existing ones.

    Returns (merged, added_count, updated_count). Newest first.
    """
    index: Dict[str, Dict[str, Any]] = {}
    order: List[str] = []
    for item in existing:
        key = _key(item)
        if key not in index:
            order.append(key)
        index[key] = item

    added = updated = 0
    for item in fresh:
        key = _key(item)
        if key in index:
            old = index[key]
            merged_item = {**old, **{k: v for k, v in item.items() if v not in (None, "", [], {})}}
            merged_item["first_seen_at"] = old.get("first_seen_at") or item.get("scraped_at")
            index[key] = merged_item
            updated += 1
        else:
            item.setdefault("first_seen_at", item.get("scraped_at"))
            index[key] = item
            order.append(key)
            added += 1

    merged = [index[key] for key in order]
    merged.sort(key=_sort_key, reverse=True)
    if max_stored > 0:
        merged = merged[:max_stored]
    return merged, added, updated


def save(path: str, payload: Dict[str, Any]) -> None:
    """Write JSON atomically (tmp file + os.replace) with pretty formatting."""
    directory = os.path.dirname(os.path.abspath(path)) or "."
    os.makedirs(directory, exist_ok=True)
    handle = tempfile.NamedTemporaryFile(
        "w", encoding="utf-8", dir=directory, prefix=".news-", suffix=".tmp", delete=False
    )
    try:
        with handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
        os.replace(handle.name, path)
    except BaseException:
        try:
            os.unlink(handle.name)
        except OSError:
            pass
        raise
    log.info("wrote %s", path)
