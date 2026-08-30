"""Resolve `news.google.com/rss/articles/...` redirect links to real article URLs.

Strategy (first success wins):
1. Google News internal `batchexecute` RPC (`Fbv4je` / garturlreq) - the reliable
   method for the current, encrypted article ids.
2. Legacy base64 payload decoding (older ids embed the target url directly).
3. Plain HTTP follow: sometimes Google answers with a normal redirect or with a
   consent/refresh page that still contains the destination url.
"""
from __future__ import annotations

import base64
import json
import logging
import re
from typing import Optional
from urllib.parse import unquote, urlparse

from . import http_client
from .config import settings

log = logging.getLogger(__name__)

BATCH_URL = "https://news.google.com/_/DotsSplashUi/data/batchexecute"
_ARTICLE_ID_RE = re.compile(r"/rss/articles/([^?/#]+)")
_URL_IN_TEXT_RE = re.compile(r"https?://[^\s\"'<>\\)]+")


def is_google_news_url(url: str) -> bool:
    host = (urlparse(url).hostname or "").lower()
    return host.endswith("news.google.com")


def _article_id(url: str) -> Optional[str]:
    match = _ARTICLE_ID_RE.search(url)
    return match.group(1) if match else None


# --------------------------------------------------------------------------- #
# 1) batchexecute RPC
# --------------------------------------------------------------------------- #
def _fetch_signature(url: str) -> Optional[tuple[str, str, str]]:
    """Return (article_id, timestamp, signature) scraped from the redirect page."""
    html = http_client.get_text(url)
    if not html:
        return None
    aid = re.search(r'data-n-a-id="([^"]+)"', html)
    ts = re.search(r'data-n-a-ts="([^"]+)"', html)
    sg = re.search(r'data-n-a-sg="([^"]+)"', html)
    if not (aid and ts and sg):
        # Sometimes only the query-string id is available; fall back to it.
        fallback_id = _article_id(url)
        if fallback_id and ts and sg:
            return fallback_id, ts.group(1), sg.group(1)
        return None
    return aid.group(1), ts.group(1), sg.group(1)


def _resolve_via_batchexecute(url: str) -> Optional[str]:
    signature = _fetch_signature(url)
    if not signature:
        return None
    article_id, timestamp, sig = signature

    inner = json.dumps(
        [
            "garturlreq",
            [
                ["X", "X", ["X", "X"], None, None, 1, 1,
                 f"{settings.country()}:{settings.language().split('-')[0]}",
                 None, 1, None, None, None, None, None, 0, 1],
                "X", "X", 1, [1], 1, 1, None, 0, 0, None, 0,
            ],
            article_id,
            int(timestamp),
            sig,
        ]
    )
    payload = {"f.req": json.dumps([[["Fbv4je", inner, None, "generic"]]])}

    response = http_client.post(
        BATCH_URL,
        params={
            "rpcids": "Fbv4je",
            "source-path": "/rss/articles/",
            "hl": settings.language(),
            "gl": settings.country(),
        },
        data=payload,
        headers={
            "Content-Type": "application/x-www-form-urlencoded;charset=UTF-8",
            "Referer": "https://news.google.com/",
            "X-Same-Domain": "1",
        },
    )
    if response is None:
        return None

    body = response.text.lstrip(")]}'\n")
    try:
        envelope = json.loads(body)
    except json.JSONDecodeError:
        match = _URL_IN_TEXT_RE.search(body.replace("\\/", "/"))
        return match.group(0) if match else None

    for frame in envelope:
        if not (isinstance(frame, list) and len(frame) > 2 and frame[0] == "wrb.fr"):
            continue
        try:
            result = json.loads(frame[2])
        except (TypeError, json.JSONDecodeError):
            continue
        if isinstance(result, list) and len(result) > 1 and isinstance(result[1], str):
            candidate = result[1]
            if candidate.startswith("http"):
                return candidate
    return None


# --------------------------------------------------------------------------- #
# 2) legacy base64 payload
# --------------------------------------------------------------------------- #
def _resolve_via_base64(url: str) -> Optional[str]:
    article_id = _article_id(url)
    if not article_id:
        return None
    padded = article_id + "=" * (-len(article_id) % 4)
    try:
        raw = base64.urlsafe_b64decode(padded)
    except (ValueError, TypeError):
        return None
    text = raw.decode("latin-1", errors="ignore")
    match = _URL_IN_TEXT_RE.search(text)
    if not match:
        return None
    candidate = match.group(0).rstrip("\u0001\u0002\u0003 ")
    return candidate if not is_google_news_url(candidate) else None


# --------------------------------------------------------------------------- #
# 3) plain HTTP follow / html sniffing
# --------------------------------------------------------------------------- #
def _resolve_via_redirect(url: str) -> Optional[str]:
    response = http_client.get(url, allow_redirects=True)
    if response is None:
        return None

    if not is_google_news_url(response.url):
        return response.url

    html = response.text
    patterns = (
        r'<meta[^>]+http-equiv="refresh"[^>]+url=([^"\'>]+)',
        r'<link[^>]+rel="canonical"[^>]+href="(https?://[^"]+)"',
        r'<a[^>]+href="(https?://(?!news\.google\.com)[^"]+)"[^>]*>\s*(?:Opening|Read)',
        r'data-n-au="([^"]+)"',
    )
    for pattern in patterns:
        match = re.search(pattern, html, re.IGNORECASE)
        if match:
            candidate = unquote(match.group(1)).strip()
            if candidate.startswith("http") and not is_google_news_url(candidate):
                return candidate
    return None


def resolve(url: str) -> Optional[str]:
    """Resolve one Google News link into the publisher's article URL."""
    if not url:
        return None
    if not is_google_news_url(url):
        return url

    for name, strategy in (
        ("batchexecute", _resolve_via_batchexecute),
        ("base64", _resolve_via_base64),
        ("redirect", _resolve_via_redirect),
    ):
        try:
            resolved = strategy(url)
        except Exception as exc:  # never let one strategy kill the run
            log.debug("resolver %s crashed: %s", name, exc)
            resolved = None
        if resolved and not is_google_news_url(resolved):
            log.debug("resolved via %s -> %s", name, resolved)
            return resolved

    log.warning("could not resolve google news url: %s", url[:90])
    return None
