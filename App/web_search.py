"""Web search for product specs — fetches real data before AI generation.

If search fails, returns empty string and the app falls back to AI-only mode.
"""

from __future__ import annotations

import re
import ssl
import time
from html.parser import HTMLParser
from urllib.parse import quote_plus, urlparse
from urllib.request import Request, urlopen


class _TextExtractor(HTMLParser):
    """Extract visible text from HTML, skipping scripts/styles/nav."""

    def __init__(self):
        super().__init__()
        self._skip = False
        self._skip_tags = {"script", "style", "nav", "header", "footer", "noscript"}
        self._pieces: list[str] = []

    def handle_starttag(self, tag, attrs):
        if tag in self._skip_tags:
            self._skip = True

    def handle_endtag(self, tag):
        if tag in self._skip_tags:
            self._skip = False

    def handle_data(self, data):
        if not self._skip:
            text = data.strip()
            if text:
                self._pieces.append(text)

    def get_text(self) -> str:
        return " ".join(self._pieces)


def _fetch_url(url: str, timeout: int = 8) -> str:
    """Fetch URL content with a desktop User-Agent."""
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/120.0.0.0 Safari/537.36"
        ),
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
    }
    req = Request(url, headers=headers)
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    try:
        with urlopen(req, timeout=timeout, context=ctx) as resp:
            raw = resp.read(200_000)  # 200KB max per page
            charset = resp.headers.get_content_charset() or "utf-8"
            return raw.decode(charset, errors="replace")
    except Exception:
        return ""


def _html_to_text(html: str) -> str:
    """Convert HTML to clean text."""
    if not html:
        return ""
    parser = _TextExtractor()
    try:
        parser.feed(html)
    except Exception:
        pass
    text = parser.get_text()
    # Collapse whitespace
    text = re.sub(r"\s+", " ", text).strip()
    return text


def _search_ddg(query: str, max_results: int = 5) -> list[dict]:
    """Search DuckDuckGo HTML and return result URLs + titles."""
    url = f"https://html.duckduckgo.com/html/?q={quote_plus(query)}"
    html = _fetch_url(url, timeout=10)
    if not html:
        return []
    results = []
    # DDG HTML results: <a class="result__a" href="...">
    for m in re.finditer(
        r'<a[^>]+class="result__a"[^>]+href="([^"]+)"[^>]*>(.*?)</a>',
        html, re.DOTALL
    ):
        raw_url = m.group(1)
        title_html = m.group(2)
        title = re.sub(r"<[^>]+>", "", title_html).strip()
        # DDG wraps URLs in redirect
        if "uddg=" in raw_url:
            from urllib.parse import parse_qs
            parsed = urlparse(raw_url)
            qs = parse_qs(parsed.query)
            real_url = qs.get("uddg", [raw_url])[0]
        else:
            real_url = raw_url
        if real_url.startswith("http"):
            results.append({"url": real_url, "title": title})
        if len(results) >= max_results:
            break
    return results


def _is_spec_page(url: str, title: str) -> bool:
    """Heuristic: is this page likely to have product specs?"""
    combined = (url + " " + title).lower()
    bad = ["youtube.com", "facebook.com", "instagram.com", "twitter.com",
           "tiktok.com", "reddit.com/r/", "wikipedia.org/wiki/"]
    if any(b in combined for b in bad):
        return False
    good = ["fender.com", "ibanez.com", "yamaha.com", "shure.com", "sennheiser",
            "roland.com", "boss", "korg.com", "akg.com", "behringer",
            "marshall", "gibson.com", "epiphone", "martin guitar",
            "thomann.de", "sweetwater.com", "guitarcenter.com",
            "reverb.com", "musicstore.com", "bax-shop",
            "equipboard.com", "findmyguitar.com", "scmusic.com.au"]
    if any(g in combined for g in good):
        return True
    # Generic: if title contains specs/review/product
    if any(kw in combined for kw in ["spec", "review", "product", "detail",
                                      "description", "features"]):
        return True
    return True  # allow by default, we'll just extract text


def search_product_info(product_title: str, max_pages: int = 3) -> str:
    """Search the web for product information and return extracted text.

    Returns a text blob of relevant product info, or empty string if
    search fails (app falls back to AI-only mode).
    """
    title = (product_title or "").strip()
    if not title:
        return ""

    # Clean up title for search — remove "Used", "El Guitar", etc.
    clean_title = title
    for word in ["Used", "used", "მეორადი", "El Guitar", "el guitar",
                 "Electric Guitar", "electric guitar"]:
        clean_title = clean_title.replace(word, "")
    clean_title = re.sub(r"\s+", " ", clean_title).strip()
    if not clean_title:
        clean_title = title

    query = f"{clean_title} specs features"
    try:
        results = _search_ddg(query, max_results=8)
    except Exception:
        return ""

    if not results:
        return ""

    # Filter to spec-worthy pages
    filtered = [r for r in results if _is_spec_page(r["url"], r["title"])]
    if not filtered:
        filtered = results[:max_pages]

    chunks: list[str] = []
    for item in filtered[:max_pages]:
        url = item["url"]
        try:
            html = _fetch_url(url, timeout=8)
            if not html:
                continue
            text = _html_to_text(html)
            if not text or len(text) < 100:
                continue
            # Take first 3000 chars of visible text
            snippet = text[:3000]
            chunks.append(f"--- Source: {item['title']} ({url}) ---\n{snippet}")
            time.sleep(0.3)  # polite delay
        except Exception:
            continue

    if not chunks:
        return ""

    web_text = "\n\n".join(chunks)
    # Truncate to ~8000 chars to keep OpenAI prompt reasonable
    if len(web_text) > 8000:
        web_text = web_text[:8000] + "\n[...truncated]"
    return web_text
