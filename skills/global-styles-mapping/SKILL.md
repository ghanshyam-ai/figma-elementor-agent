---
name: global-styles-mapping
description: How `global.json` (from the Figma plugin) maps to Elementor's active kit `_elementor_page_settings`. Rules for color slot assignment, typography presets, and spacing.
---

# Global styles mapping

The Figma plugin emits `global.json`:

```jsonc
{
  "colors":     [{"name":"primary", "value":"#FFF",   "usage":71}, ...],
  "typography": [{"name":"display", "fontFamily":"Inter", "fontWeight":300,
                  "fontSize":64,    "lineHeight":70,    "letterSpacing":0}, ...],
  "spacing":    [1, 2, 3, 6, 8, 10, 12, 16, 20, 24, 32, 40, ...],
  "radii":      [2, 3, 4, 4.5, 5]
}
```

The mapper in `scripts/import_elementor.py::map_global_to_kit_settings()`
turns that into Elementor kit `_elementor_page_settings`.

## Color → kit colors

Elementor's UI exposes exactly **four system color slots**: `primary`,
`secondary`, `text`, `accent`. Everything else is "custom".

Assignment rules (in order):

1. If a color's `name` matches one of the four canonical slugs, claim that slot.
2. Fill remaining slots in declaration order (top of `colors[]` first).
3. Anything past index 3 → `custom_colors[]`. Stable id is `md5(value)[0:7]`.

The Figma plugin sorts colors by **usage**, so the most-used color claims
"primary" by default. Name-matched slots take precedence over usage order
when both apply.

## Typography → kit typography

Elementor system typography ids correspond to canonical heading slots. The
plugin emits arbitrary names (`display`, `h1`, `h2`, `h3`, `h4`, `body`,
`small`, `caption`, `caption-strong`). Mapper rules:

1. For each unique name the plugin emits, take the **first** entry as the
   representative (multiple weights for one name → keep the first; Elementor
   only stores one preset per slug).
2. Slugify the name (`caption-strong` → `caption_strong`).
3. Skip names not in the recognized set above.
4. Convert `fontWeight` to a string ("300" not 300) — Elementor expects a string.
5. `lineHeight: "auto"` → omit the line-height key (Elementor falls back to font default).
6. `letterSpacing: 0` → omit (cleaner UI).

The first font family encountered becomes `default_generic_fonts`.

## Spacing → widget gap

Pick the smallest "sane" spacing value (8–32px) for `space_between_widgets`.
That sets the default vertical gap between widgets in a container.

## Container width

Not currently set automatically. Elementor's default is 1140px. The Figma
designs we've seen use 1920px frames; a 1140 boxed inside a 1920 design is
correct for most desktop layouts. If you want a wider container:

```python
page_settings["container_width"] = {"unit": "px", "size": 1280, "sizes": []}
```

## Why merging, not replacing

The bridge's `/kit` endpoint merges the keys we send into the existing
`_elementor_page_settings`. This preserves any non-color/typography settings
the user may have configured (e.g. layout breakpoints, lightbox defaults).
The trade-off: removing a color means clearing it explicitly (send the
canonical slot with a different value).

## Verifying the mapping landed

Open the WP page editor → **Elementor → Site Settings**. The four system
colors should match `global.json` first four entries (after slug
re-assignment). Type previews should show the new fonts.

If colors don't appear:
- The `_id` must be one of `primary|secondary|text|accent` for system slots.
- The active kit may be different from the one you patched. Re-check via
  `GET /figma-importer/v1/health → active_kit`.

If typography doesn't appear:
- `typography_typography: "custom"` is required, otherwise Elementor ignores
  the per-element keys.
- Font weight must be a string.
