# Figma Importer Bridge — install instructions

This is a **must-use plugin** the agent uses to write into Elementor's private
post meta (`_elementor_data`) and the active kit's settings. WordPress core
REST does not expose those keys, so this bridge fills the gap.

## Install (one-time)

1. Copy `figma-importer-bridge.php` into your WordPress site at:

   ```
   wp-content/mu-plugins/figma-importer-bridge.php
   ```

   If the `mu-plugins` folder does not exist, create it. Must-use plugins are
   activated automatically — no admin click required.

2. Verify it's live by hitting:

   ```
   https://YOUR-SITE/wp-json/figma-importer/v1/health
   ```

   You should see JSON with `"ok": true` and the active Elementor version.

## What it exposes

All endpoints sit under `/wp-json/figma-importer/v1/` and require an
authenticated request (Application Password + admin user).

| Method  | Path                          | Capability       | Purpose |
|---------|-------------------------------|------------------|---------|
| GET     | `/health`                     | public           | Sanity check + Elementor version readout |
| POST    | `/page`                       | `edit_pages`     | Create or update a page; writes `_elementor_data` |
| POST    | `/template`                   | `edit_posts`     | Create an `elementor_library` template (header / footer / section) |
| POST    | `/kit`                        | `manage_options` | Merge new keys into active kit's `_elementor_page_settings` |
| GET     | `/elementor-data/{id}`        | `edit_posts`     | Read decoded `_elementor_data` |
| PATCH   | `/elementor-data/{id}`        | `edit_posts`     | Replace `_elementor_data` (used by auto-fixer) |

## Security

- Every write endpoint uses a `current_user_can(...)` capability check.
- Authentication is delegated to WordPress (Basic over HTTPS via Application
  Passwords). Make sure the site is HTTPS.
- The plugin does **not** introduce any new options or unauth surface.

## Uninstall

Delete `wp-content/mu-plugins/figma-importer-bridge.php`. No DB cleanup
needed — this bridge stores nothing of its own.
