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
# One-time install (per machine)
brew install python@3.12 node                                   # macOS
npm install -g @anthropic-ai/claude-code

# Per project — drop the agent + ZIP next to LocalWP's wp-config.php
cd ~/Local\ Sites/<site-name>/
git clone <this-repo> figma-elementor-agent
mv ~/Downloads/your-design.zip .

# Configure WP credentials
cd figma-elementor-agent
cp config.example.json config.json
$EDITOR config.json          # fill wp_url, wp_user, wp_password, theme_slug

# Register agents + skills with Claude Code (once per project)
mkdir -p .claude
ln -sfn ../agents .claude/agents
ln -sfn ../skills .claude/skills

# Run
claude
> start
```

That's it. The orchestrator handles WP-root detection, bridge install,
dependency install, ZIP detection, plan generation, build, visual diff,
auto-fix, and artifact archival.

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
2. **Fill in `config.json`.**
   ```bash
   cd ~/Local\ Sites/<site-name>/figma-elementor-agent
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
3. **Symlink agents + skills into `.claude/`** (Claude Code reads them
   from there).
   ```bash
   mkdir -p .claude
   ln -sfn ../agents .claude/agents
   ln -sfn ../skills .claude/skills
   ```
4. **Run.**
   ```bash
   claude
   ```
   Type `start` at the Claude prompt.

On first run the orchestrator auto-installs everything else:
- Detects `wp_root` by walking up to `wp-config.php`.
- Copies `scripts/wp-bridge/figma-importer-bridge.php` into
  `<wp-root>/wp-content/mu-plugins/`.
- Creates `.venv/` and installs `requests` + `Pillow`.
- Runs `npm install` inside `scripts/` for Playwright + pixelmatch.
- Writes `project-config.json` (mode 600).
- Verifies the bridge at `/wp-json/figma-importer/v1/health`.

If a later step errors with *"Executable doesn't exist"*, run:
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
3. Fill in `config.json` (same fields as macOS).
4. Register agents + skills using a junction (no admin needed):
   ```powershell
   cd "$env:USERPROFILE\Local Sites\<site-name>\figma-elementor-agent"
   New-Item -ItemType Directory -Force -Path .claude | Out-Null
   cmd /c mklink /J .claude\agents agents
   cmd /c mklink /J .claude\skills skills
   ```
5. Run `claude` and type `start`.

Windows-specific notes:
- All direct script calls use `.venv\Scripts\python.exe` instead of
  `.venv/bin/python`.
- Quote paths with spaces (`"Local Sites"`).
- If Playwright fails: `cd scripts; npx playwright install chromium`.
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
| **B. Extract** | unpacks the ZIP into `build/<export>/` | n/a |
| **B′. Plan + preflight** *(new)* | `build/build-plan.json`, `build/widget-review-queue.json`, design-system warnings | `--skip-plan` (rare) |
| Plan-stage Claude review | dispatches Claude only for sections with widget confidence < 0.7 (capped at 5 calls) | empty queue auto-skips |
| Fix-history pre-apply | when a prior successful run for this slug exists, re-applies its patches before the import | runs only when `build/fix_history.json` exists |
| **C–I. Build** | applies kit globals, creates header/footer/popup templates, uploads assets, creates the page | various `--skip-*` flags |
| **J. Visual review** | per-breakpoint + **per-section** pixel diff *(new)* | n/a |
| **K. Auto-fix** | up to 3 iterations; records successful patches into `fix_history.json` | n/a |
| **Quality gate** | drift ≤ 5% across captured breakpoints, global coverage ≥ 70%, header + footer present | (mandatory) |
| **L. Archive** *(new)* | copies the run's artifacts into `pages/<slug>/<timestamp>/` | n/a |

### Command reference for the Claude prompt

| You type | What runs |
|----------|-----------|
| `start` | Full build (home page) — globals + header/footer + page + visual diff. |
| `start --page-only` | 2nd+ page on the same site — skips globals + theme builder + reuse. |
| `start --page-only --page-slug about` | Same, with an explicit slug. |
| `start no confirmations` | Skip the "Proceed? [Y/n]" prompt. |
| `start build only the hero, skip the footer` | Free-form scoping in natural language. |
| `start inline-only` | Force inline header/footer (skip Theme Builder gate; useful when no Pro). |

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
A.  Setup            wp-setup            auth + bridge + Elementor + GF + Pro
B.  Import           importer            extract ZIP, load enrichment
B′. Plan             build_plan.py       NEW — read-only plan + preflight + widget queue
C.  Globals          global-styles       kit colors / typography + design-token CSS
D.  Optimize         optimization        token resolver + widget inference + collapse
E.  Architecture     wp-architecture     route sections to header/footer/popup/page
F.  Templates        theme-builder       create header/footer/popup/archive/single
G.  Forms            form-intelligence   Gravity Forms creation + shortcode
H.  Reuse            template-reuse      fingerprint dedupe into library templates
I.  Page             page-builder        assets + tree → page
J.  Visual review    visual-reviewer     Playwright capture + pixel diff + per-section drift
K.  Auto-fix         auto-fixer          patch loop (max 3 iterations) + fix-history cache
L.  Archive          finalize_artifacts.py  NEW — copy run into pages/<slug>/<ts>/
```

Phases C → I run inside `scripts/import_elementor.py` as a single
process. The named phases are the agent's mental model for triaging
failures, not separate script invocations.

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
│   ├── import_elementor.py       ← main pipeline; --plan-only writes the plan
│   ├── build_plan.py             ← NEW — plan + widget-review queue (read-only)
│   ├── preflight.py              ← NEW — design-system token check
│   ├── claude_review.py          ← Claude review bundles; --from-plan for plan stage
│   ├── visual_compare.py         ← Playwright + pixelmatch; --per-section adds per-section drift
│   ├── playwright_section_rects.js  ← NEW — fetches live section bounding rects
│   ├── fix_history.py            ← NEW — patch cache for re-runs (apply / show)
│   ├── finalize_artifacts.py     ← NEW — archives a run into pages/<slug>/<ts>/
│   ├── fix_plan.py               ← prioritized fix candidates
│   ├── patch_elementor.py        ← targeted JSON patches
│   ├── enrich.py                 ← loads ai-layout / tokens / validation / assets
│   ├── section_finder.py         ← recursive section walker (any depth)
│   ├── widget_inference.py       ← agent's own widget detectors
│   ├── auto_layout_inference.py  ← infer flex from absolute positions
│   ├── design_tokens.py          ← :root --token-* CSS bridge
│   ├── optimize.py               ← token resolver + collapse + depth cap
│   ├── architecture.py           ← popup trigger inference
│   ├── template_reuse.py         ← fingerprint dedupe
│   ├── form_intelligence.py      ← Gravity Forms detection + creation
│   ├── dynamic_content.py        ← blog grid → Posts widget
│   ├── validation_layer.py       ← confidence score + screenshot fallbacks
│   ├── section_crops.py          ← per-section PNG crops
│   ├── prompt_template.py        ← prompt-driven 404 / search / popup
│   ├── project_state.py          ← cross-run state cache
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
└── pages/                        ← NEW — per-run archive (gitignored, persists)
    └── <slug>/<timestamp>/
        ├── build-plan.json
        ├── import-report.json
        ├── widget-review-queue.json
        ├── state.json
        ├── fix_history.json
        ├── diff/{report.json, diff.png, ...}
        └── manifest.json
```

---

## CLI reference

These run directly after the first `claude > start` has bootstrapped.
On Windows substitute `.venv\Scripts\python.exe` for `.venv/bin/python`.

### `import_elementor.py` — full pipeline

| Flag | Effect |
|------|--------|
| `--plan-only` | **NEW.** Extract + plan + preflight; exit 0 before any WP writes. |
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
| `--per-section` | **NEW.** Add per-section drift to `report.json::sections[]` by cropping the live page by each section's bounding rect. |
| `--threshold X` | Drift fraction above which a breakpoint fails (default 0.05). |
| `--viewports default \| desktop-only \| <json>` | Which viewports to capture. |
| `--width N` | Desktop width (default 1920). |
| `--page-slug <slug>` | Override page slug. |

### `claude_review.py` — Claude-as-Author bundles

| Flag | Effect |
|------|--------|
| `--from-plan` | **NEW.** Build bundles from `build/build-plan.json` (plan stage). |
| `--build` | Build bundles from `build/import-report.json` + `build/diff/report.json` (post-import). |
| `--list` | List existing bundles. |
| `--confidence X` | Confidence floor for bundle inclusion. |
| `--drift X` | Drift ceiling for bundle inclusion (post-import only). |
| `--max N` | Cap dispatch count (default 5). |

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
| `build/data.json` | After import | Rewritten Elementor tree (PRE-regen ids) — input to claude_review + patches. |
| `build/id_map.json` | After import | pre-regen → post-regen id mapping; bridges live `data-id` to source nodes. |
| `build/import-report.json` | After import | Confidence score + risk areas + global coverage + fallback indices. |
| `build/diff/report.json` | After review | Drift % per breakpoint + per y-band + per-section drift (with `--per-section`). |
| `build/diff/{live,expected,diff}.png` | After review | Visual diff artifacts. |
| `build/diff/sections/<data_id>.{png,diff.png}` | With `--per-section` | Live crop + diff per section. |
| `build/fix_history.json` | After auto-fix | Cached patches keyed by `_figma_name + kind`. Re-applied on next run. |
| `pages/<slug>/<ts>/` | After build | Permanent archive — full snapshot of one run. |
| `project-state.json` | Cross-run | Kit id, template ids, form ids, asset map, imported pages. |

---

## Constraints / known gaps

- **Elementor Pro** unlocks Theme Builder display conditions. Without
  Pro, the orchestrator prompts you for `install` or `inline` at Phase A
  and persists the choice — no more silent exit-7 failure.
- **Search / 404 templates** still require Pro for "Other" template
  assignment. The agent creates the post but prints explicit manual-
  assignment instructions when Pro isn't detected.
- **Gravity Forms** is the only form provider supported.
- **Visual diff** uses pixel-level comparison. Carousels, animations,
  or video backgrounds may register false positives — bands containing
  screenshot fallbacks are marked `manual-review` to compensate.
- **Auto-fixer** loops 3 times max. Structural drift > 15% on a section
  goes to Claude review rather than heuristic spacing patches.
- **`fix_history.json`** is keyed by `_figma_name`. If you rename layers
  in Figma between runs, the cache for those sections becomes stale and
  is skipped — that's expected.

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
