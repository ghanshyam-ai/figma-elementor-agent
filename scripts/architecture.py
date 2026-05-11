"""
WordPress / Elementor architecture router.

Maps each top-level section in the Figma export to a placement decision:

    {
      "kind": "header" | "footer" | "popup" | "archive" | "single" |
              "search" | "404" | "page",
      "section_index": 0,
      "elementor_node": <ref to data.json content[i]>,
      "ai_section": <ref to ai-layout.json sections[i]>,
      "reason": "matched sectionPurpose=hero / fallback to page",
    }

The orchestrator consumes these decisions to build the appropriate
Elementor library entries (Theme Builder when Pro is active) before the
remaining `kind=page` sections are written into the page tree itself.

Detection sources, in priority order:
    1. sectionPurpose from ai-layout.json   (most reliable)
    2. semanticRole                          (hero/navbar/footer)
    3. Figma layer name regex                (popup, modal, 404, search)
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from enrich import Enrichment


# Layer-name patterns that trigger a non-page placement when sectionPurpose
# doesn't already cover the case. Case-insensitive.
NAME_PATTERNS: dict[str, re.Pattern[str]] = {
    "popup":   re.compile(r"\b(popup|modal|dialog|overlay|lightbox)\b", re.IGNORECASE),
    "archive": re.compile(r"\b(archive|blog[- ]?list|posts?[- ]?grid|articles?[- ]?grid)\b", re.IGNORECASE),
    "single":  re.compile(r"\b(single[- ]?post|post[- ]?detail|article[- ]?detail)\b", re.IGNORECASE),
    "search":  re.compile(r"\b(search[- ]?result|search[- ]?page)\b", re.IGNORECASE),
    "404":     re.compile(r"\b(404|not[- ]?found)\b", re.IGNORECASE),
    "header":  re.compile(r"\b(header|nav(bar)?|topbar)\b", re.IGNORECASE),
    "footer":  re.compile(r"\b(footer)\b", re.IGNORECASE),
}

# sectionPurpose → kind. Only the structural placements; "content" intents
# (cta, faq, testimonial, ...) flow through to the page tree.
PURPOSE_TO_KIND: dict[str, str] = {
    "navbar": "header",
    "footer": "footer",
    # Everything else is page content (hero, cta, faq, pricing, …).
}


@dataclass
class Placement:
    kind: str
    section_index: int
    elementor_node: dict
    ai_section: dict | None
    reason: str


def route_sections(
    elementor_content: list,
    e: Enrichment,
) -> list[Placement]:
    """Decide a placement for each top-level section.

    When ai-layout.json is missing (older plugin export, or a malformed
    file), falls back to layer-name regexes alone — header/footer are
    still detected, popup/archive/single/search/404 just won't be.
    """
    placements: list[Placement] = []
    for i, el in enumerate(elementor_content):
        if not isinstance(el, dict):
            continue
        sec = e.section_by_index[i] if i < len(e.section_by_index) else None
        # Top-level widgets (e.g. an entire section that the widget-pref
        # pass converted into a single icon-list / accordion) belong on the
        # page itself — they aren't a header/footer/popup.
        if el.get("elType") != "container":
            placements.append(Placement(
                kind="page",
                section_index=i,
                elementor_node=el,
                ai_section=sec,
                reason="top-level widget",
            ))
            continue
        kind, reason = _decide(el, sec)
        placements.append(Placement(
            kind=kind,
            section_index=i,
            elementor_node=el,
            ai_section=sec,
            reason=reason,
        ))
    return placements


def _decide(el: dict, sec: dict | None) -> tuple[str, str]:
    purpose = (sec or {}).get("sectionPurpose")
    role = (sec or {}).get("role")
    name = (sec or {}).get("name") or (el.get("settings") or {}).get("_figma_name") or ""

    # 1. Explicit sectionPurpose for header/footer — most reliable.
    if purpose in PURPOSE_TO_KIND:
        return PURPOSE_TO_KIND[purpose], f"sectionPurpose={purpose}"

    # 2. Role-based detection (hero stays a page section, navbar/footer route).
    if role == "navbar":
        return "header", "role=navbar"
    if role == "footer":
        return "footer", "role=footer"

    # 3. Name-pattern detection for popup / archive / single / search / 404.
    for kind, pat in NAME_PATTERNS.items():
        if kind in ("header", "footer"):
            continue  # already handled above
        if pat.search(name):
            return kind, f"name~={pat.pattern}"

    # 4. Fallback to page content.
    return "page", f"default (purpose={purpose or '—'}, role={role or '—'})"


# ---------------------------------------------------------------------------
# Convenience filters used by the orchestrator
# ---------------------------------------------------------------------------

def page_content(placements: list[Placement]) -> list[dict]:
    """The Elementor nodes that should land on the actual page (in order)."""
    return [p.elementor_node for p in placements if p.kind == "page"]


def by_kind(placements: list[Placement], kind: str) -> list[Placement]:
    return [p for p in placements if p.kind == kind]


def summary(placements: list[Placement]) -> dict[str, int]:
    out: dict[str, int] = {}
    for p in placements:
        out[p.kind] = out.get(p.kind, 0) + 1
    return out


# ---------------------------------------------------------------------------
# Popup trigger inference  (item #9)
# ---------------------------------------------------------------------------

# Layer-name signals → popup trigger config. Order matters: the first match
# wins so "exit-intent newsletter popup" is treated as exit_intent, not
# newsletter / page_load.
POPUP_TRIGGER_RULES: list[tuple[re.Pattern[str], dict]] = [
    (re.compile(r"\bexit[- ]?intent\b", re.IGNORECASE), {
        "triggers": {"exit_intent": "yes"},
    }),
    (re.compile(r"\bscroll\b", re.IGNORECASE), {
        "triggers": {"on_scroll": "yes", "on_scroll_threshold": {"unit": "%", "size": 50}},
    }),
    (re.compile(r"\b(after|delay|timer|wait)\b", re.IGNORECASE), {
        "triggers": {"on_page_load": "yes"},
        "timing":   {"page_views":  {"min": 1}},
        "delay":    {"unit": "sec", "size": 5},
    }),
    (re.compile(r"\bnewsletter|subscribe|sign[- ]?up\b", re.IGNORECASE), {
        "triggers": {"on_page_load": "yes"},
        "timing":   {"sessions": {"min": 1}},
        "delay":    {"unit": "sec", "size": 3},
    }),
    (re.compile(r"\binactivity|idle\b", re.IGNORECASE), {
        "triggers": {"user_inactivity": "yes", "user_inactivity_threshold": {"unit": "sec", "size": 30}},
    }),
    (re.compile(r"\bcookie|consent|gdpr\b", re.IGNORECASE), {
        "triggers": {"on_page_load": "yes"},
        "frequency": "session",
    }),
]


def popup_settings_for_node(node: dict, ai_section: dict | None) -> dict:
    """Infer Elementor `_elementor_popup_settings` from layer name + ai signals.

    The returned dict is forwarded to `client.create_template(popup_settings=...)`.
    Always sets a sane default position + `prevent_close_on_background_click`
    so the popup is dismissable without the developer touching anything.
    """
    base: dict = {
        "position":           "center center",
        "horizontal_offset":  {"unit": "px", "size": 0},
        "vertical_offset":    {"unit": "px", "size": 0},
        "close_button_delay": {"unit": "sec", "size": 0},
        "prevent_close_on_background_click": "",
    }
    name = (
        (ai_section or {}).get("name")
        or (node.get("settings") or {}).get("_figma_name")
        or ""
    )
    inferred: dict = {}
    for pat, payload in POPUP_TRIGGER_RULES:
        if pat.search(name):
            inferred = payload
            break
    if not inferred:
        # Default: open as soon as the user lands on a page that matches the
        # popup's display condition. Conservative — never auto-pops without
        # the developer setting a condition first.
        inferred = {"triggers": {"on_page_load": "yes"}}

    merged = {**base, **inferred}
    # Frequency cap so the popup doesn't show every page-load. Developers
    # rarely want to spam users; "show once per session" is a safer default.
    merged.setdefault("frequency", "session")
    return merged


def popup_hint_for(node: dict, ai_section: dict | None, settings: dict) -> str:
    """Human-readable note explaining what trigger we picked + the override path."""
    name = (ai_section or {}).get("name") or (node.get("settings") or {}).get("_figma_name") or "popup"
    triggers = settings.get("triggers", {})
    if triggers.get("on_page_load"):
        kind = "page-load"
    elif triggers.get("exit_intent"):
        kind = "exit-intent"
    elif triggers.get("on_scroll"):
        kind = "on-scroll"
    elif triggers.get("user_inactivity"):
        kind = "user-inactivity"
    else:
        kind = "manual"
    return (
        f"popup '{name}' will trigger on {kind} (frequency=session). "
        f"Override in wp-admin → Templates → Popups → '{name}' → Display Conditions."
    )
