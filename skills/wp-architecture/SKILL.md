---
name: wp-architecture
description: How the agent routes each Figma section to the right WordPress / Elementor surface — Theme Builder (header / footer), Pop-up Builder, Archive / Single templates, or page content. Multi-signal scoring (plugin signals + name regex + geometric + structural); plugin signals below 0.6 confidence are treated as hints, not facts.
---

# WordPress architecture router

Backed by `scripts/architecture.py` + `scripts/section_finder.py`. The
router answers one question per Figma section (at any depth, not just
top-level): **where on the WordPress site does this belong?**

## Decision sources — multi-signal, no single oracle

The router scores every container with five independent detectors and
picks the highest-confidence kind. **No single signal is authoritative.**

1. **`sectionPurpose`** from `ai-layout.json` (plugin hint).
   * Only trusted when the plugin's own confidence ≥ 0.6.
   * Below 0.6, treated as a *hint* logged in the section's `reason`
     but allowed to be outranked by signals 2-5.
2. **`_ai_role` / `role`** (plugin semantic role).
   * Boosted: `navbar` / `footer` get a 0.9 floor because Theme Builder
     placement is a hard gate — we want a template, not inline content.
3. **Layer-name regex** — `Header|Navbar|Topbar|Site Header`,
   `Footer|Site Footer`, `Footer Column|Footer Links`, `Hero|Banner`,
   `Popup|Modal`, `Archive|Blog List`, `Single Post`, etc.
   * Carries more weight when the plugin abstained (confidence < 0.6).
4. **Geometric** — top-of-page slim full-width = header; bottom-of-page
   full-width = footer.
5. **Structural** — full-bleed image + heading + button = hero;
   N stacked link-like widgets inside a footer container = footer column.

When two detectors disagree by < 0.1, the section is marked
`ambiguous` and the orchestrator dispatches Claude-as-Author for it.

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
  Placement(kind='header',         section_index=0, reason='role=navbar + name~=header', ...),
  Placement(kind='page',           section_index=1, reason='default (purpose=hero@0.35-hint)', ...),
  Placement(kind='popup',          section_index=2, reason='name~=popup', ...),
  Placement(kind='footer',         section_index=3, reason='sectionPurpose=footer', ...),
  Placement(kind='footer-column',  section_index=4, reason='structural: 5 link-like children', ...),
]
```

`page_content(placements)` returns just the nodes that should land on
the actual page; everything else is consumed by `client.create_template()`
or by the menu-creation phase (footer-columns each become their own
nav-menu post).

## Theme Builder gate (mandatory)

The importer aborts with exit code 7 when header AND footer are not
both detected (unless `--no-require-theme-builder` is passed). This is
the user-requirement-driven gate: header/footer MUST be Theme Builder
templates, never inline content on the page body. If detection fails,
the agent prompts the developer to add `header_pattern` /
`footer_pattern` overrides to `project-config.json` and re-run.
