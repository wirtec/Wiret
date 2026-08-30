"""Central configuration (overridable via environment variables)."""
from __future__ import annotations

import os
from dataclasses import dataclass, field


def _env_str(key: str, default: str) -> str:
    value = os.environ.get(key)
    return value.strip() if value and value.strip() else default


def _env_int(key: str, default: int) -> int:
    try:
        return int(os.environ.get(key, "").strip())
    except (TypeError, ValueError):
        return default


def _env_bool(key: str, default: bool) -> bool:
    raw = os.environ.get(key)
    if raw is None or not raw.strip():
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


DEFAULT_FEED_URL = (
    "https://news.google.com/rss/search"
    "?q=technology&hl=en-US&gl=US&ceid=US:en"
)

DESKTOP_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36"
)


@dataclass
class Settings:
    """Runtime settings for the scraper."""

    feed_url: str = field(default_factory=lambda: _env_str("FEED_URL", DEFAULT_FEED_URL))
    output_file: str = field(default_factory=lambda: _env_str("OUTPUT_FILE", "news.json"))

    # How many feed items to process per run (0 = all items in the feed).
    max_items: int = field(default_factory=lambda: _env_int("MAX_ITEMS", 25))
    # Parallel workers used while downloading/parsing articles.
    workers: int = field(default_factory=lambda: _env_int("WORKERS", 6))
    # Network settings.
    timeout: int = field(default_factory=lambda: _env_int("TIMEOUT", 25))
    retries: int = field(default_factory=lambda: _env_int("RETRIES", 3))
    user_agent: str = field(default_factory=lambda: _env_str("USER_AGENT", DESKTOP_UA))

    # Keep at most this many articles inside news.json (0 = unlimited).
    max_stored: int = field(default_factory=lambda: _env_int("MAX_STORED", 500))
    # Re-scrape an article that already exists in news.json?
    refresh_existing: bool = field(default_factory=lambda: _env_bool("REFRESH_EXISTING", False))
    # Max images stored per article.
    max_images: int = field(default_factory=lambda: _env_int("MAX_IMAGES", 12))
    # Politeness delay (seconds) between requests inside one worker.
    delay: float = field(default_factory=lambda: float(_env_str("DELAY", "0.4")))

    def language(self) -> str:
        """Extract the `hl` value from the feed URL (defaults to en-US)."""
        from urllib.parse import parse_qs, urlparse

        query = parse_qs(urlparse(self.feed_url).query)
        return (query.get("hl") or ["en-US"])[0]

    def country(self) -> str:
        from urllib.parse import parse_qs, urlparse

        query = parse_qs(urlparse(self.feed_url).query)
        return (query.get("gl") or ["US"])[0]


settings = Settings()
