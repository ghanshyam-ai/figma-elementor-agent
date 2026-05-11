---
name: confidence-fallback
description: How the agent self-evaluates each Figma section's confidence (from ai-layout.json) and falls back to a screenshot image widget for sections it can't be sure of. Prevents confidently-wrong layouts.
---

# Confidence + fallback

Lives in `scripts/validation_layer.py`. Runs after the optimization
passes and before architecture routing.

## What "confidence" means

The plugin's `extractor.ts` assigns every node a semantic role
(`hero`, `card`, `pricing-card`, `accordion`, `slider`, ...) and a
confidence score 0..1 based on signals like: name pattern, structural
shape, child types, presence of clickable behaviour. ai-layout.json
carries that score on every section.

`validation.json` adds higher-level warnings: low-role-confidence,
absolute-layout, mixed-fonts, unsupported-effect, large-raster,
hidden-layer-skipped, unnamed-layer.

The agent combines both into a single import-level number.

## The number

```
confidence = mean(section.confidence for all sections)
           - 0.10 × #error warnings
           - 0.02 × #warn warnings
           - 0.00 × #info warnings
```

Capped at [0, 1]. Printed at the end of every Phase F run:

```
✓ Confidence: 0.79  (sections=12, low=2, warn=3, err=0)
```

## Risk areas

Each low-confidence section AND each non-info validation warning
becomes a `RiskArea`:

```python
RiskArea(
    kind="low-confidence",     # or "warning"
    nodeId="123:45",
    nodeName="Pricing Cards",
    detail="role=card confidence=0.42 reason=structural-only",
    severity="warn",
)
```

The first 5 are printed inline; the full list lives in
`build/import-report.json`.

## Fallback strategy

For top-level sections whose confidence is below
`--low-confidence-threshold` (default 0.5):

1. **If a screenshot exists** for that section's Figma node id —
   `build/<export>/screenshots/<id>*.png` — and that screenshot was
   uploaded to the WP media library (the agent uploads them
   alongside `assets/images/`), the entire section is replaced with
   a full-bleed container holding one image widget pointing at the
   screenshot. The page renders pixel-perfect for that section, just
   non-editable.

2. **Otherwise** — the section is left in place but tagged with
   `_low_confidence: true` on its container settings, so the
   developer sees a private flag in the editor sidebar telling
   them "I wasn't sure about this one, please review."

## Why image-as-fallback (not "leave it")

A confidently-wrong Elementor structure is harder to fix than a
non-editable image. The image is honest: it's a screenshot, the
developer knows to redesign it. A wrong-but-rendered structure
encourages incremental tweaking that compounds the wrongness.

## Disabling fallbacks

```bash
python3 scripts/import_elementor.py --skip-fallbacks
```

Useful when you want to triage what the agent thinks is risky without
swapping anything yet — `import-report.json` still surfaces the list,
the page just renders the structural attempt.

## Tuning the threshold

```bash
python3 scripts/import_elementor.py --low-confidence-threshold 0.3
```

* 0.5 (default) — moderately conservative, swaps clearly-uncertain
  sections only.
* 0.3 — only swap the very worst.
* 0.7 — very aggressive; swaps anything the plugin hedged on.

The right value depends on how strict your design system is. Pages
built from a tight Figma component library should run at 0.3; pages
from loose freehand layouts at 0.5.

## Output: `build/import-report.json`

```jsonc
{
  "confidence": 0.79,
  "riskAreas": [
    {"kind":"low-confidence","nodeId":"123:45","detail":"role=card …","severity":"warn"},
    {"kind":"warning","nodeId":"234:11","detail":"[mixed-fonts] Section uses 4 fonts","severity":"info"}
  ],
  "fallbackSectionIndices": [2, 7],
  "summary": {
    "sections_total": 12, "sections_low_confidence": 2,
    "warnings_warn": 3, "warnings_error": 0, "warnings_info": 5,
    "containers": 41, "widgets": 87
  }
}
```

The visual-reviewer (Phase G) reads this report and weights its
attention toward `riskAreas` first.
