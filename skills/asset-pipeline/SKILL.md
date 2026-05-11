---
name: asset-pipeline
description: How images from the Figma plugin export are uploaded to the WP media library and how their references are rewritten inside the Elementor data tree before the page is created.
---

# Asset pipeline

Source: `build/<export>/assets/images/img_*.{png,jpg,...}`
Sink:   WP media library via `POST /wp-json/wp/v2/media`

## Order of operations

1. **Upload first**, then rewrite. Never try to rewrite a URL we haven't
   uploaded — the resulting page will reference assets the live site can't serve.
2. The mapper records `{filename → {url, id}}` for every successful upload.
3. After all uploads, walk the in-memory copy of `data.json.content` and
   replace every reference whose path matches one of the source filenames.
4. Then send the rewritten tree to `/figma-importer/v1/page`.

## Upload mechanics

- Endpoint: `POST /wp/v2/media`
- Body: raw bytes (NOT multipart). Set headers:
  - `Content-Disposition: attachment; filename="img_1.png"`
  - `Content-Type: image/png` (mime detected from extension)
- Auth: same Application Password as everything else.
- Response: `{id, source_url, ...}` — keep both.

Failures to handle:
- `413 Payload Too Large` → host's `client_max_body_size` is below the file.
  Print the failing filename + size, skip, continue with others.
- `415 Unsupported Media Type` → mime mismatch. Re-detect with `file --mime`.
- Network timeout → retry once with doubled timeout, then skip.

## URL patterns the mapper recognises

The Figma plugin writes asset URLs in three forms inside `data.json`:

```
"assets/images/img_1.png"
"build/<export-name>/assets/images/img_1.png"   # rare, but defensive
"img_1.png"                                      # bare filename
```

All three are matched by the regex in `import_elementor.py::ASSET_REF_RE`.
Anything starting with `http://` or `https://` is **not** rewritten — those
are already live URLs (logos served from a CDN, etc.).

## Where references appear

The plugin uses local paths in three contexts inside the Elementor JSON:

| Setting | Shape | Example |
|---------|-------|---------|
| `background_image` | `{url, id}` | container backgrounds |
| `image` (widget) | `{url, id}` | image widgets |
| `image_carousel`, `gallery` items (future) | `[{url, id}, ...]` | carousels |

The walker handles `{url, id}` objects generically — when it sees `url` as
a string and the parent dict is the asset reference, it sets both `url` and
`id` from the map.

## After rewrite — sanity check

Before posting to the bridge, walk the tree and assert no `assets/images/`
substring remains. The smoke test in `scripts/import_elementor.py` already
does this implicitly via the regex; if you ever see "missing image" boxes
in the live page, it's the first thing to verify.

```python
import re, json
def find_unrewritten(node, hits):
    if isinstance(node, dict):
        for k, v in node.items():
            if isinstance(v, str) and "assets/images/" in v:
                hits.append((k, v))
            else:
                find_unrewritten(v, hits)
    elif isinstance(node, list):
        for it in node:
            find_unrewritten(it, hits)
```

## Cleanup

Uploaded media is **not** automatically removed when re-importing. If you
re-run the agent multiple times against the same site, the media library
will accumulate duplicates. Manual cleanup or a `--reset-media` flag is a
reasonable follow-up — not currently implemented.
