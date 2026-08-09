"""OpenAI-powered product field generation for Acoustic Smart Filler."""

from __future__ import annotations

import json
import re
from typing import Any

from openai import OpenAI

from youtube_tools import ensure_video_watch_urls, is_youtube_search_url, resolve_youtube_watch_url


SYSTEM_PROMPT = """You are a product data specialist for the Acoustic.ge CS-Cart store (audio / pro-audio retail in Georgia).
Return ONLY valid JSON matching the schema. Do not wrap in markdown.

LANGUAGE (MANDATORY):
- Write ALL customer-facing text in GEORGIAN (ქართული): full_description, promo_text, page_title,
  meta_description, meta_keywords, video title, video description, notes_for_user, and any free-text feature values you invent.
- Headings inside HTML must also be Georgian (e.g. ტექნიკური მახასიათებლები, უპირატესობები).
- Keep brand names, model numbers, unit symbols (Hz, dB, XLR), and latin seo_name as-is.
- Do NOT write product descriptions in English or Russian unless a field is explicitly not for customers.

PRIMARY SOURCE: product_title (the CS-Cart product Name / model line).
You must INFER from the title: brand, product type, intended use, and which store categories fit.
Do not invent a completely different product.

======== CATEGORIES (CRITICAL) ========
- page_context.category_tree shows the FULL hierarchical category tree (indented).
- page_context.category_catalog has id+label+path+parent_id for each category.
- Read the tree to understand the structure BEFORE choosing.
- Pick the ONE most specific leaf category that best matches the product type.
- Return EXACT labels from category_catalog (copy spelling character-for-character).
- ALWAYS pick the deepest leaf (subcategory), never a vague top-level parent when children exist.
- Examples for acoustic.ge-style trees:
  • Electric guitar title → leaf like „ელექტრო“ under „კატეგორია: გიტარა“
    NEVER: bass, acoustic, classical, tools, mics, DJ, mixers, amps alone.
  • Acoustic guitar → „აკუსტიკური“ / acoustic leaf only
  • Classical guitar → „კლასიკური“ / classical leaf only
  • Bass guitar → „ბას-გიტარა“ leaf only (NOT cabinets, NOT effects, NOT accessories)
  • Bass cabinet/amp → „გამაძლიერებელი/კომბი“ under „ბასი“
  • Microphone → „მიკროფონები“ leaf only
  • Guitar effect/pedal → „ეფექტები“ under „კატეგორია: გიტარა“ (NOT „ელექტრო“ — that's for guitars!)
  • Keyboard stand → „კლავიშის“ under „სადგამები“
  • Mixer → mixer/console leaf only
- If no catalog label is a clear, exact fit for this product type, return [] (empty) — do NOT guess.
- NEVER invent category names missing from category_catalog.

======== FEATURES / SPECS (CRITICAL) ========
- page_context.feature_catalog lists form controls with their option labels/ids from the page.
- For EVERY feature except ავტორი/Author, pick the best value based on product_title.
- ESPECIALLY critical:
  • ბრენდი / Brand — must match the brand inferred from the product title (e.g. "EV PRO 780" → Electro-Voice / EV option if present).
  • მდგომარეობა / Condition — if product_title contains "Used" or "მეორადი", you MUST set მდგომარეობა to the option "მეორადი" (exact catalog label). Otherwise use the best fitting option.
  • Other selectable specs: choose the closest option label from each feature's options list.
- SELECT features: value MUST be an EXACT option.label from that feature's options (preferred) or option value id if labels missing.
- Do NOT invent brand names that are not in the brand options list when options are provided — pick the closest catalog brand.
- Free-text features only: Georgian text, short.
- Return feature_values for as many features as possible:
  { "id", "field_name", "label", "value" }  value = exact option label when possible
- Also return features map label -> value.

features note — ავტორი / Author:
- Do NOT set "ავტორი" / "Author". The app always fills the logged-in admin user.

full_description (CRITICAL — do not write short blurbs):
- Entire body in Georgian.
- HTML only with: p, ul, li, strong, br, h3 (no scripts, no styles).
- REQUIRED structure:
  1) Opening paragraph (2–4 sentences) about what the product is and who it is for, based on the title.
  2) A Georgian heading then a SPECIFICATIONS bullet list with AT LEAST 8 items (prefer 10–14).
  3) A short Georgian "უპირატესობები" bullet list (4–6 items).
  4) A closing paragraph in Georgian.
- Minimum length: roughly 180–350 words of visible text. Always include the specs <ul> list.

promo_text: 1–2 short Georgian paragraphs or a small bullet list (HTML), marketing-focused.

SEO (Georgian text; seo_name stays latin):
- page_title ~50–65 chars, meta_description ~140–160 chars, meta_keywords 6–12 Georgian terms, seo_name latin slug.

product_name: clean up the product_title — fix capitalization, spacing, and formatting.
  Example: "ibanez gio gax-70 black" → "Ibanez Gio GAX-70 Black". Keep brand names properly capitalized.
  Do NOT change the actual product name, just fix formatting.
price: if page_context.price is non-empty, KEEP that exact string. Else "".
old_price: check page_context.old_price and page_context.price:
  - If old_price is "0" or empty AND price is non-empty AND numeric price < 100 (GEL):
    set old_price = price (copy the same value).
  - If old_price is "0" or empty AND price >= 100 (GEL):
    leave old_price empty ("") and add a note in notes_for_user:
    "ძველი ფასი ცარიელია — მიუთითეთ ხელით."
  - If old_price already has a non-zero value: keep it as-is.
tags: return 3-8 relevant tags in Georgian (comma-separated string) based on the product.
  Examples: "ელექტრო გიტარა, გიტარა, Ibanez, დამწყები, მუსიკალური ინსტრუმენტი".

videos (AB Video gallery):
- Always return at least 1 video entry derived from the product title.
- url: prefer youtube watch?v= if known; else "" (app resolves). Never search results URLs.
  NEVER return placeholder URLs like watch?v=VIDEO_ID, watch?v=XXXX, watch?v=YOUR_ID.
- title + description in Georgian. provider youtube, status A, position 0.

CATEGORIES extra rules:
- MANDATORY: Always include "FINA" category if it exists in category_catalog, for EVERY product.
- GIFT category: If the product is a small, attractive item suitable as a gift (e.g. picks, straps,
  capos, tuners, small accessories, cables, stands, kazoos, harmonicas, small pedals, etc.),
  also include "აჩუქე" category if it exists in category_catalog. Use judgment: large/expensive
  items (guitars, keyboards, mixers, PA systems) are NOT gift items.

Do NOT invent local image paths. Never invent stock/SKU banking data.
"""


def _schema_hint() -> dict[str, Any]:
    return {
        "product_name": "cleaned up product_title",
        "price": "keep page price if present",
        "old_price": "if old_price is 0/empty and price < 100, copy price; else keep existing",
        "tags": "3-8 Georgian tags, comma-separated",
        "full_description": "LONG Georgian HTML: intro + ul specs (8+) + უპირატესობები + closing",
        "promo_text": "short Georgian HTML",
        "page_title": "Georgian string",
        "meta_description": "Georgian string",
        "meta_keywords": "Georgian terms, comma-separated",
        "seo_name": "latin slug",
        "categories": ["EXACT label from category_catalog", "..."],
        "features": {"ბრენდი": "exact option label", "მდგომარეობა": "exact option label"},
        "feature_values": [
            {
                "id": "feature id",
                "field_name": "exact name attribute from scan",
                "label": "ბრენდი or other feature label",
                "value": "EXACT option label from feature_catalog",
            }
        ],
        "videos": [
            {
                "url": "https://www.youtube.com/watch?v=… or empty",
                "title": "Georgian title",
                "description": "Georgian description",
                "provider": "youtube",
                "position": 0,
                "status": "A",
            }
        ],
        "notes_for_user": "Georgian notes if any",
    }


def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", str(s or "").lower()).strip()


def _is_author_label(label: str) -> bool:
    n = _norm(label)
    return "ავტორ" in n or n in ("author", "authors")


def _is_brand_label(label: str) -> bool:
    n = _norm(label)
    return "ბრენდ" in n or n in ("brand", "brands", "make", "manufacturer")


def _is_condition_label(label: str) -> bool:
    n = _norm(label)
    return "მდგომარეობ" in n or "condition" in n or "state" == n


def _title_indicates_used(title: str) -> bool:
    """Product title means second-hand / used gear."""
    t = f" {_norm(title)} "
    if not t.strip():
        return False
    # Latin "used" as whole word; Georgian "მეორადი" anywhere
    if re.search(r"(?<![a-z0-9])used(?![a-z0-9])", t):
        return True
    if "მეორადი" in t:
        return True
    if re.search(r"(?<![a-z0-9])second[\s\-]*hand(?![a-z0-9])", t):
        return True
    return False


def _pick_used_condition_option(options: list[dict]) -> dict | None:
    """Prefer catalog option მეორადი / Used for condition feature."""
    if not options:
        return None
    ranked: list[tuple[int, dict]] = []
    for o in options:
        if not isinstance(o, dict):
            continue
        lab = _norm(o.get("label") or "")
        val = _norm(o.get("value") or "")
        blob = f"{lab} {val}"
        score = 0
        if "მეორადი" in blob:
            score = 100
        elif re.search(r"(?<![a-z0-9])used(?![a-z0-9])", blob):
            score = 90
        elif "second" in blob and "hand" in blob:
            score = 80
        elif "preowned" in blob or "pre-owned" in blob:
            score = 70
        if score:
            ranked.append((score, o))
    if not ranked:
        return None
    ranked.sort(key=lambda x: -x[0])
    return ranked[0][1]


def _compact_feature_catalog(features: list, *, max_features: int = 40, max_opts: int = 180) -> list[dict]:
    out: list[dict] = []
    for feat in (features or [])[:max_features]:
        if not isinstance(feat, dict):
            continue
        label = str(feat.get("label") or feat.get("name") or "").strip()
        if _is_author_label(label):
            # Skip author — app fills logged-in user
            continue
        opts = feat.get("options") or []
        # Brand lists can be huge — keep more; still cap for prompt size
        cap = max_opts
        if _is_brand_label(label):
            cap = max(max_opts, 250)
        opt_out = []
        seen: set[str] = set()
        for o in opts:
            if not isinstance(o, dict):
                continue
            lab = str(o.get("label") or "").strip()
            val = str(o.get("value") or "").strip()
            if not lab and not val:
                continue
            if lab.isdigit() and not o.get("selected"):
                continue
            key = val + "|" + lab
            if key in seen:
                continue
            seen.add(key)
            opt_out.append({"value": val, "label": lab or val})
            if len(opt_out) >= cap:
                break
        out.append(
            {
                "id": str(feat.get("id") or ""),
                "label": label,
                "field_name": str(feat.get("field_name") or ""),
                "selection_mode": str(
                    feat.get("selection_mode") or feat.get("kind") or ("multi" if feat.get("multiple") else "single")
                ),
                "options": opt_out,
                "options_count": len(opts) if isinstance(opts, list) else 0,
            }
        )
    return out


def _category_tree_summary(catalog: list[dict]) -> str:
    """Build a compact hierarchical category tree string for the AI prompt."""
    if not catalog:
        return ""
    by_id: dict[str, dict] = {}
    for c in catalog:
        cid = str(c.get("id") or "").strip()
        if cid:
            by_id[cid] = c
    # Roots = entries with no parent_id
    roots: list[dict] = []
    children_of: dict[str, list[dict]] = {}
    for c in catalog:
        pid = str(c.get("parent_id") or "").strip()
        if pid and pid in by_id:
            children_of.setdefault(pid, []).append(c)
        else:
            roots.append(c)
    lines: list[str] = []
    def _walk(node: dict, depth: int) -> None:
        lab = str(node.get("label") or "").strip()
        if not lab:
            return
        lines.append(f"{'  ' * depth}{lab}")
        cid = str(node.get("id") or "").strip()
        for child in children_of.get(cid, []):
            _walk(child, depth + 1)
    for root in sorted(roots, key=lambda x: str(x.get("label") or "")):
        _walk(root, 0)
    return "\n".join(lines)


def _compact_category_catalog(page_context: dict, *, max_items: int = 2500) -> list[dict]:
    raw = page_context.get("available_category_options") or []
    out: list[dict] = []
    seen: set[str] = set()
    if isinstance(raw, list):
        for it in raw:
            if not isinstance(it, dict):
                continue
            lab = str(it.get("label") or "").strip()
            val = str(it.get("value") or it.get("id") or "").strip()
            if not lab:
                continue
            key = val + "|" + lab
            if key in seen:
                continue
            seen.add(key)
            out.append(
                {
                    "id": val or lab,
                    "label": lab,
                    "parent_id": str(it.get("parent_id") or "").strip(),
                    "path": str(it.get("path") or lab).strip(),
                }
            )
            if len(out) >= max_items:
                break
    if not out:
        for name in page_context.get("available_categories") or []:
            s = str(name).strip()
            if s and s not in seen:
                seen.add(s)
                out.append({"id": s, "label": s, "parent_id": "", "path": s})
            if len(out) >= max_items:
                break
    return out


def _best_option_match(want: str, options: list[dict]) -> dict | None:
    """Snap an AI (or title) string onto the closest catalog option."""
    w = _norm(want)
    if not w or not options:
        return None
    # Exact label / value
    for o in options:
        lab = _norm(o.get("label") or "")
        val = str(o.get("value") or "")
        if lab == w or val == str(want).strip():
            return o
    # Label contains want or want contains label (brand abbreviations)
    best = None
    best_score = 0
    w_tokens = [t for t in re.split(r"[^a-z0-9\u10a0-\u10ff]+", w) if len(t) > 1]
    for o in options:
        lab = _norm(o.get("label") or "")
        if not lab or lab.isdigit():
            continue
        score = 0
        if lab in w or w in lab:
            score = 50 + min(len(lab), 20)
        else:
            l_tokens = [t for t in re.split(r"[^a-z0-9\u10a0-\u10ff]+", lab) if len(t) > 1]
            if w_tokens and l_tokens:
                common = set(w_tokens) & set(l_tokens)
                if common:
                    score = 10 * len(common) + max(len(t) for t in common)
                # Prefix match (EV ↔ Electro-Voice less reliable — token EV)
                for wt in w_tokens:
                    for lt in l_tokens:
                        if wt == lt or (len(wt) >= 2 and lt.startswith(wt)) or (
                            len(lt) >= 2 and wt.startswith(lt)
                        ):
                            score = max(score, 8 + min(len(wt), len(lt)))
        if score > best_score:
            best_score = score
            best = o
    return best if best_score >= 8 else None


def _guess_brand_from_title(title: str, options: list[dict]) -> dict | None:
    """Heuristic: pick catalog brand that appears in the product title."""
    t = _norm(title)
    if not t or not options:
        return None
    best = None
    best_score = 0
    for o in options:
        lab = str(o.get("label") or "").strip()
        nlab = _norm(lab)
        if not nlab or nlab.isdigit() or len(nlab) < 2:
            continue
        score = 0
        if nlab in t:
            score = 100 + len(nlab)
        else:
            # Token match: "electro-voice" vs "electro voice" vs EV
            for tok in re.split(r"[\s\-/]+", nlab):
                if len(tok) >= 3 and tok in t:
                    score = max(score, 40 + len(tok))
            # Common short brands
            if nlab in ("ev", "shure", "sennheiser", "akg", "rode", "yamaha", "boss", "behringer"):
                if re.search(rf"(?:^|[\s\-_/]){re.escape(nlab)}(?:$|[\s\-_/0-9])", t):
                    score = max(score, 90)
        if score > best_score:
            best_score = score
            best = o
    return best if best_score >= 40 else None


def _snap_categories_to_catalog(cats: Any, catalog: list[dict]) -> list[str]:
    """EXACT catalog labels only — no fuzzy / partial category snapping."""
    if not isinstance(cats, list) or not catalog:
        return []
    by_norm: dict[str, str] = {}
    for x in catalog:
        lab = str(x.get("label") or "").strip()
        if lab:
            by_norm[_norm(lab)] = lab
    labels_out: list[str] = []
    seen: set[str] = set()
    for c in cats:
        if isinstance(c, dict):
            want = str(c.get("label") or c.get("name") or c.get("value") or "").strip()
        else:
            want = str(c or "").strip()
        if not want:
            continue
        hit = by_norm.get(_norm(want))
        if not hit:
            continue
        key = _norm(hit)
        if key in seen:
            continue
        seen.add(key)
        labels_out.append(hit)
    return labels_out


# Product-type → category keywords (Georgian + English store names)
# Blocks stop nonsense picks like „ხელსაწყოები“ for an electric guitar.
_GUITAR_TITLE_SIGNAL = re.compile(
    r"guitar|გიტარ|strat(?:ocaster)?|telecaster|les\s*paul|superstrat|"
    r"hollow\s*body|solid\s*body|\bgio\b|\bgax\b|\brg\d|\bs?g\d{2,}",
    re.I,
)

_PRODUCT_KIND_RULES: list[dict[str, Any]] = [
    {
        "kind": "electric_guitar",
        "title_any": [
            r"electric\s*guitar",
            r"ელექტრო\s*გიტარ",
            r"ელ\.?\s*გიტარ",
            r"strat(?:ocaster)?",
            r"telecaster",
            r"les\s*paul",
            r"superstrat",
            r"hollow\s*body",
            r"solid\s*body",
            r"\bgio\b",
            r"\bgax\b",
            r"\brg\d",
            r"\bs?g\d{2,}",
            # brand + guitar signal handled in _infer_product_kind
            r"\bibanez\b",
            r"\bepiphone\b",
            r"\bfender\b",
            r"\bgibson\b",
            r"\bjackson\b",
            r"\bcharvel\b",
            r"\bsquier\b",
            r"\bprs\b",
            r"\besp\b",
            r"\bschecter\b",
        ],
        "require_guitar_signal_for": [
            r"\bibanez\b",
            r"\bepiphone\b",
            r"\bfender\b",
            r"\bgibson\b",
            r"\bjackson\b",
            r"\bcharvel\b",
            r"\bsquier\b",
            r"\bprs\b",
            r"\besp\b",
            r"\bschecter\b",
            r"\byamaha\b",
        ],
        "title_must_not": [
            r"bass\s*guitar",
            r"ბას\s*გიტარ",
            r"(?:^|[\s\-/])bass(?:$|[\s\-/0-9])",
            r"acoustic\s*guitar",
            r"აკუსტიკ",
            r"classical\s*guitar",
            r"კლასიკურ",
            r"amplifier",
            r"amp\b",
            r"გამაძლიერებ",
            r"pedal",
            r"effect",
            r"cab(?:inet)?\b",
        ],
        "prefer": [
            "ელექტრო გიტარ",
            "electric guitar",
            "electric guitars",
            "ელექტრო",
            "electric",
        ],
        "want": ["ელექტრო", "electric"],
        "block": [
            "ხელსაწყო",
            "tool",
            "tools",
            "power tool",
            "ბურღ",
            "drill",
            "მიკროფონ",
            "microphone",
            " mic",
            "მიქსერ",
            "mixer",
            "დრამ",
            "drum",
            "პიანინ",
            "piano",
            "keyboard",
            "კლავიშ",
            "dj",
            "გადახდ",
            "ტრანსპორტ",
            "ბას გიტარ",
            "ბასი გიტარ",
            "bass guitar",
            "bass guitars",
            "bass",
            "ბასი",
            " ukulele",
            "უკულელ",
            "აკუსტიკ",
            "acoustic",
            "კლასიკურ",
            "classical",
            "amplifier",
            "გამაძლიერებ",
            "pedal",
            "effect",
        ],
    },
    {
        "kind": "acoustic_guitar",
        "title_any": [
            r"acoustic\s*guitar",
            r"აკუსტიკ.{0,12}გიტარ",
            r"folk\s*guitar",
            r"steel\s*string",
        ],
        "title_must_not": [
            r"electric\s*guitar",
            r"ელექტრო\s*გიტარ",
            r"classical\s*guitar",
            r"კლასიკურ.{0,12}გიტარ",
            r"bass\s*guitar",
            r"ბას\s*გიტარ",
        ],
        "prefer": ["აკუსტიკ", "acoustic guitar", "acoustic", "folk guitar"],
        "want": ["აკუსტიკ", "acoustic"],
        "block": [
            "ხელსაწყო",
            "tool",
            "მიკროფონ",
            "microphone",
            "mixer",
            "მიქსერ",
            "electric guitar",
            "ელექტრო",
            "bass",
            "ბას",
            "კლასიკურ",
            "classical",
        ],
    },
    {
        "kind": "classical_guitar",
        "title_any": [
            r"classical\s*guitar",
            r"კლასიკურ.{0,12}გიტარ",
            r"nylon\s*string",
            r"კლასიკ",
        ],
        "title_must_not": [
            r"electric\s*guitar",
            r"ელექტრო\s*გიტარ",
            r"bass\s*guitar",
            r"ბას\s*გიტარ",
        ],
        "prefer": ["კლასიკურ", "classical guitar", "classical", "nylon"],
        "want": ["კლასიკურ", "classical"],
        "block": [
            "ხელსაწყო",
            "tool",
            "ელექტრო",
            "electric",
            "bass",
            "ბას",
            "მიკროფონ",
            "microphone",
        ],
    },
    {
        "kind": "bass_guitar",
        "title_any": [r"bass\s*guitar", r"ბას\s*გიტარ", r"\bbass\b", r"\bბასი\b"],
        "prefer": ["ბას გიტარ", "bass guitar", "ბასი", "bass"],
        "want": ["ბას", "bass"],
        "block": [
            "ხელსაწყო",
            "tool",
            "მიკროფონ",
            "microphone",
            "ელექტრო გიტარ",
            "electric guitar",
            "აკუსტიკ",
            "acoustic",
        ],
    },
    {
        "kind": "microphone",
        "title_any": [
            r"\bmic\b",
            r"microphone",
            r"მიკროფონ",
            r"\bSM\d{2,}",
            r"\bBETA\s*\d",
            r"\bRE\d{2}",
            r"wireless\s*system",
        ],
        "prefer": ["მიკროფონ", "microphone", "mic"],
        "want": ["მიკროფონ", "microphone", "mic"],
        "block": [
            "ხელსაწყო", "tool", "გიტარ", "guitar", "დრამ", "drum", "mixer",
            "პროცესორ", "processor", "ეფექტ", "effect",
            "კაბელ", "cable", "სადგამ", "stand", "ჩანთ", "bag", "case", "ყუთ",
        ],
    },
    {
        "kind": "mixer",
        "title_any": [r"\bmixer\b", r"მიქსერ", r"mixing\s*console", r"soundcraft", r"x32", r"xair"],
        "prefer": ["მიქსერ", "mixer", "console", "ხმის"],
        "want": ["მიქსერ", "mixer"],
        "block": ["ხელსაწყო", "tool", "გიტარ", "guitar", "მიკროფონ"],
    },
    {
        "kind": "bass_cabinet",
        "title_any": [
            r"ბას.*კაბინეტ",
            r"bass.*cabinet",
            r"cab(?:inet)?\s*\d",
            r"410t|115|210|810",
            r"ampeg|hartke|markbass|aguilar",
        ],
        "title_must_not": [
            r"bass\s*guitar",
            r"ბას\s*გიტარ",
        ],
        "prefer": ["გამაძლიერებელი", "კომბი", "amplifier", "amp"],
        "want": ["ბას", "bass", "გამაძლიერებელ"],
        "block": ["გიტარ", "guitar", "მიკროფონ", "microphone"],
    },
    {
        "kind": "keyboard_stand",
        "title_any": [
            r"კლავიშ.*სადგამ",
            r"keyboard.*stand",
            r"stand.*keyboard",
            r"\bk&m\b.*stand",
            r"\bk&m\b.*სადგამ",
        ],
        "title_must_not": [
            r"კლავიშ.*ინსტრუმენტ",
            r"სინთეზატორ",
            r"synthesizer",
        ],
        "prefer": ["კლავიშის", "keyboard", "stand", "სადგამ"],
        "want": ["კლავიშ", "keyboard", "სადგამ", "stand"],
        "block": ["გიტარ", "guitar", "მიკროფონ", "microphone", "სინთეზატორ", "synthesizer"],
    },
    {
        "kind": "guitar_effect",
        "title_any": [
            r"გიტარ.*ეფექტ",
            r"guitar.*effect",
            r"guitar.*pedal",
            r"ეფექტ.*პედალ",
            r"effect.*pedal",
            r"stompbox",
            r"overdrive",
            r"distortion",
            r"reverb.*pedal",
            r"delay.*pedal",
            r"chorus",
            r"flanger",
            r"phaser",
            r"wah.*wah",
            r"string\s*9",
            r"string9",
            r"string\s*ensemble",
            r"electro\s*harmonix",
            r"\behx\b",
        ],
        "title_must_not": [
            r"guitar\s*amplifier",
            r"გიტარ.*გამაძლიერებელ",
            r"bass\s*guitar",
            r"ბას\s*გიტარ",
        ],
        "prefer": ["ეფექტ", "effect", "პედალ", "pedal"],
        "want": ["ეფექტ", "effect", "გიტარ", "guitar"],
        "block": [
            "სასცენო", "stage", "განათებ", "light", "ბოლის", "fog",
            "ლაზერ", "laser", "დისკო", "disco", "სტრობ", "strobe",
            "თოვლის", "snow", "საპნის", "bubble", "პიროტექნიკა",
            "ამპლიფიკატორ", "amplifier", "გამაძლიერებელ", "amp\b",
        ],
    },
]


def _category_has_bass(lab: str) -> bool:
    n = _norm(lab)
    if not n:
        return False
    if "bass guitar" in n or "bass guitars" in n:
        return True
    if "ბას გიტარ" in n or "ბასი გიტარ" in n:
        return True
    if re.search(r"(?:^|[\s\-_/])(?:bass|ბასი|ბას)(?:$|[\s\-_/])", n):
        return True
    return False


def _category_has_acoustic(lab: str) -> bool:
    n = _norm(lab)
    return bool(n and re.search(r"acoustic|აკუსტიკ", n))


def _category_has_classical(lab: str) -> bool:
    n = _norm(lab)
    return bool(n and re.search(r"classical|კლასიკურ", n))


def _category_has_electric(lab: str) -> bool:
    n = _norm(lab)
    return bool(n and re.search(r"electric|ელექტრო", n))


def _category_has_guitar_family(lab: str) -> bool:
    n = _norm(lab)
    return bool(n and re.search(r"guitar|გიტარ", n))


def _infer_product_kind(title: str) -> str | None:
    t = _norm(title)
    if not t:
        return None

    # Ordered: more specific product types first
    if re.search(
        r"bass\s*guitar|ბას\s*გიტარ|(?:^|[\s\-/])bass(?:$|[\s\-/0-9])|(?:^|[\s\-/])ბასი?(?:$|[\s\-/])",
        t,
    ):
        if re.search(r"bass|ბას", t) and not re.search(
            r"electric\s*guitar|ელექტრო\s*გიტარ", t
        ):
            return "bass_guitar"

    order = (
        "classical_guitar",
        "acoustic_guitar",
        "electric_guitar",
        "microphone",
        "mixer",
    )
    for kind in order:
        rule = _kind_rule(kind)
        if not rule:
            continue
        if any(re.search(rx, t, re.I) for rx in rule.get("title_must_not") or []):
            continue
        matched = [rx for rx in (rule.get("title_any") or []) if re.search(rx, t, re.I)]
        if not matched:
            continue
        need_sig = set(rule.get("require_guitar_signal_for") or [])
        brand_only = bool(need_sig) and all(rx in need_sig for rx in matched)
        if brand_only and not _GUITAR_TITLE_SIGNAL.search(t):
            continue
        return kind

    # Bare "guitar/გიტარ" without acoustic/classical/bass → treat as electric
    if _GUITAR_TITLE_SIGNAL.search(t) and not re.search(
        r"bass|ბას|acoustic|აკუსტიკ|classical|კლასიკურ", t, re.I
    ):
        return "electric_guitar"
    return None


def _kind_rule(kind: str | None) -> dict[str, Any] | None:
    if not kind:
        return None
    for rule in _PRODUCT_KIND_RULES:
        if rule.get("kind") == kind:
            return rule
    return None


def _category_blob(entry: dict, by_id: dict[str, dict] | None = None) -> str:
    """Label + path + parent name — for short leaves like „ელექტრო“ under გიტარა."""
    lab = str(entry.get("label") or "").strip()
    path = str(entry.get("path") or lab).strip()
    parent_lab = ""
    pid = str(entry.get("parent_id") or "").strip()
    if by_id and pid and pid in by_id:
        parent_lab = str(by_id[pid].get("label") or "").strip()
    return _norm(f"{lab} {path} {parent_lab}")


def _category_score_for_kind(label: str, kind: str | None) -> int:
    """Score how well a store category label fits the product kind. Negative = reject."""
    # Compatibility: label-only scoring when caller has no full entry
    return _category_score_entry({"label": label, "path": label, "parent_id": ""}, kind, None)


def _category_score_entry(
    entry: dict,
    kind: str | None,
    by_id: dict[str, dict] | None,
) -> int:
    """Strict score for one catalog entry. Negative = hard reject. 0 = not a fit."""
    lab = str(entry.get("label") or "").strip()
    if not lab:
        return 0
    blob = _category_blob(entry, by_id)
    lab_n = _norm(lab)
    if not kind:
        return 0

    rule = _kind_rule(kind)
    if not rule:
        return 0

    # Hard family exclusions on full blob
    if kind == "electric_guitar":
        if _category_has_bass(blob):
            return -1000
        if _category_has_acoustic(blob) and not _category_has_electric(lab_n):
            return -1000
        if _category_has_classical(blob) and not _category_has_electric(lab_n):
            return -1000
    if kind == "acoustic_guitar":
        if _category_has_bass(blob):
            return -1000
        if _category_has_electric(blob) and not _category_has_acoustic(blob):
            return -1000
        if _category_has_classical(blob) and not _category_has_acoustic(lab_n):
            return -800
    if kind == "classical_guitar":
        if _category_has_bass(blob):
            return -1000
        if _category_has_electric(blob) and not _category_has_classical(blob):
            return -1000
    if kind == "bass_guitar":
        if _category_has_electric(blob) and not _category_has_bass(blob):
            return -1000
        if _category_has_guitar_family(blob) and not _category_has_bass(blob):
            # plain electric/acoustic guitar cats for a bass product
            if not _category_has_bass(lab_n):
                return -1000

    for b in rule.get("block") or []:
        bn = _norm(b)
        if bn and len(bn) >= 3 and bn in blob:
            return -1000

    score = 0
    strong_prefer = False
    is_leaf = bool(str(entry.get("parent_id") or "").strip())

    for p in rule.get("prefer") or []:
        pn = _norm(p)
        if not pn:
            continue
        if lab_n == pn or blob == pn:
            score = max(score, 320 if is_leaf else 240)
            strong_prefer = True
        elif pn in lab_n:
            # exact phrase inside label — rest must not add conflicting types
            rest = lab_n.replace(pn, " ").strip()
            if kind == "electric_guitar" and re.search(
                r"ბას|bass|აკუსტ|acoustic|კლასიკ|classic", rest
            ):
                continue
            score = max(score, 260 if is_leaf else 200)
            strong_prefer = True
        elif pn in blob:
            # path/parent context e.g. parent გიტარა + leaf ელექტრო
            score = max(score, 220 if is_leaf else 120)

    for w in rule.get("want") or []:
        wn = _norm(w)
        if wn and len(wn) >= 3 and wn in blob:
            score += 25

    # Kind-specific strict boosts
    if kind == "electric_guitar":
        if _category_has_electric(blob) and (
            _category_has_guitar_family(blob) or is_leaf
        ):
            score += 120
            if _category_has_electric(lab_n):
                strong_prefer = True
        elif _category_has_electric(lab_n) and is_leaf:
            # leaf „ელექტრო“ under გიტარა path
            if _category_has_guitar_family(blob) or re.search(r"გიტარ|guitar", blob):
                score += 180
                strong_prefer = True
        elif _category_has_guitar_family(blob) and not (
            _category_has_bass(blob)
            or _category_has_acoustic(blob)
            or _category_has_classical(blob)
            or _category_has_electric(blob)
        ):
            # vague „გიტარები“ parent-only — weak, only if no better leaf
            score = max(score, 40) if not is_leaf else max(score, 60)

    if kind == "acoustic_guitar":
        if _category_has_acoustic(blob):
            score += 150 if is_leaf else 80
            if _category_has_acoustic(lab_n):
                strong_prefer = True
        if _category_has_acoustic(lab_n) and is_leaf:
            score += 80

    if kind == "classical_guitar":
        if _category_has_classical(blob):
            score += 150 if is_leaf else 80
            if _category_has_classical(lab_n):
                strong_prefer = True
        if _category_has_classical(lab_n) and is_leaf:
            score += 80

    if kind == "bass_guitar" and _category_has_bass(blob):
        score += 150 if is_leaf else 90
        if _category_has_bass(lab_n):
            strong_prefer = True

    if kind in ("microphone", "mixer"):
        if score < 100:
            return 0
        if score >= 200:
            strong_prefer = True

    # Cap vague roots; allow roots that are the store's real category name
    # (e.g. „ბას გიტარები“, „მიკროფონები“) when prefer phrases hit the label.
    if not is_leaf and score > 0 and not strong_prefer:
        score = min(score, 90)

    return score


# Minimum positive score to accept a category under a known product kind
_STRICT_CAT_MIN_SCORE = 180
# Prefer a single best category unless second is almost as strong
_STRICT_CAT_MAX_KEEP = 1
_STRICT_CAT_SECOND_MIN = 280


def _select_categories_for_product(
    title: str,
    ai_cats: list[str],
    catalog: list[dict],
    *,
    max_keep: int = 3,
) -> list[str]:
    """
    Category filter: exact-ish AI labels that fit product kind,
    otherwise best leaf catalog matches above a high score threshold.
    For unknown product kinds, trust AI if the label exists in the catalog.
    """
    kind = _infer_product_kind(title)
    max_keep = max(1, min(int(max_keep or 1), 4))

    if not catalog:
        return ai_cats[:max_keep] if ai_cats else []

    by_id: dict[str, dict] = {}
    for c in catalog:
        if not isinstance(c, dict):
            continue
        cid = str(c.get("id") or c.get("value") or "").strip()
        if cid:
            by_id[cid] = c

    # parent ids that have children → never pick those as the final category
    parent_ids_with_kids = {
        str(c.get("parent_id") or "").strip()
        for c in catalog
        if isinstance(c, dict) and str(c.get("parent_id") or "").strip()
    }

    def is_blocked_main(c: dict) -> bool:
        cid = str(c.get("id") or c.get("value") or "").strip()
        return bool(cid and cid in parent_ids_with_kids)

    def score_entry(c: dict) -> int:
        if is_blocked_main(c):
            return -1000
        return _category_score_entry(c, kind, by_id)

    # Index labels for exact AI mapping
    by_lab: dict[str, dict] = {}
    for c in catalog:
        if not isinstance(c, dict):
            continue
        lab = str(c.get("label") or "").strip()
        if lab:
            by_lab[_norm(lab)] = c

    # Always include FINA if it exists in catalog
    fina_lab = None
    for c in catalog:
        if not isinstance(c, dict):
            continue
        if _norm(str(c.get("label") or "")) == "fina":
            fina_lab = str(c.get("label") or "").strip()
            break

    kept: list[tuple[int, str]] = []
    seen: set[str] = set()
    for lab in ai_cats or []:
        lab = str(lab or "").strip()
        if not lab:
            continue
        c = by_lab.get(_norm(lab))
        if not c:
            continue
        sc = score_entry(c)
        key = _norm(lab)
        if key in seen:
            continue
        seen.add(key)
        # If product kind is known, require strict score
        if kind and sc < _STRICT_CAT_MIN_SCORE:
            continue
        # If kind is unknown, trust AI if the label exists in the catalog
        if not kind and sc < 0:
            continue  # still block obviously wrong picks
        kept.append((sc, str(c.get("label") or lab)))

    # Add FINA category automatically if not already present
    if fina_lab and _norm(fina_lab) not in seen:
        kept.append((100, fina_lab))
        seen.add(_norm(fina_lab))

    if not kept:
        # Catalog rescue: only high-confidence leaves for known product kind
        if not kind:
            # Unknown kind: trust AI suggestions if they're valid catalog labels
            out: list[str] = []
            for lab in ai_cats or []:
                lab = str(lab or "").strip()
                if lab and lab in by_lab and _norm(lab) not in {_norm(x) for x in out}:
                    out.append(lab)
            if fina_lab and _norm(fina_lab) not in {_norm(x) for x in out}:
                out.append(fina_lab)
            return out[:max_keep]
        ranked: list[tuple[int, str]] = []
        for c in catalog:
            if not isinstance(c, dict):
                continue
            lab = str(c.get("label") or "").strip()
            if not lab:
                continue
            sc = score_entry(c)
            if sc >= _STRICT_CAT_MIN_SCORE:
                ranked.append((sc, lab))
        ranked.sort(key=lambda x: (-x[0], x[1]))
        kept = ranked

    if not kept:
        return []

    kept.sort(key=lambda x: (-x[0], x[1]))
    # Unique labels by score
    out: list[str] = []
    seen2: set[str] = set()
    for sc, lab in kept:
        key = _norm(lab)
        if key in seen2:
            continue
        if out:
            # only allow a 2nd pick if BOTH are extremely strong
            if sc < _STRICT_CAT_SECOND_MIN and max_keep <= 2:
                break
        seen2.add(key)
        out.append(lab)
        if len(out) >= max_keep:
            break
    return out


def match_category_options_strict(
    title: str,
    ai_hints: list[str],
    options: list[dict],
    *,
    max_keep: int = 3,
) -> list[dict]:
    """
    Map strict category labels → full option dicts from available_category_options.
    Empty if nothing is a confident match (prefer empty over wrong).
    """
    catalog = []
    for o in options:
        if not isinstance(o, dict):
            continue
        catalog.append(
            {
                "id": str(o.get("value") or o.get("id") or ""),
                "label": str(o.get("label") or ""),
                "parent_id": str(o.get("parent_id") or ""),
                "path": str(o.get("path") or o.get("label") or ""),
            }
        )
    labels = _select_categories_for_product(
        title, list(ai_hints or []), catalog, max_keep=max_keep
    )
    if not labels:
        return []
    by_n = {_norm(str(o.get("label") or "")): o for o in options if isinstance(o, dict)}
    out: list[dict] = []
    seen: set[str] = set()
    for lab in labels:
        o = by_n.get(_norm(lab))
        if not o:
            continue
        key = str(o.get("value") or o.get("id") or lab)
        if key in seen:
            continue
        seen.add(key)
        out.append(o)
    return out


def _snap_feature_values(
    data: dict,
    feature_catalog: list[dict],
    product_title: str,
) -> None:
    """Align AI feature_values to exact catalog options; fill brand/condition from title."""
    fv = data.get("feature_values")
    if not isinstance(fv, list):
        fv = []
    # Index catalog by label/id/field
    by_key: dict[str, dict] = {}
    for feat in feature_catalog:
        for k in (
            _norm(feat.get("label") or ""),
            str(feat.get("id") or ""),
            str(feat.get("field_name") or ""),
        ):
            if k:
                by_key[k] = feat

    used_fields: set[str] = set()
    new_fv: list[dict] = []

    def add_item(feat: dict, value_label: str, value_id: str = "", *, replace: bool = False) -> None:
        key = str(feat.get("field_name") or feat.get("id") or feat.get("label") or "")
        if key in used_fields:
            if not replace:
                return
            # Replace existing entry for this feature
            new_fv[:] = [
                x
                for x in new_fv
                if str(x.get("field_name") or x.get("id") or x.get("label") or "") != key
            ]
            used_fields.discard(key)
        used_fields.add(key)
        if value_id and value_id != value_label:
            vals = [value_id, value_label]
        elif value_id:
            vals = [value_id]
        else:
            vals = [value_label]
        new_fv.append(
            {
                "id": str(feat.get("id") or ""),
                "field_name": str(feat.get("field_name") or ""),
                "label": str(feat.get("label") or ""),
                "value": value_label,
                "values": vals,
            }
        )

    force_used = _title_indicates_used(product_title)

    for item in fv:
        if not isinstance(item, dict):
            continue
        lab = str(item.get("label") or item.get("name") or "")
        if _is_author_label(lab):
            continue
        feat = (
            by_key.get(_norm(lab))
            or by_key.get(str(item.get("field_name") or ""))
            or by_key.get(str(item.get("id") or ""))
        )
        # Title Used/მეორადი always wins for condition — skip AI value here
        if force_used and feat and _is_condition_label(str(feat.get("label") or lab)):
            continue
        raw_val = item.get("value")
        if isinstance(raw_val, list):
            wants = [str(x) for x in raw_val if str(x).strip()]
        else:
            wants = [str(raw_val).strip()] if str(raw_val or "").strip() else []
        if item.get("values") and isinstance(item.get("values"), list):
            wants.extend(str(x) for x in item["values"] if str(x).strip())

        if feat:
            opts = list(feat.get("options") or [])
            matched = None
            for w in wants:
                matched = _best_option_match(w, opts)
                if matched:
                    break
            if matched:
                add_item(
                    feat,
                    str(matched.get("label") or matched.get("value") or ""),
                    str(matched.get("value") or ""),
                )
            elif wants and not opts:
                add_item(feat, wants[0])
            elif wants:
                # keep first want as free-ish text; app will try match
                add_item(feat, wants[0])
        elif wants:
            new_fv.append(
                {
                    "id": str(item.get("id") or ""),
                    "field_name": str(item.get("field_name") or ""),
                    "label": lab,
                    "value": wants[0],
                }
            )

    # Ensure brand from title if AI missed
    for feat in feature_catalog:
        if not _is_brand_label(str(feat.get("label") or "")):
            continue
        key = str(feat.get("field_name") or feat.get("id") or feat.get("label") or "")
        if key in used_fields:
            break
        hit = _guess_brand_from_title(product_title, list(feat.get("options") or []))
        if hit:
            add_item(
                feat,
                str(hit.get("label") or hit.get("value") or ""),
                str(hit.get("value") or ""),
            )
        break

    # Title has Used / მეორადი → მდგომარეობა = მეორადი (force)
    if force_used:
        for feat in feature_catalog:
            if not _is_condition_label(str(feat.get("label") or "")):
                continue
            opts = list(feat.get("options") or [])
            hit = _pick_used_condition_option(opts)
            if hit:
                add_item(
                    feat,
                    str(hit.get("label") or hit.get("value") or "მეორადი"),
                    str(hit.get("value") or ""),
                    replace=True,
                )
            else:
                add_item(feat, "მეორადი", replace=True)
            break

    data["feature_values"] = new_fv
    # Rebuild features map for UI matching
    fmap: dict[str, str] = {}
    for item in new_fv:
        lab = str(item.get("label") or "").strip()
        val = item.get("value")
        if lab and val is not None:
            fmap[lab] = val if not isinstance(val, list) else (val[0] if val else "")
    data["features"] = fmap


def _ensure_rich_description(title: str, html: str, language: str) -> str:
    """If the model returned a short description, expand with a specs list (always Georgian)."""
    text = re.sub(r"<[^>]+>", " ", html or "")
    text = re.sub(r"\s+", " ", text).strip()
    has_list = bool(re.search(r"<ul[\s>]", html or "", re.I)) and (html or "").lower().count("<li") >= 5
    if len(text) >= 400 and has_list:
        return html

    intro = (
        f"<p><strong>{title}</strong> — პროფესიონალური აუდიო პროდუქტი Acoustic.ge-ს ასორტიმენტიდან. "
        f"ეს გვერდი აღწერს მოდელს სათაურის მიხედვით და მიზნად ისახავს სწრაფად აჩვენოს, "
        f"რა ტიპის მოწყობილობაა და რა გამოყენებისთვისაა განკუთვნილი.</p>"
    )
    specs_h = "<p><strong>ტექნიკური მახასიათებლები / სპეციფიკაციები</strong></p>"
    benefits_h = "<p><strong>უპირატესობები</strong></p>"
    close = (
        f"<p>შეარჩიეთ <strong>{title}</strong> თუ გჭირდებათ საიმედო გადაწყვეტა ამ კატეგორიაში. "
        f"დამატებითი დეტალებისა და თავსებადობისთვის მიმართეთ Acoustic.ge-ს კონსულტანტს.</p>"
    )
    specs = [
        "პროდუქტის ტიპი: განისაზღვრება მოდელის სახელწოდებით",
        "ბრენდი / სერია: სათაურიდან",
        "გამოყენება: სცენა, სტუდია ან ინსტალაცია (კატეგორიის მიხედვით)",
        "სიგნალის ხარისხი: პროფესიონალური აუდიო კლასი",
        "კონსტრუქცია: სამუშაო დატვირთვისთვის გათვლილი კორპუსი",
        "შეერთება: სტანდარტული პრო-აუდიო ინტერფეისი (მოდელის ტიპის მიხედვით)",
        "თავსებადობა: ტიპიური მიქშერები / ინტერფეისები / გამაძლიერებლები",
        "კვება: მოდელის კლასის შესაბამისი (phantom / ქსელი / არ არის)",
        "მობილურობა: სტაციონარული ან გადასატანი გამოყენება",
        "აქსესუარები: დამოკიდებულია კომპლექტაციაზე",
        "თავსებადი გარემო: კონცერტი, ჩაწერა, მოლაპარაკება, ინსტალაცია",
        "გარანტია და მხარდაჭერა: მაღაზიის პირობების შესაბამისად",
    ]
    benefits = [
        "ნათელი პოზიციონირება პროდუქტის სახელიდან",
        "შესაფერისი პროფესიონალური და ნახევრად-პრო გამოყენებისთვის",
        "მარტივი ინტეგრაცია სტანდარტულ აუდიო სისტემებში",
        "შესაფერისი Acoustic.ge კატალოგისთვის",
    ]
    specs_ul = "<ul>" + "".join(f"<li>{s}</li>" for s in specs) + "</ul>"
    benefits_ul = "<ul>" + "".join(f"<li>{b}</li>" for b in benefits) + "</ul>"
    if html and len(text) > 40 and not has_list:
        return f"{html}\n{specs_h}\n{specs_ul}\n{benefits_h}\n{benefits_ul}"
    return f"{intro}\n{specs_h}\n{specs_ul}\n{benefits_h}\n{benefits_ul}\n{close}"


def _default_video(title: str) -> dict:
    t = (title or "").strip() or "პროდუქტი"
    watch = resolve_youtube_watch_url(t, existing_url="")
    return {
        "url": watch,
        "title": f"{t} — ვიდეო",
        "description": f"პროდუქტის ვიდეო: {t}.",
        "provider": "youtube",
        "position": 0,
        "status": "A",
    }


def generate_product_fields(
    *,
    api_key: str,
    model: str,
    content_language: str,
    page_context: dict,
    product_title: str,
    extra_notes: str = "",
    web_context: str = "",
) -> dict:
    title = (product_title or "").strip()
    if not title:
        raise RuntimeError(
            "Product title is empty on the CS-Cart page. "
            "Enter a product name in the Name field, then run Generate again."
        )

    content_language = "Georgian"
    client = OpenAI(api_key=api_key)

    available_features = (page_context or {}).get("available_features") or []
    feature_catalog = _compact_feature_catalog(available_features)
    category_catalog = _compact_category_catalog(page_context or {})

    compact_context = {
        "product_name": (page_context or {}).get("product_name"),
        "price": (page_context or {}).get("price"),
        "old_price": (page_context or {}).get("old_price"),
        "existing_tags": (page_context or {}).get("existing_tags"),
        "product_code": (page_context or {}).get("product_code"),
        "feature_catalog": feature_catalog,
        "category_catalog": category_catalog,
        "category_tree": _category_tree_summary(category_catalog),
        # keep short lists for older prompt compatibility
        "available_categories": [c.get("label") for c in category_catalog[:80]],
        "available_features_labels": [f.get("label") for f in feature_catalog],
        "video_gallery": (page_context or {}).get("video_gallery") or {},
        "web_search_results": web_context[:6000] if web_context else "",
    }

    user_payload = {
        "content_language": "Georgian (ქართული) — REQUIRED for all prose fields",
        "product_title": title,
        "extra_notes": (extra_notes or "").strip(),
        "page_context": compact_context,
        "required_json_schema": _schema_hint(),
        "instruction": (
            "PRIMARY TASK: from product_title alone, choose correct store CATEGORIES and FEATURES "
            "(especially ბრენდი/Brand and მდგომარეობა) using EXACT labels from category_catalog and "
            "feature_catalog options. "
            "Also write full_description, promo_text, SEO, and video texts in Georgian. "
            "Never invent brands or categories missing from the catalogs. "
            "Do not set ავტორი (app sets logged-in user). "
            "\n\nCATEGORY SELECTION (CRITICAL):\n"
            "- page_context.category_tree shows the FULL hierarchical category tree (indented).\n"
            "- page_context.category_catalog has id+label+path+parent_id for each category.\n"
            "- Read the tree to understand the structure BEFORE choosing.\n"
            "- Pick the ONE most specific leaf category that best matches the product.\n"
            "- Examples of correct picks:\n"
            "  • 'EV PRO 780 MIC' → მიკროფონები (leaf under სტუდია)\n"
            "  • 'Harley Benton SolidBass 410T ბას-გიტარის კაბინეტი' → გამაძლიერებელი/კომბი (leaf under ბასი)\n"
            "  • 'K&M 18997 კლავიშის სადგამი' → კლავიშის (leaf under სადგამები)\n"
            "  • 'Electro Harmonix String9' → ეფექტები (leaf under კატეგორია: გიტარა)\n"
            "  • 'Ibanez GAX-70' → ელექტრო (leaf under კატეგორია: გიტარა)\n"
            "- WARNING: 'Electro Harmonix' is a BRAND name — it does NOT mean 'electric guitar'.\n"
            "  The product 'Electro Harmonix String9' is a GUITAR EFFECT PEDAL, not an electric guitar.\n"
            "  Its category is ეფექტები (under კატეგორია: გიტარა), NOT ელექტრო.\n"
            "- NEVER pick: აქსესუარები (too generic), top-level parents, or wrong product family.\n"
            "- If no leaf is a clear fit, return empty categories array.\n"
            "\nWEB SEARCH RESULTS: if page_context.web_search_results is non-empty, "
            "PREFER real specifications, frequency response, polar patterns, connector types, "
            "weight, dimensions, and other factual details from those results over your own knowledge. "
            "Still write the description in Georgian. If web search is empty, use your knowledge but "
            "keep specs realistic for the product type."
        ),
    }

    response = client.chat.completions.create(
        model=model,
        temperature=0.35,
        response_format={"type": "json_object"},
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": json.dumps(user_payload, ensure_ascii=False)},
        ],
    )

    text = response.choices[0].message.content or "{}"
    data = json.loads(text)
    if not isinstance(data, dict):
        raise RuntimeError("OpenAI returned non-object JSON.")

    if not (data.get("product_name") or "").strip():
        data["product_name"] = title

    page_price = str((page_context or {}).get("price") or "").strip()
    if page_price and not str(data.get("price") or "").strip():
        data["price"] = page_price

    # old_price: if AI didn't set it, apply the rule locally
    page_old_price = str((page_context or {}).get("old_price") or "").strip()
    ai_old_price = str(data.get("old_price") or "").strip()
    if not ai_old_price or ai_old_price == "0":
        if page_old_price and page_old_price != "0":
            data["old_price"] = page_old_price
        elif page_price:
            try:
                pnum = float(page_price.replace(",", "."))
                if pnum < 100:
                    data["old_price"] = page_price
                else:
                    data["old_price"] = ""
            except ValueError:
                data["old_price"] = ""

    # tags: ensure it's a string
    tags_val = data.get("tags")
    if isinstance(tags_val, list):
        data["tags"] = ", ".join(str(t).strip() for t in tags_val if str(t).strip())

    data["full_description"] = _ensure_rich_description(
        title, str(data.get("full_description") or ""), content_language
    )

    # Snap categories to real catalog labels, then STRICT filter by product type
    snapped = _snap_categories_to_catalog(data.get("categories"), category_catalog)
    data["categories"] = _select_categories_for_product(
        title, snapped, category_catalog, max_keep=3
    )
    data["product_kind"] = _infer_product_kind(title) or ""

    # Post-process: fix common AI category mistakes based on title keywords
    title_n = _norm(title)
    cat_set = set(_norm(c) for c in data["categories"])

    # Guitar effect/pedal should never be in "ელექტრო" (electric guitar) category
    if re.search(r"ეფექტ|effect|pedal|stompbox|overdrive|distortion|reverb|delay|chorus|flanger|phaser|wah", title_n, re.I):
        wrong_cats = {"ელექტრო", "ელექტრო-ჩასაბერი", "ელექტრო-აკუსტიკური"}
        if cat_set & wrong_cats:
            # Find "ეფექტები" in catalog
            for c in category_catalog:
                if _norm(str(c.get("label") or "")) == "ეფექტები":
                    data["categories"] = ["ეფექტები"]
                    break
            else:
                data["categories"] = [c for c in data["categories"] if _norm(c) not in wrong_cats]

    # Bass cabinet should never be in "ბას-გიტარა" (bass guitar) category
    if re.search(r"კაბინეტ|cabinet|410t|115|210|810", title_n, re.I):
        if _norm("ბას-გიტარა") in cat_set:
            for c in category_catalog:
                if _norm(str(c.get("label") or "")) == "გამაძლიერებელი/კომბი":
                    data["categories"] = ["გამაძლიერებელი/კომბი"]
                    break

    # Keyboard stand should never be in "აქსესუარები" or "სინთეზატორი"
    if re.search(r"სადგამ|stand", title_n, re.I) and re.search(r"კლავიშ|keyboard", title_n, re.I):
        wrong_cats = {"აქსესუარები", "სინთეზატორი", "კლავიშებიანი"}
        if cat_set & wrong_cats:
            for c in category_catalog:
                if _norm(str(c.get("label") or "")) == "კლავიშის":
                    data["categories"] = ["კლავიშის"]
                    break

    # Remove generic "აქსესუარები" if a more specific category exists
    if "აქსესუარები" in cat_set and len(data["categories"]) > 1:
        data["categories"] = [c for c in data["categories"] if _norm(c) != "აქსესუარები"]
        if not data["categories"]:
            data["categories"] = ["აქსესუარები"]

    videos = data.get("videos")
    if not isinstance(videos, list) or not videos:
        data["videos"] = [_default_video(title)]
    else:
        fixed = []
        for v in videos:
            if not isinstance(v, dict):
                continue
            item = dict(v)
            if not str(item.get("url") or "").strip():
                item.setdefault("title", f"{title} — ვიდეო")
                item.setdefault("description", f"პროდუქტის ვიდეო: {title}.")
                item.setdefault("provider", "youtube")
                item.setdefault("position", 0)
                item.setdefault("status", "A")
            fixed.append(item)
        data["videos"] = fixed or [_default_video(title)]

    data["videos"] = ensure_video_watch_urls(data["videos"], product_title=title)
    cleaned_vids = []
    for v in data["videos"]:
        if not isinstance(v, dict):
            continue
        u = str(v.get("url") or "").strip()
        # Strip AI placeholder URLs — real YouTube IDs are exactly 11 chars [A-Za-z0-9_-]
        if u:
            vid_match = re.search(r"watch\?v=([^&\s]+)", u, re.I)
            if vid_match:
                vid = vid_match.group(1)
                is_placeholder = (
                    len(vid) != 11
                    or not re.match(r"^[A-Za-z0-9_-]{11}$", vid)
                    or re.search(r"VIDEO_ID|XXXX|YOUR_|PLACEHOLDER|EXAMPLE|STRING\d", vid, re.I)
                )
                if is_placeholder:
                    u = ""
                    v["url"] = ""
            u = ""
            v["url"] = ""
        if is_youtube_search_url(u):
            resolved = resolve_youtube_watch_url(title, existing_url=u)
            if resolved and not is_youtube_search_url(resolved):
                v = {**v, "url": resolved}
        if not str(v.get("url") or "").strip():
            # Empty URL → try to resolve a real YouTube video from the title
            resolved = resolve_youtube_watch_url(title, existing_url="")
            if resolved:
                v = {**v, "url": resolved}
        cleaned_vids.append(v)
    data["videos"] = cleaned_vids or [_default_video(title)]

    # Normalize / snap features onto catalog (brand, condition, …)
    # Seed feature_values from features map when needed
    fv = data.get("feature_values")
    if not isinstance(fv, list) or not fv:
        features_map = data.get("features") if isinstance(data.get("features"), dict) else {}
        data["feature_values"] = [
            {"label": k, "value": v, "field_name": "", "id": ""}
            for k, v in features_map.items()
            if not _is_author_label(str(k))
        ]

    _snap_feature_values(data, feature_catalog, title)

    # Strip author again after snap
    data["feature_values"] = [
        item
        for item in (data.get("feature_values") or [])
        if isinstance(item, dict) and not _is_author_label(str(item.get("label") or ""))
    ]
    if isinstance(data.get("features"), dict):
        data["features"] = {
            k: v
            for k, v in data["features"].items()
            if not _is_author_label(str(k))
        }

    return data
