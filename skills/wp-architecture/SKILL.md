---
name: wp-architecture
description: How the agent routes each Figma section to the right WordPress / Elementor surface — Theme Builder (header / footer), Pop-up Builder, Archive / Single templates, or page content. Driven by ai-layout's `sectionPurpose` first, semantic role second, layer-name regex last.
---

# WordPress architecture router

Backed by `scripts/architecture.py`. The router answers one question per
top-level Figma section: **where on the WordPress site does this belong?**

## Decision sources, in priority order

1. **`sectionPurpose`** from `ai-layout.json` — the most reliable signal,
   set by the plugin's `aiLayout.ts` from descendant counts + name regex.
2. **`role`** (`SemanticRole`) — used when sectionPurpose is `content`
   but the role is `navbar` / `footer`.
3. **Figma layer name** — case-insensitive regexes for popup, archive,
   single, search, 404. Only fires when 1 + 2 didn't already decide.

## sectionPurpose → routing target

| sectionPurpose | kind | What the agent creates |
|----------------|------|------------------------|
| `navbar` | `header` | `elementor_library` (header), conditions `include/general` (with Pro) |
| `footer` | `footer` | `elementor_library` (footer), conditions `include/general` |
| `hero`, `cta`, `feature-grid`, `feature-comparison`, `social-proof`, `trust`, `faq`, `pricing`, `testimonial`, `gallery`, `lead-capture`, `content` | `page` | Stays on the page tree |

## Name-regex routing (only when purpose+role didn't match)

| Pattern | kind | Output |
|---------|------|--------|
| `popup\|modal\|dialog\|overlay\|lightbox` | `popup` | `elementor_library` (popup) |
| `archive\|blog[- ]?list\|posts?[- ]?grid\|articles?[- ]?grid` | `archive` | `elementor_library` (archive), conditions `include/post_archive` |
| `single[- ]?post\|post[- ]?detail\|article[- ]?detail` | `single` | `elementor_library` (single-page), conditions `include/in_singular/post` |
| `search[- ]?result\|search[- ]?page` | `search` | manual (no auto-condition) |
| `404\|not[- ]?found` | `404` | manual |

## Pro vs Free fall-back

The bridge applies `_elementor_conditions` only when Elementor Pro is
present (`health.elementor_pro` truthy). Without Pro, the templates are
created in the library but won't auto-apply — the developer has to
assign them manually in `wp-admin → Templates → Theme Builder`.

## When ai-layout.json is missing

Older plugin exports omit `ai-layout.json`. The router still finds
header / footer via name regex (`header|nav(bar)?|topbar` and `footer`),
matching the legacy `find_section()` behaviour. Popup / archive /
single / search / 404 only work when ai-layout is present, since
sectionPurpose is the strong signal.

## Why this is its own module

The previous design used a single regex per kind, and any unmatched
section silently became page content. That worked for header/footer
but made adding popup / archive / single / search / 404 awkward —
each new kind required another regex + another ad-hoc branch. Routing
through one declarative table keeps each kind a one-line decision.

## Output shape (used by the orchestrator)

```python
[
  Placement(kind='header',  section_index=0, reason='sectionPurpose=navbar', ...),
  Placement(kind='page',    section_index=1, reason='default (purpose=hero)', ...),
  Placement(kind='popup',   section_index=2, reason='name~=popup', ...),
  Placement(kind='footer',  section_index=3, reason='sectionPurpose=footer', ...),
]
```

`page_content(placements)` returns just the nodes that should land on
the actual page; everything else is consumed by `client.create_template()`.
