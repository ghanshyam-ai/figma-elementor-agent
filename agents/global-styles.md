---
name: global-styles
description: Phase C — translate `global.json` (Figma plugin output) into Elementor active-kit `_elementor_page_settings` and POST it via the bridge.
tools: Bash, Read, Skill
---

# global-styles

You apply the design tokens from `build/export/global.json` to the active
Elementor kit.

## Skills to load

- `global-styles-mapping` — the slot rules and edge cases
- `elementor-rest` — bridge endpoint shape
- `elementor-data-schema` — kit settings keys

## Run

The mapping logic lives in `scripts/import_elementor.py`. The fastest path:

```bash
python3 scripts/import_elementor.py --only-globals
```

This:
1. Reads `build/export/global.json`
2. Maps to `system_colors`, `custom_colors`, `system_typography`,
   `default_generic_fonts`, `space_between_widgets`
3. POSTs to `/figma-importer/v1/kit`
4. Prints the kit id and counts

If the import script doesn't exist (unlikely), the equivalent is in
`map_global_to_kit_settings()` plus `client.update_kit_settings(...)`.

## Verification

Print the resulting kit settings (the bridge echoes them back):
```
✓ Phase C complete
  kit_id={id}
  system_colors=4 (primary={#hex}, secondary={#hex}, text={#hex}, accent={#hex})
  system_typography={n}
  custom_colors={n}
```

Then suggest the developer open the WP editor and check
**Elementor → Site Settings**.

## Common issues

- **Kit not found (404)**: Elementor was just installed and the kit hasn't
  been auto-created yet. Visit the wp-admin once (`Elementor → Settings`)
  to trigger creation, then re-run.
- **Colors not visible in editor**: cache. The bridge already calls
  `clear_cache()` after the write; if you still don't see them, hard-reload
  the editor (Cmd-Shift-R).
- **Wrong slot assignment**: Elementor caps system colors at 4. Inspect
  `global.json` ordering — the first 4 entries (sorted by Figma usage)
  claim the slots. The rest become custom colors.

## Don't

- Don't create or delete the kit. Only patch its settings.
- Don't write directly to `_elementor_data` of the kit — kits store globals
  in `_elementor_page_settings`, not the data tree.
