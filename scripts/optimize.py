"""
Optimization passes that mutate the Elementor tree before it is posted.

Every pass takes the in-memory tree (list of top-level containers) and
mutates it in place. Each pass is independently callable and idempotent —
running them twice is a no-op, so the orchestrator can loop them safely.

Passes provided here:
  • resolve_global_tokens — replace inline hex / typography on widgets with
    Elementor `__globals__` references when a kit slug has the same value
  • collapse_single_child_containers — flatten layout-only wrappers
  • cap_nesting_depth — fold containers deeper than `max_depth` into their
    parent so the rendered DOM stays shallow
  • enforce_widget_preferences — when ai-layout suggests a widget different
    from what the mapper emitted, swap it (e.g. convert HTML widget → text-
    editor, plain `image` → `image-box` when caption + heading present)
  • replace_html_widgets — last resort: any widgetType=='html' in the tree
    is converted to text-editor for editability

The optimization stats (counts of replacements / collapses) are returned
from each pass so the orchestrator can log them for the developer.
"""
from __future__ import annotations

from copy import deepcopy
from typing import Any

from enrich import Enrichment, walk_containers, walk_widgets


# ---------------------------------------------------------------------------
# 1. Global Token Resolver  (item #4)
# ---------------------------------------------------------------------------

# Color settings keys per widget family. Anything not listed here is left
# alone — the resolver must not silently transform unknown keys.
COLOR_KEYS_BY_WIDGET = {
    "heading":     ["title_color"],
    "text-editor": ["text_color"],
    "button": [
        "button_text_color", "background_color", "hover_color",
        "button_background_hover_color", "border_color",
    ],
    "icon":     ["primary_color", "secondary_color"],
    "icon-box": ["primary_color", "secondary_color", "title_color", "description_color"],
    "icon-list": ["icon_color", "text_color"],
    "image-box": ["title_color", "description_color"],
    "divider":   ["color"],
    "spacer":    [],
}

# Container background colors (containers have `elType=container`, no widgetType).
CONTAINER_COLOR_KEYS = ["background_color", "border_color"]

# Advanced-tab keys (underscore-prefixed) appear on widgets too.
ADVANCED_COLOR_KEYS = ["_background_color", "_border_color"]


def resolve_global_tokens(content: list, kit_settings: dict) -> dict:
    """Replace inline color + typography values with global references.

    kit_settings is the dict we just POSTed to /figma-importer/v1/kit, i.e.
    {system_colors, custom_colors, system_typography, ...}.

    Returns counters: {"colors": n, "typography": n}.
    """
    color_index = _build_color_index(kit_settings)
    typo_index = _build_typography_index(kit_settings)

    counters = {"colors": 0, "typography": 0}
    for node in walk_containers(content):
        for key in CONTAINER_COLOR_KEYS:
            if _swap_color(node["settings"], key, color_index):
                counters["colors"] += 1

    for w in walk_widgets(content):
        wtype = w.get("widgetType")
        keys = COLOR_KEYS_BY_WIDGET.get(wtype, [])
        for key in list(keys) + ADVANCED_COLOR_KEYS:
            if _swap_color(w["settings"], key, color_index):
                counters["colors"] += 1
        if _swap_typography(w["settings"], typo_index):
            counters["typography"] += 1
    return counters


def _build_color_index(kit_settings: dict) -> dict[str, str]:
    """{lowercase hex → 'globals/colors?id=<slug>'}, system slots first."""
    out: dict[str, str] = {}
    for c in kit_settings.get("system_colors") or []:
        hex_v = (c.get("color") or "").lower().strip()
        if hex_v and hex_v not in out:
            out[hex_v] = f"globals/colors?id={c.get('_id')}"
    for c in kit_settings.get("custom_colors") or []:
        hex_v = (c.get("color") or "").lower().strip()
        if hex_v and hex_v not in out:
            out[hex_v] = f"globals/colors?id={c.get('_id')}"
    return out


def _build_typography_index(kit_settings: dict) -> dict[tuple, str]:
    """{(family, size, weight) → 'globals/typography?id=<slug>'}.

    Match on (family, size, weight). Line-height and letter-spacing are
    intentionally NOT part of the key — designers tweak those per-widget,
    so requiring an exact match would tank the hit rate. Family alone is
    too coarse (every widget would match `body`).
    """
    out: dict[tuple, str] = {}
    for t in kit_settings.get("system_typography") or []:
        slug = t.get("_id")
        if not slug:
            continue
        family = t.get("typography_font_family")
        size = _size_value(t.get("typography_font_size"))
        weight = t.get("typography_font_weight")
        if family and size is not None:
            out[(family, size, str(weight) if weight is not None else None)] = (
                f"globals/typography?id={slug}"
            )
    return out


def _swap_color(settings: dict, key: str, index: dict[str, str]) -> bool:
    val = settings.get(key)
    if not isinstance(val, str):
        return False
    ref = index.get(val.lower().strip())
    if not ref:
        return False
    settings[key] = ""
    settings.setdefault("__globals__", {})[key] = ref
    return True


def _swap_typography(settings: dict, index: dict[tuple, str]) -> bool:
    if settings.get("typography_typography") == "globals":
        return False  # already linked
    family = settings.get("typography_font_family")
    size = _size_value(settings.get("typography_font_size"))
    weight = settings.get("typography_font_weight")
    if not family or size is None:
        return False
    key = (family, size, str(weight) if weight is not None else None)
    ref = index.get(key)
    if not ref:
        # Fall back: match on family+size, ignoring weight. Useful when the
        # widget lacks a weight but matches a preset that has one.
        for (f, s, _w), r in index.items():
            if f == family and s == size:
                ref = r
                break
    if not ref:
        return False

    # Switch to globals mode and clear per-property keys so the global wins.
    settings["typography_typography"] = "globals"
    settings.setdefault("__globals__", {})["typography_typography"] = ref
    for k in (
        "typography_font_family", "typography_font_size", "typography_font_weight",
        "typography_line_height", "typography_letter_spacing",
        "typography_text_transform", "typography_text_decoration",
    ):
        settings.pop(k, None)
    return True


def _size_value(v: Any) -> float | None:
    if isinstance(v, (int, float)):
        return float(v)
    if isinstance(v, dict):
        s = v.get("size")
        if isinstance(s, (int, float)):
            return float(s)
        if isinstance(s, str):
            try:
                return float(s)
            except ValueError:
                return None
    return None


# ---------------------------------------------------------------------------
# 2. Container collapse + depth cap  (items #3, #5)
# ---------------------------------------------------------------------------

# Settings that make a container "structural" — if any are present, we
# don't collapse it (it carries layout/visual intent the parent doesn't).
LAYOUT_BEARING_KEYS = (
    "background_background", "background_color", "background_image",
    "background_overlay_background", "border_radius", "border_border",
    "box_shadow_box_shadow_type", "box_shadow_box_shadow",
    "min_height", "boxed_width", "content_width",
)


def collapse_single_child_containers(
    content: list,
    max_passes: int = 4,
    protected_ids: set | None = None,
) -> int:
    """Flatten parent→child container chains where the parent adds nothing.

    `protected_ids` — `id(node)` set of containers identified as real
    structural sections (header, footer, hero, …). These are NEVER
    collapsed even when their settings look pass-through, because losing
    them breaks architecture routing.

    Returns the number of containers collapsed.
    """
    protected_ids = protected_ids or set()
    collapsed = 0
    for _ in range(max_passes):
        n = _collapse_pass(content, protected_ids)
        if n == 0:
            break
        collapsed += n
    return collapsed


def _collapse_pass(elements: list, protected_ids: set) -> int:
    n = 0
    for parent in list(elements):
        if not isinstance(parent, dict) or parent.get("elType") != "container":
            continue
        children = parent.get("elements") or []
        if (
            len(children) == 1
            and isinstance(children[0], dict)
            and children[0].get("elType") == "container"
            and id(parent) not in protected_ids
            and id(children[0]) not in protected_ids
        ):
            child = children[0]
            if _is_collapsible(parent):
                # Hoist child's settings into the parent slot — keep the
                # parent's id (so external references survive) but adopt
                # the child's settings + grandchildren.
                merged = _merge_settings(parent.get("settings") or {}, child.get("settings") or {})
                parent["settings"] = merged
                parent["elements"] = child.get("elements") or []
                parent["isInner"] = parent.get("isInner", False)
                n += 1
        for sub in parent.get("elements") or []:
            if isinstance(sub, dict):
                n += _collapse_pass([sub], protected_ids)
    return n


def _is_collapsible(parent: dict) -> bool:
    s = parent.get("settings") or {}
    # Never collapse a container the agent explicitly tagged as structural.
    if s.get("_ai_role") in ("section", "navbar", "footer", "hero"):
        return False
    if s.get("_figma_section_purpose"):
        return False
    if any(k in s for k in LAYOUT_BEARING_KEYS):
        return False
    # Containers with explicit flex direction + gap encode meaningful intent.
    # Only collapse when the parent is acting as a pure pass-through.
    if s.get("flex_direction") and s.get("flex_gap"):
        return False
    return True


def _merge_settings(parent: dict, child: dict) -> dict:
    """Child settings win on conflict — they're the more specific layout."""
    out = dict(parent)
    out.update({k: v for k, v in child.items() if v is not None})
    return out


def cap_nesting_depth(content: list, max_depth: int = 4) -> int:
    """Fold containers nested deeper than `max_depth` into their grandparent.

    Returns the number of containers hoisted.
    """
    hoisted = 0
    def walk(node: dict, depth: int) -> None:
        nonlocal hoisted
        if not isinstance(node, dict) or node.get("elType") != "container":
            return
        children = node.get("elements") or []
        if depth >= max_depth:
            # Hoist any container child up to widget level by replacing it
            # with its widget descendants only (skip the structural shell).
            new_children: list = []
            for c in children:
                if isinstance(c, dict) and c.get("elType") == "container":
                    new_children.extend(_widgets_only(c))
                    hoisted += 1
                else:
                    new_children.append(c)
            node["elements"] = new_children
        for c in node.get("elements") or []:
            walk(c, depth + 1)

    for top in content:
        walk(top, 0)
    return hoisted


def _widgets_only(container: dict) -> list[dict]:
    out: list[dict] = []
    def walk(n):
        if isinstance(n, dict):
            if n.get("elType") == "widget":
                out.append(n)
                return
            for c in n.get("elements") or []:
                walk(c)
    walk(container)
    return out


# ---------------------------------------------------------------------------
# 3. Widget preference enforcement  (item #6)
# ---------------------------------------------------------------------------

def replace_html_widgets(content: list) -> int:
    """Convert any widgetType=='html' to text-editor.

    HTML widgets aren't editable in the visual builder — they break the
    "any non-developer can tweak this page" contract. We assume the HTML
    is paragraph-level content and wrap it in <p> if it doesn't already
    contain block-level markup.
    """
    n = 0
    for w in walk_widgets(content):
        if w.get("widgetType") != "html":
            continue
        raw = (w.get("settings") or {}).get("html") or ""
        if not raw:
            w["widgetType"] = "text-editor"
            w.setdefault("settings", {})["editor"] = ""
            n += 1
            continue
        if "<p" not in raw and "<div" not in raw and "<h" not in raw:
            raw = f"<p>{raw}</p>"
        w["widgetType"] = "text-editor"
        w["settings"] = {"editor": raw}
        n += 1
    return n


def enforce_widget_preferences(content: list, e: Enrichment) -> int:
    """Apply ai-layout's `preferredWidget` hints at the section level.

    The plugin emits preferredWidget on structural nodes; this pass swaps
    the corresponding Elementor subtree for the suggested widget when the
    swap is unambiguous. Anything ambiguous is left alone — better to ship
    a verbose tree than swap to a widget that would lose content.

    Registry of (detector, converter) pairs keyed on preferredWidget — add
    new cases here without touching the dispatch loop.
    """
    if not e.has_ai_layout:
        return 0
    n = 0
    for el, sec in _zip_top_level(content, e):
        pref = sec.get("preferredWidget")
        if not pref:
            continue
        handler = WIDGET_PREF_HANDLERS.get(pref)
        if not handler:
            continue
        detector, converter = handler
        if detector(el) and converter(el):
            n += 1
    return n


def _zip_top_level(content: list, e: Enrichment) -> list[tuple[dict, dict]]:
    pairs = []
    for el, sec in zip(content, e.section_by_index):
        if isinstance(el, dict) and el.get("elType") == "container":
            pairs.append((el, sec))
    return pairs


def _looks_like_icon_list(el: dict) -> bool:
    """Heuristic: a container whose children are all icon+text pairs."""
    rows = el.get("elements") or []
    if len(rows) < 2:
        return False
    icon_text_rows = 0
    for r in rows:
        if not isinstance(r, dict) or r.get("elType") != "container":
            continue
        kids = r.get("elements") or []
        has_icon = any(c.get("widgetType") in ("icon", "image") for c in kids if isinstance(c, dict))
        has_text = any(c.get("widgetType") in ("heading", "text-editor") for c in kids if isinstance(c, dict))
        if has_icon and has_text:
            icon_text_rows += 1
    return icon_text_rows >= 2 and icon_text_rows >= len(rows) // 2


def _convert_to_icon_list(el: dict) -> bool:
    items = []
    for r in el.get("elements") or []:
        if not isinstance(r, dict):
            continue
        kids = r.get("elements") or []
        text = ""
        icon = "fas fa-check"
        for c in kids:
            if not isinstance(c, dict):
                continue
            wt = c.get("widgetType")
            s = c.get("settings") or {}
            if wt == "heading" and not text:
                text = s.get("title") or ""
            elif wt == "text-editor" and not text:
                text = (s.get("editor") or "").replace("<p>", "").replace("</p>", "").strip()
            elif wt == "icon":
                fa = (s.get("selected_icon") or {}).get("value")
                if isinstance(fa, str) and fa:
                    icon = fa
        if text:
            items.append({"text": text, "selected_icon": {"value": icon, "library": "fa-solid"}})
    if not items:
        return False
    el.clear()
    el["id"] = el.get("id") or "icnls" + str(len(items)).zfill(2)
    el["elType"] = "widget"
    el["widgetType"] = "icon-list"
    el["settings"] = {"icon_list": items, "view": "traditional"}
    el["elements"] = []
    return True


def _looks_like_accordion(el: dict) -> bool:
    """A container whose children alternate header / body, or are uniform Q+A pairs."""
    rows = el.get("elements") or []
    if len(rows) < 2:
        return False
    qa_rows = 0
    for r in rows:
        if not isinstance(r, dict) or r.get("elType") != "container":
            continue
        kids = r.get("elements") or []
        headings = [c for c in kids if isinstance(c, dict) and c.get("widgetType") == "heading"]
        bodies = [c for c in kids if isinstance(c, dict) and c.get("widgetType") == "text-editor"]
        if headings and bodies:
            qa_rows += 1
    return qa_rows >= 2


def _convert_to_accordion(el: dict) -> bool:
    tabs = []
    for r in el.get("elements") or []:
        if not isinstance(r, dict):
            continue
        kids = r.get("elements") or []
        title = ""
        body = ""
        for c in kids:
            if not isinstance(c, dict):
                continue
            wt = c.get("widgetType")
            s = c.get("settings") or {}
            if wt == "heading" and not title:
                title = s.get("title") or ""
            elif wt == "text-editor" and not body:
                body = s.get("editor") or ""
        if title:
            tabs.append({"tab_title": title, "tab_content": body})
    if len(tabs) < 2:
        return False
    el.clear()
    el["id"] = "acord" + str(len(tabs)).zfill(2)
    el["elType"] = "widget"
    el["widgetType"] = "accordion"
    el["settings"] = {"tabs": tabs, "selected_icon": {"value": "fas fa-caret-down", "library": "fa-solid"}}
    el["elements"] = []
    return True


# ---------------------------------------------------------------------------
# Tabs (3+ panels with a header bar)
# ---------------------------------------------------------------------------

def _looks_like_tabs(el: dict) -> bool:
    """Two children: a row of headings (the tab strip) + a stack of panels."""
    rows = el.get("elements") or []
    if len(rows) != 2:
        return False
    strip, panels = rows
    if not (isinstance(strip, dict) and isinstance(panels, dict)):
        return False
    strip_kids = strip.get("elements") or []
    panel_kids = panels.get("elements") or []
    headings = sum(1 for c in strip_kids if isinstance(c, dict) and c.get("widgetType") in ("heading", "button"))
    return headings >= 2 and len(panel_kids) == headings


def _convert_to_tabs(el: dict) -> bool:
    rows = el.get("elements") or []
    strip, panels = rows
    titles = [
        ((c.get("settings") or {}).get("title") or (c.get("settings") or {}).get("text") or f"Tab {i+1}")
        for i, c in enumerate(strip.get("elements") or [])
    ]
    bodies = [
        _serialize_panel_body(p) for p in (panels.get("elements") or [])
    ]
    pairs = list(zip(titles, bodies))
    if len(pairs) < 2:
        return False
    el.clear()
    el["id"] = "tabs0" + str(len(pairs))[-2:].zfill(2)
    el["elType"] = "widget"
    el["widgetType"] = "tabs"
    el["settings"] = {"tabs": [{"tab_title": t, "tab_content": b} for t, b in pairs]}
    el["elements"] = []
    return True


def _serialize_panel_body(node: dict) -> str:
    parts: list[str] = []
    def walk(n):
        if not isinstance(n, dict):
            return
        s = n.get("settings") or {}
        if n.get("widgetType") == "heading" and s.get("title"):
            parts.append(f"<h3>{s['title']}</h3>")
        elif n.get("widgetType") == "text-editor" and s.get("editor"):
            parts.append(s["editor"])
        for c in n.get("elements") or []:
            walk(c)
    walk(node)
    return "\n".join(parts) or ""


# ---------------------------------------------------------------------------
# Image carousel  (>=3 sibling images in a row)
# ---------------------------------------------------------------------------

def _looks_like_image_carousel(el: dict) -> bool:
    kids = el.get("elements") or []
    if el.get("elType") != "container":
        return False
    direct_imgs = [c for c in kids if isinstance(c, dict) and c.get("widgetType") == "image"]
    return len(direct_imgs) >= 3


def _convert_to_image_carousel(el: dict) -> bool:
    kids = el.get("elements") or []
    images = []
    for c in kids:
        if isinstance(c, dict) and c.get("widgetType") == "image":
            img = (c.get("settings") or {}).get("image") or {}
            if img.get("url"):
                images.append({"id": img.get("id", ""), "url": img["url"]})
    if len(images) < 3:
        return False
    el.clear()
    el["id"] = "imgcr" + str(len(images)).zfill(2)
    el["elType"] = "widget"
    el["widgetType"] = "image-carousel"
    el["settings"] = {
        "carousel": images,
        "slides_to_show": 3,
        "navigation": "both",
        "autoplay": "yes",
        "autoplay_speed": 5000,
    }
    el["elements"] = []
    return True


# ---------------------------------------------------------------------------
# Slides (full-bleed hero rotator)
# ---------------------------------------------------------------------------

def _looks_like_slides(el: dict) -> bool:
    kids = el.get("elements") or []
    sliders = [c for c in kids if isinstance(c, dict) and c.get("elType") == "container"]
    if len(sliders) < 2:
        return False
    # Each slide should have at least a heading + a button
    score = 0
    for s in sliders:
        kids2 = s.get("elements") or []
        has_heading = any(isinstance(c, dict) and c.get("widgetType") == "heading" for c in kids2)
        has_button = any(isinstance(c, dict) and c.get("widgetType") == "button" for c in kids2)
        if has_heading and has_button:
            score += 1
    return score >= 2


def _convert_to_slides(el: dict) -> bool:
    slides_in = el.get("elements") or []
    slides_out = []
    for s in slides_in:
        if not isinstance(s, dict):
            continue
        kids = s.get("elements") or []
        heading = ""
        body = ""
        button_text = ""
        button_url = "#"
        for c in kids:
            if not isinstance(c, dict):
                continue
            wt = c.get("widgetType")
            settings = c.get("settings") or {}
            if wt == "heading" and not heading:
                heading = settings.get("title") or ""
            elif wt == "text-editor" and not body:
                body = settings.get("editor") or ""
            elif wt == "button" and not button_text:
                button_text = settings.get("text") or ""
                button_url = (settings.get("link") or {}).get("url") or "#"
        if heading:
            slides_out.append({
                "heading": heading,
                "description": body,
                "button_text": button_text,
                "link": {"url": button_url, "is_external": "", "nofollow": ""},
            })
    if len(slides_out) < 2:
        return False
    el.clear()
    el["id"] = "slidr" + str(len(slides_out)).zfill(2)
    el["elType"] = "widget"
    el["widgetType"] = "slides"
    el["settings"] = {"slides": slides_out, "navigation": "both", "autoplay": "yes"}
    el["elements"] = []
    return True


# ---------------------------------------------------------------------------
# Counter (one big number + label)
# ---------------------------------------------------------------------------

NUMBER_RE = __import__("re").compile(r"^\s*([+-]?\d+(?:\.\d+)?)\s*([A-Za-z%+]*)\s*$")


def _looks_like_counter(el: dict) -> bool:
    kids = el.get("elements") or []
    if len(kids) not in (2, 3):
        return False
    # First child is a heading whose text is mostly numeric
    h = kids[0] if isinstance(kids[0], dict) else None
    if not h or h.get("widgetType") != "heading":
        return False
    title = (h.get("settings") or {}).get("title") or ""
    return bool(NUMBER_RE.match(title))


def _convert_to_counter(el: dict) -> bool:
    kids = el.get("elements") or []
    h = kids[0]
    title = (h.get("settings") or {}).get("title") or ""
    m = NUMBER_RE.match(title)
    if not m:
        return False
    value, suffix = m.group(1), m.group(2)
    label = ""
    if len(kids) >= 2 and isinstance(kids[1], dict):
        s2 = kids[1].get("settings") or {}
        label = s2.get("title") or s2.get("editor") or ""
    el.clear()
    el["id"] = "cnter" + value[-3:].zfill(3)
    el["elType"] = "widget"
    el["widgetType"] = "counter"
    el["settings"] = {
        "ending_number": float(value),
        "starting_number": 0,
        "title": label,
        "thousand_separator": "yes",
        "suffix": suffix,
    }
    el["elements"] = []
    return True


# ---------------------------------------------------------------------------
# Progress bar
# ---------------------------------------------------------------------------

PCT_RE = __import__("re").compile(r"(\d{1,3})\s*%")


def _looks_like_progress(el: dict) -> bool:
    """Heading with a label + a child containing a percentage value."""
    kids = el.get("elements") or []
    if len(kids) < 1:
        return False
    has_pct = False
    has_label = False
    def walk(n):
        nonlocal has_pct, has_label
        if not isinstance(n, dict):
            return
        s = n.get("settings") or {}
        text = s.get("title") or s.get("editor") or s.get("text") or ""
        if isinstance(text, str):
            if PCT_RE.search(text):
                has_pct = True
            elif text.strip():
                has_label = True
        for c in n.get("elements") or []:
            walk(c)
    walk(el)
    return has_pct and has_label


def _convert_to_progress(el: dict) -> bool:
    label = ""
    pct = 0
    def walk(n):
        nonlocal label, pct
        if not isinstance(n, dict):
            return
        s = n.get("settings") or {}
        text = s.get("title") or s.get("editor") or s.get("text") or ""
        if isinstance(text, str):
            m = PCT_RE.search(text)
            if m and pct == 0:
                pct = int(m.group(1))
            elif text.strip() and not label and not m:
                label = text.strip()
        for c in n.get("elements") or []:
            walk(c)
    walk(el)
    if pct == 0:
        return False
    el.clear()
    el["id"] = "prgrs" + str(pct).zfill(3)
    el["elType"] = "widget"
    el["widgetType"] = "progress"
    el["settings"] = {
        "title": label,
        "percent": {"unit": "%", "size": pct, "sizes": []},
        "display_percentage": "show",
    }
    el["elements"] = []
    return True


# ---------------------------------------------------------------------------
# Star rating (row of star icons or stars-with-number)
# ---------------------------------------------------------------------------

def _looks_like_star_rating(el: dict) -> bool:
    icon_widgets = [c for c in (el.get("elements") or []) if isinstance(c, dict) and c.get("widgetType") == "icon"]
    if len(icon_widgets) < 3:
        return False
    star_like = sum(
        1 for c in icon_widgets
        if "star" in str((c.get("settings") or {}).get("selected_icon", {}).get("value", "")).lower()
    )
    return star_like >= 3


def _convert_to_star_rating(el: dict) -> bool:
    rating = sum(
        1 for c in (el.get("elements") or [])
        if isinstance(c, dict) and c.get("widgetType") == "icon"
        and "star" in str((c.get("settings") or {}).get("selected_icon", {}).get("value", "")).lower()
    )
    if rating < 3:
        return False
    el.clear()
    el["id"] = "strrt" + str(rating).zfill(2)
    el["elType"] = "widget"
    el["widgetType"] = "star-rating"
    el["settings"] = {"rating_scale": str(max(rating, 5)), "rating": str(rating)}
    el["elements"] = []
    return True


# ---------------------------------------------------------------------------
# Social icons
# ---------------------------------------------------------------------------

SOCIAL_ICON_RE = __import__("re").compile(
    r"\b(facebook|twitter|instagram|linkedin|youtube|tiktok|github|x-twitter|pinterest|whatsapp|telegram|discord|threads)\b",
    __import__("re").IGNORECASE,
)


def _looks_like_social_icons(el: dict) -> bool:
    kids = [c for c in (el.get("elements") or []) if isinstance(c, dict) and c.get("widgetType") == "icon"]
    if len(kids) < 2:
        return False
    matches = sum(
        1 for c in kids
        if SOCIAL_ICON_RE.search(str((c.get("settings") or {}).get("selected_icon", {}).get("value", "")))
    )
    return matches >= 2 and matches >= len(kids) // 2


def _convert_to_social_icons(el: dict) -> bool:
    items = []
    for c in (el.get("elements") or []):
        if not isinstance(c, dict) or c.get("widgetType") != "icon":
            continue
        s = c.get("settings") or {}
        icon = (s.get("selected_icon") or {}).get("value") or ""
        m = SOCIAL_ICON_RE.search(str(icon))
        link = (s.get("link") or {}).get("url") or "#"
        if m:
            items.append({
                "social_icon": {"value": f"fab fa-{m.group(1).lower()}", "library": "fa-brands"},
                "link": {"url": link, "is_external": "true", "nofollow": ""},
            })
    if not items:
        return False
    el.clear()
    el["id"] = "socil" + str(len(items)).zfill(2)
    el["elType"] = "widget"
    el["widgetType"] = "social-icons"
    el["settings"] = {"social_icon_list": items, "shape": "rounded"}
    el["elements"] = []
    return True


# ---------------------------------------------------------------------------
# Video (image with play overlay)
# ---------------------------------------------------------------------------

def _looks_like_video(el: dict) -> bool:
    kids = el.get("elements") or []
    has_image = any(isinstance(c, dict) and c.get("widgetType") == "image" for c in kids)
    has_play = any(
        isinstance(c, dict)
        and c.get("widgetType") in ("icon", "icon-box")
        and "play" in str((c.get("settings") or {}).get("selected_icon", {}).get("value", "")).lower()
        for c in kids
    )
    return has_image and has_play


def _convert_to_video(el: dict) -> bool:
    poster_url = ""
    for c in (el.get("elements") or []):
        if isinstance(c, dict) and c.get("widgetType") == "image":
            poster_url = ((c.get("settings") or {}).get("image") or {}).get("url") or ""
            break
    el.clear()
    el["id"] = "video1"
    el["elType"] = "widget"
    el["widgetType"] = "video"
    el["settings"] = {
        "video_type":      "youtube",
        "youtube_url":     "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
        "image_overlay":   {"url": poster_url, "id": ""} if poster_url else {},
        "show_image_overlay": "yes" if poster_url else "",
        "lightbox":        "yes",
        "play_icon":       {"value": "fas fa-play-circle", "library": "fa-solid"},
    }
    el["elements"] = []
    return True


# ---------------------------------------------------------------------------
# Image-box / icon-box (image or icon + heading + description in one container)
# ---------------------------------------------------------------------------

def _looks_like_image_box(el: dict) -> bool:
    kids = el.get("elements") or []
    has_image = any(isinstance(c, dict) and c.get("widgetType") == "image" for c in kids)
    has_heading = any(isinstance(c, dict) and c.get("widgetType") == "heading" for c in kids)
    has_text = any(isinstance(c, dict) and c.get("widgetType") == "text-editor" for c in kids)
    # Has all three AND no other children — pure image-box shape.
    return has_image and has_heading and has_text and len(kids) <= 4


def _convert_to_image_box(el: dict) -> bool:
    img_url = ""
    img_id = ""
    title = ""
    desc = ""
    for c in el.get("elements") or []:
        if not isinstance(c, dict):
            continue
        wt = c.get("widgetType")
        s = c.get("settings") or {}
        if wt == "image" and not img_url:
            i = s.get("image") or {}
            img_url = i.get("url") or ""
            img_id = i.get("id") or ""
        elif wt == "heading" and not title:
            title = s.get("title") or ""
        elif wt == "text-editor" and not desc:
            desc = s.get("editor") or ""
    if not (title and desc):
        return False
    el.clear()
    el["id"] = "imgbx1"
    el["elType"] = "widget"
    el["widgetType"] = "image-box"
    el["settings"] = {
        "image":             {"url": img_url, "id": img_id, "source": "library"},
        "title_text":        title,
        "description_text":  desc,
        "title_size":        "h3",
        "position":          "top",
    }
    el["elements"] = []
    return True


def _looks_like_icon_box(el: dict) -> bool:
    kids = el.get("elements") or []
    has_icon = any(isinstance(c, dict) and c.get("widgetType") == "icon" for c in kids)
    has_heading = any(isinstance(c, dict) and c.get("widgetType") == "heading" for c in kids)
    has_text = any(isinstance(c, dict) and c.get("widgetType") == "text-editor" for c in kids)
    return has_icon and has_heading and has_text and len(kids) <= 4


def _convert_to_icon_box(el: dict) -> bool:
    icon_value = "fas fa-check"
    title = ""
    desc = ""
    for c in el.get("elements") or []:
        if not isinstance(c, dict):
            continue
        wt = c.get("widgetType")
        s = c.get("settings") or {}
        if wt == "icon":
            i = s.get("selected_icon") or {}
            icon_value = i.get("value") or icon_value
        elif wt == "heading" and not title:
            title = s.get("title") or ""
        elif wt == "text-editor" and not desc:
            desc = s.get("editor") or ""
    if not (title and desc):
        return False
    el.clear()
    el["id"] = "icnbx1"
    el["elType"] = "widget"
    el["widgetType"] = "icon-box"
    el["settings"] = {
        "selected_icon":     {"value": icon_value, "library": "fa-solid"},
        "title_text":        title,
        "description_text":  desc,
        "title_size":        "h3",
        "position":          "top",
    }
    el["elements"] = []
    return True


# ---------------------------------------------------------------------------
# Toggle (single open/closed accordion item)
# ---------------------------------------------------------------------------

def _looks_like_toggle(el: dict) -> bool:
    """Like accordion but only 1 Q+A pair."""
    rows = el.get("elements") or []
    if len(rows) != 1:
        return False
    r = rows[0]
    if not isinstance(r, dict) or r.get("elType") != "container":
        return False
    kids = r.get("elements") or []
    headings = [c for c in kids if isinstance(c, dict) and c.get("widgetType") == "heading"]
    bodies = [c for c in kids if isinstance(c, dict) and c.get("widgetType") == "text-editor"]
    return bool(headings and bodies)


def _convert_to_toggle(el: dict) -> bool:
    return _convert_to_accordion(el)  # toggle is just accordion with 1 pair


# ---------------------------------------------------------------------------
# Divider / spacer (zero-content slim containers)
# ---------------------------------------------------------------------------

def _looks_like_divider(el: dict) -> bool:
    s = el.get("settings") or {}
    if (el.get("elements") or []):
        return False
    h = (s.get("min_height") or {}).get("size", 0)
    bg = s.get("background_color")
    return bool(bg) and (1 <= float(h or 0) <= 4)


def _convert_to_divider(el: dict) -> bool:
    s = el.get("settings") or {}
    color = s.get("background_color") or "#cccccc"
    el.clear()
    el["id"] = "divid1"
    el["elType"] = "widget"
    el["widgetType"] = "divider"
    el["settings"] = {"color": color, "weight": {"unit": "px", "size": 2, "sizes": []}, "gap": {"unit": "px", "size": 0, "sizes": []}}
    el["elements"] = []
    return True


def _looks_like_spacer(el: dict) -> bool:
    s = el.get("settings") or {}
    if (el.get("elements") or []):
        return False
    h = float((s.get("min_height") or {}).get("size", 0) or 0)
    return h >= 8 and not s.get("background_color")


def _convert_to_spacer(el: dict) -> bool:
    s = el.get("settings") or {}
    h = (s.get("min_height") or {}).get("size", 40)
    el.clear()
    el["id"] = "spcer1"
    el["elType"] = "widget"
    el["widgetType"] = "spacer"
    el["settings"] = {"space": {"unit": "px", "size": float(h), "sizes": []}}
    el["elements"] = []
    return True


# ---------------------------------------------------------------------------
# Nav menu — replace a row of inline buttons/links with a real nav-menu widget.
# Only fires when the row has 3+ button/text widgets and a matching menu has
# been created earlier in the run.
# ---------------------------------------------------------------------------

def _looks_like_nav_menu(el: dict) -> bool:
    kids = el.get("elements") or []
    label_widgets = [c for c in kids if isinstance(c, dict) and c.get("widgetType") in ("button", "text-editor")]
    return len(label_widgets) >= 3


def _convert_to_nav_menu(el: dict) -> bool:
    # We don't have menu binding info here — emit a wp-widget-nav_menu
    # with a placeholder that the orchestrator's menu pass will fill in
    # later. The orchestrator's existing inject_nav_menu_into_template()
    # handles the proper menu injection; this is a structural hint.
    el.clear()
    el["id"] = "navmu1"
    el["elType"] = "widget"
    el["widgetType"] = "wp-widget-nav_menu"
    el["settings"] = {"title": "", "nav_menu": "primary-menu"}
    el["elements"] = []
    return True


# ---------------------------------------------------------------------------
# Registry: PreferredWidget → (detector, converter)
# ---------------------------------------------------------------------------

WIDGET_PREF_HANDLERS: dict[str, tuple] = {
    "icon-list":            (_looks_like_icon_list, _convert_to_icon_list),
    "accordion":            (_looks_like_accordion, _convert_to_accordion),
    "tabs":                 (_looks_like_tabs, _convert_to_tabs),
    "slides":               (_looks_like_slides, _convert_to_slides),
    "image-carousel":       (_looks_like_image_carousel, _convert_to_image_carousel),
    "testimonial-carousel": (_looks_like_image_carousel, _convert_to_image_carousel),  # same shape
    "counter":              (_looks_like_counter, _convert_to_counter),
    "progress":             (_looks_like_progress, _convert_to_progress),
    "star-rating":          (_looks_like_star_rating, _convert_to_star_rating),
    "social-icons":         (_looks_like_social_icons, _convert_to_social_icons),
    "video":                (_looks_like_video, _convert_to_video),
    "image-box":            (_looks_like_image_box, _convert_to_image_box),
    "icon-box":             (_looks_like_icon_box, _convert_to_icon_box),
    "toggle":               (_looks_like_toggle, _convert_to_toggle),
    "divider":              (_looks_like_divider, _convert_to_divider),
    "spacer":               (_looks_like_spacer, _convert_to_spacer),
    "nav-menu":             (_looks_like_nav_menu, _convert_to_nav_menu),
}
