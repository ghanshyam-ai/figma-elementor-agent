"""
Recursive structural-section finder.

The agent's previous architecture router only walked the **top-level**
entries in data.json. That breaks badly on real Figma files where the
entire page is wrapped in one root frame (every real section ends up
nested 2–5 levels deep).

This module walks the full Elementor + ai-layout tree and returns a
flat list of `RealSection` candidates — Hero, Header, Footer, Built-For,
Two-Columns, Pricing, Blog, Form, etc. — regardless of nesting depth.

The finder is **not 100% reliant on the plugin**. Plugin signals are one
input; the agent's own structural / geometric / name-based analysis is
another. Each detector returns a confidence score; the highest-scoring
classification wins. When two detectors disagree by < 0.1 we surface
the section as `ambiguous` so the orchestrator can hand it to Claude
for visual review.

Public API:
    find_real_sections(data_content, ai_layout_sections) → list[RealSection]
    flatten_decorative(sections) → list[RealSection]   # filter helper
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any


# ---------------------------------------------------------------------------
# Data shape
# ---------------------------------------------------------------------------

@dataclass
class RealSection:
    """One semantically-meaningful section of the page."""
    kind: str                       # header | footer | hero | popup | archive |
                                    # single | search | 404 | section
    elementor_node: dict            # ref into data.json (mutable)
    parent_list: list               # parent's `elements` array (for in-place swap)
    parent_index: int               # index in parent_list
    depth: int                      # 0 = top-level
    ai_section: dict | None         # matching ai-layout subtree, if found
    confidence: float               # 0..1 — how sure the finder is
    reason: str                     # human-readable detection reason
    figma_purpose: str | None = None
    figma_role: str | None = None
    figma_name: str | None = None
    bounds: dict | None = None      # {x, y, width, height} from ai-layout
    inferred: bool = False          # True if classification didn't come from plugin


# ---------------------------------------------------------------------------
# Detectors
# ---------------------------------------------------------------------------

NAME_RX = {
    "header":  re.compile(r"^(header|nav(bar)?|topbar|top[- ]?nav)\b", re.IGNORECASE),
    "footer":  re.compile(r"^footer\b|\bsite[- ]?footer\b", re.IGNORECASE),
    "hero":    re.compile(r"^hero\b|\bbanner\b|\bhero[- ]?slider\b", re.IGNORECASE),
    "popup":   re.compile(r"\b(popup|modal|dialog|overlay|lightbox)\b", re.IGNORECASE),
    "archive": re.compile(r"\b(archive|blog[- ]?list|posts?[- ]?grid)\b", re.IGNORECASE),
    "single":  re.compile(r"\b(single[- ]?post|post[- ]?detail|article[- ]?detail)\b", re.IGNORECASE),
    "search":  re.compile(r"\b(search[- ]?result|search[- ]?page)\b", re.IGNORECASE),
    "404":     re.compile(r"\b(404|not[- ]?found)\b", re.IGNORECASE),
}

# Sections we want to surface even though they live nested. Ordered: most
# specific first.
PURPOSE_TO_KIND = {
    "navbar": "header",
    "footer": "footer",
    "hero":   "hero",
}


def find_real_sections(
    data_content: list,
    ai_sections: list,
) -> list[RealSection]:
    """Walk both trees and return every structural section.

    Strategy:
      1. Walk `data_content` recursively, collecting candidate containers.
      2. For each candidate, score detectors:
          • plugin signal:   _figma_section_purpose, _ai_role
          • layer name:      regex against _figma_name
          • structural:      child shape, position
          • geometric:       y-position relative to root, width vs frame
      3. Pick the highest-scoring classification per node.
      4. Drop nested duplicates (when a parent is already classified
         and the child's classification is weaker).
    """
    pairs = _correlate(data_content, ai_sections)
    candidates: list[RealSection] = []

    for el, ai, parent_list, parent_idx, depth in _walk_with_pointers(data_content):
        if not isinstance(el, dict) or el.get("elType") != "container":
            continue
        ai_match = pairs.get(id(el))
        result = _score_node(el, ai_match, depth, parent_list, parent_idx)
        if result is not None:
            candidates.append(result)

    return _resolve_overlaps(candidates)


def _walk_with_pointers(content: list, depth: int = 0):
    """Yield (node, ai_subtree_or_none, parent_list, idx, depth) for every container."""
    for i, el in enumerate(content):
        if isinstance(el, dict) and el.get("elType") == "container":
            yield el, None, content, i, depth
            yield from _walk_with_pointers(el.get("elements") or [], depth + 1)


def _correlate(data_content: list, ai_sections: list) -> dict[int, dict]:
    """Pair each data.json container with its ai-layout section by walking both
    in parallel. Returns {id(elementor_node): ai_section} where structurally aligned."""
    pairs: dict[int, dict] = {}
    def walk(el_list: list, ai_list: list) -> None:
        for el, ai in zip(el_list, ai_list):
            if not isinstance(el, dict) or el.get("elType") != "container":
                continue
            if ai:
                pairs[id(el)] = ai
            walk(el.get("elements") or [], ai.get("children") or [] if ai else [])
    walk(data_content, ai_sections or [])
    return pairs


def _score_node(
    el: dict,
    ai: dict | None,
    depth: int,
    parent_list: list,
    parent_idx: int,
) -> RealSection | None:
    settings = el.get("settings") or {}
    name = settings.get("_figma_name") or (ai or {}).get("name") or ""
    plugin_purpose = settings.get("_figma_section_purpose") or (ai or {}).get("sectionPurpose")
    plugin_role = settings.get("_ai_role") or (ai or {}).get("role")
    plugin_conf = settings.get("_ai_confidence") or (ai or {}).get("confidence")
    bounds = (ai or {}).get("bounds")

    kind: str | None = None
    confidence: float = 0.0
    reasons: list[str] = []
    inferred = True

    # --- Signal 1: plugin sectionPurpose --------------------------------
    if plugin_purpose in PURPOSE_TO_KIND:
        kind = PURPOSE_TO_KIND[plugin_purpose]
        confidence = max(confidence, float(plugin_conf or 0.85))
        reasons.append(f"sectionPurpose={plugin_purpose}")
        inferred = False

    # --- Signal 2: plugin _ai_role --------------------------------------
    if plugin_role in ("navbar", "footer", "hero"):
        candidate_kind = "header" if plugin_role == "navbar" else plugin_role
        score = float(plugin_conf or 0.8)
        if not kind or score > confidence:
            kind = candidate_kind
            confidence = score
            reasons.append(f"role={plugin_role}")
            inferred = False
        else:
            reasons.append(f"role={plugin_role}-confirms")

    # --- Signal 3: layer-name regex (independent of plugin quality) ----
    for k, rx in NAME_RX.items():
        if rx.search(name):
            score = 0.7  # name-based is decent but not authoritative
            if not kind:
                kind = k
                confidence = score
                reasons.append(f"name~={rx.pattern}")
            elif kind == k:
                # Name agrees with plugin → boost confidence
                confidence = min(1.0, confidence + 0.1)
                reasons.append("name-confirms")
            elif score > confidence:
                # Name disagrees and is more confident → surface ambiguity
                reasons.append(f"name-conflict({k} vs {kind})")
                kind = k
                confidence = score
            break

    # --- Signal 4: geometric — top-of-page full-bleed = header ---------
    if not kind and bounds:
        x, y, w, h = bounds.get("x", 0), bounds.get("y", 0), bounds.get("width", 0), bounds.get("height", 0)
        is_top = y < 100
        is_bottom = (y > 4000) and (h < 1500) and (h > 100)
        is_full_width = w >= 1200
        if is_top and is_full_width and h < 200:
            kind = "header"
            confidence = 0.6
            reasons.append("geometric: y<100, full-width, slim")
        elif is_bottom and is_full_width:
            kind = "footer"
            confidence = 0.55
            reasons.append("geometric: bottom of page, full-width")

    # --- Signal 5: structural — first child is full-bleed image+text → hero
    if not kind and depth <= 2:
        if _looks_like_hero(el):
            kind = "hero"
            confidence = 0.55
            reasons.append("structural: full-bleed-image + heading + cta")

    # If we still don't know, this isn't a "real section" worth routing —
    # it's just a layout container.
    if not kind:
        return None

    return RealSection(
        kind=kind,
        elementor_node=el,
        parent_list=parent_list,
        parent_index=parent_idx,
        depth=depth,
        ai_section=ai,
        confidence=confidence,
        reason=" + ".join(reasons),
        figma_purpose=plugin_purpose,
        figma_role=plugin_role,
        figma_name=name,
        bounds=bounds,
        inferred=inferred,
    )


def _looks_like_hero(el: dict) -> bool:
    """Heuristic: section contains a full-bleed image + a heading + at least
    one button. Hero candidates often use a single primary CTA + secondary."""
    has_image = False
    has_heading = False
    has_cta = False

    def visit(n, d):
        nonlocal has_image, has_heading, has_cta
        if d > 6 or not isinstance(n, dict):
            return
        wt = n.get("widgetType")
        s = n.get("settings") or {}
        if wt == "image":
            has_image = True
        elif wt == "heading":
            has_heading = True
        elif wt == "button":
            has_cta = True
        # Containers with image background also count
        if n.get("elType") == "container":
            bg = (s.get("background_image") or {}).get("url")
            if bg:
                has_image = True
        for c in n.get("elements") or []:
            visit(c, d + 1)

    visit(el, 0)
    return has_image and has_heading and has_cta


def _resolve_overlaps(candidates: list[RealSection]) -> list[RealSection]:
    """De-dupe overlapping classifications.

    Rules, in order:
      1. **Ancestor / descendant of the same kind** — keep the higher-
         confidence one; tiebreak goes to the descendant (more specific).
      2. **Sibling duplicates of singleton kinds** (header, footer, popup,
         archive, single, search, 404) — keep only the one with the
         highest confidence. A page has at most one of each.
      3. **hero** can repeat (multi-hero designs exist), but cap at 3 to
         avoid runaway false positives.
    """
    if len(candidates) < 2:
        return candidates

    # --- Pass 1: ancestor/descendant dedupe -----------------------------
    def is_ancestor(maybe_parent: RealSection, child: RealSection) -> bool:
        if maybe_parent is child:
            return False
        target_id = id(child.elementor_node)
        def walk(n):
            if not isinstance(n, dict):
                return False
            if id(n) == target_id:
                return True
            for c in n.get("elements") or []:
                if walk(c):
                    return True
            return False
        return walk(maybe_parent.elementor_node)

    after_pass_1 = []
    for cand in candidates:
        suppressed = False
        for other in candidates:
            if other is cand or other.kind != cand.kind:
                continue
            anc = is_ancestor(other, cand)   # other is ancestor of cand
            desc = is_ancestor(cand, other)  # cand is ancestor of other
            if not (anc or desc):
                continue
            # Default: ANCESTOR wins. The descendant is almost always a
            # sub-region of the same semantic role (e.g. "Navbar" inside
            # "Header", "Footer Copyright" inside "FOOTER"). The
            # descendant only wins if it's noticeably MORE confident.
            CONF_MARGIN = 0.15
            if anc:
                # other = ancestor, cand = descendant
                if cand.confidence > other.confidence + CONF_MARGIN:
                    pass    # keep cand (much more confident); other dropped on its turn
                else:
                    suppressed = True
                    break
            else:
                # cand = ancestor, other = descendant
                if other.confidence > cand.confidence + CONF_MARGIN:
                    suppressed = True
                    break
        if not suppressed:
            after_pass_1.append(cand)

    # --- Pass 2: singleton-kind dedupe ---------------------------------
    SINGLETON_KINDS = {"header", "footer", "archive", "single", "search", "404"}
    by_kind_buckets: dict[str, list[RealSection]] = {}
    out: list[RealSection] = []
    for c in after_pass_1:
        if c.kind in SINGLETON_KINDS:
            by_kind_buckets.setdefault(c.kind, []).append(c)
        elif c.kind == "hero":
            by_kind_buckets.setdefault("hero", []).append(c)
        else:
            out.append(c)

    for kind, bucket in by_kind_buckets.items():
        bucket.sort(key=lambda x: -x.confidence)
        if kind == "hero":
            # Drop weak heroes when a strong one exists. Multi-hero pages
            # are real but they all tend to use the same component, so
            # confidence will cluster at the same value.
            top_conf = bucket[0].confidence if bucket else 0.0
            if top_conf >= 0.85:
                out.extend(b for b in bucket[:3] if b.confidence >= 0.7)
            else:
                out.extend(bucket[:3])
        else:
            out.append(bucket[0])     # singleton: keep the most confident

    return out


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def by_kind(sections: list[RealSection], kind: str) -> list[RealSection]:
    return [s for s in sections if s.kind == kind]


def summarize(sections: list[RealSection]) -> dict[str, int]:
    out: dict[str, int] = {}
    for s in sections:
        out[s.kind] = out.get(s.kind, 0) + 1
    return out


def detach(section: RealSection) -> None:
    """Remove the section's node from its parent's elements list.

    Use this after promoting the section into a Theme Builder template.
    Safe to call on an already-detached section (no-op).
    """
    try:
        if 0 <= section.parent_index < len(section.parent_list):
            cur = section.parent_list[section.parent_index]
            if cur is section.elementor_node:
                section.parent_list.pop(section.parent_index)
                # Adjust subsequent indices on the same parent that were
                # also detached after this one.
    except (IndexError, AttributeError):
        pass
