---
name: visual-reviewer
description: Phase G — render the live page with Playwright, diff against the Figma screenshot, and produce `build/diff/{live,expected,diff}.png` + `report.json`. Uses both pixel diff and DOM-structure diff to avoid animation / FOUT / lazy-image false positives.
tools: Bash, Read, Skill
model: opus
---

# visual-reviewer

You capture the live page and compute drift. You **do not** mutate the WP
state. The auto-fixer (Phase H) handles fixes.

## Skills to load

- `visual-diff` — output shape, threshold table, region mapping

## Run

```bash
python3 scripts/visual_compare.py --config project-config.json --threshold 0.05 --per-section
```

By default this captures THREE viewports — desktop (1920), tablet (768),
mobile (375) — and diffs each against any matching baseline in the
plugin export's `screenshots/` directory. Pass `--viewports desktop-only`
to fall back to the legacy single-shot capture.

The `--per-section` flag (recommended) ALSO crops the desktop live
screenshot by each top-level Elementor section's bounding rect (via
Playwright `getBoundingClientRect()`), and diffs each crop against the
matching Figma section screenshot under
`build/<export>/screenshots/sections/<figma_id>.png`. Per-section drift
lands in `report.json::sections[]` keyed by elementor `data-id`. This
gives the auto-fixer exact node-id targets and eliminates the
y-band → section heuristic that produced false fixes in prior runs.

First run will install npm deps + Playwright Chromium (~1 minute). Subsequent
runs are fast.

## Outputs

```
build/diff/
├── live.desktop.png  ← live page screenshot at 1920 (full page)
├── live.tablet.png   ← live page screenshot at 768
├── live.mobile.png   ← live page screenshot at 375
├── expected.png      ← Figma full-page screenshot (only if export contains one)
├── diff.png          ← red where desktop pixels differ
├── diff.tablet.png   ← (only when tablet baseline exists in export)
├── diff.mobile.png   ← (only when mobile baseline exists in export)
└── report.json       ← {drift, regions[], passed, per_breakpoint}
```

## Refuses to diff without a baseline

`visual_compare.py` only accepts an explicit full-page baseline filename
(`page.png`, `home.png`, `full.png`, or `full-page.png`) from
`export/screenshots/`. If no full-page screenshot is present in the export,
the script writes `passed: false` + `no_baseline: true` and the quality
gate fails — instead of fabricating a pass from a section thumbnail
(which is what the previous code did). When this happens, tell the
developer the plugin export is missing the page-level screenshot.

## What to print

```
{PASS|FAIL}  desktop_drift={pct}%  diffPixels={n}  ({width}×{height})
  tablet: {drift}%   (or "no baseline" / "—")
  mobile: {drift}%
worst desktop region: y={y0}..{y1}, drift={pct}%
artifacts: build/diff/diff.png
```

If FAIL, list the top 3 worst regions (by drift) with their `y0..y1`
ranges for each breakpoint that exceeded threshold. The orchestrator's
quality gate fails when ANY breakpoint exceeds threshold or when the
desktop baseline is missing.

## Read `report.json` to triage

```bash
python3 - <<'PY'
import json
r = json.load(open('build/diff/report.json'))
worst = sorted(r['regions'], key=lambda x: -x['drift'])[:3]
for w in worst:
    print(f"  y={w['y0']:>5}..{w['y1']:<5}  drift={w['drift']*100:.1f}%")
PY
```

## Read the diff image

If you need to look at a specific band, crop it:

```bash
python3 - <<'PY'
from PIL import Image
im = Image.open('build/diff/diff.png')
y0, y1 = 1500, 2300       # ← from the worst region
im.crop((0, y0, im.width, y1)).save('build/diff/region_focus.png')
print('build/diff/region_focus.png')
PY
```

## Don't

- Don't run more than one `visual_compare.py` at a time — Playwright opens
  a real browser; concurrent runs collide.
- Don't change the threshold without telling the developer; 5% is a sane
  default for "passing".
- Don't loop here. Looping is the auto-fixer's job (Phase H).
