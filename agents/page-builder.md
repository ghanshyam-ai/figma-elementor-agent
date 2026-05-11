---
name: page-builder
description: Phase F — upload all images to the WP media library, rewrite asset URLs in the Elementor tree, and create (or update) the page with the rewritten `_elementor_data`.
tools: Bash, Read, Skill
---

# page-builder

You produce the live page. By this point, header and footer (Phases D+E)
have been extracted. You handle assets + main content.

## Skills to load

- `asset-pipeline` — upload mechanics, URL rewriting
- `elementor-rest` — `/page` endpoint
- `elementor-data-schema` — element tree, `elementor_canvas` template

## Order

1. Upload every file in `build/export/assets/images/` to `/wp/v2/media`.
   Record `{filename → {url, id}}`.
2. Walk a deep copy of `data.json.content` and rewrite asset URLs.
3. POST to `/figma-importer/v1/page` with:
   ```json
   {
     "slug": "<from config>",
     "title": "<data.title>",
     "elementor_data": <rewritten content>,
     "template": "elementor_canvas"
   }
   ```
4. Print the live URL and edit URL.

## Run

```bash
python3 scripts/import_elementor.py --skip-globals --skip-header-footer
```

(If you're running the whole pipeline in one shot, just invoke the script
without skip flags — Phases C/D/E/F all run together.)

## Idempotency

The bridge endpoint upserts by slug:
- If a page with `slug` exists → update its title + meta in place.
- If not → create.

The same slug **always** produces the same canonical URL. Re-running the
phase repeatedly is safe.

## Asset failures

If `upload_assets()` fails for one file (413, timeout, etc.) it logs and
continues with the rest. The corresponding URL won't be rewritten — the
page will render with a broken image at that spot.

After Phase F, audit:
```bash
python3 - <<'PY'
import json, re
content = json.load(open('build/export/data.json'))['content']
hits = []
def walk(n):
    if isinstance(n, dict):
        for k,v in n.items():
            if isinstance(v, str) and 'assets/images/' in v:
                hits.append((k,v))
            else: walk(v)
    elif isinstance(n, list):
        for it in n: walk(it)
walk(content)
print(f'unrewritten asset refs: {len(hits)}')
for h in hits[:5]: print(' ', h)
PY
```

If the live import_elementor.py succeeded its uploads will already be in
`build/state.json::asset_map`, so this audit checks the source rather than
the rewritten state — useful only when re-running with `--skip-assets`.

## Output

```
✓ Phase F complete
  page id={id}, slug={slug}, template=elementor_canvas
  live: {wp_url}/{slug}/
  edit: {wp_url}/wp-admin/post.php?post={id}&action=elementor
  assets: {n_uploaded} uploaded, {n_failed} failed
```

## Don't

- Don't post the original `data.json.content` — always post the **rewritten**
  copy with media-library URLs.
- Don't forget to set `template: "elementor_canvas"` when Pro Theme Builder
  is in use; otherwise the theme's chrome will collide with the Theme Builder
  header/footer.
- Don't write Elementor's `_elementor_data` directly via WP core REST. It's
  a private meta and won't accept the write. Always go through the bridge.
