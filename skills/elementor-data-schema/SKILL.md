---
name: elementor-data-schema
description: Shape of the Elementor element tree (`_elementor_data`), the widgets the Figma plugin emits, and the settings we read or write most often.
---

# Elementor data schema

`_elementor_data` is a JSON-encoded array of element nodes. The top level is
an array of containers; each container has `elements` (more containers or
widgets) recursively.

## Element node

```jsonc
{
  "id": "el00001",            // 7-char unique id (set by builder; we generate too)
  "elType": "container" | "widget",
  "isInner": false,           // containers only; true when nested
  "widgetType": "heading" | "text-editor" | "image" | "button" | "spacer",  // widgets only
  "settings": { /* see below */ },
  "elements": [ /* children */ ]
}
```

## Containers

Common settings keys our pipeline reads/writes:

| Key | Type | Notes |
|-----|------|-------|
| `content_width` | `"boxed"` \| `"full"` | full = 100vw, boxed = constrained |
| `flex_direction` | `"row"` \| `"column"` | from Figma Auto Layout |
| `flex_gap` | `{unit, size, sizes}` | itemSpacing |
| `flex_justify_content` | `flex-start` \| `center` \| `space-between` \| ... | primaryAlign |
| `flex_align_items` | same | counterAlign |
| `flex_wrap` | `nowrap` \| `wrap` | |
| `padding` | `{unit, top, right, bottom, left, isLinked}` | |
| `width` | `{unit, size}` | unit `px` or `%` |
| `min_height` | `{unit, size}` | |
| `background_background` | `"classic"` (when used) | |
| `background_color` | hex | |
| `background_image` | `{url, id}` | id is WP attachment id |
| `_figma_id`, `_figma_name` | private | used by our agent for traceability |

## Widgets

### `heading`
```jsonc
{
  "title": "Some text",
  "header_size": "h1" | "h2" | "h3" | "h4" | "h5" | "h6" | "div" | "span" | "p",
  "align": "left" | "center" | "right",
  "title_color": "#hex",
  "typography_typography": "custom",
  "typography_font_family": "Inter",
  "typography_font_size": {"unit":"px", "size": 64, "sizes": []},
  "typography_font_weight": "300",
  "typography_line_height": {"unit":"px", "size": 70, "sizes": []},
  "typography_letter_spacing": {"unit":"px", "size": 0, "sizes": []},
  "typography_text_transform": "none",
  "typography_text_decoration": "none"
}
```

### `text-editor`
```jsonc
{
  "editor": "<p>HTML body…</p>",   // can contain <p>, <strong>, <em>, <a>
  "align": "left",
  "text_color": "#hex",
  "typography_*": same as heading
}
```

### `image`
```jsonc
{
  "image": {"url": "https://...", "id": 123},
  "image_size": "full",
  "align": "center"
}
```
**Always** populate both `url` and `id` after upload — Elementor uses `id`
for srcset/responsive sizes; URL alone renders, but without responsive variants.

### `button`
```jsonc
{
  "text": "Click me",
  "link": {"url": "#", "is_external": false, "nofollow": false},
  "align": "left",
  "size": "md",
  "button_text_color": "#fff",
  "background_color": "#0066ff",
  "border_radius": {"unit":"px", "top":"6","right":"6","bottom":"6","left":"6","isLinked":true}
}
```

### `spacer`
```jsonc
{
  "space": {"unit": "px", "size": 40, "sizes": []}
}
```

## Kit (active) — `_elementor_page_settings`

This meta lives on the post that `get_option('elementor_active_kit')` points at.
The bridge merges (not replaces) the keys we send.

```jsonc
{
  "system_colors": [
    {"_id": "primary",   "title": "Primary",   "color": "#…"},
    {"_id": "secondary", "title": "Secondary", "color": "#…"},
    {"_id": "text",      "title": "Text",      "color": "#…"},
    {"_id": "accent",    "title": "Accent",    "color": "#…"}
  ],
  "custom_colors": [
    {"_id": "<7-char>", "title": "label", "color": "#…"}
  ],
  "system_typography": [
    {
      "_id": "primary",                       // canonical: primary|secondary|text|accent
      "title": "Primary",
      "typography_typography": "custom",
      "typography_font_family": "Inter",
      "typography_font_weight": "300",
      "typography_font_size": {"unit": "px", "size": 64, "sizes": []},
      "typography_line_height": {"unit": "px", "size": 70, "sizes": []}
    }
  ],
  "container_width": {"unit": "px", "size": 1140, "sizes": []},
  "default_generic_fonts": "Inter",
  "space_between_widgets": {"unit": "px", "size": 20, "sizes": []}
}
```

> Note: Elementor's UI exposes 4 system-color slots. Anything beyond those
> belongs in `custom_colors`. The `_id` of a system color must be one of
> `primary|secondary|text|accent` for it to overwrite the default slot.

## Library templates — `elementor_library` post type

Set the term on `elementor_library_type` taxonomy:
- `header`, `footer`, `single`, `archive`, `popup`, `section`, `page`

For Pro Theme Builder conditions, set `_elementor_conditions` post meta to
an array of strings like `"include/general"` (apply everywhere) or
`"include/in_singular/post"`.

## Useful invariants

- Every node has a unique `id`. When mutating in place we keep the same id;
  when grafting, regenerate ids to avoid collisions.
- `_elementor_data` is stored slash-escaped (`wp_slash`). The bridge handles
  this for us — never call `wp_slash` from Python.
- Removing the active kit post breaks the site. Treat its id as read-only;
  only patch `_elementor_page_settings` on it.

## How the bridge writes element data (important)

Our bridge uses Elementor's **official Document API** rather than writing
`_elementor_data` directly with `update_post_meta`:

- `\Elementor\Plugin::$instance->documents->create($type, $postarr)` —
  create a new page or `elementor_library` template.
- `\Elementor\Plugin::$instance->documents->get($id)` — get an existing one.
- `$document->save(['elements' => ..., 'settings' => ...])` — official save
  flow that runs hooks, version-stamps the document, regenerates the per-post
  CSS file, etc.
- `\Elementor\Plugin::$instance->db->iterate_data($tree, callback)` — walk
  the tree; the callback receives every element and is where we run each
  control's `on_import` method (this is what makes images, links, carousels,
  galleries, etc. resolve correctly across the entire widget zoo).

For globals:
- `\Elementor\Plugin::$instance->kits_manager->get_active_kit_for_frontend()`
  returns the active kit document.
- `\Elementor\Core\Settings\Manager::get_settings_managers('page')` is the
  page settings manager whose `save_settings($merged, $kit_id)` is the
  official way to write kit globals (mirrors to autosaves, clears CSS).

These are all documented Elementor APIs — the same code paths Elementor's
own editor uses when a developer clicks Save. Using them (instead of writing
`_elementor_data` directly) is what makes the imported output behave
identically to a hand-built page.

## What this means in practice

If you ever need to mutate `_elementor_data` from PHP land, **don't** call
`update_post_meta($id, '_elementor_data', ...)` directly. Use the Document
API instead — it's the same one the Elementor editor uses when you click Save.
Direct meta writes skip the version-stamping, hooks, and CSS regen, which
is exactly the class of bug that produces "looks broken in the editor" or
"page renders unstyled on first visit".
