# Figma → Elementor Agent

Convert a Figma plugin export ZIP into a live Elementor page on
WordPress. The agent applies global tokens, builds Theme Builder
header/footer, creates the page, runs a pixel diff against the Figma
screenshot, and auto-fixes drift — all driven by Claude Code.

Most of the work runs in deterministic Python (token-efficient). Claude
sub-Agents are only dispatched for low-confidence widget picks or
high-drift sections.

---

## TL;DR — what a developer actually does

```bash
# 1. One-time host install (per machine — run from anywhere)
brew install python@3.12 node                                   # macOS
npm install -g @anthropic-ai/claude-code

# 2. Per project — drop the agent + ZIP next to LocalWP's wp-config.php
cd ~/Local\ Sites/<site-name>/
git clone <this-repo> figma-elementor-agent
mv ~/Downloads/your-design.zip .

# 3. Install the Playwright browser (run INSIDE scripts/ — required for visual diff)
cd figma-elementor-agent/scripts
npm install
npx playwright install chromium
cd ..

# 4. Configure WP credentials
cp config.example.json config.json
$EDITOR config.json          # fill wp_url, wp_user, wp_password, theme_slug

# 5. Register agents + skills with Claude Code (once per project)
mkdir -p .claude
ln -sfn ../agents .claude/agents
ln -sfn ../skills .claude/skills

# 6. Run
claude
> start
```

That's it. The orchestrator handles WP-root detection, bridge install,
Python venv + pip install, ZIP detection, plan generation, build,
visual diff, auto-fix, and artifact archival.

> **Where each install runs**
> - `brew install …` and `npm install -g …` → **system-wide**, any directory.
> - `npx playwright install chromium` → **must be run inside `scripts/`** (Playwright is declared in `scripts/package.json`; Chromium downloads into `scripts/node_modules/playwright/.local-browsers/`).
> - The orchestrator runs `npm install` inside `scripts/` and creates `.venv/` at the repo root automatically on first `start` — but the **Chromium binary download (~170 MB)** is the one step worth doing up front so the visual-diff phase doesn't stall.

---

## Prerequisites

Host-machine tools the bootstrap can't install for you:

| Tool | Purpose | macOS | Windows |
|------|---------|-------|---------|
| Python 3.10–3.13 | runs import + diff pipeline | `brew install python@3.12` | [python.org installer](https://www.python.org/downloads/) — tick **Add Python to PATH** |
| Node 18+ | Playwright + Claude Code | `brew install node` | [nodejs.org LTS](https://nodejs.org) |
| Claude Code CLI | runs the agent | `npm install -g @anthropic-ai/claude-code` | same, in PowerShell |
| Local WordPress | the target site | [LocalWP](https://localwp.com) | LocalWP / WSL2 |

WordPress site requirements:
- **Elementor** (Free or Pro) plugin installed and activated.
- **Permalinks** set to *Post name* (Settings → Permalinks).
- *(Optional)* **Elementor Pro** — required for Theme Builder display
  conditions (header / footer / archive / single auto-apply). Without
  Pro, the agent will offer an "inline" build instead of failing.
- *(Optional)* **Gravity Forms** — required if your Figma file has form
  sections you want wired up automatically.

> **Python 3.14 note** — its cookie-handling changes break older
> `requests`. Either upgrade (`pip install --upgrade 'requests>=2.32.3'`)
> or build the venv against 3.12 / 3.13.

---

## Setup — macOS / Linux

1. **Place the agent + ZIP next to WordPress.** Either of these layouts
   works — the agent walks up to find `wp-config.php`.
   ```
   ~/Local Sites/<site-name>/
   ├── app/public/                ← WordPress
   ├── conf/                       (LocalWP)
   ├── logs/                       (LocalWP)
   ├── figma-elementor-agent/     ← this repo
   └── your-design.zip            ← Figma plugin export
   ```
2. **Install the Playwright Chromium browser** (one-time, inside `scripts/`).
   The visual-diff phase needs it; the orchestrator runs `npm install` for
   you but does **not** auto-download the ~170 MB Chromium binary.
   ```bash
   cd ~/Local\ Sites/<site-name>/figma-elementor-agent/scripts
   npm install
   npx playwright install chromium
   cd ..
   ```
3. **Fill in `config.json`.**
   ```bash
   cp config.example.json config.json
   $EDITOR config.json
   ```
   ```json
   {
     "wp_url": "http://<site-name>.local",
     "wp_user": "admin",
     "wp_password": "<password from LocalWP>",
     "theme_slug": "hello-elementor"
   }
   ```
4. **Symlink agents + skills into `.claude/`** (Claude Code reads them
   from there).
   ```bash
   mkdir -p .claude
   ln -sfn ../agents .claude/agents
   ln -sfn ../skills .claude/skills
   ```
5. **Run.**
   ```bash
   claude
   ```
   Type `start` at the Claude prompt.

On first run the orchestrator auto-installs everything else:
- Detects `wp_root` by walking up to `wp-config.php`.
- Copies `scripts/wp-bridge/figma-importer-bridge.php` into
  `<wp-root>/wp-content/mu-plugins/`.
- Creates `.venv/` at the repo root and installs `requests` + `Pillow`.
- Re-runs `npm install` inside `scripts/` (idempotent) for Playwright + pixelmatch.
- Writes `project-config.json` (mode 600).
- Verifies the bridge at `/wp-json/figma-importer/v1/health`.

If you skipped step 2 and the visual-diff phase errors with
*"Executable doesn't exist … run npx playwright install"*, run it now
from inside `scripts/`:
```bash
cd scripts && npx playwright install chromium
```

---

## Setup — Windows

Use **PowerShell** (not legacy Command Prompt).

1. Install Python (tick "Add to PATH"), Node LTS, Claude Code, LocalWP.
2. Place the agent + ZIP inside the LocalWP site folder:
   ```
   %USERPROFILE%\Local Sites\<site-name>\
   ├── app\public\
   ├── figma-elementor-agent\
   └── your-design.zip
   ```
3. Install the Playwright Chromium browser inside `scripts\` (one-time):
   ```powershell
   cd "$env:USERPROFILE\Local Sites\<site-name>\figma-elementor-agent\scripts"
   npm install
   npx playwright install chromium
   cd ..
   ```
4. Fill in `config.json` (same fields as macOS).
5. Register agents + skills using a junction (no admin needed):
   ```powershell
   New-Item -ItemType Directory -Force -Path .claude | Out-Null
   cmd /c mklink /J .claude\agents agents
   cmd /c mklink /J .claude\skills skills
   ```
6. Run `claude` and type `start`.

Windows-specific notes:
- All direct script calls use `.venv\Scripts\python.exe` instead of
  `.venv/bin/python`.
- Quote paths with spaces (`"Local Sites"`).
- If you skipped step 3 and Playwright fails at the diff phase:
  `cd scripts; npx playwright install chromium`.
- WSL2 is the cleanest alternative — setup matches macOS step-for-step.

---

## Daily use — building pages

After setup, every build is the same three commands.

```bash
cd <path-to>/figma-elementor-agent
# overwrite the previous ZIP if needed — filename doesn't matter
claude
> start
```

### What the orchestrator does per run

| Step | What it produces | Skip with |
|------|-------------------|-----------|
| **A. Setup** | confirms Elementor + Pro + GF presence (Pro-missing → prompts you for `install` or `inline`) | n/a |
| **B. Extract** | unpacks the ZIP into `build/<export>/` | `--from-cache` |
| **B′. Plan + preflight** | `build/build-plan.json`, `build/widget-review-queue.json`, design-system + missing-baseline warnings | `--skip-plan` |
| Plan-stage Claude review | dispatches Opus 4.7 for sections with widget confidence < 0.7. Adaptive budget: 8 calls on first run, 3 on incremental | empty queue auto-skips |
| Fix-history pre-apply | when a prior successful run for this slug exists, re-applies its patches before the import | runs only when `build/fix_history.json` exists |
| **WP-drift check** | before page write, compares live `_elementor_data` against last archived run; refuses to overwrite hand-edits unless `--force` | `--force` to override |
| **C–I. Build** | kit globals (+ delta-E fuzzy color match + font-weight canon), Theme Builder templates (partial mode OK), assets (content-hash dedup), responsive defaults (stack/scale mobile + tablet) | various `--skip-*` flags |
| **J. Visual review** | per-breakpoint pixel diff + **per-section drift** + **DOM-structure diff** (rescues animation/FOUT/lazy-image false positives) | n/a |
| **K. Auto-fix** | priority-queue Claude review by section purpose × severity, up to 3 iterations; records successful patches into `fix_history.json` | n/a |
| **Quality gate** | drift ≤ 5% (captured breakpoints), global coverage ≥ 70%, ≥ 1 of {header, footer} (partial OK), zero asset failures, optional perf budget | (mandatory) |
| **L. A11y audit** | static accessibility audit: alt-text, heading hierarchy, WCAG contrast → `a11y-report.json` | n/a |
| **L. Figma feedback** | `figma-suggestions.md` — what to fix in Figma to lift the next gate (repeated colors → tokens, missing baselines, etc.) | n/a |
| **L. Regression diff** | compares this run with the previous archived run for the same slug → `regression-report.json` | n/a |
| **L. Archive** | copies all of the above into `pages/<slug>/<timestamp>/` | n/a |

### Command reference for the Claude prompt

| You type | What runs |
|----------|-----------|
| `start` | Full build — globals + header/footer + page + visual diff + DOM diff + a11y + Figma feedback + regression diff. |
| `start --page-only` | 2nd+ page on the same site — skips globals + theme builder + reuse. |
| `start --page-only --page-slug about` | Same, with an explicit slug. |
| `start no confirmations` | Skip the "Proceed? [Y/n]" prompt. The phrases `just build`, `permissions granted`, `do not ask` all trigger auto-confirm. |
| `start build only the hero, skip the footer` | Free-form scoping in natural language. |
| `start inline-only` | Force inline header/footer (skip Theme Builder gate; useful when no Pro). |
| `start --from-cache` | Skip ZIP extraction + optimization; re-push `build/data.json` to the live page. ~10s instead of ~3 min. |
| `start --force` | Overwrite the live page even when WP-side drift is detected (the page was hand-edited in WP admin since the last build). |

The first page on each new site is the **home page** — its ZIP should
contain header / footer / globals / page content. Subsequent pages
auto-skip those phases when `project-state.json` shows a prior
successful run.

### Plan-first workflow (recommended for new designs)

When you're not sure the agent's widget picks will be right, run
plan-only first to see them without writing to WP:

```bash
.venv/bin/python scripts/import_elementor.py --plan-only
.venv/bin/python scripts/build_plan.py --print   # human-readable table
```

This emits:
- `build/build-plan.json` — every detected section with its kind,
  confidence, chosen widget, and tokens it will consume.
- `build/widget-review-queue.json` — subset with widget confidence
  below 0.7. The orchestrator dispatches Claude for these at plan
  stage when you run a real `start`.

If the plan looks wrong, fix the Figma source (better layer names,
clearer brand colors, complete typography styles) and re-export the ZIP
before running `start`. Catching mistakes here is far cheaper than
catching them post-import via visual diff.

### Re-running after a Figma tweak

`fix_history.json` is keyed by `_figma_name + kind`. When a section is
renamed in Figma the cached patch for it will be skipped (logged but
non-fatal). To reset the cache:

```bash
rm build/fix_history.json
```

---

## Pipeline overview

```
A.  Setup            wp-setup              auth + bridge + Elementor + GF + Pro
B.  Import           importer              extract ZIP, load enrichment
B′. Plan             build_plan.py         read-only plan + preflight + widget queue
C.  Globals          global-styles         kit colors (+ delta-E fuzzy) / typography (+ canon) + tokens
D.  Optimize         optimization          token resolver + widget inference + fingerprint guard + collapse
E.  Architecture     wp-architecture       route sections to header/footer/popup/page
F.  Templates        theme-builder         create header/footer/popup/archive/single (partial OK)
G.  Forms            form-intelligence     Gravity Forms creation + shortcode
H.  Reuse            template-reuse        fingerprint dedupe into library templates
I.  Page             page-builder          assets (content-hash dedup) + tree + responsive defaults → page
                                            (WP-drift check before write; --force to override)
J.  Visual review    visual-reviewer       pixel + per-section + DOM-structure diff
K.  Auto-fix         auto-fixer            priority-queue Claude (Opus 4.7) + patch loop (max 3 iter)
L.  A11y audit       a11y_audit.py         alt-text + heading hierarchy + WCAG contrast
L.  Feedback         figma_feedback.py     figma-suggestions.md — what to fix upstream
L.  Regression       regression_diff.py    drift / coverage delta vs the previous archived run
L.  Archive          finalize_artifacts.py copy run into pages/<slug>/<ts>/
```

Phases C → I run inside `scripts/import_elementor.py` as a single
process. The named phases are the agent's mental model for triaging
failures, not separate script invocations. The L-phase auxiliaries
(a11y, feedback, regression diff) run inside `finalize_artifacts.py`
so they always produce output even if any individual one errors.

**Model policy.** The project-orchestrator, visual-reviewer, and
auto-fixer agents pin `model: opus` in their frontmatter. The
Claude-as-Author sub-Agent dispatches inherit Opus 4.7 for design-
fidelity reasoning. The deterministic Python pipeline does not use an
LLM, so the Opus cost only applies to the 3–8 review dispatches per
build.

---

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
├── agents/                       ← sub-agent definitions
│   ├── project-orchestrator.md
│   ├── importer.md
│   ├── wp-setup.md
│   ├── global-styles.md
│   ├── theme-builder.md
│   ├── page-builder.md
│   ├── visual-reviewer.md
│   └── auto-fixer.md
│
├── skills/                       ← shared knowledge
│   ├── elementor-rest/   elementor-data-schema/   elementor-widgets/
│   ├── global-styles-mapping/   global-tokens/    asset-pipeline/
│   ├── theme-builder/    visual-diff/    wp-architecture/
│   ├── optimization/     confidence-fallback/    template-reuse/
│   ├── dynamic-content/  form-intelligence/
│
├── scripts/                      ← Python + Node helpers
│   ├── import_elementor.py       ← main pipeline; --plan-only, --from-cache, --force
│   ├── build_plan.py             ← plan + widget-review queue (read-only)
│   ├── preflight.py              ← design-system token + missing-baseline check
│   ├── claude_review.py          ← priority-queue Claude bundles; --prior-runs for adaptive budget
│   ├── visual_compare.py         ← pixel diff; --per-section, --dom-diff
│   ├── dom_diff.py               ← NEW — DOM-structure diff (rescues FOUT / animation false positives)
│   ├── playwright_section_rects.js  ← fetches live section bounding rects
│   ├── playwright_dom_fingerprint.js  ← NEW — fetches widget-type tree + text hashes
│   ├── widget_fingerprints.py    ← NEW — per-kind structural ceiling (descendants, types, text density)
│   ├── widget_inference.py       ← agent's widget detectors (gated by fingerprints)
│   ├── fix_history.py            ← patch cache for re-runs (apply / show)
│   ├── fix_plan.py               ← prioritized fix candidates
│   ├── patch_elementor.py        ← targeted JSON patches
│   ├── a11y_audit.py             ← NEW — alt-text + heading hierarchy + WCAG contrast
│   ├── figma_feedback.py         ← NEW — figma-suggestions.md generator
│   ├── regression_diff.py        ← NEW — build N vs N-1 drift / coverage delta
│   ├── finalize_artifacts.py     ← archives run + runs a11y + feedback + regression diff
│   ├── enrich.py                 ← loads ai-layout / tokens / validation / assets
│   ├── section_finder.py         ← recursive section walker (any depth)
│   ├── auto_layout_inference.py  ← infer flex from absolute positions
│   ├── design_tokens.py          ← :root --token-* CSS bridge
│   ├── optimize.py               ← token resolver (delta-E + typo canon) + collapse + responsive defaults
│   ├── architecture.py           ← popup trigger inference
│   ├── template_reuse.py         ← fingerprint dedupe
│   ├── form_intelligence.py      ← Gravity Forms detection + creation
│   ├── dynamic_content.py        ← blog grid → Posts widget
│   ├── validation_layer.py       ← confidence score + screenshot fallbacks
│   ├── section_crops.py          ← per-section PNG crops
│   ├── prompt_template.py        ← prompt-driven 404 / search / popup
│   ├── project_state.py          ← cross-run state (asset_map_by_filename + by_hash)
│   ├── wp_client.py              ← WP REST + bridge wrapper
│   ├── playwright_capture.js     ← full-page capture (multi-viewport)
│   ├── pixelmatch_compare.js
│   ├── package.json
│   ├── requirements.txt
│   └── wp-bridge/figma-importer-bridge.php  ← auto-copied to mu-plugins
│
├── tests/                        ← pytest suite
├── .venv/                        ← auto-created (gitignored)
├── build/                        ← per-run scratch (gitignored, wiped each run)
└── pages/                        ← per-run archive (gitignored, persists)
    └── <slug>/<timestamp>/
        ├── build-plan.json
        ├── import-report.json    ← includes asset_failures + a11y_issues
        ├── widget-review-queue.json
        ├── state.json
        ├── data.json             ← pre-regen tree (used by WP-drift on next run)
        ├── id_map.json           ← pre-regen → post-regen ids
        ├── fix_history.json
        ├── wp_drift.json         ← populated when live page was hand-edited
        ├── a11y-report.json      ← alt-text + contrast + heading audit
        ├── figma-suggestions.md  ← what to fix in Figma to lift the next gate
        ├── regression-report.json  ← drift / coverage delta vs prior run
        ├── diff/{report.json, diff.png, sections/...}
        └── manifest.json
```

---

## CLI reference

These run directly after the first `claude > start` has bootstrapped.
On Windows substitute `.venv\Scripts\python.exe` for `.venv/bin/python`.

### `import_elementor.py` — full pipeline

| Flag | Effect |
|------|--------|
| `--plan-only` | Extract + plan + preflight; exit 0 before any WP writes. |
| `--from-cache` | **NEW.** Skip ZIP extraction + optimization; resume from `build/data.json`. ~10s repeats. |
| `--force` | **NEW.** Overwrite the live page even when WP-side drift is detected. |
| `--config <path>` | Override `project-config.json`. |
| `--zip <path>` | Override the auto-detected Figma ZIP. |
| `--page-slug <slug>` | Override page slug. |
| `--dry-run` | Print intended REST writes; send nothing. |
| `--page-only` | Skip globals + header/footer + reuse (2nd+ page). |
| `--only-globals` | Only apply kit globals. |
| `--no-require-theme-builder` | Disable the Theme Builder gate (allow inline header/footer). |
| `--low-confidence-threshold X` | Sections below get screenshot fallback (default 0.3). |
| `--max-depth N` | Container nesting cap (default 6). |
| `--skip-globals` / `--skip-header-footer` / `--skip-page` / `--skip-assets` | Targeted phase skips. |
| `--skip-menus` / `--skip-forms` / `--skip-template-reuse` / `--skip-fallbacks` | More targeted skips. |
| `--skip-optimize` / `--skip-responsive-defaults` | Skip optimization passes. |
| `--reset-media` / `--reset-menus` | Destructive — require `--confirm-destructive`. |

### `build_plan.py` — plan-only inspection

| Flag | Effect |
|------|--------|
| `--export-dir <path>` | Override extracted export dir (default `build/export/`). |
| `--widget-confidence-floor X` | Sections below go to widget-review queue (default 0.7). |
| `--print` | Print the plan as a human-readable table. |

### `visual_compare.py` — capture + diff

| Flag | Effect |
|------|--------|
| `--per-section` | Add per-section drift to `report.json::sections[]` by cropping by each section's bounding rect. |
| `--dom-diff` | **NEW.** Also capture live DOM and diff its widget-type tree + text against `build/data.json`. Sections that pass structure (animation / FOUT / lazy-image false positives) are marked `dom_rescued`. |
| `--threshold X` | Drift fraction above which a breakpoint fails (default 0.05). |
| `--viewports default \| desktop-only \| <json>` | Which viewports to capture. |
| `--width N` | Desktop width (default 1920). |
| `--page-slug <slug>` | Override page slug. |

### `claude_review.py` — Claude-as-Author bundles

| Flag | Effect |
|------|--------|
| `--from-plan` | Build bundles from `build/build-plan.json` (plan stage). |
| `--build` | Build bundles from `build/import-report.json` + `build/diff/report.json` (post-import). Priority queue ranks by `severity × section_purpose_weight`. |
| `--list` | List existing bundles. |
| `--confidence X` | Confidence floor for bundle inclusion. |
| `--drift X` | Drift ceiling for bundle inclusion (post-import only). |
| `--max N` | Cap dispatch count (default 5). |
| `--prior-runs N` | **NEW.** Adaptive budget: 0 ⇒ first run (8 dispatches, conf floor 0.7); >0 ⇒ incremental (3 dispatches, conf floor 0.55). |

### `dom_diff.py` — structural fingerprint diff *(new)*

| Flag | Effect |
|------|--------|
| `--url <url>` | Live page URL. Omit to write only the expected fingerprint for inspection. |
| `--width N` | Viewport width (default 1920). |
| `--expected <path>` | Path to expected `build/data.json` (default: `build/data.json`). |
| `--out <path>` | Output JSON (default: `build/diff/dom_diff.json`). |

### `regression_diff.py` — build N vs N-1 *(new)*

| Flag | Effect |
|------|--------|
| `--slug <slug>` | Page slug (default: from `project-config.json`). |
| `--baseline <dir>` | Specific run dir under `pages/<slug>/` (default: most recent). |
| `--json` | Emit JSON. |

### `figma_feedback.py` — designer feedback report *(new)*

| Flag | Effect |
|------|--------|
| `--out <path>` | Output path (default: `build/figma-suggestions.md`). |
| `--stdout` | Print to stdout instead of writing. |

### `a11y_audit.py` — static a11y audit *(new)*

| Flag | Effect |
|------|--------|
| `--data <path>` | Elementor tree (default: `build/data.json`). |
| `--state <path>` | State file with kit_globals (default: `build/state.json`). |
| `--out <path>` | Output JSON (default: `build/a11y-report.json`). |
| `--json` | Emit JSON to stdout instead of human output. |

### `verify_quality.py` — final gate

| Flag | Effect |
|------|--------|
| `--drift-threshold X` | Per-breakpoint drift ceiling (default 0.05). |
| `--min-global-coverage X` | Min color + typography coverage (default 0.7). |
| `--no-require-header` / `--no-require-footer` | Allow partial Theme Builder. |
| `--max-page-weight 2mb` | **NEW.** Perf budget on total uploaded asset size. |
| `--max-widgets N` | **NEW.** Perf budget on Elementor widget count. |
| `--max-runtime-depth N` | **NEW.** Perf budget on element nesting depth. |

### `fix_history.py` — patch cache

| Command | Effect |
|---------|--------|
| `fix_history.py show` | Print the current cache as JSON. |
| `fix_history.py apply --slug <slug>` | Pre-apply cached patches to the live page. |

### `finalize_artifacts.py` — archive a run

| Flag | Effect |
|------|--------|
| `--slug <slug>` | Page slug (default: from `project-config.json`). |
| `--keep-last N` | Prune older runs for this slug, keeping the N most recent. |

### `preflight.py` — design-system check

| Argument | Effect |
|----------|--------|
| `<path>` (positional) | Override path to `global.json`. |
| `--json` | Emit JSON instead of human-readable output. |

### `patch_elementor.py` — targeted patches

```bash
# Change a heading color
.venv/bin/python scripts/patch_elementor.py --slug home \
    --set-setting el00001 title_color '"#000000"'

# Change kit primary color
.venv/bin/python scripts/patch_elementor.py --set-color primary '#FF0055'

# Dump current tree
.venv/bin/python scripts/patch_elementor.py --slug home --get
```

---

## Reports the agent produces

| File | When | Purpose |
|------|------|---------|
| `build/build-plan.json` | Plan stage | Every section + widget pick + confidence + tokens it will use. |
| `build/widget-review-queue.json` | Plan stage | Sections with widget confidence < 0.7. Claude reviews these. |
| `build/state.json` | After import | Per-run scratch state — asset map, kit ids, template ids, placements. |
| `build/data.json` | After import | Rewritten Elementor tree (PRE-regen ids, stamped `_figma_pre_id`). |
| `build/id_map.json` | After import | pre-regen → post-regen id map built from `_figma_pre_id` markers. |
| `build/import-report.json` | After import | Confidence + global coverage + asset_failures + a11y_issues + risk areas. |
| `build/wp_drift.json` | Before write | Live-vs-archive node diff. Populated only when the live page was hand-edited in WP admin. |
| `build/diff/report.json` | After review | Drift per breakpoint + per-section drift + DOM diff result (`dom_rescued` per section). |
| `build/diff/{live,expected,diff}.png` | After review | Visual diff artifacts. |
| `build/diff/sections/<data_id>.{png,diff.png}` | With `--per-section` | Live crop + diff per section. |
| `build/diff/dom_diff.json` | With `--dom-diff` | DOM structural fingerprint diff. |
| `build/fix_history.json` | After auto-fix | Cached patches keyed by `_figma_name + kind`. Re-applied on next run. |
| `build/a11y-report.json` | After import | Alt-text + heading hierarchy + WCAG contrast issues. |
| `build/figma-suggestions.md` | After import | What to fix in Figma to lift the next gate (repeated colors, missing tokens, ambiguous layers). |
| `build/regression-report.json` | After import | Per-section drift / coverage delta vs the previous archived run. |
| `pages/<slug>/<ts>/` | After build | Permanent archive — full snapshot of one run (every artifact above + `manifest.json`). |
| `project-state.json` | Cross-run | Kit id, template ids, form ids, `asset_map_by_filename` + `asset_map_by_hash`, imported pages. |

---

## Constraints / known gaps

- **Elementor Pro** unlocks Theme Builder display conditions. Without
  Pro, the orchestrator prompts you for `install` or `inline` at Phase A
  and persists the choice — no more silent exit-7 failure.
- **Partial Theme Builder** is supported: when only one of header/footer
  is detected, the agent creates that template and lets the theme's
  default render the other. Only "neither detected" fails the gate.
- **Search / 404 templates** still require Pro for "Other" template
  assignment. The agent creates the post but prints explicit manual-
  assignment instructions when Pro isn't detected.
- **Gravity Forms** is the only form provider supported.
- **Visual diff** combines pixel + DOM-structure diff. A section that
  fails the pixel comparison but matches structurally (FOUT, lazy
  image, animation) is `dom_rescued` and not chased by the auto-fixer.
- **Auto-fixer** loops 3 times max. Structural drift > 15% on a section
  goes to Claude (Opus 4.7) review rather than heuristic spacing patches.
- **Claude budget** is adaptive: 8 dispatches on first run (no archived
  runs), 3 on incremental (fix_history covers the rest). Priority queue
  picks by `severity × section_purpose_weight`.
- **WP-side drift** is detected by comparing the live page against the
  most recent archived `data.json`. Use `--force` to overwrite hand-edits.
- **`fix_history.json`** is keyed by `_figma_name`. If you rename layers
  in Figma between runs, the cache for those sections becomes stale and
  is skipped — that's expected.
- **A11y audit** runs static checks only (alt-text, heading hierarchy,
  WCAG contrast). It does NOT crawl the live page with axe-core /
  Pa11y — extend `a11y_audit.py` if you need that.
- **Multi-language / multi-site** (Polylang, WPML, multisite) are not
  supported.

---

## Troubleshooting

**`/wp-json/figma-importer/v1/health` returns 404**
- Permalinks must be **Post name**, not "Plain".
- Confirm `<wp-root>/wp-content/mu-plugins/figma-importer-bridge.php` exists.
- `wp-admin → Plugins → Must-Use` should list "Figma Importer Bridge".
- Check `<wp-root>/wp-content/debug.log` for fatals.

**Plan-only flags "unnamed-brand-colors" or "sparse-typography"**
- Your Figma local styles don't have brand-intent names (e.g. "Brand
  Primary"). The kit's `primary` slot will fall back to a grey and
  global-color coverage will fail the 70% gate. Rename styles in Figma,
  re-export the ZIP, and re-run `--plan-only`.

**Theme Builder gate FAILED (exit code 7)**
- This now only fires when you intentionally opted into Pro mode but
  the agent can't find a header / footer section. Add `header_pattern`
  / `footer_pattern` regex overrides to `project-config.json`, or
  re-run with `start inline-only` to use inline header/footer.

**`401 Unauthorized` from REST**
- Wrong admin credentials in `config.json`. Fix it, delete
  `project-config.json`, run `start` again.

**Header / footer don't show on pages**
- Elementor Pro is required for Theme Builder display conditions.
  Without Pro, the templates exist in `Templates → Saved Templates` but
  won't auto-apply. The orchestrator now offers `inline-only` as an
  alternative when Pro is missing.

**Per-section diff returns no matches**
- The map between live `data-id` and Figma section needs
  `build/data.json` + `build/id_map.json`. Both are written by
  `import_elementor.py`. If you ran `visual_compare.py --per-section`
  without a prior successful import, the lookup will be empty. Run a
  full `start` first.

**Build aborted with `WP-side drift detected` (exit 5)**
- The live page differs from what the last archived build wrote. This
  almost always means someone edited the page in WP admin between runs.
  Inspect `build/wp_drift.json` for the per-node changes. To proceed:
  - **Preserve the manual edits**: pull them into the Figma file +
    re-export the ZIP, then re-run `start`.
  - **Overwrite anyway**: re-run with `start --force`. The hand-edits
    will be lost.

**DOM-diff rescued sections from a pixel fail**
- The pixel diff flagged drift but the live DOM structure + text
  matches the expected tree. Common causes: web fonts loading after
  the screenshot (FOUT), lazy-loaded images, carousel auto-play
  starting before capture. These are NOT real bugs and the auto-fixer
  + Claude review skip them. See `build/diff/dom_diff.json` for
  per-section details.

**`figma-suggestions.md` repeats the same suggestions every run**
- The suggestions reflect the current build's inline colors / typography.
  If your Figma file genuinely has those values inline (not bound to a
  token), the suggestion will recur until you tokenize them in Figma.
  Once tokenized + re-exported, the global-coverage gate clears and
  the suggestion disappears.

**`--from-cache` exits with code 8**
- `--from-cache` needs a previously-successful build (it reads
  `build/data.json`). Run `start` once normally first.

**Globals saved to Site Settings but widgets still show inline values**
- The bridge's `iterate_data` pass preserves `__globals__` refs only in
  the patched bridge. Delete
  `<wp-root>/wp-content/mu-plugins/figma-importer-bridge.php` and run
  `start` again to re-copy the current version.

**Sections look pixel-perfect but aren't editable**
- Those are screenshot fallbacks — sections the confidence layer
  couldn't classify. Tagged `_low_confidence: true`. Either improve the
  Figma file's auto-layout coverage or pass
  `--low-confidence-threshold 0.3` to be more aggressive.

**Module import errors after `pip install`**
- Python 3.14 cookie changes break older `requests`. Upgrade
  (`pip install --upgrade 'requests>=2.32.3'`) or rebuild the venv
  against Python 3.12 / 3.13.

**Windows: `mklink` fails with "You do not have sufficient privilege"**
- Use the junction form (`mklink /J`) instead — no elevation needed.

**Windows: `python` not found**
- The python.org installer's "Add to PATH" checkbox wasn't ticked.
  Reinstall and tick it, or add the install dir to PATH manually.

---

## Testing

```bash
.venv/bin/python -m pytest tests/ -v
```

Covers: optimize passes, architecture routing, popup-trigger inference,
validation / confidence, template reuse (top-level + nested), form
intelligence, dynamic content, design tokens, prompt templates.

---

## Claude-as-Author pattern

The deterministic Python pipeline handles the easy 80% (asset upload,
kit globals, structural section finding, widget inference, auto-layout
inference, template creation). For the hard 20%, the orchestrator
dispatches Claude sub-Agents:

| Stage | When | Bundle source |
|-------|------|---------------|
| **Plan stage** (NEW) | Widget pick confidence < 0.7 | `build/widget-review-queue.json` |
| **Post-import** | Section confidence < 0.6 OR live-vs-expected drift > 15% | `build/claude-review/section-*.json` |

Both stages share a 5-dispatch budget per build. See `CLAUDE.md` for the
full review-bundle protocol.

The plan-stage dispatch is the bigger win — catching a wrong widget
pick before the import runs eliminates a full render cycle. The
post-import dispatch is the fallback for visual issues the deterministic
pipeline produces in valid-but-not-pixel-faithful output.

---

## License

See `LICENSE`.
