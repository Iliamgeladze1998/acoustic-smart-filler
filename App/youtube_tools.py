"""Resolve product title to a direct YouTube watch URL (not a search page)."""

from __future__ import annotations

import re
from typing import Any
from urllib.parse import parse_qs, quote_plus, urlparse, urlunparse
from urllib.request import Request, urlopen

WATCH_RE = re.compile(
    r"(?:youtube\.com/watch\?[^\"'\s]*v=|youtu\.be/|youtube\.com/embed/|youtube\.com/shorts/)([A-Za-z0-9_-]{6,})",
    re.I,
)
VIDEO_ID_RE = re.compile(r"(?:[?&]v=|/embed/|/shorts/|youtu\.be/)([A-Za-z0-9_-]{6,})")
HTML_VIDEO_ID_RE = re.compile(r'"videoId"\s*:\s*"([A-Za-z0-9_-]{11})"')


def is_youtube_search_url(url: str) -> bool:
    u = (url or "").lower()
    return "youtube.com/results" in u or "search_query=" in u


def is_youtube_watch_url(url: str) -> bool:
    if not url:
        return False
    if is_youtube_search_url(url):
        return False
    return bool(WATCH_RE.search(url) or VIDEO_ID_RE.search(url))


def extract_video_id(url: str) -> str | None:
    if not url:
        return None
    m = VIDEO_ID_RE.search(url)
    if m:
        return m.group(1)
    m = WATCH_RE.search(url)
    if m:
        return m.group(1)
    return None


def to_watch_url(video_id: str) -> str:
    vid = (video_id or "").strip()
    return f"https://www.youtube.com/watch?v={vid}" if vid else ""


def normalize_youtube_url(url: str) -> str:
    """Convert youtu.be / embed / shorts to watch?v= form. Pass through non-YT."""
    u = (url or "").strip()
    if not u:
        return ""
    if is_youtube_search_url(u):
        return u
    vid = extract_video_id(u)
    if vid:
        return to_watch_url(vid)
    return u


def _ddgs_youtube_url(query: str) -> str | None:
    """Best watch URL via DuckDuckGo (videos backend, then site: filter)."""
    q = (query or "").strip()
    if not q:
        return None
    try:
        from ddgs import DDGS  # type: ignore
    except Exception:
        try:
            from duckduckgo_search import DDGS  # type: ignore
        except Exception:
            return None

    def dig(items: list[Any]) -> str | None:
        for item in items or []:
            if not isinstance(item, dict):
                continue
            for key in ("content", "url", "href", "link", "embed_url", "iframe"):
                cand = str(item.get(key) or "").strip()
                if not cand:
                    continue
                if is_youtube_watch_url(cand) or extract_video_id(cand):
                    return normalize_youtube_url(cand)
                m = WATCH_RE.search(cand) or VIDEO_ID_RE.search(cand)
                if m:
                    return to_watch_url(m.group(1) if m.lastindex else m.group(0))
            # Sometimes title/body embeds a bare video id
            blob = " ".join(str(item.get(k) or "") for k in ("title", "body", "content", "url"))
            m = re.search(r"(?:v=|/watch\?v=)([A-Za-z0-9_-]{11})", blob)
            if m:
                return to_watch_url(m.group(1))
        return None

    try:
        with DDGS() as ddgs:
            # Prefer video search when available
            for method_name, args, kwargs in (
                ("videos", (q,), {"max_results": 8}),
                ("text", (f"{q} site:youtube.com",), {"max_results": 10}),
                ("text", (f"{q} youtube",), {"max_results": 10}),
            ):
                method = getattr(ddgs, method_name, None)
                if not callable(method):
                    continue
                try:
                    raw = list(method(*args, **kwargs) or [])
                except TypeError:
                    try:
                        raw = list(method(q) or [])
                    except Exception:
                        continue
                except Exception:
                    continue
                hit = dig(raw)
                if hit:
                    return hit
    except Exception:
        return None
    return None


def _scrape_youtube_search_page(query: str) -> str | None:
    """Parse first videoId from a YouTube results HTML page."""
    q = (query or "").strip()
    if not q:
        return None
    url = f"https://www.youtube.com/results?search_query={quote_plus(q)}"
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
        ),
        "Accept-Language": "en-US,en;q=0.9",
    }
    try:
        req = Request(url, headers=headers)
        with urlopen(req, timeout=18) as resp:
            html = resp.read().decode("utf-8", errors="ignore")
    except Exception:
        return None
    # Prefer watchEndpoint videoIds in initial data order
    ids = HTML_VIDEO_ID_RE.findall(html)
    # Skip obvious non-video or empty
    seen: set[str] = set()
    for vid in ids:
        if not vid or vid in seen or len(vid) != 11:
            continue
        seen.add(vid)
        return to_watch_url(vid)
    return None


def resolve_youtube_watch_url(
    title: str,
    *,
    existing_url: str = "",
    extra_query: str = "review",
) -> str:
    """
    Return a youtube.com/watch?v=… URL for the product.
    If existing_url is already a watch/embed/short link, normalize and return it.
    """
    existing = (existing_url or "").strip()
    if existing and is_youtube_watch_url(existing):
        return normalize_youtube_url(existing)

    title = (title or "").strip()
    # Pull query from search URL if that's what we got
    search_q = ""
    if existing and is_youtube_search_url(existing):
        try:
            qs = parse_qs(urlparse(existing).query)
            search_q = (qs.get("search_query") or [""])[0]
        except Exception:
            search_q = ""
    query = search_q or (f"{title} {extra_query}".strip() if title else "")
    if not query:
        return existing or ""

    for finder in (
        lambda: _ddgs_youtube_url(query),
        lambda: _ddgs_youtube_url(f"{title} official" if title else query),
        lambda: _scrape_youtube_search_page(query),
        lambda: _scrape_youtube_search_page(title) if title else None,
    ):
        try:
            hit = finder()
        except Exception:
            hit = None
        if hit and is_youtube_watch_url(hit):
            return normalize_youtube_url(hit)

    # Last resort: leave search URL only if nothing else worked
    return existing or (f"https://www.youtube.com/results?search_query={quote_plus(query)}")


def ensure_video_watch_urls(videos: list[Any], *, product_title: str) -> list[dict[str, Any]]:
    """Fix videos[] so url is a watch link when possible."""
    out: list[dict[str, Any]] = []
    for v in videos or []:
        if not isinstance(v, dict):
            continue
        item = dict(v)
        url = str(item.get("url") or "").strip()
        item["url"] = resolve_youtube_watch_url(
            product_title or str(item.get("title") or ""),
            existing_url=url,
        )
        if not item.get("provider"):
            item["provider"] = "youtube"
        out.append(item)
    return out
