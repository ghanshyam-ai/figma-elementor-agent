# Figma → Elementor Agent

A Claude Code agent system that turns a Figma plugin export ZIP into a
fully built Elementor page on a target WordPress site — with global
tokens, Theme Builder header/footer, popup/archive/single templates,
Gravity Forms integration, screenshot fallbacks, and an auto-fix loop
driven by Claude visual reasoning.

The companion Figma plugin (maintained separately) extracts structured
data from a Figma file. **This agent does not rely 100% on the plugin** —
plugin signals are inputs to a hybrid pipeline where the agent's own
detectors, structural analysis, and (when needed) Claude sub-Agent calls
make the final classification.

## Quick start (3 steps, zero scripts)

1. Drop the agent folder + Figma export ZIP into your WordPress root:

   ```
   <wp-root>/
   ├── wp-config.php
   ├── wp-content/
   ├── figma-elementor-agent/         ← this repo
   └── your-design.zip                ← Figma plugin export
   ```

   LocalWP works too — drop them next to `app/`, `conf/`, `logs/`. The
   agent searches up + into `app/public/`, `public/`, `public_html/`,
   `wordpress/`, `htdocs/`, `wp/`, `www/`.

2. Inside `figma-elementor-agent/`, copy `config.example.json` →
   `config.json` and fill in **four fields**:

   ```json
   {
     "wp_url": "http://localhost:10048",
     "wp_user": "admin",
     "wp_password": "your-wp-admin-password",
     "theme_slug": "hello-elementor"
   }
   ```

3. Symlink agents + skills into `.claude/` so Claude Code registers them,
   then run `claude` and type `start`:

   ```bash
   mkdir -p .claude
   ln -sfn ../agents .claude/agents
   ln -sfn ../skills .claude/skills
   claude
   ```

   Then in the Claude Code prompt:

   ```
   start
   ```

   …or with a free-form instruction:

   ```
   start build the home page only
   ```

## Page-by-page workflow

The agent processes **one page per run**. The first run builds the home
page, which establishes globals + header + footer. Each subsequent page
auto-detects that prior state and skips kit / theme-builder phases:

```bash
# First run — home page (kit + header + footer + page)
start

# Second + Nth pages — drop a new ZIP, run again
.venv/bin/python scripts/import_elementor.py --page-only --page-slug about
```

The `--page-only` flag is **optional** — if `project-state.json` shows a
prior successful run, the orchestrator auto-skips globals/header/footer.

## What the orchestrator bootstraps for you

On the first `start`, before any phase runs:

- Walks up from the agent folder until it finds `wp-config.php` → that's `wp_root`.
- Copies `scripts/wp-bridge/figma-importer-bridge.php` into
  `<wp-root>/wp-content/mu-plugins/`.
- Scans the WP root + parent for a `*.zip` and picks the first match.
- Detects the active theme (prefers `hello-elementor`).
- Creates `.venv/` and `pip install`s `requests` + `Pillow` + `pytest`.
- Runs `npm install` inside `scripts/` for Playwright + pixelmatch.
- Writes `project-config.json` (mode 600).
- Verifies the bridge by hitting `/wp-json/figma-importer/v1/health`.
- Reports plugin presence: Elementor, Elementor Pro, Gravity Forms.

You never run `python`, `pip`, `npm`, or any setup script by hand.

## Pipeline overview (Phases A–K)

```
A.  Setup            wp-setup    auth + bridge + Elementor + GF + Pro
B.  Import           importer    extract ZIP, load enrichment artifacts
C.  Globals          global-styles  kit colors / typography + design-token CSS
D.  Section finder   (recursive)  find real sections at any depth, not just top
E.  Optimize         optimization
                       • global token resolution (colors → globals/colors?id=…)
                       • design-token classes (.dt-radius-md, .dt-gap-lg)
                       • widget inference (icon-list, accordion, tabs, etc.)
                       • auto-layout inference for non-flex containers
                       • container collapse + depth cap (respects structural)
                       • HTML widget → text-editor
F.  Architecture     wp-architecture  header/footer/popup/archive/single/search/404
G.  Forms            form-intelligence  Gravity Forms creation + shortcode
H.  Reuse            template-reuse  fingerprint dedupe (top-level + nested)
I.  Page             page-builder  hide_title=yes + correct page template
J.  Visual review    visual-reviewer  Playwright capture + pixel diff
K.  Auto-fix         auto-fixer
                       • reads build/import-report.json + diff/report.json
                       • runs scripts/fix_plan.py for prioritized candidates
                       • dispatches Claude sub-Agents for visual reasoning
                       • stops after 3 iterations
```

## Required developer setup (one time)

| Where | What |
|-------|------|
| WordPress | Permalinks → **Post name** (Settings → Permalinks). REST 404s on "Plain". |
| WordPress | Elementor plugin installed and active. **Elementor Pro** is recommended (Theme Builder header/footer auto-applies; popups; nav-menu widget). |
| WordPress | Theme that supports `menu-1` / `menu-2` locations. Hello Elementor does. |
| WordPress (optional) | **Gravity Forms** — when present, the agent auto-creates GF forms for any `role=form` section and replaces the visual mock-up with `[gravityform id=N]`. Without GF the form mock-up stays in place. |

Everything else is automated.

## Project layout

```
figma-elementor-agent/
├── CLAUDE.md                     ← agent entry point + Claude-as-Author rules
├── README.md                     ← this file
├── config.example.json
├── config.json                   ← dev's 4 fields (gitignored)
├── project-config.json           ← full config (auto-generated, gitignored)
├── project-state.json            ← cross-run state (gitignored)
│
├── agents/                       ← sub-agent definitions (.md)
│   ├── project-orchestrator.md
│   ├── importer.md
│   ├── wp-setup.md
│   ├── global-styles.md
│   ├── theme-builder.md
│   ├── page-builder.md
│   ├── visual-reviewer.md
│   └── auto-fixer.md
│
├── skills/                       ← shared knowledge (.md)
│   ├── elementor-rest/
│   ├── elementor-data-schema/
│   ├── global-styles-mapping/
│   ├── global-tokens/
│   ├── asset-pipeline/
│   ├── theme-builder/
│   ├── visual-diff/
│   ├── wp-architecture/
│   ├── optimization/
│   ├── confidence-fallback/
│   ├── template-reuse/
│   ├── dynamic-content/
│   └── form-intelligence/
│
├── scripts/                      ← Python + Node helpers
│   ├── import_elementor.py       ← main pipeline (Phases B–I)
│   ├── enrich.py                 ← loads ai-layout / tokens / validation / assets
│   ├── section_finder.py         ← recursive section walker (any depth)
│   ├── widget_inference.py       ← agent's own widget detectors
│   ├── auto_layout_inference.py  ← infer flex from absolute positions
│   ├── design_tokens.py          ← :root --token-* CSS bridge
│   ├── optimize.py               ← token resolver + collapse + depth cap
│   ├── architecture.py           ← popup trigger inference
│   ├── template_reuse.py         ← fingerprint-based dedupe
│   ├── form_intelligence.py      ← Gravity Forms detection + creation
│   ├── dynamic_content.py        ← blog grid → Posts widget
│   ├── validation_layer.py       ← confidence score + screenshot fallbacks
│   ├── section_crops.py          ← per-section PNG crops
│   ├── prompt_template.py        ← prompt-driven 404 / search / popup
│   ├── project_state.py          ← cross-run state cache
│   ├── visual_compare.py         ← Playwright capture + pixel diff
│   ├── fix_plan.py               ← merge import-report + diff-report
│   ├── patch_elementor.py        ← targeted JSON patches
│   ├── wp_client.py              ← WP REST + bridge wrapper
│   ├── playwright_capture.js
│   ├── pixelmatch_compare.js
│   ├── package.json
│   ├── requirements.txt
│   └── wp-bridge/
│       └── figma-importer-bridge.php  ← auto-copied to mu-plugins
│
├── tests/                        ← pytest suite (33 tests)
│   ├── conftest.py
│   ├── test_optimize.py
│   ├── test_architecture.py
│   ├── test_validation_layer.py
│   ├── test_template_reuse.py
│   ├── test_form_intelligence.py
│   ├── test_dynamic_content.py
│   ├── test_design_tokens.py
│   └── test_prompt_template.py
│
├── .venv/                        ← auto-created (gitignored)
└── build/                        ← extracted ZIP + reports (gitignored)
```

## How the import works (in depth)

### 1. Auth + bridge
The bridge mu-plugin exposes its own REST namespace (`figma-importer/v1`)
that bypasses WP core's protection of `_elementor_*` private meta keys.
`/login` validates credentials via `wp_signon()` and returns a `wp_rest`
nonce that's stored on the Python `requests.Session`. **No Application
Passwords; no external auth plugin.**

### 2. Recursive section finder (any depth)
Real Figma files often wrap the entire page in a single root frame, so
"top-level" routing is useless. `section_finder.py` walks the whole
tree and identifies sections by:

| Source | Confidence weight |
|--------|-------------------|
| plugin's `_figma_section_purpose` / `_ai_role` | 0.85–1.00 (highest) |
| layer-name regex (header, nav, footer, popup, …) | 0.70 |
| structural shape (image + heading + CTA = hero) | 0.55 |
| geometric (top-of-page slim full-width = header) | 0.55 |

Overlapping classifications resolve via:
- ancestor wins ties on same-kind matches (preserves broader scope)
- singleton kinds (header, footer, archive, etc.) keep highest confidence only
- hero may repeat but capped at 3 / dropped when weak

### 3. Optimization (Phase E)
- **Global token resolver** — replaces inline hex / typography on every
  widget with `__globals__` references pointing at kit slugs. Survives
  Elementor's `iterate_data` round-trip thanks to the bridge's
  preservation pass.
- **Design-token CSS** — Elementor's globals don't cover spacing or
  border-radius. The agent injects `:root { --token-radius-md: 8px; … }`
  into the kit's `custom_css`, plus `.dt-radius-md` utility classes,
  and tags matching widgets. One CSS edit re-skins the entire site.
- **Widget inference** — re-runs detection at every depth (not just top
  level): icon-list, accordion, tabs, slides, image-carousel, counter,
  progress, star-rating, social-icons, video, image-box, icon-box,
  toggle, divider, spacer, nav-menu.
- **Auto-layout inference** — for non-flex containers with absolute
  children, infers `flex_direction` / `flex_gap` / alignment from the
  children's positions and promotes the container to real flex.
- **Container collapse** + **depth cap** — protect structural sections
  via `protected_ids` so headers/footers/heroes survive.

### 4. Architecture routing
Each `RealSection` is dispatched to a Theme Builder template type or
left on the page:

| Kind | Template type | Conditions (with Pro) |
|------|---------------|------------------------|
| header | header | `include/general` |
| footer | footer | `include/general` |
| popup | popup | inferred trigger (page-load / exit-intent / scroll / inactivity / cookie) |
| archive | archive | `include/post_archive` |
| single | single-page | `include/in_singular/post` |
| search | search-results | manual assignment |
| 404 | error-404 | manual assignment |
| hero / cta / etc. | (page) | renders inline |

Templates are **slug-upserted** — re-running with the same Figma file
updates existing templates instead of duplicating.

### 5. Form intelligence
Sections with `_ai_role: form` (any depth) become real Gravity Forms.
Field-type inference from labels:

| Label regex | GF type |
|-------------|---------|
| `email` | `email` |
| `phone\|mobile\|tel` | `phone` |
| `website\|url` | `website` |
| `message\|comment\|details` | `textarea` |
| `address\|street\|city\|zip` | `address` |
| `name` | `name` |
| `number\|amount\|qty` | `number` |
| _other_ | `text` |

Without Gravity Forms installed, the visual mock-up stays in place and
the agent prints a hint.

### 6. Confidence + fallbacks
Every section carries a confidence score (plugin's + agent's combined).
Sections below `--low-confidence-threshold` (default 0.5) get replaced
with their per-section screenshot — the page renders pixel-perfect for
that band even when the structural attempt would have been wrong. The
visual reviewer marks those bands as `manual-review` so a clean pixel
diff there isn't reported as a false PASS.

### 7. Page settings
Set automatically on every page:
- `hide_title: yes` — Elementor's "Hide Title" toggle
- `template: elementor_canvas` when the agent created its own header/footer
- `template: elementor_header_footer` (Full Width) when no header/footer
  was detected, so the theme's chrome wraps the content
- `container_width` derived from the Figma frame width (capped at 1920px)

### 8. Visual review + auto-fix
`visual_compare.py` captures the live page with Playwright (Chromium
headless), diffs against the Figma screenshot via pixelmatch, and writes
`report.json` with per-band drift. The auto-fixer reads
`build/import-report.json` + `build/diff/report.json`, runs
`scripts/fix_plan.py` for a prioritized candidate list, and applies
patches via `patch_elementor.py`. Up to 3 iterations.

For high-drift / low-confidence bands, the auto-fixer **dispatches Claude
sub-Agents** with the live crop + expected crop + current Elementor JSON,
and applies whatever JSON patch Claude proposes (Claude-as-Author pattern,
documented in `CLAUDE.md`).

## Direct CLI use (power users)

After the first `start` has bootstrapped, every script runs standalone:

```bash
# End-to-end import
.venv/bin/python scripts/import_elementor.py

# Apply only global styles (no header/footer/page)
.venv/bin/python scripts/import_elementor.py --only-globals

# Page-by-page mode (skip globals + theme builder + reuse)
.venv/bin/python scripts/import_elementor.py --page-only --page-slug about

# Dry run — print intended writes without sending
.venv/bin/python scripts/import_elementor.py --dry-run

# Reset prior agent uploads in the WP media library (destructive)
.venv/bin/python scripts/import_elementor.py --reset-media

# Lower the confidence threshold (more screenshot fallbacks)
.venv/bin/python scripts/import_elementor.py --low-confidence-threshold 0.7

# Visual diff after import
.venv/bin/python scripts/visual_compare.py

# Targeted patch
.venv/bin/python scripts/patch_elementor.py --slug home --set-setting el00001 title_color '"#000"'

# Prompt-driven 404 / search / popup (no Figma file)
.venv/bin/python scripts/prompt_template.py --type 404 \
    --spec '{"title":"Not found","primary_cta":{"text":"Home","url":"/"},"show_search":true}'

# Run the test suite
.venv/bin/python -m pytest tests/ -v
```

## CLI flags reference

| Flag | Effect |
|------|--------|
| `--config <path>` | Override `project-config.json` location |
| `--zip <path>` | Override the auto-detected Figma ZIP |
| `--page-slug <slug>` | Override the page slug (default: from config) |
| `--dry-run` | Print intended REST writes, send nothing |
| `--page-only` | Skip globals + header/footer + reuse (2nd+ page mode) |
| `--reset-media` | Delete agent-uploaded attachments before upload (destructive) |
| `--skip-globals` | Don't touch kit settings |
| `--skip-menus` | Don't create / update WP nav menus |
| `--reset-menus` | Wipe menu items before re-adding (destructive) |
| `--skip-header-footer` | Don't create theme-builder templates |
| `--skip-page` | Skip page creation |
| `--skip-assets` | Skip media upload |
| `--skip-optimize` | Skip token resolver / collapse / inference passes |
| `--skip-forms` | Don't create Gravity Forms |
| `--skip-template-reuse` | Don't dedupe duplicates into library templates |
| `--skip-fallbacks` | Don't replace low-confidence sections with screenshots |
| `--max-depth N` | Container nesting depth cap (default: 6) |
| `--low-confidence-threshold X` | Sections below get screenshot fallback (default: 0.5) |
| `--only-globals` | Shortcut: only apply globals, skip everything else |

## Reports the agent produces

| File | Purpose |
|------|---------|
| `build/state.json` | Per-run scratch state (asset map, optimize stats, placements) |
| `build/import-report.json` | Confidence score + risk areas + fallback indices |
| `build/diff/report.json` | Drift % per y-band + manual-review bands |
| `build/diff/{live,expected,diff}.png` | Visual diff artifacts |
| `project-state.json` | Cross-run cache (kit_id, template ids, form ids, asset map, imported pages) |

## Testing

```bash
.venv/bin/python -m pytest tests/ -v
```

33 tests cover: optimize passes, architecture routing, popup-trigger
inference, validation/confidence, template reuse (top-level + nested),
form intelligence, dynamic content, design tokens, prompt templates.

## Constraints / known gaps

- **Elementor Pro** unlocks Theme Builder display conditions (header,
  footer, archive, single auto-apply). Without Pro, library posts are
  created but must be assigned manually.
- **Search results / 404** templates require Pro's "Other" template type
  for assignment. The agent creates the post but prints explicit
  instructions when Pro isn't detected.
- **Gravity Forms** is the only form provider currently supported. The
  bridge has GF endpoints; alternative providers (CF7, WPForms) would
  need a parallel implementation.
- **Visual diff** uses pixel-level comparison. Areas with carousels,
  animations, or video backgrounds may show false positives — the
  visual reviewer flags fallback bands as `manual-review` to compensate.
- **Auto-fixer** loops 3 times max. Structural drift > 50% on a section
  is flagged for manual fix rather than auto-patched (too risky).

## Troubleshooting

**`/wp-json/figma-importer/v1/health` returns 404**
- `Settings → Permalinks` must be **Post name**, not "Plain".
- Confirm `<wp-root>/wp-content/mu-plugins/figma-importer-bridge.php` exists.
- `wp-admin → Plugins → Must-Use` should list "Figma Importer Bridge".
- Check the PHP error log for fatals.

**`401 Unauthorized` from REST**
- Wrong admin username/password in `config.json`. Edit it, delete
  `project-config.json`, run `start` again.
- LocalWP saves the password you set during site creation.

**Header/footer don't show on pages**
- Elementor Pro is required for Theme Builder display conditions.
  Without Pro, the templates exist in `Templates → Saved Templates` but
  won't auto-apply.

**Globals saved to Site Settings but widgets still show inline values**
- The bridge's `iterate_data` pass preserves `__globals__` references —
  but ONLY in the patched bridge. If you bootstrapped before
  `figma-importer-bridge.php` was patched, force a re-copy by deleting
  the file in `wp-content/mu-plugins/` and running `start` again.

**`wp-config.php not found`**
- The agent folder isn't inside (or beside) a WordPress site root. Move
  it under `<wp-root>/` or to the LocalWP site root.

**Module import errors after `pip install`**
- Python 3.14 has cookie-handling changes that older `requests` versions
  hit. Run `.venv/bin/pip install --upgrade 'requests>=2.32.3'`. If the
  issue persists, recreate the venv against Python 3.12 / 3.13.

**Sections look pixel-perfect but aren't editable**
- Those are screenshot fallbacks — sections the agent's confidence layer
  couldn't classify reliably. They're tagged with `_low_confidence: true`
  and surface in `build/import-report.json::riskAreas`. Either improve
  the Figma file's auto-layout coverage or pass
  `--low-confidence-threshold 0.3` to be more permissive.

**Re-running creates duplicate templates**
- Shouldn't happen — templates are slug-upserted via the bridge. If you
  see duplicates, the prior run probably left a template in the trash;
  empty the trash in `wp-admin → Templates` and re-run.

## Claude-as-Author pattern

For sections where the deterministic pipeline lands a low-confidence
result OR shows >15% drift in the visual diff, the orchestrator
dispatches a Claude sub-Agent with:

- the section's expected crop + live crop
- the current Elementor JSON for the node
- the relevant ai-layout subtree
- the kit's global slugs

Claude returns a JSON patch the agent applies via `patch_elementor.py`.
Up to 5 sub-Agent dispatches per build. See `CLAUDE.md` for the
full protocol.

This is what closes the gap from "pixel-faithful but mechanical" to
"production-quality Elementor". The Python pipeline does the easy 80%;
Claude reasons about the hard 20%.

## License

Internal use.
