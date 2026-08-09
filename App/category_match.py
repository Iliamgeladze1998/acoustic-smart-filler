"""Data-driven category classifier for the Acoustic.ge category tree.

Design goals
------------
* No hand-written per-product rules. The store's own category tree is the only
  taxonomy source, so new categories work without code changes.
* Two-stage reasoning that mirrors how the tree is actually built:
  family (root, e.g. „კატეგორია: გიტარა“) + item type (leaf, e.g. „ეფექტები“).
  Leaf labels repeat across families („ეფექტები“, „აქსესუარები“,
  „გამაძლიერებელი/კომბი“ …), so a leaf is only valid inside a matching family.
* Never returns a wrong-family leaf: candidates are scored as (family, leaf)
  pairs, so a bass cabinet cannot land in „ბას-გიტარა“ and a guitar pedal
  cannot land in „ელექტრო“.
* Falls back to the general family category when no leaf is confident, instead
  of returning nothing.

The caller supplies short Georgian hints produced by the model
(family + product type). Matching then happens Georgian-to-Georgian against the
real labels, which avoids a translation dictionary.
"""

from __future__ import annotations

import math
import re
from typing import Any, Iterable

__all__ = [
    "build_tree",
    "classify",
    "compact_tree_prompt",
    "GENERIC_LEAF_STEMS",
]

# Nodes that must never be returned as the product category itself.
# Their descendants stay selectable (e.g. DJ / MIDI live under FINA).
_NON_PICKABLE = {
    "trash category",
    "პრობლემური ნივთები",
    "საახალწლო ფასდაკლებები",
    "აჩუქე",
    "fina",
}

# Leaf names that carry almost no product-type information on their own.
# They only win when the product type explicitly mentions them.
GENERIC_LEAF_STEMS = (
    "აქსესუარ",
    "სხვადასხვა",
    "ნაწილებ",
    "სხვა",
)

_MIN_PREFIX = 4  # Georgian is agglutinative: compare word stems by prefix
_LEAF_MIN_SCORE = 1.15
_FAMILY_MIN_SCORE = 0.85
_GENERIC_PENALTY = 0.35

# Type-word synonyms: when the AI uses a word not present in any catalog label,
# these map it to the store's vocabulary. Practical lexical resource, like
# stemming or stop-words — not a per-product rule.
#   „კაბინეტი“ → გამაძლიერებელი (the store sells bass cabinets under
#   „გამაძლიერებელი/კომბი“, there is no „კაბინეტი“ leaf).
_TYPE_SYNONYMS: dict[str, list[str]] = {
    "კაბინეტი": ["გამაძლიერებელი", "კომბი"],
    "კომბო": ["გამაძლიერებელი", "კომბი"],
    "პედალი": ["ეფექტი", "ეფექტები"],
    "სტეკი": ["გამაძლიერებელი", "კომბი"],
    "ჰედი": ["გამაძლიერებელი"],
}


def _norm(s: Any) -> str:
    return re.sub(r"\s+", " ", str(s or "").lower()).strip()


def _tokens(text: Any) -> list[str]:
    """Split into comparable word stems (Georgian, latin, digits)."""
    raw = re.split(r"[^0-9a-zა-ჰ&+]+", _norm(text))
    out: list[str] = []
    for t in raw:
        t = t.strip("&+")
        if len(t) < 2:
            continue
        out.append(t)
    return out


# Georgian nominal endings, longest first. „თეფშები“ / „თეფში“ → „თეფშ“.
_SUFFIXES = (
    "ებისთვის", "ისთვის", "ებისა", "ებში", "ებზე", "ებით", "ებად", "ების",
    "ებს", "ები", "ისა", "თან", "ის", "ში", "ზე", "თა", "ად", "ით", "ებ",
    "ს", "ი", "ა", "ო", "ე",
)
_MIN_STEM = 3


def _stems(t: str) -> set[str]:
    """All plausible stems. „ში“/„თა“ can be a case ending or part of the root
    („თეფში“, „ჩანთა“), so keep every candidate and match on any shared stem."""
    out = {t}
    if not re.search(r"[ა-ჰ]", t):
        return out
    for suf in _SUFFIXES:
        if t.endswith(suf) and len(t) - len(suf) >= _MIN_STEM:
            out.add(t[: -len(suf)])
    return out


def _tok_match(a: str, b: str) -> bool:
    if a == b:
        return True
    sa, sb = _stems(a), _stems(b)
    if sa & sb:
        return True
    for x in sa:
        if len(x) < _MIN_PREFIX:
            continue
        for y in sb:
            if len(y) >= _MIN_PREFIX and (x.startswith(y) or y.startswith(x)):
                return True
    return False


def build_tree(catalog: Iterable[dict]) -> dict[str, Any]:
    """Index the flat catalog into roots/children/leaf sets with token weights."""
    nodes: dict[str, dict] = {}
    order: list[str] = []
    for c in catalog or []:
        if not isinstance(c, dict):
            continue
        cid = str(c.get("id") or c.get("value") or "").strip()
        label = str(c.get("label") or "").strip()
        if not cid or not label:
            continue
        if cid in nodes:
            continue
        nodes[cid] = {
            "id": cid,
            "label": label,
            "parent_id": str(c.get("parent_id") or "").strip(),
            "path": str(c.get("path") or label).strip(),
        }
        order.append(cid)

    children: dict[str, list[str]] = {}
    for cid, n in nodes.items():
        pid = n["parent_id"]
        if pid and pid in nodes:
            children.setdefault(pid, []).append(cid)
        else:
            n["parent_id"] = ""

    def root_of(cid: str) -> str:
        seen: set[str] = set()
        cur = cid
        while True:
            pid = nodes[cur]["parent_id"]
            if not pid or pid not in nodes or pid in seen:
                return cur
            seen.add(pid)
            cur = pid

    for cid in order:
        nodes[cid]["root_id"] = root_of(cid)
        nodes[cid]["is_leaf"] = not children.get(cid)

    # Document frequency of every label token → rare tokens weigh more.
    df: dict[str, int] = {}
    for cid in order:
        for t in set(_tokens(nodes[cid]["label"])):
            df[t] = df.get(t, 0) + 1
    total = max(1, len(order))
    weights = {t: math.log(1.0 + total / (1.0 + n)) for t, n in df.items()}

    roots = [cid for cid in order if not nodes[cid]["parent_id"]]
    return {
        "nodes": nodes,
        "order": order,
        "children": children,
        "roots": roots,
        "weights": weights,
    }


def _weight(tree: dict, tok: str) -> float:
    w = tree["weights"].get(tok)
    if w is not None:
        return w
    # Unseen token: assume rare
    return 2.0


def _head_weighted(tokens: list[str], *, lo: float = 0.35, hi: float = 1.0) -> list[tuple[str, float]]:
    """Georgian noun phrases put the head noun last: „ბას გიტარის კაბინეტი“.

    Later tokens describe *what the product is*, earlier ones describe context
    (family, material, brand line), so weight increases towards the end.
    """
    n = len(tokens)
    if n == 0:
        return []
    if n == 1:
        return [(tokens[0], hi)]
    return [(t, lo + (hi - lo) * i / (n - 1)) for i, t in enumerate(tokens)]


def _overlap_score(
    tree: dict,
    label: str,
    hints: list[tuple[str, float]],
    *,
    damp_tokens: set[str] | None = None,
) -> float:
    """Weighted share of the label explained by the hints.

    ``damp_tokens`` are words already accounted for at family level (the root
    label). Without damping, a short leaf like „ბას-გიტარა“ looks fully
    explained by the family word „ბასი“ and beats the real type „გამაძლიერებელი“.
    """
    lab_toks = _tokens(label)
    if not lab_toks or not hints:
        return 0.0
    matched = 0.0
    denom = 0.0
    for lt in lab_toks:
        w = _weight(tree, lt)
        damp = 0.35 if (damp_tokens and any(_tok_match(lt, d) for d in damp_tokens)) else 1.0
        denom += w * damp
        best = 0.0
        for ht, hw in hints:
            if _tok_match(lt, ht):
                best = max(best, hw)
        if best:
            matched += w * damp * best
    if matched <= 0:
        return 0.0
    denom = denom or 1.0
    # Reward absolute evidence and coverage of the label.
    return matched / denom * min(2.0, 0.6 + matched / 3.0) + matched / 6.0


def _is_generic(label: str) -> bool:
    n = _norm(label)
    return any(g in n for g in GENERIC_LEAF_STEMS)


def _pickable(node: dict) -> bool:
    return _norm(node["label"]) not in _NON_PICKABLE


def compact_tree_prompt(catalog: Iterable[dict], *, max_chars: int = 3000) -> str:
    """One line per family: „root: leaf, leaf, …“ — cheap context for the model."""
    tree = build_tree(catalog)
    nodes, children = tree["nodes"], tree["children"]
    lines: list[str] = []
    for rid in tree["roots"]:
        root = nodes[rid]
        if _norm(root["label"]) in {"trash category"}:
            continue
        kids: list[str] = []
        stack = list(children.get(rid, []))
        while stack:
            cid = stack.pop(0)
            kids.append(nodes[cid]["label"])
            stack.extend(children.get(cid, []))
        if kids:
            lines.append(f"{root['label']}: " + ", ".join(dict.fromkeys(kids)))
        else:
            lines.append(root["label"])
    out = "\n".join(lines)
    return out[:max_chars]


def classify(
    *,
    catalog: Iterable[dict],
    title: str = "",
    family_hint: str = "",
    type_hint: str = "",
    ai_labels: Iterable[str] | None = None,
    max_keep: int = 1,
) -> dict[str, Any]:
    """Return the best (family, leaf) match for a product.

    Result: {"labels": [...], "family": str, "leaf": str, "score": float,
             "mode": "leaf" | "family" | "ai" | "none", "trace": [...]}
    """
    tree = build_tree(catalog)
    nodes, children = tree["nodes"], tree["children"]
    if not nodes:
        return {"labels": [], "family": "", "leaf": "", "score": 0.0, "mode": "none", "trace": []}

    ai_valid: list[str] = []
    by_norm_label = {_norm(n["label"]): n for n in nodes.values()}
    for lab in ai_labels or []:
        n = by_norm_label.get(_norm(lab))
        if n and _pickable(n):
            ai_valid.append(n["label"])
    ai_norm = {_norm(x) for x in ai_valid}

    type_toks = _tokens(type_hint)
    fam_toks = _tokens(family_hint)
    # Latin/model tokens from the title help for labels like „DJ“, „MIDI კლავიატურა“.
    title_toks = [t for t in _tokens(title) if re.search(r"[a-z]", t)]

    # Item evidence: every type word counts; family damping (below) is what
    # separates the discriminating word from the family word. The last word of a
    # Georgian noun phrase is the head („… კაბინეტი“, „… გამაძლიერებელი“) and
    # gets a bonus so it can outweigh an incidental family word or a compound
    # split („ბას-გიტარა“ → ბას + გიტარა, both matching „ბას-გიტარის“).
    # BUT: the head bonus only applies to words NOT in the family hint — a head
    # like „გიტარა“ is the family word and boosting it would lift every
    # guitar-related leaf (including „ბას-გიტარა“) instead of discriminating.
    fam_norm = {_norm(t) for t in fam_toks}
    # Discriminating head noun: last type token that is NOT a family word.
    # Used for head-coverage penalty below.
    head_tok = ""
    for t in reversed(type_toks):
        if _norm(t) not in fam_norm:
            head_tok = t
            break
    # Synonym expansion: if the head noun has known synonyms in the store's
    # vocabulary, add them as extra item hints so leaves like
    # „გამაძლიერებელი/კომბი“ can match a type like „ბას-გიტარის კაბინეტი“.
    syn_hints: list[tuple[str, float]] = []
    if head_tok:
        for syn in _TYPE_SYNONYMS.get(head_tok, []):
            syn_hints.append((syn, 1.5))
    item_hints = [
        (t, 1.5 if (i == len(type_toks) - 1 and _norm(t) not in fam_norm) else 1.0)
        for i, t in enumerate(type_toks)
    ] + syn_hints + [(t, 0.6) for t in title_toks]
    # Family evidence: every word counts equally.
    family_hints = (
        [(t, 1.0) for t in fam_toks]
        + [(t, 0.85) for t in type_toks]
        + [(t, 0.6) for t in title_toks]
    )

    # ---- family scores -------------------------------------------------
    fam_score: dict[str, float] = {}
    for rid in tree["roots"]:
        root = nodes[rid]
        s = _overlap_score(tree, root["label"], family_hints)
        # „კატეგორია: გიტარა“ → also match the bare word
        if ":" in root["label"]:
            s = max(s, _overlap_score(tree, root["label"].split(":", 1)[1], family_hints))
        fam_score[rid] = s

    # A confidently matching leaf is itself evidence for its family.
    leaf_best_by_root: dict[str, tuple[float, str]] = {}
    candidates: list[tuple[float, str, str]] = []  # (score, root_id, node_id)

    # Root words, used to detect leaves that are named after a family
    # („გიტარის“ under „სადგამები“ means *guitar stand*, not a guitar).
    root_toks: list[str] = []
    for rid in tree["roots"]:
        root_toks.extend(_tokens(nodes[rid]["label"]))

    # Roots that have specific children — a leaf whose label matches such a
    # root is a generic cross-listing (e.g. „სადგამები“ under „კლავიშებიანი“
    # when the real „სადგამები“ root has გიტარის / კლავიშის / მიკროფონის …).
    roots_with_kids: set[str] = set()
    for rid in tree["roots"]:
        if children.get(rid):
            roots_with_kids.add(_norm(nodes[rid]["label"]))

    # ---- pass 1: base scores + head-match tracking ----
    # Head coverage: if the type hint has a discriminating head noun (not a
    # family word), a leaf that matches other type words but misses the head is
    # likely wrong — BUT only penalize if a sibling in the SAME family matches
    # the head, so we don't punish leaves like „კლავიშის“ when the head
    # „სადგამი“ is the parent concept (no sibling matches it either).
    head_matches_root: set[str] = set()  # root_ids that have a head-matching leaf
    raw: list[tuple[float, str, str, list[str]]] = []  # (base, rid, cid, leaf_toks)

    for cid in tree["order"]:
        node = nodes[cid]
        if not _pickable(node) or not node["is_leaf"]:
            continue
        rid = node["root_id"]
        damp = set(_tokens(nodes[rid]["label"]))
        base = _overlap_score(tree, node["label"], item_hints, damp_tokens=damp)
        lt = _tokens(node["label"])

        # A leaf named after another family only applies when its own parent
        # concept is mentioned too, e.g. „მიკროფონის“ needs „სადგამი“.
        if any(_tok_match(x, rt) for x in lt for rt in root_toks):
            parent = nodes.get(node["parent_id"])
            if parent and parent["id"] != cid:
                ctx = [t for t in _tokens(parent["label"]) if not any(_tok_match(t, x) for x in lt)]
                if ctx and not any(
                    _tok_match(t, h) for t in ctx for h, _w in item_hints + family_hints
                ):
                    base *= 0.25
        # Cross-listing: leaf label identical to a root that has specific children.
        if _norm(node["label"]) in roots_with_kids and _norm(node["label"]) != _norm(nodes[rid]["label"]):
            base *= 0.45

        # Head match: the leaf matches the head noun OR one of its synonyms.
        if head_tok:
            head_syns = [head_tok] + _TYPE_SYNONYMS.get(head_tok, [])
            if any(_tok_match(x, hs) for x in lt for hs in head_syns):
                head_matches_root.add(rid)

        raw.append((base, rid, cid, lt))

    # ---- pass 2: apply penalties + generic/AI adjustments ----
    # 1) Head orphan: the discriminating head noun matches NO leaf anywhere.
    #    Penalize leaves that match only non-head tokens AND have low coverage,
    #    so the family fallback can take over. A leaf with high coverage
    #    („ეფექტები“ matches „ეფექტების“ fully) is still a good pick even if
    #    the head „პედალი“ is just a form word not in the catalog.
    head_orphaned = bool(head_tok) and not head_matches_root
    head_syns = [head_tok] + _TYPE_SYNONYMS.get(head_tok, []) if head_tok else []
    for base, rid, cid, lt in raw:
        node = nodes[cid]
        # Low-coverage penalty: a leaf where <50% of its own tokens match any
        # type/family hint is a weak partial match („ვოკალის პროცესორი/ეფექტი“
        # matches only „ვოკალის“ out of 3 tokens → 33% coverage).
        cov = 0.0
        if lt:
            cov = sum(1 for x in lt if any(_tok_match(x, h) for h, _ in item_hints + family_hints)) / len(lt)
        if cov < 0.5:
            base *= 0.5
        # Head-mismatch penalty: a sibling in the SAME family matches the head
        # noun (or a synonym) but THIS leaf does not — it's the wrong type.
        # „ბას-გიტარა“ misses head „გამაძლიერებელი“ while „გამაძლიერებელი/კომბი“
        # matches it → „ბას-გიტარა“ is wrong for a bass cabinet.
        if head_tok and rid in head_matches_root and not any(
            _tok_match(x, hs) for x in lt for hs in head_syns
        ):
            base *= 0.5
        # Head-orphan + low coverage: almost certainly wrong („ბას-გიტარა“
        # matches „ბას“+„გიტარა“ from „ბას-გიტარის კაბინეტი“ but misses head
        # „კაბინეტი“ and has only 2/2 tokens from family words, not the type).
        if head_orphaned and cov < 0.5:
            base *= 0.4
        # Head-orphan + all tokens are family/root words: the leaf matches only
        # family-level vocabulary, not the actual product type („ბას-გიტარა“
        # for a bass cabinet — both „ბას“ and „გიტარა“ are root labels).
        if head_orphaned and lt and all(
            any(_tok_match(x, rt) for rt in root_toks) for x in lt
        ):
            base *= 0.5
        if _is_generic(node["label"]):
            explicit = any(
                any(_tok_match(t, g) for g in _tokens(node["label"])) for t in type_toks
            )
            if not explicit:
                base *= _GENERIC_PENALTY
        if _norm(node["label"]) in ai_norm:
            base += 0.45
        prev = leaf_best_by_root.get(rid)
        if not prev or base > prev[0]:
            leaf_best_by_root[rid] = (base, node["label"])
        candidates.append((base, rid, cid))

    for rid, (lscore, _lab) in leaf_best_by_root.items():
        if lscore >= _LEAF_MIN_SCORE:
            fam_score[rid] = fam_score.get(rid, 0.0) + min(0.9, lscore * 0.35)
    # AI's own picks vote for their family too.
    for lab in ai_valid:
        n = by_norm_label.get(_norm(lab))
        if n:
            fam_score[n["root_id"]] = fam_score.get(n["root_id"], 0.0) + 0.4

    best_root, best_fam = "", 0.0
    for rid, s in fam_score.items():
        if s > best_fam:
            best_root, best_fam = rid, s

    trace: list[str] = []
    if best_root:
        trace.append(f"family={nodes[best_root]['label']} score={best_fam:.2f}")

    # ---- joint (family, leaf) choice -----------------------------------
    # Only leaves with enough evidence of their own compete; the family score
    # then decides between equally plausible leaves in different families.
    # The family bonus is gated by the leaf's own score so a weak-own leaf
    # can't ride on its family to beat a strong-own leaf elsewhere.
    _GATE_REF = 2.5  # leaf own-score at which the full family bonus applies
    best: tuple[float, str, float] | None = None  # (total, node_id, own)
    for base, rid, cid in candidates:
        if base < _LEAF_MIN_SCORE:
            continue
        gate = min(1.0, base / _GATE_REF)
        total = base + 0.8 * fam_score.get(rid, 0.0) * gate
        if rid == best_root:
            total += 0.25 * gate
        if best is None or total > best[0]:
            best = (total, cid, base)

    labels: list[str] = []
    mode = "none"
    leaf_label = ""
    family_label = nodes[best_root]["label"] if best_root else ""
    score = 0.0

    if best:
        _total, cid, leaf_own = best
        node = nodes[cid]
        trace.append(
            f"leaf={node['label']} own={leaf_own:.2f} total={_total:.2f} "
            f"under={nodes[node['root_id']]['label']}"
        )
        labels = [node["label"]]
        leaf_label = node["label"]
        family_label = nodes[node["root_id"]]["label"]
        score = _total
        mode = "leaf"

    if not labels and best_root and best_fam >= _FAMILY_MIN_SCORE:
        root = nodes[best_root]
        if _pickable(root):
            labels = [root["label"]]
            family_label = root["label"]
            score = best_fam
            mode = "family"
            trace.append("fallback=general family category")

    if not labels and ai_valid:
        labels = ai_valid[:max_keep]
        mode = "ai"
        trace.append("fallback=ai labels")

    return {
        "labels": labels[:max_keep],
        "family": family_label,
        "leaf": leaf_label,
        "score": round(score, 3),
        "mode": mode,
        "trace": trace,
    }
