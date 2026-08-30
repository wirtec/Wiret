"""Google News RSS feed parsing (stdlib XML, no extra deps)."""
from __future__ import annotations

import hashlib
import logging
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from typing import List, Optional
from xml.etree import ElementTree as ET

from . import http_client
from .config import settings

log = logging.getLogger(__name__)

NS = {"media": "http://search.yahoo.com/mrss/"}
_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"[ \t\u00a0]+")


def _clean_html_to_text(html: str) -> str:
    text = re.sub(r"(?is)<br\s*/?>|</p>|</li>", "\n", html or "")
    text = _TAG_RE.sub(" ", text)
    text = (
        text.replace("&nbsp;", " ")
        .replace("&amp;", "&")
        .replace("&quot;", '"')
        .replace("&#39;", "'")
        .replace("&lt;", "<")
        .replace("&gt;", ">")
    )
    text = _WS_RE.sub(" ", text)
    return "\n".join(line.strip() for line in text.splitlines() if line.strip()).strip()


_ENTITY_RE = re.compile(r"&(?!#\d+;|#x[0-9a-fA-F]+;|amp;|lt;|gt;|quot;|apos;)([a-zA-Z][a-zA-Z0-9]{1,31});")


def _sanitize_xml(xml_text: str) -> str:
    """Escape HTML entities (e.g. `&nbsp;`) that are undefined in plain XML."""
    xml_text = xml_text.lstrip("\ufeff \t\r\n")
    return _ENTITY_RE.sub(lambda m: f"&amp;{m.group(1)};", xml_text)


def _parse_date(raw: Optional[str]) -> Optional[str]:
    """RFC-822 date from the feed -> ISO-8601 UTC string."""
    if not raw:
        return None
    try:
        parsed = parsedate_to_datetime(raw)
    except (TypeError, ValueError):
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


@dataclass
class FeedItem:
    """One entry of the Google News RSS feed."""

    id: str
    title: str                       # <title>
    header: str                      # publisher / source  (the "header" of the news)
    google_news_url: str             # redirect url (news.google.com/rss/articles/...)
    published_at: Optional[str]      # ISO-8601 UTC
    published_raw: Optional[str]     # original <pubDate> string
    description_html: str            # raw <description> html
    description_text: str            # description as plain text
    description_links: List[str] = field(default_factory=list)  # links inside <description>
    feed_image: Optional[str] = None  # <media:content> thumbnail if present


def _text(node: Optional[ET.Element]) -> str:
    return (node.text or "").strip() if node is not None else ""


def _split_title(title: str) -> tuple[str, str]:
    """Google News titles look like "Real headline - Publisher"."""
    if " - " in title:
        head, _, source = title.rpartition(" - ")
        head, source = head.strip(), source.strip()
        if head and source and len(source) <= 80:
            return head, source
    return title.strip(), ""


def parse_feed(xml_text: str) -> List[FeedItem]:
    """Turn feed XML into `FeedItem` objects."""
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError:
        try:  # retry with HTML entities escaped
            root = ET.fromstring(_sanitize_xml(xml_text))
        except ET.ParseError as exc:
            log.error("cannot parse feed xml: %s", exc)
            return []

    items: List[FeedItem] = []
    for node in root.iterfind(".//channel/item"):
        link = _text(node.find("link"))
        if not link:
            continue

        raw_title = _text(node.find("title"))
        headline, title_source = _split_title(raw_title)
        source = _text(node.find("source")) or title_source

        description_html = _text(node.find("description"))
        description_links = re.findall(r'href="([^"]+)"', description_html)

        media = node.find("media:content", NS)
        feed_image = media.get("url") if media is not None else None

        guid = _text(node.find("guid")) or link
        items.append(
            FeedItem(
                id=hashlib.sha1(guid.encode("utf-8")).hexdigest()[:16],
                title=headline or raw_title,
                header=source,
                google_news_url=link,
                published_at=_parse_date(_text(node.find("pubDate"))),
                published_raw=_text(node.find("pubDate")) or None,
                description_html=description_html,
                description_text=_clean_html_to_text(description_html),
                description_links=description_links,
                feed_image=feed_image,
            )
        )

    log.info("parsed %s items from feed", len(items))
    return items


def fetch_feed(feed_url: Optional[str] = None) -> List[FeedItem]:
    """Download and parse the configured Google News RSS feed."""
    url = feed_url or settings.feed_url
    log.info("fetching feed: %s", url)
    xml_text = http_client.get_text(url)
    if not xml_text:
        log.error("feed download failed")
        return []
    items = parse_feed(xml_text)
    if settings.max_items > 0:
        items = items[: settings.max_items]
    return items


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")
