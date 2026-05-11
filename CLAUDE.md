# Figma → Elementor Agent — Auto-Start

This repo turns a Figma plugin export ZIP into a live Elementor WordPress page.
The companion Figma plugin (developed separately) produces a ZIP containing
valid Elementor JSON (`data.json`), design tokens (`global.json`), screenshots,
and image assets. This agent's job is to **push that into WordPress, configure
global settings, build header + footer, and visually verify** the result.

## Developer flow (zero scripts, zero deps to install by hand)

1. Drop two things into the WordPress root:
   - the `figma-elementor-agent/` folder (this repo)
   - the Figma plugin export `*.zip`
2. Inside `figma-elementor-agent/`, copy `config.example.json` → `config.json`
   and fill in **four fields**:
   ```json
   {
     "wp_url": "http://localhost:10048",
     "wp_user": "admin",
     "wp_password": "your-wp-admin-password",
     "theme_slug": "hello-elementor"
   }
   ```
3. Run `claude` from inside `figma-elementor-agent/`.
4. Type `start` (optionally with a free-form instruction, e.g.
   `start build the home page and skip the footer for now`).

That's it. The agent handles WP-root detection, bridge install, dep install,
ZIP detection, the full build, the visual diff, and the auto-fix loop.

## Expected layout

Two valid placements — the agent finds WP automatically:

**Inside the WP root** (cleanest):
```
<wp-root>/                              ← contains wp-config.php
├── wp-config.php
├── wp-content/mu-plugins/              ← bridge plugin auto-installed here
├── figma-elementor-agent/
│   └── config.json                     ← dev fills in 4 fields
└── your-design.zip
```

**At the LocalWP site root** (sibling of `app/`, `conf/`, `logs/`):
```
~/Local Sites/<site-name>/
├── app/public/                         ← WordPress lives here (auto-detected)
├── conf/
├── logs/
├── figma-elementor-agent/
│   └── config.json
└── your-design.zip
```

The agent searches the current dir + ancestors and probes the well-known
subpaths `app/public/`, `public/`, `public_html/`, `wordpress/`, `htdocs/`,
`wp/`, `www/`.

## On Session Start — Do This Immediately

When a session begins, do not wait for further instructions. Immediately:

1. Check whether `config.json` exists in this folder.
   - **If missing** → tell the developer to copy `config.example.json` to
     `config.json` and fill in the four fields. Stop and wait.
   - **If present** → continue.
2. Wait for the developer's first message.
   - If it begins with `start` (alone or with extra text), invoke the
     `project-orchestrator` agent with argument `start` and pass any
     trailing text as the developer's free-form instruction.
   - Any other message → answer it, then offer to run `start` when ready.

You are the entry point. Read the state, self-plan, and begin. The developer
only runs `claude` and types `start`; you handle the rest.

## What the orchestrator bootstraps automatically

On the first `start`, **before** any phase runs, the `project-orchestrator`:

1. Reads `config.json` (the 4 dev-supplied fields).
2. Walks up from this folder to find `wp-config.php` → that's `wp_root`.
3. Copies `scripts/wp-bridge/figma-importer-bridge.php` into
   `<wp-root>/wp-content/mu-plugins/` (creating the folder if needed).
4. Auto-detects the Figma export `*.zip` in the WP root or its parent.
5. Ensures Python deps (`requests`, `Pillow`) are installed inside a local
   `.venv/`, and Node deps (Playwright + pixelmatch) inside `scripts/node_modules/`.
6. Writes `project-config.json` (mode 600) — the full config every
   downstream script and sub-agent reads.
7. Verifies the bridge by hitting `/wp-json/figma-importer/v1/health`.

Once `project-config.json` exists, subsequent `start` runs skip steps 2–6
unless something is missing or stale.

## System Overview

| Agent | Role |
|-------|------|
| `project-orchestrator` | Master coordinator. Bootstraps, then runs phases A–F, dispatches sub-agents. |
| `importer` | Unzips and validates the Figma export. Produces a normalized work tree under `build/`. |
| `wp-setup` | Verifies WP REST auth, ensures Elementor (and Pro if needed) is active. |
| `global-styles` | Applies `global.json` (colors + typography) to Elementor Site Settings. |
| `theme-builder` | Detects header/footer sections and creates Elementor Theme Builder templates. |
| `page-builder` | Uploads assets, rewrites URLs, creates the page, writes `_elementor_data`. |
| `visual-reviewer` | Renders the live page (Playwright), diffs vs Figma screenshot, emits issues. |
| `auto-fixer` | Patches `_elementor_data` to resolve detected drift, re-renders, re-checks. |

## Phase Plan

```
A. Setup           → project-orchestrator → wp-setup
B. Import          → importer (extract ZIP → build/)
C. Globals         → global-styles
D. Theme Builder   → theme-builder (header + footer)
E. Page            → page-builder
F. Review + Fix    → visual-reviewer → auto-fixer (loop, max 3 iters)
```

## Config files

Two config files live in this folder:

**`config.json`** — the only file the developer touches. Four fields:
`wp_url`, `wp_user`, `wp_password`, `theme_slug`. Gitignored.

**`project-config.json`** — generated by the orchestrator on first run.
Includes `config.json` + auto-detected `wp_root`, `zip_path`, `page_slug`,
header/footer patterns, menu names/locations, fonts/forms/CPT lists.
All sub-agents and scripts read this. Gitignored.

`fonts`, `forms`, and `custom_post_types` are the only project-level
extras retained from the previous ACF system. Page-requirement configs and
feature lists are intentionally removed — Elementor JSON is the source of truth.

## Key Paths

```
<wp-root>/figma-elementor-agent/   ← repo root
├── config.example.json            ← template; copy to config.json
├── config.json                    ← dev's 4 fields (gitignored)
├── project-config.json            ← full config (auto-generated; gitignored)
├── build/                         ← extracted ZIP + working artifacts
│   └── export/{data,global,metadata}.json + screenshots/ + assets/
├── agents/                        ← agent definitions (.md)
├── skills/                        ← skill definitions (.md)
└── scripts/                       ← Python + Node helpers
    └── wp-bridge/figma-importer-bridge.php  ← copied into wp-content/mu-plugins/
```

## Claude-as-Author pattern (high-quality builds)

The deterministic Python pipeline (`import_elementor.py`) handles asset
upload, kit globals, structural section finding, widget inference,
auto-layout inference, and template creation. For sections where it
lands a low-confidence result OR the visual-diff shows >15% drift in
that section's band, the orchestrator should **dispatch a Claude
sub-Agent** to author the section directly:

1. After Phase F (page created), read `build/import-report.json` and
   `build/diff/report.json` (post Phase G). Identify sections with:
   * `confidence < 0.7` in the import report, OR
   * the section's y-band has `drift > 0.15` in the diff report
2. For each such section, prepare a Claude review bundle:
   ```python
   {
     "section_kind": "hero" | "feature-grid" | "testimonial" | …,
     "elementor_json": <current node from data.json>,
     "ai_subtree":     <matching ai-layout subtree>,
     "tokens":         <relevant slice of tokens.json>,
     "expected_crop":  "build/<export>/screenshots/sections/<id>.png",
     "live_crop":      "build/diff/sections/<id>.png",
     "kit_globals":    <relevant slugs from kit settings>
   }
   ```
3. Invoke the `Agent` tool with `subagent_type=general-purpose` and a
   prompt that includes the above bundle. Ask the sub-agent to:
   * Compare `expected_crop` to `live_crop`
   * Examine the current `elementor_json`
   * Return a JSON patch (RFC 6902 style or a settings dict diff) that
     would make the live render match the expected
4. Apply the patch via `scripts/patch_elementor.py`.
5. Re-render via `scripts/visual_compare.py`. If new drift on that band
   ≥ previous drift, revert. Otherwise proceed.

This is the **Claude-as-Author** pattern. Python ≠ generator; Python =
infrastructure. Claude reasons about layout, spacing, widget choice;
Python applies the resulting JSON.

Bound the loop:
* No more than 5 sub-Agent dispatches per build (each is a context
  window cost).
* Skip sections marked `manual_review` in `fix_plan.py` output.
* Stop when overall drift ≤ 5% OR the same section was patched twice
  with no improvement.

## Rules — Always Enforced

- **Never** hardcode credentials, URLs, or paths — read from `project-config.json`.
- **Never** modify the homepage of a target site without explicit confirmation.
- **Always** dry-run unfamiliar REST writes (`--dry-run`) before sending live.
- **Always** upload assets before rewriting URLs in `_elementor_data`.
- **Always** capture before/after screenshots when auto-fixer mutates a page.
- **Always** stop the auto-fix loop after 3 iterations and report remaining drift.
- **Never** rely 100% on the Figma plugin's high-level decisions. Plugin
  signals (`preferredWidget`, `sectionPurpose`, `_ai_role`) are inputs,
  not gospel. The agent's own structural detectors must produce a
  classification independently and the highest-confidence answer wins.
