"""Download a publisher page and extract full article text + images + metadata."""
from __future__ import annotations

import json
import logging
import re
from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urljoin, urlparse, urlsplit, urlunsplit

from bs4 import BeautifulSoup

from . import http_client
from .config import settings

log = logging.getLogger(__name__)

try:  # optional but strongly recommended: much better full-text quality
    import trafilatura
    from trafilatura.settings import use_config as _tfl_use_config

    _TFL_CONFIG = _tfl_use_config()
    _TFL_CONFIG.set("DEFAULT", "EXTRACTION_TIMEOUT", "0")
    HAS_TRAFILATURA = True
except Exception:  # pragma: no cover
    trafilatura = None
    _TFL_CONFIG = None
    HAS_TRAFILATURA = False

_BAD_IMAGE_HINTS = (
    "sprite", "logo", "icon", "avatar", "favicon", "placeholder", "1x1",
    "pixel", "tracking", "advert", "/ads/", "doubleclick", "spacer",
    "amazon-adsystem", "gravatar", "blank.gif", "loader",
)
_IMAGE_EXT_RE = re.compile(r"\.(jpe?g|png|webp|gif|avif)(?:[?#]|$)", re.IGNORECASE)
_JUNK_SELECTORS = (
    "script", "style", "noscript", "template", "iframe", "form", "nav", "aside",
    "footer", "header", "svg", "button",
)
_JUNK_CLASS_RE = re.compile(
    r"(share|social|newsletter|subscribe|promo|advert|related|recirc|comment|"
    r"paywall|cookie|breadcrumb|tag-list|author-bio|most-read|trending|sidebar)",
    re.IGNORECASE,
)
_CONTENT_SELECTORS = (
    "article",
    "main article",
    '[itemprop="articleBody"]',
    ".article-body", ".article__body", ".article-content", ".articleBody",
    ".story-body", ".story-content", ".post-content", ".entry-content",
    ".c-entry-content", ".rich-text", "#article-body", "#articleBody",
    "main",
)


@dataclass
class Image:
    url: str
    alt: str = ""
    caption: str = ""
    role: str = "body"  # lead | body | feed


@dataclass
class Article:
    url: str
    final_url: str = ""
    site: str = ""
    title: str = ""
    heading: str = ""            # <h1> of the publisher page
    authors: List[str] = field(default_factory=list)
    published_at: Optional[str] = None
    modified_at: Optional[str] = None
    summary: str = ""
    text: str = ""               # full article text
    html: str = ""               # cleaned article html
    word_count: int = 0
    char_count: int = 0
    lead_image: Optional[str] = None
    images: List[Image] = field(default_factory=list)
    extractor: str = ""
    error: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        data = asdict(self)
        data["images"] = [asdict(img) if not isinstance(img, dict) else img for img in self.images]
        return data


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #
def _norm_ws(text: str) -> str:
    text = text.replace("\u00a0", " ")
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return "\n".join(line.strip() for line in text.splitlines()).strip()


def _meta(soup: BeautifulSoup, *names: str) -> str:
    for name in names:
        for attr in ("property", "name", "itemprop"):
            tag = soup.find("meta", attrs={attr: name})
            if tag and tag.get("content"):
                return tag["content"].strip()
    return ""


def _jsonld_objects(soup: BeautifulSoup) -> List[dict]:
    found: List[dict] = []

    def walk(node: Any) -> None:
        if isinstance(node, dict):
            found.append(node)
            for value in node.values():
                walk(value)
        elif isinstance(node, list):
            for value in node:
                walk(value)

    for tag in soup.find_all("script", attrs={"type": re.compile("ld\\+json", re.I)}):
        raw = tag.string or tag.get_text() or ""
        try:
            walk(json.loads(raw))
        except (json.JSONDecodeError, TypeError):
            cleaned = re.sub(r",\s*([}\]])", r"\1", raw.strip())
            try:
                walk(json.loads(cleaned))
            except Exception:
                continue
    return found


def _is_article_ld(obj: dict) -> bool:
    typ = obj.get("@type")
    types = typ if isinstance(typ, list) else [typ]
    return any(
        isinstance(t, str) and t.lower().replace(" ", "") in
        {"newsarticle", "article", "reportagenewsarticle", "blogposting", "webpage"}
        for t in types
    )


def _authors_from_ld(obj: dict) -> List[str]:
    raw = obj.get("author") or obj.get("creator")
    names: List[str] = []
    if isinstance(raw, str):
        names = [raw]
    elif isinstance(raw, dict):
        names = [str(raw.get("name", ""))]
    elif isinstance(raw, list):
        for entry in raw:
            if isinstance(entry, str):
                names.append(entry)
            elif isinstance(entry, dict):
                names.append(str(entry.get("name", "")))
    return [n.strip() for n in names if n and n.strip()]


def _clean_url(url: str, base: str) -> Optional[str]:
    if not url:
        return None
    url = url.strip().split()[0] if " " in url.strip() else url.strip()
    if url.startswith("data:") or url.startswith("blob:"):
        return None
    absolute = urljoin(base, url)
    parsed = urlparse(absolute)
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        return None
    low = absolute.lower()
    if any(hint in low for hint in _BAD_IMAGE_HINTS):
        return None
    return absolute


def _looks_like_image(url: str) -> bool:
    if _IMAGE_EXT_RE.search(url):
        return True
    # CDN endpoints without extension, e.g. /image/resize?...&format=webp
    return bool(re.search(r"(images?|photo|media|cdn|thumb)", urlparse(url).path, re.I))


_SIZE_PARAM_RE = re.compile(r"(?:^|&)(w|h|width|height|quality|q|resize|fit|v|s|size)=[^&]*", re.IGNORECASE)


def _image_identity(url: str) -> str:
    """Identity used to de-duplicate the same image served in several sizes."""
    parts = urlsplit(url)
    query = _SIZE_PARAM_RE.sub("", parts.query).strip("&")
    path = re.sub(r"-\d{2,4}x\d{2,4}(?=\.[a-z]{3,4}$)", "", parts.path, flags=re.IGNORECASE)
    return urlunsplit((parts.scheme, parts.netloc, path, query, ""))


def _pick_from_srcset(srcset: str) -> str:
    """Pick the widest candidate of a srcset attribute."""
    best, best_width = "", -1
    for part in srcset.split(","):
        chunk = part.strip().split()
        if not chunk:
            continue
        url = chunk[0]
        width = -1
        if len(chunk) > 1 and chunk[1].endswith("w"):
            try:
                width = int(chunk[1][:-1])
            except ValueError:
                width = -1
        if width > best_width:
            best, best_width = url, width
    return best


def _collect_images(soup: BeautifulSoup, base: str, container) -> List[Image]:
    images: List[Image] = []
    seen: set[str] = set()

    def add(url: Optional[str], alt: str = "", caption: str = "", role: str = "body") -> None:
        if not url or not _looks_like_image(url):
            return
        identity = _image_identity(url)
        if identity in seen:
            return
        seen.add(identity)
        images.append(Image(url=url, alt=_norm_ws(alt)[:400], caption=_norm_ws(caption)[:600], role=role))

    # 1) social / lead images
    for name in ("og:image", "og:image:secure_url", "twitter:image", "twitter:image:src"):
        add(_clean_url(_meta(soup, name), base), role="lead")

    # 2) JSON-LD images
    for obj in _jsonld_objects(soup):
        if not _is_article_ld(obj):
            continue
        raw = obj.get("image") or obj.get("thumbnailUrl")
        candidates: List[str] = []
        if isinstance(raw, str):
            candidates = [raw]
        elif isinstance(raw, dict):
            candidates = [str(raw.get("url", ""))]
        elif isinstance(raw, list):
            for entry in raw:
                if isinstance(entry, str):
                    candidates.append(entry)
                elif isinstance(entry, dict):
                    candidates.append(str(entry.get("url", "")))
        for candidate in candidates:
            add(_clean_url(candidate, base), role="lead")

    # 3) images inside the article body (with <figcaption> when available)
    scope = container if container is not None else soup
    for figure in scope.find_all(["figure", "picture", "img"]):
        if figure.name == "img":
            img = figure
        else:
            img = figure.find("img")
        caption = ""
        parent_figure = figure if figure.name == "figure" else figure.find_parent("figure")
        if parent_figure is not None:
            figcaption = parent_figure.find("figcaption")
            if figcaption:
                caption = figcaption.get_text(" ", strip=True)

        source = figure.find("source") if figure.name in ("figure", "picture") else None
        srcset = ""
        if source is not None:
            srcset = source.get("srcset") or source.get("data-srcset") or ""
        if img is not None and not srcset:
            srcset = img.get("srcset") or img.get("data-srcset") or ""

        url_raw = ""
        if srcset:
            url_raw = _pick_from_srcset(srcset)
        if not url_raw and img is not None:
            for attr in ("src", "data-src", "data-original", "data-lazy-src", "data-image-src", "data-hi-res-src"):
                if img.get(attr):
                    url_raw = img[attr]
                    break
        alt = img.get("alt", "") if img is not None else ""
        add(_clean_url(url_raw, base), alt=alt, caption=caption)

        if settings.max_images and len(images) >= settings.max_images:
            break

    if settings.max_images:
        images = images[: settings.max_images]
    return images


def _fallback_text(soup: BeautifulSoup) -> tuple[str, str, Any]:
    """BeautifulSoup based extraction: (text, html, container)."""
    work = BeautifulSoup(str(soup), "lxml")
    for tag in work.find_all(_JUNK_SELECTORS):
        tag.decompose()
    for tag in work.find_all(attrs={"class": _JUNK_CLASS_RE}):
        tag.decompose()
    for tag in work.find_all(attrs={"id": _JUNK_CLASS_RE}):
        tag.decompose()

    best_node, best_score = None, 0
    for selector in _CONTENT_SELECTORS:
        for node in work.select(selector):
            paragraphs = node.find_all("p")
            score = sum(len(p.get_text(strip=True)) for p in paragraphs)
            if score > best_score:
                best_node, best_score = node, score
    if best_node is None:
        best_node = work.body or work

    blocks: List[str] = []
    for element in best_node.find_all(["h2", "h3", "h4", "p", "li", "blockquote", "pre"]):
        chunk = element.get_text(" ", strip=True)
        if not chunk or len(chunk) < 2:
            continue
        if element.name in ("h2", "h3", "h4"):
            blocks.append(f"\n{chunk}\n")
        elif element.name == "li":
            blocks.append(f"- {chunk}")
        else:
            blocks.append(chunk)
    text = _norm_ws("\n\n".join(blocks))
    if len(text) < 200:  # last resort
        text = _norm_ws(best_node.get_text("\n", strip=True))
    return text, str(best_node), best_node


def extract_from_html(html: str, url: str, final_url: Optional[str] = None) -> Article:
    """Extract text/images/metadata from raw page HTML."""
    article = Article(url=url, final_url=final_url or url)
    soup = BeautifulSoup(html, "lxml")

    article.site = _meta(soup, "og:site_name", "application-name") or (
        urlparse(article.final_url).hostname or ""
    ).replace("www.", "")
    article.title = (
        _meta(soup, "og:title", "twitter:title")
        or (soup.title.get_text(strip=True) if soup.title else "")
    )
    h1 = soup.find("h1")
    article.heading = h1.get_text(" ", strip=True) if h1 else article.title
    article.summary = _meta(soup, "og:description", "description", "twitter:description")
    article.published_at = (
        _meta(soup, "article:published_time", "datePublished", "publish-date", "pubdate") or None
    )
    article.modified_at = _meta(soup, "article:modified_time", "dateModified") or None
    authors = [a for a in _meta(soup, "author", "article:author", "byl").split(",") if a.strip()]

    for obj in _jsonld_objects(soup):
        if not _is_article_ld(obj):
            continue
        authors = authors or _authors_from_ld(obj)
        article.published_at = article.published_at or obj.get("datePublished")
        article.modified_at = article.modified_at or obj.get("dateModified")
        article.summary = article.summary or str(obj.get("description") or "")
        article.heading = article.heading or str(obj.get("headline") or "")
    article.authors = [a.strip() for a in authors if a and a.strip()][:8]

    # ---- full text -------------------------------------------------------- #
    text, body_html, container = "", "", None
    if HAS_TRAFILATURA:
        try:
            text = (
                trafilatura.extract(
                    html,
                    url=article.final_url,
                    include_comments=False,
                    include_tables=True,
                    include_images=False,
                    include_links=False,
                    favor_recall=True,
                    config=_TFL_CONFIG,
                )
                or ""
            )
            if text:
                article.extractor = "trafilatura"
                body_html = (
                    trafilatura.extract(
                        html,
                        url=article.final_url,
                        output_format="html",
                        include_comments=False,
                        include_tables=True,
                        include_images=True,
                        config=_TFL_CONFIG,
                    )
                    or ""
                )
        except Exception as exc:
            log.debug("trafilatura failed for %s: %s", url[:80], exc)

    fallback_text, fallback_html, container = _fallback_text(soup)
    if len(fallback_text) > len(text) * 1.25 or len(text) < 400:
        if len(fallback_text) > len(text):
            text, body_html = fallback_text, fallback_html
            article.extractor = "bs4"

    article.text = _norm_ws(text)
    article.html = body_html
    article.word_count = len(article.text.split())
    article.char_count = len(article.text)

    # ---- images ----------------------------------------------------------- #
    article.images = _collect_images(soup, article.final_url, container)
    lead = next((img.url for img in article.images if img.role == "lead"), None)
    article.lead_image = lead or (article.images[0].url if article.images else None)

    if not article.text:
        article.error = "empty_text"
    return article


def _amp_candidates(url: str, html: Optional[str]) -> List[str]:
    """Possible AMP versions of a page (AMP is rarely bot-blocked)."""
    candidates: List[str] = []
    if html:
        match = re.search(
            r'<link[^>]+rel=["\'][^"\']*amphtml[^"\']*["\'][^>]+href=["\']([^"\']+)',
            html, re.IGNORECASE,
        )
        if not match:
            match = re.search(
                r'<link[^>]+href=["\']([^"\']+)["\'][^>]+rel=["\'][^"\']*amphtml',
                html, re.IGNORECASE,
            )
        if match:
            candidates.append(urljoin(url, match.group(1)))

    parts = urlsplit(url)
    base = urlunsplit((parts.scheme, parts.netloc, parts.path.rstrip("/"), "", ""))
    candidates += [f"{base}/amp/", f"{base}/amp", f"{base}?amp=1", f"{base}?outputType=amp"]

    unique: List[str] = []
    for candidate in candidates:
        if candidate and candidate != url and candidate not in unique:
            unique.append(candidate)
    return unique[:4]


def _download(url: str, *, retries: int = 2, timeout: Optional[int] = None) -> Tuple[Optional[str], Optional[str], Optional[str]]:
    """GET a page -> (html, final_url, error)."""
    response = http_client.get(url, allow_redirects=True, retries=retries, timeout=timeout)
    if response is None:
        return None, None, "download_failed"

    content_type = (response.headers.get("Content-Type") or "").lower()
    if content_type and "html" not in content_type and "xml" not in content_type:
        return None, response.url, f"unsupported_content_type:{content_type.split(';')[0]}"

    if not response.encoding or response.encoding.lower() == "iso-8859-1":
        response.encoding = response.apparent_encoding or "utf-8"
    return response.text, response.url, None


MIN_GOOD_CHARS = 900


def fetch_article(url: str) -> Article:
    """Download the publisher page (with AMP fallbacks) and extract everything."""
    html, final_url, error = _download(url)

    best: Optional[Article] = None
    if html:
        best = extract_from_html(html, url, final_url)
        if best.char_count >= MIN_GOOD_CHARS:
            return best

    # Blocked (403/503), JS-only, or truncated page -> try AMP variants.
    for candidate in _amp_candidates(url, html):
        # AMP probes are best-effort: fail fast so one hostile site can't stall the run.
        amp_html, amp_final, amp_error = _download(
            candidate, retries=1, timeout=min(settings.timeout, 12)
        )
        if not amp_html:
            log.debug("amp candidate failed (%s): %s", amp_error, candidate[:90])
            continue
        amp_article = extract_from_html(amp_html, url, amp_final or candidate)
        amp_article.extractor = f"{amp_article.extractor}+amp"
        if best is None or amp_article.char_count > best.char_count:
            best = amp_article
        if best.char_count >= MIN_GOOD_CHARS:
            return best

    if best is not None:
        if best.char_count and best.char_count < MIN_GOOD_CHARS:
            best.error = best.error or "short_text"
        return best

    return Article(url=url, final_url=final_url or url, error=error or "download_failed")
