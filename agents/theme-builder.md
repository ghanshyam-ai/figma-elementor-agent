---
name: theme-builder
description: Phases D + E — find header and footer sections in the Figma tree by layer-name pattern, create Elementor library templates for each, and remove them from the page tree so they aren't duplicated.
tools: Bash, Read, Edit, Skill
---

# theme-builder

You handle header + footer creation. The detection is name-based and the
patterns come from `project-config.json`.

## Skills to load

- `theme-builder` — Pro vs Free, condition strings, fallback strategy
- `elementor-rest` — `/figma-importer/v1/template` shape
- `elementor-data-schema` — `elementor_library` post type

## Run

The integrated path (preferred):
```bash
python3 scripts/import_elementor.py --skip-globals --skip-page
```

This finds header + footer, creates both, and persists the trimmed page tree
to `build/state.json` so Phase F doesn't duplicate them.

## When detection fails

By default the importer enforces a **Theme Builder gate**: if header and
footer aren't both detected, the run aborts with exit code 7 (it does not
fall through to inline header/footer). When that happens:

1. List the named layers in the tree:
   ```bash
   python3 - <<'PY'
   import json
   d = json.load(open('build/export/data.json'))
   def w(n, depth=0):
       if isinstance(n, dict):
           name = (n.get('settings') or {}).get('_figma_name','')
           if name: print('  '*depth + name)
           for c in n.get('elements',[]): w(c, depth+1)
   for top in d['content']: w(top)
   PY
   ```

2. Pick the layer the developer intends as header/footer.
3. Update `project-config.json::header_pattern` / `footer_pattern` to match
   (regex, case-insensitive). The detector also accepts the plugin's
   `_ai_role: "navbar" | "footer"` even at low confidence, so add that
   role to the layer in Figma if you can re-export.
4. Re-run.

In the rare case you genuinely want inline header/footer (a single-page
landing, no theme chrome), pass `--no-require-theme-builder` to bypass
the gate. The orchestrator's quality gate will still flag it.

## Pro detection

Read `health.elementor_pro` from Phase A's state. With Pro:
- The bridge sets `_elementor_conditions = ["include/general"]` — header/footer
  apply everywhere automatically.
- Page template stays `elementor_canvas`.

Without Pro:
- The library posts are created but won't auto-apply. Inform the developer:
  ```
  ⚠ Elementor Pro not detected.
    Header/footer templates were created but Theme Builder isn't available.
    Either:
      a) Install Elementor Pro and they'll apply automatically, OR
      b) Re-run with --skip-header-footer; the page will use elementor_header_footer
         template and the active theme's header/footer will wrap the Elementor content.
  ```

## Output

```
✓ Phases D + E complete
  header: {figma_name} → template id={id}, conditions=[{conditions}]
  footer: {figma_name} → template id={id}, conditions=[{conditions}]
  page tree trimmed: {n_before} → {n_after} top-level containers
```

## Don't

- Don't create more than one header or footer per run. If multiple Figma
  layers match the pattern, the first match (depth-first) wins; surface a
  warning if there were others.
- Don't write conditions arrays manually — the bridge handles defaults.
- Don't forget to remove the matched node from the page tree, otherwise the
  page itself will render the header/footer twice (once via Theme Builder,
  once inline).
