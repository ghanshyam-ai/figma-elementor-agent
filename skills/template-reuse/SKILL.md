---
name: template-reuse
description: Detects Figma component instances + structurally-identical Elementor subtrees, hoists the canonical instance into an Elementor library template, and replaces duplicates with shortcode references. Smaller `_elementor_data`, one editable source for repeated UI.
---

# Template reuse

Lives in `scripts/template_reuse.py`. Runs after architecture routing,
on the page-bound nodes only. Headers and footers are already
single-instance by definition; popups don't repeat.

## Two detection strategies

### 1. ai-layout signal (preferred)

When `ai-layout.json` is present, every section carries:
* `componentFingerprint` — the same value for every instance of the
  same Figma component / variant.
* `instanceGroup` — set on subtrees that share a fingerprint regardless
  of variant (e.g. all "Pricing Card" instances even with different
  prices).

The plugin's `extractor.ts` computes both during the Figma walk; the
agent just reads them.

### 2. Structural hash (fallback)

When ai-layout is missing, OR when a section has no
componentFingerprint (the layer wasn't a Figma component instance),
we compute a SHA-1 over the subtree's "skeleton" — settings keys
listed in `HASH_SETTINGS_KEYS`, recursively. Two containers that
hash the same have the same shape + the same settings.

Skeleton excludes:
* `id` (sequential, doesn't affect equivalence)
* `_offset_*` (position offsets — same shape can be placed elsewhere)
* `_figma_id` / `_figma_name` (traceability metadata)

## Output: ReuseGroup

```python
ReuseGroup(
    fingerprint="abc123…",          # the matching key
    section_indices=[3, 5, 7],      # all instances by index in content[]
    canonical_index=3,              # first one becomes the template
    template_id=42,                 # filled in after bridge call
    title="Pricing Card",           # from sec.name or _figma_name
)
```

## What gets created in WordPress

For each group with N≥2 instances:
1. The first instance is sent to `client.create_template(template_type="section")`
   → an `elementor_library` post of type `section`.
2. The remaining N-1 instances in the page tree are replaced with:

```jsonc
{
  "elType": "container",
  "isInner": false,
  "settings": { "_template_ref": 42, "_template_ref_title": "Pricing Card" },
  "elements": [{
    "elType": "widget", "widgetType": "shortcode",
    "settings": { "shortcode": "[elementor-template id=\"42\"]" }
  }]
}
```

Render-time, Elementor's shortcode resolver pulls the template's
`_elementor_data` and inlines it. The blob shipped to the database is
much smaller — one canonical copy plus N-1 shortcode references.

## Cost / benefit

* **Cost**: tiny render-time shortcode resolve per duplicate, plus
  one extra database query per page that uses the template.
* **Benefit**: editing the canonical changes every instance; smaller
  `_elementor_data` blobs (faster page loads in the editor); cleaner
  Theme Builder library.

For groups of 2, the cost ≈ benefit. For 5+ instances (typical
pricing tables, feature grids), the win is large.

## Disabling

```bash
python3 scripts/import_elementor.py --skip-template-reuse
```

Useful when you want every section editable in place rather than via
a shared template — e.g. a one-off landing page where reuse adds
overhead without payoff.

## Limitation: nested duplicates

The current implementation only detects top-level duplicates. If a
"Feature Card" component appears 3 times nested inside a parent
container (rather than as 3 top-level sections), it's not deduped —
because the agent doesn't yet have reliable per-node correlation
between `data.json` ids (`el00001`) and `ai-layout.json` ids (Figma
node ids). Once the plugin stamps `_figma_id` onto Elementor settings
this limitation goes away. Tracked in the plugin gap list.
