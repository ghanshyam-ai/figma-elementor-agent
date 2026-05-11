---
name: elementor-rest
description: How to talk to WordPress + Elementor over REST. WP core endpoints, Application Password auth, and the figma-importer-bridge mu-plugin endpoints we depend on.
---

# Elementor REST cheat sheet

The agent communicates with the target site over the WordPress REST API.
Authentication is HTTP Basic with an **Application Password** (NOT the login
password). Create one at `wp-admin → Users → Profile → Application Passwords`.

All paths below are appended to `{wp_url}/wp-json/`.

## WordPress core endpoints we use

| Method | Path                | Purpose | Cap required |
|--------|---------------------|---------|--------------|
| GET    | `wp/v2/users/me`    | sanity-check auth | `read` |
| GET    | `wp/v2/plugins`     | list installed plugins | `manage_plugins` |
| POST   | `wp/v2/plugins/<slug>` | activate / install plugin (`{status:'active'}`) | `manage_plugins` |
| POST   | `wp/v2/media`       | upload image (raw bytes + `Content-Disposition: filename=...`) | `upload_files` |
| GET/PUT| `wp/v2/pages/<id>`  | read/update page metadata | `edit_pages` |

Plugin activation via REST works only when:
- The user is admin (capability `manage_plugins`).
- For *installs* from wp.org repo, the file system must be writable; in many
  managed hosts this is disabled, in which case the agent should print clear
  manual install instructions instead of retrying.

## Why we ship a bridge plugin

WP core REST does **not** expose private post meta keys (those starting with
`_`). Elementor stores everything there:

- `_elementor_data` — the page's element tree (JSON-encoded, slashed)
- `_elementor_template_type` — `wp-page` | `header` | `footer` | `section`
- `_elementor_edit_mode` — `builder`
- `_elementor_version` — current Elementor version
- `_elementor_page_settings` — kit-level globals (on the active kit post)

To write these from REST, we ship `figma-importer-bridge.php` as a must-use
plugin. It registers its own routes that call `update_post_meta` directly.

## Bridge plugin endpoints

Namespace: `figma-importer/v1`

| Method | Path                          | Purpose |
|--------|-------------------------------|---------|
| GET    | `health`                      | Returns `{ok, elementor, elementor_pro, active_kit}` |
| POST   | `page`                        | `{slug, title, elementor_data[], template?}` → create/update page |
| POST   | `template`                    | `{template_type, title, elementor_data[], conditions?}` → elementor_library |
| POST   | `kit`                         | `{page_settings}` → merge into active kit `_elementor_page_settings` |
| GET    | `elementor-data/{id}`         | read decoded `_elementor_data` |
| PATCH  | `elementor-data/{id}`         | replace `_elementor_data` (used by auto-fixer) |

### Calling the bridge from Python

```python
from wp_client import WPClient

client = WPClient(cfg["wp_url"], cfg["wp_user"], cfg["wp_app_password"])

# health
print(client.bridge_health())

# create or update a page
client.create_or_update_page(
    slug="home",
    title="Home",
    elementor_data=tree,           # the data.json["content"] array
    template="elementor_canvas",   # full-width, no theme header/footer
)

# create a header template
client.create_template(
    template_type="header",
    title="Site Header",
    elementor_data=[header_node],
)

# update kit globals
client.update_kit_settings({
    "system_colors": [
        {"_id": "primary",   "title": "Primary",   "color": "#0F172A"},
        {"_id": "secondary", "title": "Secondary", "color": "#3B82F6"},
    ],
})
```

## Failure handling rules

- **Auth 401** → tell the user the Application Password is wrong/expired.
- **403 on plugin activation** → host disables filesystem writes; print manual
  install instructions and abort that phase, don't keep retrying.
- **404 on `/figma-importer/v1/health`** → bridge plugin not installed; print
  install instructions (`scripts/wp-bridge/README.md`) and abort.
- **404 on `/figma-importer/v1/kit`** → no active kit; ensure Elementor is
  activated, then re-run.
- **500 on writes** → log the response body verbatim; do not retry blindly.

## Page templates

When creating the page, set `template` to one of:
- `elementor_canvas` — no theme header/footer; recommended when our agent
  builds Theme Builder header/footer separately.
- `elementor_header_footer` — theme header/footer wrap the Elementor content.
- `default` — uses the theme's `page.php`.

Default to `elementor_canvas` unless the user opts out.
