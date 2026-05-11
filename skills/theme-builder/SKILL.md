---
name: theme-builder
description: How the agent detects header/footer sections in the Figma export, creates Elementor library templates for them, and the difference between Pro (Theme Builder) and Free (manual placement).
---

# Header / Footer creation

## Detection

Header and footer are detected by **Figma layer name pattern** matching
against `_figma_name` in any node's settings. Defaults from
`project-config.json`:

```json
{
  "header_pattern": "header|nav|topbar",
  "footer_pattern": "footer"
}
```

The walker is depth-first; the first match in the tree wins.

If your Figma file uses different naming (e.g. "Top Bar", "Site Footer 2024"),
update the patterns in `project-config.json`. Don't rename in Figma.

## Extraction

Once a header/footer node is found, the agent:

1. Sends the node (as a single-element array) to `/figma-importer/v1/template`
   with `template_type: "header"` (or `"footer"`).
2. **Removes the node from the page tree** so it isn't duplicated when the
   page is created.

Both happen in `scripts/import_elementor.py` — see `find_section()` and
`remove_node_by_id()`.

## Pro vs Free

Elementor **Pro** ships Theme Builder. With Pro:

- The bridge sets `_elementor_conditions = ["include/general"]` so the
  template applies to every page automatically.
- Page template can stay `elementor_canvas` — Theme Builder injects header/footer.

Without Pro:

- The library post is created, but Elementor Free has no Theme Builder, so
  it won't auto-apply.
- The agent should fall back to keeping the header/footer **inline** in the
  page tree (don't remove them) and use the `elementor_header_footer`
  template so the theme's chrome wraps content.

The bridge tells us via `health.elementor_pro` whether Pro is active.
Adjust strategy accordingly:

```python
health = client.bridge_health()
if health.get("elementor_pro"):
    # Pro flow: extract H/F, create templates, remove from page
else:
    # Free flow: keep H/F inline, skip template creation
```

`scripts/import_elementor.py` currently always extracts (Pro flow). To opt
into Free flow:

```bash
python3 scripts/import_elementor.py --skip-header-footer
```

…and pick `elementor_header_footer` as the page template.

## Conditions (Pro)

Common condition strings:
- `include/general` — apply everywhere
- `include/in_singular/page` — all pages
- `include/in_singular/post` — all posts
- `exclude/in_singular/page/<id>` — exclude one page

Combinations are arrays:
```json
["include/general", "exclude/in_singular/page/42"]
```

The bridge writes `_elementor_conditions` directly; Elementor Pro picks it
up on the next page render. Cache flush is forced by the bridge after every
write.

## Editing after creation

The header/footer templates are real `elementor_library` posts. Open them
via the `edit_url` returned from the bridge, or under
`wp-admin → Templates → Theme Builder → Headers/Footers`.

`scripts/patch_elementor.py` accepts `--post-id <id>` to edit them by id
(slug lookup only finds pages, not library posts).

## When detection fails

If `find_section()` returns nothing for header or footer:
- Inspect `data.json` for the actual `_figma_name` values:
  ```bash
  python3 -c "import json; d=json.load(open('build/export/data.json'));
  def w(n,d=0):
   import sys
   if isinstance(n, dict):
    name = (n.get('settings') or {}).get('_figma_name','')
    if name: print(' '*d + name)
    for c in n.get('elements',[]): w(c, d+2)
  for top in d['content']: w(top)" | head -30
  ```
- Update `header_pattern` / `footer_pattern` in `project-config.json` and
  re-run with `--skip-globals --skip-page` to only redo header/footer.
