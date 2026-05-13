"""
Template reuse via Figma component fingerprints + structural hashing.

Two detection strategies, used in this order:

    1. ai-layout.json's `componentFingerprint` / `instanceGroup` — every
       Figma component instance shares a fingerprint, so identical cards /
       headers / pricing tables across sections collapse into one group.

    2. Structural hash of the Elementor subtree (when no ai-layout signal
       is present, or for nodes the plugin didn't fingerprint). Two
       containers with the same shape + settings hash to the same digest.

For each group of N≥2 identical sections:
    • The first section becomes the canonical "library template" — created
      via `client.create_template(template_type="section", ...)`.
    • The remaining N-1 sections are replaced in the live page tree with
      a `shortcode` widget: `[elementor-template id="..."]`.

This trades a tiny render-time shortcode resolve for a much smaller
`_elementor_data` blob and one editable source for repeated UI.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field

from enrich import Enrichment


@dataclass
class _Site:
    """Pointer to a container instance: parent.elements[index] = node."""
    parent: list
    index: int
    node: dict
    depth: int  # 0 = top-level


@dataclass
class ReuseGroup:
    fingerprint: str
    sites: list[_Site] = field(default_factory=list)
    canonical: _Site | None = None
    template_id: int | None = None
    template_slug: str = ""
    title: str = ""
    # True when this group's fingerprint matches a template recorded on a
    # prior page (project_state.component_library). In that case the page's
    # site(s) get rewritten to shortcodes pointing at `pre_existing_template_id`
    # — no new template is created on this run.
    reused_from_state: bool = False
    pre_existing_template_id: int | None = None
    pre_existing_page_slug: str = ""

    @property
    def section_indices(self) -> list[int]:
        """Top-level section indices (deprecated; kept for backward compat)."""
        return [s.index for s in self.sites if s.depth == 0]

    @property
    def canonical_index(self) -> int | None:
        return self.canonical.index if (self.canonical and self.canonical.depth == 0) else None


# Containers smaller than this are usually layout shells, not reusable units.
# Set conservatively; raise to avoid creating templates for trivial wrappers.
MIN_REUSE_WIDGETS = 2

# Minimum subtree size — single-widget wrappers don't justify a template.
MIN_SUBTREE_NODES = 3


def detect_reuse_groups(content: list, e: Enrichment, state=None) -> list[ReuseGroup]:
    """Find duplicate containers anywhere in the tree (top-level + nested).

    Each duplicate group needs to clear two bars before becoming a template:
      • at least 2 instances, AND
      • the canonical subtree contains >= MIN_REUSE_WIDGETS widgets and
        >= MIN_SUBTREE_NODES total nodes (so we don't template trivial
        single-widget wrappers).

    When ``state`` (a ``ProjectState`` from ``project_state.py``) is provided,
    a parallel pass also surfaces single-instance containers whose fingerprint
    already lives in ``state.component_library`` — those get marked
    ``reused_from_state=True`` and skip the ≥2-instance threshold because
    the canonical lives on a previously-built page.
    """
    groups_by_fp: dict[str, ReuseGroup] = {}
    cross_page_groups_by_fp: dict[str, ReuseGroup] = {}

    # Phase 1 — collect sites + fingerprints by walking the whole tree.
    for site in _all_container_sites(content):
        node, depth = site.node, site.depth
        settings = node.get("settings") or {}
        # Three fingerprint sources, in priority order:
        #   1. plugin-emitted `_figma_fingerprint` on the node (most reliable;
        #      survives nesting + matches Figma component instances)
        #   2. ai-layout's componentFingerprint / instanceGroup (top-level only)
        #   3. agent-computed structural hash (works without plugin support)
        fp = settings.get("_figma_fingerprint")
        if not fp and depth == 0 and site.index < len(e.section_by_index):
            sec = e.section_by_index[site.index] or {}
            fp = sec.get("componentFingerprint") or sec.get("instanceGroup")
        if not fp:
            fp = _structural_hash(node)

        # Cross-page reuse: does this fingerprint match a template from a
        # prior page? If so, queue a state-reuse group regardless of how
        # many local instances exist (a single occurrence is enough to
        # justify replacing it with a shortcode reference).
        stored = state.find_component(fp) if state is not None else None

        g = groups_by_fp.setdefault(fp, ReuseGroup(fingerprint=str(fp)[:64]))
        g.sites.append(site)
        if stored:
            xg = cross_page_groups_by_fp.setdefault(
                fp,
                ReuseGroup(
                    fingerprint=str(fp)[:64],
                    reused_from_state=True,
                    pre_existing_template_id=stored.get("template_id"),
                    pre_existing_page_slug=stored.get("first_page_slug", ""),
                    template_id=stored.get("template_id"),
                    template_slug=stored.get("template_slug", "") or _slugify(stored.get("title", "")),
                    title=stored.get("title", "") or f"Reusable Block {str(fp)[:6]}",
                ),
            )
            xg.sites.append(site)
        if not g.title:
            if depth == 0 and site.index < len(e.section_by_index):
                sec = e.section_by_index[site.index] or {}
                if sec.get("name"):
                    g.title = sec["name"]
            if not g.title:
                g.title = settings.get("_figma_name") or f"Reusable Block {str(fp)[:6]}"

    # Phase 2 — keep only groups that pass the size + count thresholds.
    out: list[ReuseGroup] = []
    # Pass A: cross-page reuse. No ≥2-instance requirement here because the
    # canonical lives on a prior page. We still apply the size threshold so
    # we don't shortcode a single-widget wrapper.
    for fp, g in cross_page_groups_by_fp.items():
        canonical = g.sites[0]
        widgets, total = _count_subtree(canonical.node)
        if widgets < MIN_REUSE_WIDGETS or total < MIN_SUBTREE_NODES:
            continue
        g.canonical = canonical
        out.append(g)

    # Pass B: within-page duplicate detection (unchanged behavior). Skip
    # fingerprints already handled by Pass A so we don't double-create.
    for fp, g in groups_by_fp.items():
        if fp in cross_page_groups_by_fp:
            continue
        if len(g.sites) < 2:
            continue
        canonical = g.sites[0]
        widgets, total = _count_subtree(canonical.node)
        if widgets < MIN_REUSE_WIDGETS or total < MIN_SUBTREE_NODES:
            continue
        # When duplicates appear at multiple depths (e.g. card at top-level
        # AND nested inside a different container), prefer the nested
        # canonical so the top-level slot keeps its sectionPurpose.
        nested = [s for s in g.sites if s.depth > 0]
        g.canonical = nested[0] if nested else canonical
        g.template_slug = _slugify(g.title)
        out.append(g)

    # When two groups overlap (a parent + one of its children both
    # qualify), keep the larger of the two so we don't try to write a
    # shortcode inside a node we're also templating.
    return _drop_overlapping(out)


def replace_duplicates_with_shortcodes(content: list, groups: list[ReuseGroup]) -> int:
    """Swap each non-canonical site for a shortcode reference. Returns count.

    Must be called AFTER `template_id` is populated on every group.

    For ``reused_from_state`` groups every site is rewritten — the canonical
    lives on a prior page, so there's no instance on this page that needs
    preserving in-tree. For within-page groups (the existing behavior) all
    sites are also rewritten because the canonical's subtree is already
    lifted into the library template via ``client.create_template``.
    """
    n = 0
    for g in groups:
        if not g.template_id or not g.canonical:
            continue
        for site in g.sites:
            shortcode_node = _make_template_shortcode(g.template_id, g.title)
            site.parent[site.index] = shortcode_node
            n += 1
    return n


def _all_container_sites(content: list) -> list[_Site]:
    """Depth-first list of every container site in the tree."""
    out: list[_Site] = []
    def walk(parent_list: list, depth: int) -> None:
        for i, el in enumerate(parent_list):
            if isinstance(el, dict) and el.get("elType") == "container":
                out.append(_Site(parent=parent_list, index=i, node=el, depth=depth))
                walk(el.get("elements") or [], depth + 1)
    walk(content, 0)
    return out


def _count_subtree(node: dict) -> tuple[int, int]:
    """(widget_count, total_node_count) for the subtree rooted at `node`."""
    widgets = 0
    total = 0
    def walk(n):
        nonlocal widgets, total
        if not isinstance(n, dict):
            return
        total += 1
        if n.get("elType") == "widget":
            widgets += 1
        for c in n.get("elements") or []:
            walk(c)
    walk(node)
    return widgets, total


def _drop_overlapping(groups: list[ReuseGroup]) -> list[ReuseGroup]:
    """Remove groups whose canonical lives inside another group's canonical."""
    if len(groups) < 2:
        return groups
    # Build a map of node-id-by-python-id for quick "is descendant of" check.
    ancestors: dict[int, set[int]] = {}
    for g in groups:
        cn = g.canonical.node if g.canonical else None
        if not cn:
            continue
        ancestors[id(cn)] = _all_descendant_ids(cn)
    keep: list[ReuseGroup] = []
    for g in groups:
        cn = g.canonical.node if g.canonical else None
        if not cn:
            continue
        if any(
            other is not g and id(cn) in ancestors.get(id(other.canonical.node), set())
            for other in groups if other.canonical
        ):
            continue
        keep.append(g)
    return keep


def _all_descendant_ids(node: dict) -> set[int]:
    out: set[int] = set()
    def walk(n):
        if isinstance(n, dict):
            out.add(id(n))
            for c in n.get("elements") or []:
                walk(c)
    for c in node.get("elements") or []:
        walk(c)
    return out


def _slugify(s: str) -> str:
    import re as _re
    s = _re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-")
    return s or "reusable-block"


def _make_template_shortcode(template_id: int, title: str) -> dict:
    """Container holding a single shortcode widget that pulls the template."""
    return {
        "id": "tpref" + str(template_id).zfill(3)[-3:],
        "elType": "container",
        "isInner": False,
        "settings": {
            "content_width": "boxed",
            "_template_ref": template_id,        # private marker for traceability
            "_template_ref_title": title,
        },
        "elements": [
            {
                "id": "tpsht" + str(template_id).zfill(3)[-3:],
                "elType": "widget",
                "widgetType": "shortcode",
                "settings": {
                    "shortcode": f'[elementor-template id="{template_id}"]',
                },
                "elements": [],
            }
        ],
    }


# ---------------------------------------------------------------------------
# Structural hashing
# ---------------------------------------------------------------------------

# Settings that are layout-significant (we hash these). Position offsets
# and ids are intentionally excluded so structurally-identical sections
# at different page positions still hash the same.
#
# Copy fields (`title`, `text`, `editor`) are intentionally **omitted** so
# that an accordion-of-FAQs on Home matches an accordion-of-FAQs on About
# even when the questions differ — that's the whole point of cross-page
# component reuse. `header_size` and `size` (heading level / icon size) are
# kept because they're structural choices, not content.
HASH_SETTINGS_KEYS = (
    "elType", "widgetType", "isInner",
    "flex_direction", "flex_gap", "flex_justify_content", "flex_align_items",
    "flex_wrap", "padding", "min_height", "boxed_width", "content_width",
    "background_background", "background_color", "border_radius",
    "header_size", "size",
)


def _structural_hash(node: dict) -> str:
    h = hashlib.sha1()
    h.update(_canonical_json(node).encode("utf-8"))
    return h.hexdigest()[:16]


def _canonical_json(node: dict) -> str:
    skeleton = _skeleton(node)
    return json.dumps(skeleton, sort_keys=True, separators=(",", ":"))


def _skeleton(node):
    if isinstance(node, dict):
        out = {}
        for k in HASH_SETTINGS_KEYS:
            if k in node:
                out[k] = node[k]
        s = node.get("settings") or {}
        out["_settings"] = {k: s[k] for k in HASH_SETTINGS_KEYS if k in s}
        kids = node.get("elements") or []
        if kids:
            out["_children"] = [_skeleton(c) for c in kids if isinstance(c, dict)]
        return out
    return None
