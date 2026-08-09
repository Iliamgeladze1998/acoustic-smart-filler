"""
Disk + memory cache for stable store catalog data (categories, feature variants).

Product-specific fields (name, description, selected values) are never cached —
only shared option lists that are identical across product edits for the same shop.
"""

from __future__ import annotations

import hashlib
import json
import time
from pathlib import Path
from typing import Any

CACHE_DIR = Path(__file__).resolve().parent / "scrape_cache"
# Category tree changes rarely; feature brand/variant lists change slowly.
CATEGORIES_TTL_S = 72 * 3600  # 3 days
FEATURES_TTL_S = 48 * 3600  # 2 days
_MIN_CATEGORY_ITEMS = 8
_MIN_FEATURE_HUMANS = 2

# In-process mirrors (faster than disk on 2nd+ product in same app run)
_mem_categories: dict[str, list[dict[str, Any]]] = {}
_mem_features: dict[str, dict[str, list[dict[str, Any]]]] = {}


def _ensure_dir() -> Path:
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    return CACHE_DIR


def shop_key(admin_base_url: str) -> str:
    """Stable short key per admin/shop host."""
    raw = (admin_base_url or "default").strip().lower()
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:16]


def _cat_path(key: str) -> Path:
    return _ensure_dir() / f"categories_{key}.json"


def _feat_path(key: str) -> Path:
    return _ensure_dir() / f"features_{key}.json"


def _tree_quality(items: list | None) -> tuple[int, int]:
    if not items:
        return (0, 0)
    n = len(items)
    with_parent = sum(
        1 for x in items if isinstance(x, dict) and str(x.get("parent_id") or "").strip()
    )
    return (with_parent, n)


def categories_are_usable(items: list | None) -> bool:
    wp, n = _tree_quality(items)
    if n < _MIN_CATEGORY_ITEMS:
        return False
    # Real hierarchy required (parent→sub), not flat dump only
    return wp >= max(4, int(n * 0.10))


def load_categories(admin_base_url: str) -> list[dict[str, Any]] | None:
    key = shop_key(admin_base_url)
    mem = _mem_categories.get(key)
    if mem and categories_are_usable(mem):
        return [dict(x) for x in mem]

    path = _cat_path(key)
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None
    if not isinstance(data, dict):
        return None
    ts = float(data.get("saved_at") or 0)
    if ts and (time.time() - ts) > CATEGORIES_TTL_S:
        return None
    items = data.get("items")
    if not isinstance(items, list) or not categories_are_usable(items):
        return None
    clean = [dict(x) for x in items if isinstance(x, dict)]
    _mem_categories[key] = clean
    return [dict(x) for x in clean]


def save_categories(admin_base_url: str, items: list[dict[str, Any]]) -> None:
    if not categories_are_usable(items):
        return
    key = shop_key(admin_base_url)
    clean = [dict(x) for x in items if isinstance(x, dict)]
    _mem_categories[key] = clean
    payload = {
        "saved_at": time.time(),
        "admin": admin_base_url,
        "count": len(clean),
        "with_parent": _tree_quality(clean)[0],
        "items": clean,
    }
    try:
        path = _cat_path(key)
        path.write_text(
            json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
            encoding="utf-8",
        )
    except Exception:
        pass


def load_feature_option_map(admin_base_url: str) -> dict[str, list[dict[str, Any]]]:
    """feature_id / field key → list of {value, label} with human labels."""
    key = shop_key(admin_base_url)
    if key in _mem_features and _mem_features[key]:
        return {
            k: [dict(o) for o in v]
            for k, v in _mem_features[key].items()
            if isinstance(v, list)
        }

    path = _feat_path(key)
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    if not isinstance(data, dict):
        return {}
    ts = float(data.get("saved_at") or 0)
    if ts and (time.time() - ts) > FEATURES_TTL_S:
        return {}
    feats = data.get("features")
    if not isinstance(feats, dict):
        return {}
    out: dict[str, list[dict[str, Any]]] = {}
    for fk, opts in feats.items():
        if not isinstance(opts, list):
            continue
        cleaned = []
        for o in opts:
            if not isinstance(o, dict):
                continue
            lab = str(o.get("label") or "").strip()
            val = str(o.get("value") or "").strip()
            if not lab or lab.isdigit():
                continue
            cleaned.append({"value": val or lab, "label": lab})
        if len(cleaned) >= _MIN_FEATURE_HUMANS:
            out[str(fk)] = cleaned
    _mem_features[key] = out
    return {k: [dict(o) for o in v] for k, v in out.items()}


def merge_and_save_feature_options(
    admin_base_url: str,
    discovered: dict[str, list[dict[str, Any]]],
) -> None:
    """Merge new feature option lists into disk/memory (keep union, prefer human labels)."""
    if not discovered:
        return
    key = shop_key(admin_base_url)
    existing = load_feature_option_map(admin_base_url)
    for fk, opts in discovered.items():
        if not fk or not isinstance(opts, list):
            continue
        by_v: dict[str, dict] = {}
        for src in (existing.get(fk) or []) + opts:
            if not isinstance(src, dict):
                continue
            lab = str(src.get("label") or "").strip()
            val = str(src.get("value") or "").strip()
            if not lab:
                continue
            # Prefer non-numeric human labels
            prev = by_v.get(val or lab)
            if not prev:
                by_v[val or lab] = {"value": val or lab, "label": lab}
            else:
                pl = str(prev.get("label") or "")
                if pl.isdigit() and lab and not lab.isdigit():
                    prev["label"] = lab
        cleaned = list(by_v.values())
        humans = [o for o in cleaned if not str(o.get("label") or "").isdigit()]
        if len(humans) >= _MIN_FEATURE_HUMANS:
            existing[str(fk)] = humans if len(humans) >= len(cleaned) // 2 else cleaned

    _mem_features[key] = existing
    payload = {
        "saved_at": time.time(),
        "admin": admin_base_url,
        "feature_count": len(existing),
        "features": existing,
    }
    try:
        path = _feat_path(key)
        path.write_text(
            json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
            encoding="utf-8",
        )
    except Exception:
        pass


def clear_memory() -> None:
    _mem_categories.clear()
    _mem_features.clear()
