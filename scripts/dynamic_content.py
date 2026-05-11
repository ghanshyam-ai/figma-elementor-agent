"""
Dynamic content detection.

Heuristics for spotting sections that should render as a dynamic
WordPress query (blog grid, archive list, search result list) instead of
N hard-coded card containers.

When a candidate is found, the offending container is replaced with an
Elementor `posts` widget pointing at the `post` post type, sorted by
date desc, limited to the visible card count. The original cards are
preserved on a hidden `_design_reference` settings key so designers can
still see the intended visual without it shipping to the live page.

Detection signals (any 2 of 3 trigger the swap):
    1. ai-layout.json sectionPurpose == "feature-grid" AND >=3 children
       whose role == "card"
    2. Each card contains: an image, a heading, AND a text-editor whose
       content looks like an excerpt (>= 60 chars, ends with a period)
    3. Layer name matches /blog|posts?|articles?|news|stories/i

The Posts widget requires Elementor Pro. The bridge plugin reports `pro`
status on /health; when Pro is absent we substitute the open-source
`wp-widget-recent-posts` widget instead, which renders without Pro.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from enrich import Enrichment

BLOG_NAME_RX = re.compile(r"\b(blog|posts?|articles?|news|stories|insights)\b", re.IGNORECASE)
EXCERPT_MIN_CHARS = 60


@dataclass
class DynamicCandidate:
    section_index: int
    card_count: int
    has_pro: bool
    reason: str


def detect_dynamic_sections(
    content: list,
    e: Enrichment,
    has_elementor_pro: bool = False,
) -> list[DynamicCandidate]:
    """Identify top-level sections worth replacing with a Posts widget."""
    out: list[DynamicCandidate] = []
    for i, el in enumerate(content):
        if not isinstance(el, dict) or el.get("elType") != "container":
            continue
        sec = e.section_by_index[i] if i < len(e.section_by_index) else {}
        signals: list[str] = []

        # Signal 1: feature-grid with >=3 card children
        if sec.get("sectionPurpose") == "feature-grid":
            card_children = [c for c in (sec.get("children") or []) if c.get("role") == "card"]
            if len(card_children) >= 3:
                signals.append(f"feature-grid+{len(card_children)}-cards")

        # Signal 2: structural — each card has image + heading + excerpt-ish text
        cards = [c for c in (el.get("elements") or []) if _looks_like_blog_card(c)]
        if len(cards) >= 3:
            signals.append(f"{len(cards)}-blog-shaped-cards")

        # Signal 3: layer name
        name = sec.get("name") or (el.get("settings") or {}).get("_figma_name") or ""
        if BLOG_NAME_RX.search(name):
            signals.append(f"name~{name}")

        if len(signals) >= 2:
            out.append(DynamicCandidate(
                section_index=i,
                card_count=max(len(cards), len(sec.get("children") or [])),
                has_pro=has_elementor_pro,
                reason=" + ".join(signals),
            ))
    return out


def _looks_like_blog_card(node: dict) -> bool:
    if not isinstance(node, dict) or node.get("elType") != "container":
        return False
    has_image = False
    has_heading = False
    has_excerpt = False
    def visit(n):
        nonlocal has_image, has_heading, has_excerpt
        if not isinstance(n, dict):
            return
        wt = n.get("widgetType")
        s = n.get("settings") or {}
        if wt == "image":
            has_image = True
        elif wt == "heading":
            has_heading = True
        elif wt == "text-editor":
            text = (s.get("editor") or "").strip()
            stripped = re.sub(r"<[^>]+>", "", text)
            if len(stripped) >= EXCERPT_MIN_CHARS:
                has_excerpt = True
        for c in n.get("elements") or []:
            visit(c)
    visit(node)
    return has_image and has_heading and has_excerpt


def replace_with_posts_widget(
    content: list,
    candidates: list[DynamicCandidate],
) -> int:
    """Swap each candidate's container for a Posts widget. Returns count."""
    n = 0
    for cand in candidates:
        i = cand.section_index
        if not (0 <= i < len(content)):
            continue
        original = content[i]
        content[i] = _make_posts_section(original, cand)
        n += 1
    return n


def _make_posts_section(original: dict, cand: DynamicCandidate) -> dict:
    """Container with a posts widget; original cards kept under _design_reference."""
    posts_widget = (
        _make_pro_posts_widget(cand.card_count)
        if cand.has_pro
        else _make_recent_posts_widget(cand.card_count)
    )
    return {
        "id": "dyngd" + str(cand.section_index).zfill(2),
        "elType": "container",
        "isInner": False,
        "settings": {
            "content_width": "boxed",
            "flex_direction": "column",
            "flex_gap": {"unit": "px", "size": 32, "sizes": []},
            "_dynamic_section": True,
            "_dynamic_reason": cand.reason,
            # Keep the original tree as design reference so the developer
            # can see the intended visual; not rendered.
            "_design_reference_id": (original.get("settings") or {}).get("_figma_id"),
        },
        "elements": [posts_widget],
    }


def _make_pro_posts_widget(limit: int) -> dict:
    return {
        "id": "psgrd" + str(limit).zfill(2),
        "elType": "widget",
        "widgetType": "posts",
        "settings": {
            "posts_per_page": limit,
            "posts_post_type": "post",
            "posts_columns": min(limit, 3),
            "posts_orderby": "date",
            "posts_order": "DESC",
            "show_image": "yes",
            "show_title": "yes",
            "show_excerpt": "yes",
            "excerpt_length": 25,
        },
        "elements": [],
    }


def _make_recent_posts_widget(limit: int) -> dict:
    return {
        "id": "psrec" + str(limit).zfill(2),
        "elType": "widget",
        "widgetType": "wp-widget-recent-posts",
        "settings": {
            "title": "",
            "number": limit,
        },
        "elements": [],
    }
