---
name: visual-diff
description: How the agent renders the live Elementor page, compares it pixel-by-pixel to the Figma screenshot, and turns drift into actionable fix candidates.
---

# Visual diff

`scripts/visual_compare.py` orchestrates:

1. Capture the live page with Playwright (Chromium, headless, fullPage).
2. Resize the Figma screenshot to the same width.
3. Pixel-diff via [pixelmatch](https://github.com/mapbox/pixelmatch); produce
   `diff.png` with red highlights and a `report.json` with overall + per-region drift.

## Output

```
build/diff/
├── live.png          # rendered page (full-page, ~1920×N)
├── expected.png      # Figma screenshot (resized to live width)
├── diff.png          # red where pixels differ
└── report.json       # numeric drift
```

`report.json` shape:

```jsonc
{
  "url": "https://site/home/",
  "width": 1920,
  "height": 7424,
  "totalPixels": 14254080,
  "diffPixels": 412345,
  "drift": 0.0289,            // overall (0..1)
  "threshold": 0.05,
  "passed": true,
  "regions": [
    {"y0": 0,    "y1": 371,  "drift": 0.012},
    {"y0": 371,  "y1": 742,  "drift": 0.103},   // ← worst band
    ...
  ]
}
```

## Drift thresholds (defaults)

| Drift  | Verdict |
|--------|---------|
| < 2%   | likely pass — anti-aliasing + font hinting |
| 2–5%   | acceptable for first pass; flag for review |
| 5–15%  | meaningful drift; auto-fixer should engage |
| > 15%  | structural mismatch; manual triage |

Override per-run:

```bash
python3 scripts/visual_compare.py --threshold 0.03 --width 1920
```

## Reading regions

The diff is divided into 20 horizontal bands by default. The band with the
highest drift is the strongest candidate to fix. To inspect a single band
visually, crop it from `diff.png`:

```bash
python3 -c "from PIL import Image; im=Image.open('build/diff/diff.png');
yy=2000; im.crop((0, yy, im.width, yy+800)).save('build/diff/region.png')"
```

Then open `region.png` to see what's wrong.

## Mapping regions back to Elementor sections

The Figma plugin records `_figma_name` on every container. To correlate a
high-drift band (`y0..y1` in pixels) with a section, sum the rendered
heights of containers walking the tree.

A rough heuristic (good enough for triage, not exact):

```python
# pseudo
y_cursor = 0
for top in elementor_data:
    h = top["settings"].get("min_height", {}).get("size", 0) or estimate_height(top)
    if y_cursor + h >= region_y0 and y_cursor <= region_y1:
        candidates.append(top["settings"]["_figma_name"])
    y_cursor += h
```

For an exact mapping, the auto-fixer can re-render with section-id
attributes and read `getBoundingClientRect()` via Playwright.

## Common false positives

- **Fonts**: if the live site renders Inter via Google Fonts but the Figma
  screenshot used a slightly different Inter version, expect 1–3% baseline
  drift across all text.
- **Carousels / sliders**: live pages render the first slide; Figma may
  have rendered another. Add `expected_slide=0` UX in the future or mask
  carousel regions.
- **Animations**: scroll-triggered reveals can capture mid-transition.
  `playwright_capture.js` waits 750ms post-load — bump higher if needed.
- **Cookie banners**: `playwright_capture.js` already hides common ones.
  Add new selectors there if a new one appears.

## What auto-fixer does next

1. Read `report.json`.
2. Pick the worst region (or top 3).
3. Use the agent's reasoning + screenshots cropped to that region to identify
   one of the canonical drifts:
   - **Color drift** → kit color update via `patch_elementor.py --set-color`
   - **Spacing drift** → patch container `padding` / `flex_gap`
   - **Typography drift** → patch heading `typography_*` keys
   - **Asset missing** → re-upload, re-rewrite
4. Apply via `patch_elementor.py --set-setting <node-id> <key> <value>`.
5. Re-run `visual_compare.py`.
6. Stop after 3 iterations regardless. Report what's left.

The auto-fixer **does not** restructure the tree (add/remove containers,
swap widget types). Those mismatches are flagged for manual intervention.
