"""
Enrichment loader.

Reads the auxiliary artifacts the Figma plugin emits alongside `data.json`
and correlates them to the Elementor tree the agent imports:

    ai-layout.json     → semantic roles, sectionPurpose, preferredWidget,
                         componentFingerprint, confidence, accessibility
    tokens.json        → semantic dot-paths (color.primary, font.heading.size)
                         backed by Figma local styles / variables when available
    validation.json    → ValidationReport (low-confidence roles, mixed fonts,
                         absolute layout, large rasters, ...)
    assets.json        → per-asset alt text, decorative flag, icon hint

The plugin currently does NOT stamp `_figma_id`/`_figma_name` onto Elementor
container settings, so deep correlation between Elementor element ids
(`el00001`, …) and ai-layout `id` fields (Figma node ids) is best-effort.
For top-level sections, both trees walk `roots[]` in order, so
`data['content'][i]` always pairs with `ai_layout['sections'][i]` 1:1.

For widgets, this module exposes the parent ai-layout section's `content`
bundle (heading/subheading/paragraph/buttons/image), which is enough for
the optimization passes to make widget-preference + token-resolution
decisions without exact per-widget correlation.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterator


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

@dataclass
class Enrichment:
    """Bundle of all auxiliary signals from the Figma plugin export."""
    ai_layout: dict = field(default_factory=dict)
    tokens: dict = field(default_factory=dict)
    validation: dict = field(default_factory=dict)
    assets: list = field(default_factory=list)
    # Pre-computed lookups
    asset_by_filename: dict = field(default_factory=dict)
    asset_by_node_id: dict = field(default_factory=dict)
    warning_by_node_id: dict = field(default_factory=dict)
    section_by_index: list = field(default_factory=list)
    # Tokens lookups
    color_path_to_value: dict = field(default_factory=dict)
    typography_path_to_value: dict = field(default_factory=dict)

    @property
    def has_ai_layout(self) -> bool:
        return bool(self.ai_layout.get("sections"))

    @property
    def has_tokens(self) -> bool:
        return bool(self.color_path_to_value or self.typography_path_to_value)


def load_enrichment(export_dir: Path) -> Enrichment:
    """Load every auxiliary artifact present under <export_dir>.

    Missing files are tolerated — the agent still works without enrichment,
    it just falls back to inline values.
    """
    e = Enrichment()
    e.ai_layout = _load_json(export_dir / "ai-layout.json")
    e.tokens = _load_json(export_dir / "tokens.json")
    e.validation = _load_json(export_dir / "validation.json")
    raw_assets = _load_json(export_dir / "assets.json")
    e.assets = raw_assets if isinstance(raw_assets, list) else raw_assets.get("assets", [])

    e.asset_by_filename = {a.get("filename"): a for a in e.assets if a.get("filename")}
    e.asset_by_node_id = {a.get("nodeId"): a for a in e.assets if a.get("nodeId")}

    e.warning_by_node_id = {}
    for w in (e.validation.get("warnings") or []):
        nid = w.get("nodeId")
        if not nid:
            continue
        e.warning_by_node_id.setdefault(nid, []).append(w)

    e.section_by_index = e.ai_layout.get("sections") or []
    _index_tokens(e.tokens, e)
    return e


def top_level_pairs(elementor_content: list, e: Enrichment) -> Iterator[tuple[dict, dict]]:
    """Yield (elementor_top_container, ai_section) pairs, strictly index-aligned.

    Both `data.json.content` and `ai-layout.json.sections` come from the
    same ordered walk over the plugin's `roots[]`, so positional matching
    is reliable at the top level even without `_figma_id` plumbing.
    """
    if not e.section_by_index:
        return
    for el, sec in zip(elementor_content, e.section_by_index):
        if isinstance(el, dict) and el.get("elType") == "container":
            yield el, sec


def find_section_by_purpose(e: Enrichment, *purposes: str) -> dict | None:
    """First top-level section whose sectionPurpose is in `purposes`."""
    for sec in e.section_by_index:
        if sec.get("sectionPurpose") in purposes:
            return sec
    return None


def find_section_by_role(e: Enrichment, *roles: str) -> dict | None:
    """First top-level section whose semantic role is in `roles`."""
    for sec in e.section_by_index:
        if sec.get("role") in roles:
            return sec
    return None


def low_confidence_node_ids(e: Enrichment, threshold: float = 0.5) -> list[str]:
    """Figma node ids whose semantic role landed below `threshold`."""
    out = []
    def walk(sec: dict) -> None:
        conf = sec.get("confidence")
        if isinstance(conf, (int, float)) and conf < threshold:
            if sec.get("id"):
                out.append(sec["id"])
        for c in sec.get("children") or []:
            walk(c)
    for s in e.section_by_index:
        walk(s)
    return out


# ---------------------------------------------------------------------------
# Token indexing
# ---------------------------------------------------------------------------

def _index_tokens(tokens: dict, e: Enrichment) -> None:
    """Build flat lookups from the plugin's hierarchical tokens.json.

    Two shapes are supported:
      • Authoritative shape (Figma local styles + variables):
          {
            "color": {"primary": "#22D3EE", "secondary": "#0F172A", ...},
            "font":  {"heading": {"family": "Inter", "size": 56, ...}, ...}
          }
      • Heuristic shape (no styles/variables in file):
          {
            "colors": [{"name": "primary", "value": "#…"}, ...],
            "typography": [{"name": "h1", "fontFamily": "Inter", "fontSize": 56}, ...]
          }
    """
    if not isinstance(tokens, dict):
        return

    color_block = tokens.get("color") or tokens.get("colors")
    if isinstance(color_block, dict):
        for name, val in color_block.items():
            if isinstance(val, str):
                e.color_path_to_value[f"color.{name}"] = val.lower()
                e.color_path_to_value[name.lower()] = val.lower()
    elif isinstance(color_block, list):
        for c in color_block:
            if not isinstance(c, dict):
                continue
            name = (c.get("name") or "").strip()
            val = (c.get("value") or "").strip().lower()
            if name and val:
                e.color_path_to_value[f"color.{name}"] = val
                e.color_path_to_value[name.lower()] = val

    font_block = tokens.get("font") or tokens.get("typography")
    if isinstance(font_block, dict):
        for name, payload in font_block.items():
            if isinstance(payload, dict):
                e.typography_path_to_value[f"font.{name}"] = payload
                e.typography_path_to_value[name.lower()] = payload
    elif isinstance(font_block, list):
        for t in font_block:
            if not isinstance(t, dict):
                continue
            name = (t.get("name") or "").strip()
            if not name:
                continue
            e.typography_path_to_value[f"font.{name}"] = t
            e.typography_path_to_value[name.lower()] = t


def color_to_token_path(value: str, e: Enrichment) -> str | None:
    """Reverse lookup: hex value → semantic path (e.g. '#22D3EE' → 'color.primary')."""
    if not isinstance(value, str):
        return None
    needle = value.lower().strip()
    for path, v in e.color_path_to_value.items():
        if v == needle:
            return path
    return None


# ---------------------------------------------------------------------------
# Iteration helpers
# ---------------------------------------------------------------------------

def walk_widgets(elementor_content: list) -> Iterator[dict]:
    """Yield every widget node anywhere in the tree."""
    def walk(n):
        if isinstance(n, dict):
            if n.get("elType") == "widget":
                yield n
            for c in n.get("elements") or []:
                yield from walk(c)
        elif isinstance(n, list):
            for it in n:
                yield from walk(it)
    yield from walk(elementor_content)


def walk_containers(elementor_content: list, with_depth: bool = False) -> Iterator:
    """Yield every container node, optionally with its nesting depth (root=0)."""
    def walk(n, depth):
        if isinstance(n, dict):
            if n.get("elType") == "container":
                yield (n, depth) if with_depth else n
            for c in n.get("elements") or []:
                yield from walk(c, depth + 1)
        elif isinstance(n, list):
            for it in n:
                yield from walk(it, depth)
    yield from walk(elementor_content, 0)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _load_json(path: Path):
    if not path.exists():
        return {} if path.suffix == ".json" else None
    try:
        return json.loads(path.read_text())
    except json.JSONDecodeError:
        return {}
