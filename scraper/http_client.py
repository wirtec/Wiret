"""Thin HTTP helper: shared session, retries, sane headers."""
from __future__ import annotations

import logging
import random
import threading
import time
from typing import Optional

import requests
from requests.adapters import HTTPAdapter

from .config import settings

log = logging.getLogger(__name__)

_local = threading.local()


def get_session() -> requests.Session:
    """One `requests.Session` per thread (sessions are not thread-safe)."""
    session: Optional[requests.Session] = getattr(_local, "session", None)
    if session is None:
        session = requests.Session()
        adapter = HTTPAdapter(pool_connections=10, pool_maxsize=10, max_retries=0)
        session.mount("https://", adapter)
        session.mount("http://", adapter)
        session.headers.update(
            {
                "User-Agent": settings.user_agent,
                "Accept": "text/html,application/xhtml+xml,application/xml;"
                "q=0.9,image/avif,image/webp,*/*;q=0.8",
                "Accept-Language": f"{settings.language()},en;q=0.8",
                "Cache-Control": "no-cache",
            }
        )
        _local.session = session
    return session


def request(
    method: str,
    url: str,
    *,
    retries: Optional[int] = None,
    timeout: Optional[int] = None,
    **kwargs,
) -> Optional[requests.Response]:
    """Perform a request with exponential backoff. Returns None on failure."""
    retries = settings.retries if retries is None else retries
    timeout = settings.timeout if timeout is None else timeout
    session = get_session()
    last_error: Optional[str] = None

    for attempt in range(1, max(1, retries) + 1):
        try:
            response = session.request(method, url, timeout=timeout, **kwargs)
            if response.status_code in (429, 500, 502, 503, 504):
                last_error = f"HTTP {response.status_code}"
            elif response.status_code >= 400:
                log.debug("%s %s -> HTTP %s (giving up)", method, url, response.status_code)
                return None
            else:
                return response
        except requests.RequestException as exc:  # network/DNS/TLS/timeout
            last_error = type(exc).__name__

        if attempt < retries:
            backoff = (2 ** (attempt - 1)) + random.uniform(0, 0.5)
            log.debug("retry %s/%s for %s (%s) in %.1fs", attempt, retries, url, last_error, backoff)
            time.sleep(backoff)

    log.warning("request failed: %s %s (%s)", method, url, last_error)
    return None


def get(url: str, **kwargs) -> Optional[requests.Response]:
    return request("GET", url, **kwargs)


def post(url: str, **kwargs) -> Optional[requests.Response]:
    return request("POST", url, **kwargs)


def get_text(url: str, **kwargs) -> Optional[str]:
    """GET a URL and return decoded text (with encoding sniffing)."""
    response = get(url, **kwargs)
    if response is None:
        return None
    if not response.encoding or response.encoding.lower() == "iso-8859-1":
        response.encoding = response.apparent_encoding or "utf-8"
    return response.text
