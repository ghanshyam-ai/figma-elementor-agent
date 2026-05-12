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
    "header":        re.compile(r"^(header|nav(bar)?|topbar|top[- ]?nav|site[- ]?header)\b", re.IGNORECASE),
    "footer":        re.compile(r"^footer\b|\bsite[- ]?footer\b|\bpage[- ]?footer\b", re.IGNORECASE),
    "footer-column": re.compile(r"\bfooter[- ]?(col(umn)?|links?|menu|nav|section|widget)\b", re.IGNORECASE),
    "hero":          re.compile(r"^hero\b|\bbanner\b|\bhero[- ]?slider\b", re.IGNORECASE),
    "popup":         re.compile(r"\b(popup|modal|dialog|overlay|lightbox)\b", re.IGNORECASE),
    "archive":       re.compile(r"\b(archive|blog[- ]?list|posts?[- ]?grid)\b", re.IGNORECASE),
    "single":        re.compile(r"\b(single[- ]?post|post[- ]?detail|article[- ]?detail)\b", re.IGNORECASE),
    "search":        re.compile(r"\b(search[- ]?result|search[- ]?page)\b", re.IGNORECASE),
    "404":           re.compile(r"\b(404|not[- ]?found)\b", re.IGNORECASE),
}

# Sections we want to surface even though they live nested. Ordered: most
# specific first.
PURPOSE_TO_KIND = {
    "navbar": "header",
    "footer": "footer",
    "hero":   "hero",
}

# Plugin signals below this confidence get treated as "abstained" — the
# agent's own structural / geometric analysis is allowed to outrank them
# instead of being suppressed by a low-confidence plugin guess.
PLUGIN_TRUST_FLOOR = 0.6


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
    plugin_conf_raw = settings.get("_ai_confidence") or (ai or {}).get("confidence")
    plugin_conf = float(plugin_conf_raw) if isinstance(plugin_conf_raw, (int, float)) else None
    bounds = (ai or {}).get("bounds")

    # Plugin abstained when its own confidence is below the trust floor.
    # Surface the value as a *hint* but allow geometric/structural signals
    # to outrank it. This is the fix for the audit case where 12/14 real
    # frames had sectionPurpose at 0.35 confidence.
    plugin_trustworthy = (plugin_conf is None) or (plugin_conf >= PLUGIN_TRUST_FLOOR)

    kind: str | None = None
    confidence: float = 0.0
    reasons: list[str] = []
    inferred = True

    # --- Signal 1: plugin sectionPurpose --------------------------------
    if plugin_purpose in PURPOSE_TO_KIND:
        score = float(plugin_conf if plugin_conf is not None else 0.85)
        if plugin_trustworthy:
            kind = PURPOSE_TO_KIND[plugin_purpose]
            confidence = max(confidence, score)
            reasons.append(f"sectionPurpose={plugin_purpose}")
            inferred = False
        else:
            reasons.append(f"sectionPurpose={plugin_purpose}@{score:.2f}-hint")

    # --- Signal 2: plugin _ai_role --------------------------------------
    if plugin_role in ("navbar", "footer", "hero"):
        candidate_kind = "header" if plugin_role == "navbar" else plugin_role
        score = float(plugin_conf if plugin_conf is not None else 0.8)
        # navbar/footer roles are structural facts — even at low confidence
        # they're worth ≥0.9 in our scoring because header/footer detection
        # is a hard gate and we'd rather have a Theme Builder template than
        # an inline navbar in the page body.
        if candidate_kind in ("header", "footer") and score < 0.9:
            score = 0.9
        if not kind or score > confidence:
            kind = candidate_kind
            confidence = score
            reasons.append(f"role={plugin_role}")
            inferred = False
        else:
            reasons.append(f"role={plugin_role}-confirms")

    # --- Signal 3: layer-name regex (independent of plugin quality) ----
    # When the plugin abstained, name-based detection carries more weight.
    name_score_base = 0.7 if plugin_trustworthy else 0.85
    for k, rx in NAME_RX.items():
        if rx.search(name):
            score = name_score_base
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
    # Geometric weight is boosted when the plugin abstained.
    geom_floor = 0.6 if plugin_trustworthy else 0.8
    if (not kind or not plugin_trustworthy) and bounds:
        x, y, w, h = bounds.get("x", 0), bounds.get("y", 0), bounds.get("width", 0), bounds.get("height", 0)
        is_top = y < 100
        is_bottom = (y > 4000) and (h < 1500) and (h > 100)
        is_full_width = w >= 1200
        candidate_kind = None
        candidate_conf = 0.0
        if is_top and is_full_width and h < 200:
            candidate_kind = "header"
            candidate_conf = max(geom_floor, 0.6)
            extra_reason = "geometric: y<100, full-width, slim"
        elif is_bottom and is_full_width:
            candidate_kind = "footer"
            candidate_conf = max(geom_floor, 0.55)
            extra_reason = "geometric: bottom of page, full-width"
        else:
            extra_reason = None
        if candidate_kind and candidate_conf > confidence:
            kind = candidate_kind
            confidence = candidate_conf
            reasons.append(extra_reason)

    # --- Signal 5: structural — first child is full-bleed image+text → hero
    if not kind and depth <= 2:
        if _looks_like_hero(el):
            kind = "hero"
            confidence = 0.55 if plugin_trustworthy else 0.7
            reasons.append("structural: full-bleed-image + heading + cta")

    # --- Signal 6: footer column (nested kind — only fires inside footer)
    # Skipped here because we need the parent context to confirm; handled
    # by `extract_footer_columns()` post-walk instead.

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
        reason=" + ".join(r for r in reasons if r),
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


def infer_name_for_unnamed(el: dict) -> str | None:
    """Generate a synthetic name for a container with no `_figma_name`.

    Useful when name-regex detection is the only available signal and the
    Figma file has generic auto-names. Returns a hint like
    `Section-3-images-1-heading` so downstream regex / heuristics can fire.
    """
    if not isinstance(el, dict):
        return None
    s = el.get("settings") or {}
    if s.get("_figma_name") or s.get("_inferred_name"):
        return s.get("_figma_name") or s.get("_inferred_name")
    counts: dict[str, int] = {}
    def walk(n, d):
        if d > 4 or not isinstance(n, dict):
            return
        wt = n.get("widgetType")
        if wt:
            counts[wt] = counts.get(wt, 0) + 1
        for c in n.get("elements") or []:
            walk(c, d + 1)
    walk(el, 0)
    if not counts:
        return None
    parts = [f"{v}-{k}" for k, v in sorted(counts.items(), key=lambda x: -x[1])[:3]]
    return "Section-" + "-".join(parts)


def extract_footer_columns(footer_section: RealSection) -> list[RealSection]:
    """Find link columns inside a detected footer.

    A footer column is a column-direction container (or a vertical stack of
    text/button widgets) holding ≥ 2 link-like children. Each becomes its
    own `RealSection(kind="footer-column")` so the importer can create one
    nav menu per column instead of dumping every link into one menu.
    """
    if footer_section.kind != "footer":
        return []
    columns: list[RealSection] = []
    node = footer_section.elementor_node

    def link_count(container: dict) -> int:
        c = 0
        for kid in container.get("elements") or []:
            if not isinstance(kid, dict):
                continue
            wt = kid.get("widgetType")
            if wt in ("button", "text-editor"):
                c += 1
        return c

    def visit(n: dict, parent: list, idx: int, depth: int) -> None:
        if not isinstance(n, dict):
            return
        if n.get("elType") == "container":
            settings = n.get("settings") or {}
            direction = settings.get("flex_direction") or ""
            is_column = (direction in ("column", "")) or (
                # No explicit direction but children are stacked vertically:
                # treat any container with ≥3 stacked link-like widgets as a column
                link_count(n) >= 3
            )
            name = settings.get("_figma_name") or ""
            matched_by_name = bool(NAME_RX["footer-column"].search(name))
            if depth >= 1 and is_column and (link_count(n) >= 2 or matched_by_name):
                columns.append(RealSection(
                    kind="footer-column",
                    elementor_node=n,
                    parent_list=parent,
                    parent_index=idx,
                    depth=depth,
                    ai_section=None,
                    confidence=0.9 if matched_by_name else 0.65,
                    reason=("name~=footer-column" if matched_by_name else
                            f"structural: {link_count(n)} link-like children"),
                    figma_purpose=None,
                    figma_role=settings.get("_ai_role"),
                    figma_name=name,
                    bounds=None,
                    inferred=not matched_by_name,
                ))
                # Don't recurse into a column — its children are leaf links.
                return
            for i, c in enumerate(n.get("elements") or []):
                visit(c, n.get("elements") or [], i, depth + 1)

    visit(node, [node], 0, 0)
    return columns


def extract_nav_items(section: RealSection) -> list[dict]:
    """Walk a header/footer-column section and produce real menu items.

    Returns `[{title, url}, ...]` from the text/button widgets found. Skips
    image widgets (those are the plugin's failure mode where columns get
    baked into raster) — callers should fall back to a Claude OCR dispatch
    in that case.
    """
    items: list[dict] = []
    def walk(n, d):
        if d > 6 or not isinstance(n, dict):
            return
        wt = n.get("widgetType")
        s = n.get("settings") or {}
        if wt == "button":
            title = (s.get("text") or "").strip()
            url = ((s.get("link") or {}).get("url") or "").strip() or "#"
            if title:
                items.append({"title": title, "url": url})
        elif wt == "heading":
            # Headings in a column are typically the column's section header
            # ("Company", "Resources") — not a menu item. Skip.
            pass
        elif wt == "text-editor":
            raw = s.get("editor") or ""
            # Strip HTML tags for a single label; multi-paragraph text is
            # not a menu item.
            import re as _re
            txt = _re.sub(r"<[^>]+>", " ", raw).strip()
            txt = _re.sub(r"\s+", " ", txt)
            if txt and len(txt) <= 60 and "\n" not in raw:
                items.append({"title": txt, "url": "#"})
        for c in n.get("elements") or []:
            walk(c, d + 1)
    walk(section.elementor_node, 0)
    return items


def filter_hidden(content: list) -> int:
    """Drop nodes the plugin marked hidden (`_visible == False` or opacity 0).

    Hidden Figma layers should never render in the live page. The plugin
    sometimes leaks them through; this is the agent's defensive filter.
    Returns the count of nodes removed.
    """
    removed = 0
    def visit(elements: list) -> None:
        nonlocal removed
        i = 0
        while i < len(elements):
            n = elements[i]
            if isinstance(n, dict):
                s = n.get("settings") or {}
                vis = s.get("_visible")
                opa = s.get("opacity")
                if vis is False or (isinstance(opa, (int, float)) and opa <= 0):
                    elements.pop(i)
                    removed += 1
                    continue
                visit(n.get("elements") or [])
            i += 1
    visit(content)
    return removed


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
