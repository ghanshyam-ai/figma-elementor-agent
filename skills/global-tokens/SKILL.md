---
name: global-tokens
description: How `tokens.json` from the Figma plugin and the active Elementor kit settings are stitched together so widget colour and typography keys point at `globals/colors?id=…` and `globals/typography?id=…` instead of inline hex / px values.
---

# Global Token Resolver

Lives in `scripts/optimize.py::resolve_global_tokens()`. Runs after
Phase C (kit globals are written) and before Phase F (page is created).

## Why

Inline colour and typography values mean every widget on every page
holds its own copy of the design system. Change brand-primary later
and you'd rewrite a thousand widgets. Elementor's globals system gives
you exactly one source of truth — the resolver makes our imports use
it.

The plugin already emits the values in two places:
* `global.json` — heuristic ordering by usage
* `tokens.json` — semantic dot-paths (`color.primary`, `font.heading.size`)
  backed by Figma local styles / variables when authored

The agent's `map_global_to_kit_settings()` writes the kit. Then the
resolver walks the Elementor tree and rewrites every widget that uses
those values.

## What gets rewritten

### Colours

For every widget, every key in `COLOR_KEYS_BY_WIDGET` is checked
(`title_color`, `text_color`, `button_text_color`, `background_color`,
`hover_color`, `button_background_hover_color`, `border_color`,
`primary_color`, `secondary_color`, `description_color`,
`icon_color`, `_background_color`, `_border_color`).

If the inline hex matches a slug we just wrote to the kit:

```jsonc
// before
{ "title_color": "#22D3EE" }

// after
{
  "title_color": "",
  "__globals__": { "title_color": "globals/colors?id=primary" }
}
```

Container `background_color` and `border_color` are also rewritten.

### Typography

Match key is `(family, size, weight)`. Line height + letter spacing
are intentionally NOT part of the key — designers tweak them per-widget,
so requiring exact match would tank the hit rate.

```jsonc
// before — five inline keys
{
  "typography_typography": "custom",
  "typography_font_family": "Inter",
  "typography_font_size": {"unit":"px","size":56,"sizes":[]},
  "typography_font_weight": "700",
  "typography_line_height": {"unit":"px","size":64,"sizes":[]}
}

// after — one global reference
{
  "typography_typography": "globals",
  "__globals__": { "typography_typography": "globals/typography?id=h1" }
}
```

If the exact `(family, size, weight)` key misses but `(family, size)`
matches a preset, the resolver still links it (most widgets in a
design pick one weight per family + size).

## What's NOT rewritten

* Colours that aren't in the kit. The kit has 4 system slots + N
  custom slots (everything past `colors[3]` in `global.json`). If a
  widget uses a fifth-or-later colour, it lands in `custom_colors`,
  and the resolver still finds it — but only because we put it there
  in `map_global_to_kit_settings()`. If a widget uses a colour that
  literally never appears in `global.json`, it stays inline.
* Spacing. Elementor's globals don't cover spacing tokens; only the
  kit's `space_between_widgets` is a global, and that's a default,
  not a per-widget reference.
* Border-radius. No globals slot for radii in core Elementor.

## Stats it returns

```python
{"colors": 14, "typography": 5}
```

— printed as part of Phase F output:

```
✓ Optimize: 14 colors→globals, 5 typo→globals, …
```

## Idempotent

Running the resolver twice is a no-op. It checks
`typography_typography == 'globals'` before rewriting and leaves any
already-linked widget alone.

## Testing the round-trip

After import, open a widget in Elementor's editor → the colour swatch
shows a connected icon (chain link), and the typography control
displays the global preset name instead of "Custom". Disconnect → the
widget falls back to the kit's value, not a stale inline hex.

## Plugin-side prerequisite (still missing)

When a token comes from `tokens.json` with a semantic dot-path (e.g.
`color.primary` from a Figma local style), the resolver will pick it
up because `map_global_to_kit_settings()` already feeds those into the
kit. Reverse-mapping from semantic path → kit slug is supported via
`enrich.color_to_token_path()` for future passes that need to attach
semantic names rather than hex matches.
