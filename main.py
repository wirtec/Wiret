#!/usr/bin/env python3
"""Google News RSS -> full-article scraper.

Reads a Google News RSS search feed, resolves every Google redirect link to the
real publisher URL, downloads that page, extracts the complete article text and
images, and stores everything in `news.json`.

Usage:
    python main.py                       # default technology feed
    python main.py --max-items 10        # only the 10 newest items
    python main.py --refresh             # re-scrape items already stored
    python main.py -q "artificial intelligence"
"""
from __future__ import annotations

import argparse
import logging
import sys

from scraper.config import DEFAULT_FEED_URL, settings
from scraper.pipeline import run


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="news-scraper",
        description="Scrape full articles from a Google News RSS feed into news.json",
    )
    parser.add_argument("--feed", default=settings.feed_url, help=f"RSS feed url (default: {DEFAULT_FEED_URL})")
    parser.add_argument("-q", "--query", help="build the feed url from this search query instead of --feed")
    parser.add_argument("--hl", default="en-US", help="feed language for --query (default: en-US)")
    parser.add_argument("--gl", default="US", help="feed country for --query (default: US)")
    parser.add_argument("-o", "--output", default=settings.output_file, help="output json file (default: news.json)")
    parser.add_argument("-n", "--max-items", type=int, default=settings.max_items,
                        help="max feed items per run, 0 = all (default: %(default)s)")
    parser.add_argument("-w", "--workers", type=int, default=settings.workers,
                        help="parallel download workers (default: %(default)s)")
    parser.add_argument("--timeout", type=int, default=settings.timeout, help="request timeout in seconds")
    parser.add_argument("--retries", type=int, default=settings.retries, help="retries per request")
    parser.add_argument("--max-stored", type=int, default=settings.max_stored,
                        help="max articles kept in the json file, 0 = unlimited")
    parser.add_argument("--max-images", type=int, default=settings.max_images, help="max images per article")
    parser.add_argument("--delay", type=float, default=settings.delay, help="politeness delay per request")
    parser.add_argument("--refresh", action="store_true", help="re-scrape articles already present in the json")
    parser.add_argument("-v", "--verbose", action="store_true", help="debug logging")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)-7s %(name)-18s %(message)s",
        datefmt="%H:%M:%S",
        stream=sys.stdout,
    )
    logging.getLogger("urllib3").setLevel(logging.WARNING)
    logging.getLogger("trafilatura").setLevel(logging.ERROR)

    if args.query:
        from urllib.parse import quote_plus

        settings.feed_url = (
            "https://news.google.com/rss/search"
            f"?q={quote_plus(args.query)}&hl={args.hl}&gl={args.gl}&ceid={args.gl}:{args.hl.split('-')[0]}"
        )
    else:
        settings.feed_url = args.feed

    settings.output_file = args.output
    settings.max_items = args.max_items
    settings.workers = args.workers
    settings.timeout = args.timeout
    settings.retries = args.retries
    settings.max_stored = args.max_stored
    settings.max_images = args.max_images
    settings.delay = args.delay
    settings.refresh_existing = args.refresh or settings.refresh_existing

    result = run(settings)
    return 0 if result.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
