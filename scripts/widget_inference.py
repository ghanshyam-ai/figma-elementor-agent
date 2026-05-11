"""
Independent widget inference.

The plugin emits `preferredWidget` on ai-layout sections, but for most
real designs it stays at the default `container` — uninformative. This
module re-runs widget detection on the agent side, walks the Elementor
tree at *any* depth (not just top-level), and rewrites containers into
the right widget when the structural pattern is unambiguous.

Detection sources, in priority order:
    1. plugin's preferredWidget when it's something specific
    2. plugin's _figma_section_purpose
    3. agent's structural detectors (replaced from optimize.py)

Public API:
    infer_and_swap(content) → {widget_type → count}
"""
from __future__ import annotations

from typing import Iterator

from optimize import (
    _looks_like_icon_list, _convert_to_icon_list,
    _looks_like_accordion, _convert_to_accordion,
    _looks_like_tabs, _convert_to_tabs,
    _looks_like_image_carousel, _convert_to_image_carousel,
    _looks_like_slides, _convert_to_slides,
    _looks_like_counter, _convert_to_counter,
    _looks_like_progress, _convert_to_progress,
    _looks_like_star_rating, _convert_to_star_rating,
    _looks_like_social_icons, _convert_to_social_icons,
    _looks_like_video, _convert_to_video,
    _looks_like_image_box, _convert_to_image_box,
    _looks_like_icon_box, _convert_to_icon_box,
    _looks_like_toggle, _convert_to_toggle,
    _looks_like_divider, _convert_to_divider,
    _looks_like_spacer, _convert_to_spacer,
)


# Order matters: most-specific detectors first. Once a node is converted
# we stop checking the rest for it.
INFERENCE_PIPELINE = [
    ("tabs",            _looks_like_tabs,            _convert_to_tabs),
    ("slides",          _looks_like_slides,          _convert_to_slides),
    ("accordion",       _looks_like_accordion,       _convert_to_accordion),
    ("toggle",          _looks_like_toggle,          _convert_to_toggle),
    ("icon-list",       _looks_like_icon_list,       _convert_to_icon_list),
    ("icon-box",        _looks_like_icon_box,        _convert_to_icon_box),
    ("image-box",       _looks_like_image_box,       _convert_to_image_box),
    ("image-carousel",  _looks_like_image_carousel,  _convert_to_image_carousel),
    ("counter",         _looks_like_counter,         _convert_to_counter),
    ("progress",        _looks_like_progress,        _convert_to_progress),
    ("star-rating",     _looks_like_star_rating,     _convert_to_star_rating),
    ("social-icons",    _looks_like_social_icons,    _convert_to_social_icons),
    ("video",           _looks_like_video,           _convert_to_video),
    ("divider",         _looks_like_divider,         _convert_to_divider),
    ("spacer",          _looks_like_spacer,          _convert_to_spacer),
]


def infer_and_swap(content: list, structural_node_ids: set | None = None) -> dict[str, int]:
    """Walk the entire tree, swap matching containers for their inferred widget.

    `structural_node_ids` — `id(node)` set of containers that the section
    finder identified as real structural sections. We never swap those
    (header / footer / hero etc. are routed via architecture, not collapsed
    into a single widget).

    Returns counters keyed by widget type.
    """
    counters: dict[str, int] = {}
    structural_node_ids = structural_node_ids or set()

    # Two-pass: collect candidates first, then mutate. Mutating during walk
    # changes element identity and corrupts depth-first traversal.
    candidates: list[tuple[dict, str]] = []
    for node in _walk_containers(content):
        if id(node) in structural_node_ids:
            continue
        # Skip already-converted (during this run a node could become a widget).
        if node.get("elType") != "container":
            continue
        if not (node.get("elements") or []):
            continue
        for kind, detector, _converter in INFERENCE_PIPELINE:
            try:
                if detector(node):
                    candidates.append((node, kind))
                    break
            except Exception:
                continue

    # Apply the conversion. Container becomes a widget in place.
    for node, kind in candidates:
        converter = next(c for k, _, c in INFERENCE_PIPELINE if k == kind)
        try:
            if converter(node):
                counters[kind] = counters.get(kind, 0) + 1
        except Exception:
            continue

    return counters


def _walk_containers(content) -> Iterator[dict]:
    def walk(n):
        if isinstance(n, dict):
            if n.get("elType") == "container":
                yield n
            for c in n.get("elements") or []:
                yield from walk(c)
        elif isinstance(n, list):
            for it in n:
                yield from walk(it)
    yield from walk(content)
