"""Offline unit tests (no network required): python -m pytest -q"""
from __future__ import annotations

import json
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from scraper import extractor, resolver, rss, storage  # noqa: E402

FEED_XML = """<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0" xmlns:media="http://search.yahoo.com/mrss/"><channel>
<title>"technology" - Google News</title>
<item>
  <title>Big chip news lands today - Example Times</title>
  <link>https://news.google.com/rss/articles/CBMiABCD?oc=5</link>
  <guid isPermaLink="false">CBMiABCD</guid>
  <pubDate>Fri, 29 Aug 2026 16:00:00 GMT</pubDate>
  <description>&lt;a href="https://news.google.com/rss/articles/CBMiABCD?oc=5"&gt;Big chip news lands today&lt;/a&gt;&nbsp;&nbsp;&lt;font color="#6f6f6f"&gt;Example Times&lt;/font&gt;</description>
  <source url="https://example.com">Example Times</source>
  <media:content url="https://example.com/thumb.jpg" medium="image"/>
</item>
</channel></rss>
"""

ARTICLE_HTML = """<!doctype html><html><head>
<title>Big chip news lands today | Example Times</title>
<meta property="og:site_name" content="Example Times">
<meta property="og:title" content="Big chip news lands today">
<meta property="og:description" content="A short summary of the chip news.">
<meta property="og:image" content="https://cdn.example.com/lead.jpg?w=1200">
<meta property="article:published_time" content="2026-08-29T16:00:00Z">
<script type="application/ld+json">
{"@type":"NewsArticle","headline":"Big chip news lands today",
 "author":[{"name":"Jane Doe"},{"name":"John Roe"}],
 "datePublished":"2026-08-29T16:00:00Z","image":["https://cdn.example.com/lead.jpg?w=640"]}
</script>
</head><body>
<nav>menu junk</nav>
<article>
  <h1>Big chip news lands today</h1>
  <p>%(p1)s</p>
  <h2>Why it matters</h2>
  <p>%(p2)s</p>
  <figure>
    <img src="/img/fab.jpg" alt="A chip fab" srcset="/img/fab-480.jpg 480w, /img/fab-1600.jpg 1600w">
    <figcaption>Inside the new fab.</figcaption>
  </figure>
  <ul><li>Point one</li><li>Point two</li></ul>
  <img src="/img/logo-sprite.png" alt="logo">
</article>
<div class="newsletter-signup"><p>Subscribe to our newsletter now!</p></div>
<script>var tracking = 1;</script>
</body></html>""" % {"p1": "First paragraph. " * 30, "p2": "Second paragraph. " * 30}


class TestFeedParsing(unittest.TestCase):
    def setUp(self) -> None:
        self.items = rss.parse_feed(FEED_XML)

    def test_single_item(self) -> None:
        self.assertEqual(len(self.items), 1)

    def test_title_and_header_split(self) -> None:
        item = self.items[0]
        self.assertEqual(item.title, "Big chip news lands today")
        self.assertEqual(item.header, "Example Times")

    def test_time_normalised_to_iso_utc(self) -> None:
        self.assertEqual(self.items[0].published_at, "2026-08-29T16:00:00Z")
        self.assertEqual(self.items[0].published_raw, "Fri, 29 Aug 2026 16:00:00 GMT")

    def test_description_text_and_links(self) -> None:
        item = self.items[0]
        self.assertIn("Big chip news lands today", item.description_text)
        self.assertNotIn("<a", item.description_text)
        self.assertTrue(item.description_links)
        self.assertTrue(item.description_links[0].startswith("https://news.google.com/rss/articles/"))

    def test_feed_image_and_stable_id(self) -> None:
        self.assertEqual(self.items[0].feed_image, "https://example.com/thumb.jpg")
        self.assertEqual(self.items[0].id, rss.parse_feed(FEED_XML)[0].id)

    def test_broken_xml_is_safe(self) -> None:
        self.assertEqual(rss.parse_feed("<rss><channel><item>"), [])


class TestExtractor(unittest.TestCase):
    def setUp(self) -> None:
        self.article = extractor.extract_from_html(
            ARTICLE_HTML, "https://example.com/a", "https://example.com/a"
        )

    def test_metadata(self) -> None:
        self.assertEqual(self.article.site, "Example Times")
        self.assertEqual(self.article.heading, "Big chip news lands today")
        self.assertEqual(self.article.published_at, "2026-08-29T16:00:00Z")
        self.assertEqual(self.article.summary, "A short summary of the chip news.")
        self.assertIn("Jane Doe", self.article.authors)

    def test_full_text_extracted_without_junk(self) -> None:
        self.assertIn("First paragraph.", self.article.text)
        self.assertIn("Second paragraph.", self.article.text)
        self.assertNotIn("menu junk", self.article.text)
        self.assertNotIn("var tracking", self.article.text)
        self.assertGreater(self.article.word_count, 100)
        self.assertEqual(self.article.char_count, len(self.article.text))

    def test_images(self) -> None:
        urls = [img.url for img in self.article.images]
        self.assertTrue(any("lead.jpg" in url for url in urls))
        self.assertTrue(any("fab" in url for url in urls))
        self.assertFalse(any("sprite" in url for url in urls), "junk images must be filtered")
        self.assertTrue(self.article.lead_image)

    def test_image_dedupe_across_sizes(self) -> None:
        lead_variants = [u for u in (i.url for i in self.article.images) if "lead.jpg" in u]
        self.assertEqual(len(lead_variants), 1)

    def test_srcset_picks_widest(self) -> None:
        self.assertEqual(
            extractor._pick_from_srcset("/a-480.jpg 480w, /a-1600.jpg 1600w, /a-800.jpg 800w"),
            "/a-1600.jpg",
        )

    def test_empty_html_flags_error(self) -> None:
        empty = extractor.extract_from_html("<html><body></body></html>", "https://example.com/x")
        self.assertEqual(empty.error, "empty_text")


class TestResolver(unittest.TestCase):
    def test_google_host_detection(self) -> None:
        self.assertTrue(resolver.is_google_news_url("https://news.google.com/rss/articles/AB?oc=5"))
        self.assertFalse(resolver.is_google_news_url("https://example.com/a"))

    def test_non_google_url_passthrough(self) -> None:
        self.assertEqual(resolver.resolve("https://example.com/a"), "https://example.com/a")

    def test_article_id_parsing(self) -> None:
        self.assertEqual(resolver._article_id("https://news.google.com/rss/articles/XYZ123?oc=5"), "XYZ123")


class TestStorage(unittest.TestCase):
    def setUp(self) -> None:
        self.path = "/tmp/_test_news.json"
        if os.path.exists(self.path):
            os.remove(self.path)

    def tearDown(self) -> None:
        if os.path.exists(self.path):
            os.remove(self.path)

    def test_roundtrip_and_merge(self) -> None:
        first = [{"url": "https://e.com/1", "title": "one", "published_at": "2026-01-01T00:00:00Z",
                  "scraped_at": "2026-01-01T01:00:00Z", "status": "ok"}]
        storage.save(self.path, {"articles": first, "count": 1})
        existing, meta = storage.load(self.path)
        self.assertEqual(len(existing), 1)
        self.assertEqual(meta.get("count"), 1)

        fresh = [
            {"url": "https://e.com/1", "title": "one v2", "content": "full", "status": "ok",
             "scraped_at": "2026-01-02T01:00:00Z", "published_at": "2026-01-01T00:00:00Z"},
            {"url": "https://e.com/2", "title": "two", "status": "ok",
             "scraped_at": "2026-01-03T01:00:00Z", "published_at": "2026-01-03T00:00:00Z"},
        ]
        merged, added, updated = storage.merge(existing, fresh)
        self.assertEqual((added, updated), (1, 1))
        self.assertEqual(len(merged), 2)
        self.assertEqual(merged[0]["url"], "https://e.com/2", "newest article must come first")
        updated_item = next(m for m in merged if m["url"] == "https://e.com/1")
        self.assertEqual(updated_item["title"], "one v2")
        self.assertEqual(updated_item["content"], "full")

    def test_trailing_slash_and_fragment_dedupe(self) -> None:
        merged, added, updated = storage.merge(
            [{"url": "https://e.com/a/", "status": "ok"}],
            [{"url": "https://e.com/a#top", "status": "ok"}],
        )
        self.assertEqual((len(merged), added, updated), (1, 0, 1))

    def test_max_stored_trim(self) -> None:
        items = [{"url": f"https://e.com/{i}", "published_at": f"2026-01-{i:02d}T00:00:00Z"} for i in range(1, 11)]
        merged, _, _ = storage.merge([], items, max_stored=3)
        self.assertEqual(len(merged), 3)

    def test_corrupt_file_recovers(self) -> None:
        with open(self.path, "w", encoding="utf-8") as handle:
            handle.write("{not json")
        self.assertEqual(storage.load(self.path), ([], {}))

    def test_atomic_write_is_valid_json(self) -> None:
        storage.save(self.path, {"articles": [], "count": 0})
        with open(self.path, encoding="utf-8") as handle:
            self.assertEqual(json.load(handle)["count"], 0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
