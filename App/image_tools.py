"""Find product images online (DuckDuckGo only), download, and compress for upload.

No API keys beyond OpenAI (used only to craft the search query text).
"""

from __future__ import annotations

import hashlib
import io
import json
import re
import ssl
import time
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import Request, urlopen

from openai import OpenAI

CACHE_DIR = Path(__file__).resolve().parent / "image_cache"
MAX_BYTES = 400 * 1024  # 400 KB


def ensure_cache() -> Path:
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    return CACHE_DIR


def craft_image_search_query(
    *,
    api_key: str,
    model: str,
    product_title: str,
) -> str:
    """Use OpenAI to build a short product-photo search query from the title."""
    title = (product_title or "").strip()
    if not title:
        return ""

    client = OpenAI(api_key=api_key)
    try:
        resp = client.chat.completions.create(
            model=model,
            temperature=0.2,
            messages=[
                {
                    "role": "system",
                    "content": (
                        "You write short web image search queries for product photos. "
                        "Return ONLY the search query text, no quotes or explanation. "
                        "Prefer: brand + exact model only. Max 6 words. English. "
                        "Do NOT add words like official, product, photo, review."
                    ),
                },
                {"role": "user", "content": f"Product title: {title}"},
            ],
        )
        q = (resp.choices[0].message.content or "").strip().strip('"').strip("'")
        q = re.sub(r"\s+", " ", q)
        if q:
            return q
    except Exception:
        pass
    # Short default (long AI phrases rate-limit DDG more often)
    parts = re.findall(r"[A-Za-z0-9]+", title)
    return " ".join(parts[:5]) if parts else title


def _normalize_image_hits(raw_items: list, *, backend: str, query: str) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    seen: set[str] = set()
    for i, item in enumerate(raw_items or []):
        if not isinstance(item, dict):
            continue
        url = (
            item.get("image")
            or item.get("url")
            or item.get("image_url")
            or item.get("thumbnail")
            or item.get("thumbnail_src")
            or item.get("src")
            or ""
        )
        thumb = item.get("thumbnail") or item.get("thumbnail_src") or url
        page = item.get("url") or item.get("source") or item.get("page") or item.get("host") or ""
        title = item.get("title") or item.get("alt") or ""
        if not url or not str(url).startswith("http"):
            continue
        # DDG results sometimes put the page URL in "url" and image in "image"
        if item.get("image") and str(item.get("image")).startswith("http"):
            url = item.get("image")
            if item.get("url"):
                page = item.get("url")
        key = str(url).split("?")[0]
        if key in seen:
            continue
        seen.add(key)
        results.append(
            {
                "id": f"d_{backend}_{i}_{hashlib.md5(key.encode()).hexdigest()[:10]}",
                "url": url,
                "thumbnail": thumb,
                "page_url": page if str(page).startswith("http") else "",
                "title": title,
                "source": page or backend,
                "width": item.get("width"),
                "height": item.get("height"),
                "query": query,
                "backend": backend,
            }
        )
    return results


def _ddg_query_variants(query: str) -> list[str]:
    q = re.sub(r"\s+", " ", (query or "").strip())
    variants: list[str] = []
    if q:
        variants.append(q)
    cleaned = re.sub(
        r"\b(official|product|photo|photos|image|images|review|buy|price)\b",
        " ",
        q,
        flags=re.I,
    )
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    if cleaned and cleaned.lower() not in {v.lower() for v in variants}:
        variants.append(cleaned)
    parts = cleaned.split() if cleaned else q.split()
    if len(parts) > 4:
        short = " ".join(parts[:4])
        if short.lower() not in {v.lower() for v in variants}:
            variants.append(short)
    if len(parts) > 2:
        short2 = " ".join(parts[:2])
        if short2.lower() not in {v.lower() for v in variants}:
            variants.append(short2)
    return variants or [q]


def _ddg_search_cache_path(query: str) -> Path:
    ensure_cache()
    h = hashlib.md5(query.strip().lower().encode()).hexdigest()
    return CACHE_DIR / f"search_{h}.json"


def _load_ddg_search_cache(query: str, max_age_sec: int = 6 * 3600) -> list[dict[str, Any]] | None:
    path = _ddg_search_cache_path(query)
    if not path.is_file():
        return None
    try:
        if time.time() - path.stat().st_mtime > max_age_sec:
            return None
        data = json.loads(path.read_text(encoding="utf-8"))
        items = data.get("results") or []
        return items or None
    except Exception:
        return None


def _save_ddg_search_cache(query: str, results: list[dict[str, Any]]) -> None:
    try:
        path = _ddg_search_cache_path(query)
        path.write_text(
            json.dumps({"query": query, "results": results}, ensure_ascii=False),
            encoding="utf-8",
        )
    except Exception:
        pass


def _get_ddgs_class():
    """Prefer new `ddgs` package; fall back to renamed package silently."""
    try:
        from ddgs import DDGS  # type: ignore

        return DDGS, "ddgs"
    except ImportError:
        pass
    # Suppress rename RuntimeWarning from the old package name
    import warnings

    with warnings.catch_warnings():
        warnings.simplefilter("ignore", RuntimeWarning)
        try:
            from duckduckgo_search import DDGS  # type: ignore

            return DDGS, "duckduckgo_search"
        except ImportError as exc:
            raise RuntimeError(
                "Image search package missing. Run SETUP.bat (needs: pip install ddgs)."
            ) from exc


def _search_duckduckgo_once(query: str, max_results: int, region: str = "wt-wt") -> list[dict[str, Any]]:
    """Single DuckDuckGo images call via `ddgs` (no retry storm)."""
    DDGS, pkg = _get_ddgs_class()
    try:
        client = DDGS(timeout=25)
    except TypeError:
        client = DDGS()

    raw: list = []
    # New API: images(query, **kwargs). Old API: images(keywords=...).
    try:
        raw = list(
            client.images(
                query,
                region=region,
                safesearch="off",
                max_results=max_results,
            )
        )
    except TypeError:
        try:
            raw = list(
                client.images(
                    keywords=query,
                    region=region,
                    safesearch="off",
                    max_results=max_results,
                )
            )
        except Exception:
            raw = list(client.images(query, max_results=max_results))

    return _normalize_image_hits(raw, backend=f"{pkg}:{region}", query=query)


def _search_duckduckgo(query: str, max_results: int = 18) -> list[dict[str, Any]]:
    """
    DuckDuckGo image search (no extra APIs).
    Limited attempts so CMD does not loop: cache → short query → one alternate.
    """
    query = (query or "").strip()
    if not query:
        return []

    cached = _load_ddg_search_cache(query)
    if cached:
        return cached[:max_results]

    variants = _ddg_query_variants(query)[:2]  # at most 2 query shapes
    errors: list[str] = []

    for q in variants:
        cached_v = _load_ddg_search_cache(q)
        if cached_v:
            _save_ddg_search_cache(query, cached_v)
            return cached_v[:max_results]

        try:
            hits = _search_duckduckgo_once(q, max_results=max_results, region="wt-wt")
            if hits:
                _save_ddg_search_cache(query, hits)
                _save_ddg_search_cache(q, hits)
                return hits
            errors.append(f"{q!r}: empty")
        except Exception as exc:
            msg = str(exc)
            errors.append(f"{q!r}: {msg}")
            # single short pause only on rate-limit, then try next variant once
            if "403" in msg or "ratelimit" in msg.lower():
                time.sleep(2.5)
            continue

    # One last try with a 5s cool-down if everything looked rate-limited
    if any("403" in e or "ratelimit" in e.lower() for e in errors):
        time.sleep(5)
        q = variants[-1]
        try:
            hits = _search_duckduckgo_once(q, max_results=max_results, region="us-en")
            if hits:
                _save_ddg_search_cache(query, hits)
                return hits
        except Exception as exc:
            errors.append(f"final: {exc}")

    path = _ddg_search_cache_path(query)
    if path.is_file():
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            items = data.get("results") or []
            if items:
                return items[:max_results]
        except Exception:
            pass

    detail = " | ".join(errors[-4:]) if errors else "unknown"
    raise RuntimeError(
        "DuckDuckGo rate-limited or returned no images.\n\n"
        "Wait about a minute, use a short query (e.g. EV PRO 780), try again.\n"
        f"Details: {detail}"
    )


def search_product_images(
    query: str,
    max_results: int = 18,
    *,
    google_api_key: str = "",
    google_cse_id: str = "",
    backend: str = "duckduckgo",
) -> list[dict[str, Any]]:
    """Find product images via DuckDuckGo only (extra API args ignored)."""
    _ = google_api_key, google_cse_id, backend
    query = (query or "").strip()
    if not query:
        return []
    return _search_duckduckgo(query, max_results=max_results)



def download_bytes(
    url: str,
    timeout: float = 25.0,
    *,
    referer: str | None = None,
) -> bytes:
    """Download image bytes with browser-like headers."""
    if not url or not str(url).startswith(("http://", "https://")):
        raise RuntimeError(f"Invalid image URL: {url!r}")

    parsed = urlparse(url)
    origin = f"{parsed.scheme}://{parsed.netloc}/"

    header_sets = [
        {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
            ),
            "Accept": "image/avif,image/webp,image/apng,image/*,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.9",
            "Referer": referer or origin,
            "Sec-Fetch-Dest": "image",
            "Sec-Fetch-Mode": "no-cors",
            "Sec-Fetch-Site": "cross-site",
        },
        {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
            ),
            "Accept": "*/*",
            "Referer": "https://duckduckgo.com/",
        },
        {
            "User-Agent": "Mozilla/5.0 (compatible; AcousticSmartFiller/1.0)",
            "Accept": "*/*",
        },
    ]

    try:
        import requests  # type: ignore

        for headers in header_sets:
            try:
                resp = requests.get(url, headers=headers, timeout=timeout, allow_redirects=True)
                if resp.status_code == 200 and resp.content and len(resp.content) >= 32:
                    ctype = (resp.headers.get("Content-Type") or "").lower()
                    if "html" in ctype and len(resp.content) < 5000:
                        continue
                    return resp.content
            except Exception:
                continue
    except ImportError:
        pass

    ctx = ssl.create_default_context()
    last_err: Exception | None = None
    for headers in header_sets:
        try:
            req = Request(url, headers=headers, method="GET")
            with urlopen(req, timeout=timeout, context=ctx) as resp:
                data = resp.read()
            if data and len(data) >= 32:
                return data
            last_err = RuntimeError("Empty image download")
        except (HTTPError, URLError, Exception) as exc:
            last_err = exc
            continue
    raise RuntimeError(f"HTTP download failed ({last_err})")


def _save_jpeg_under_limit(img, dest: Path, max_bytes: int = MAX_BYTES) -> Path:
    from PIL import Image

    if img.mode not in ("RGB", "L"):
        img = img.convert("RGB")
    elif img.mode == "L":
        img = img.convert("RGB")

    qualities = [92, 88, 85, 82, 78, 74, 70, 65, 60, 55]
    scales = [1.0, 0.92, 0.85, 0.78, 0.7, 0.62, 0.55, 0.48]
    best: bytes | None = None
    for scale in scales:
        work = (
            img.resize(
                (max(1, int(img.width * scale)), max(1, int(img.height * scale))),
                Image.Resampling.LANCZOS,
            )
            if scale < 1.0
            else img
        )
        for q in qualities:
            buf = io.BytesIO()
            work.save(
                buf,
                format="JPEG",
                quality=q,
                optimize=True,
                progressive=True,
                subsampling=0 if q >= 80 else 2,
            )
            data = buf.getvalue()
            if best is None or len(data) < len(best):
                best = data
            if len(data) <= max_bytes:
                dest.write_bytes(data)
                return dest
    if best is None:
        raise RuntimeError("Could not encode image")
    dest.write_bytes(best)
    return dest


def prepare_image_for_upload(
    source_url: str,
    *,
    image_id: str,
    max_bytes: int = MAX_BYTES,
    alternate_urls: list[str] | None = None,
    page_url: str | None = None,
) -> dict[str, Any]:
    from PIL import Image

    ensure_cache()
    raw_path = CACHE_DIR / f"{image_id}_raw.bin"
    out_path = (CACHE_DIR / f"{image_id}_upload.jpg").resolve()

    candidates: list[str] = []
    for u in [source_url, *(alternate_urls or [])]:
        u = (u or "").strip()
        if u and u not in candidates and u.startswith("http"):
            candidates.append(u)

    raw: bytes | None = None
    used_url = source_url
    errors: list[str] = []

    if raw_path.is_file() and raw_path.stat().st_size >= 32:
        raw = raw_path.read_bytes()
    else:
        for u in candidates:
            try:
                ref = page_url or f"{urlparse(u).scheme}://{urlparse(u).netloc}/"
                raw = download_bytes(u, referer=ref)
                used_url = u
                raw_path.write_bytes(raw)
                break
            except Exception as exc:
                errors.append(str(exc))
                raw = None

    if not raw:
        thumb = CACHE_DIR / f"{image_id}_thumb.jpg"
        if thumb.is_file() and thumb.stat().st_size >= 32:
            raw = thumb.read_bytes()
            used_url = str(thumb)
        else:
            raise RuntimeError(
                "Could not download image (host blocked the request).\n"
                "Select another result.\n" + "; ".join(errors[:3])
            )

    img = Image.open(io.BytesIO(raw))
    img.load()
    if len(raw) <= max_bytes and (img.format or "").upper() in ("JPEG", "JPG"):
        if img.mode not in ("RGB", "L"):
            img = img.convert("RGB")
        img.save(out_path, format="JPEG", quality=90, optimize=True, progressive=True)
        if out_path.stat().st_size <= max_bytes:
            return {
                "path": str(out_path),
                "bytes": out_path.stat().st_size,
                "width": img.width,
                "height": img.height,
                "source_url": used_url,
            }

    _save_jpeg_under_limit(img, out_path, max_bytes=max_bytes)
    final = Image.open(out_path)
    return {
        "path": str(out_path.resolve()),
        "bytes": out_path.stat().st_size,
        "width": final.width,
        "height": final.height,
        "source_url": used_url,
        "was_over_limit": len(raw) > max_bytes,
    }


def download_thumbnail_preview(
    url: str,
    image_id: str,
    max_side: int = 160,
    *,
    alternate_urls: list[str] | None = None,
    page_url: str | None = None,
) -> Path | None:
    from PIL import Image

    ensure_cache()
    dest = CACHE_DIR / f"{image_id}_thumb.jpg"
    if dest.is_file() and dest.stat().st_size > 0:
        return dest

    candidates: list[str] = []
    for u in [url, *(alternate_urls or [])]:
        u = (u or "").strip()
        if u and u not in candidates and u.startswith("http"):
            candidates.append(u)

    for u in candidates:
        try:
            ref = page_url or f"{urlparse(u).scheme}://{urlparse(u).netloc}/"
            raw = download_bytes(u, timeout=15.0, referer=ref)
            img = Image.open(io.BytesIO(raw))
            img.load()
            img.thumbnail((max_side, max_side), Image.Resampling.LANCZOS)
            if img.mode not in ("RGB", "L"):
                img = img.convert("RGB")
            img.save(dest, format="JPEG", quality=80, optimize=True)
            raw_path = CACHE_DIR / f"{image_id}_raw.bin"
            if len(raw) >= 8 * 1024 and (
                not raw_path.is_file() or raw_path.stat().st_size < len(raw)
            ):
                raw_path.write_bytes(raw)
            return dest
        except Exception:
            continue
    return None
