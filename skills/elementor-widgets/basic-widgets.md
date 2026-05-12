# Free Basic widgets — JSON reference

Every Free Basic widget shipped with Elementor (no Pro required). Each
entry has the widget's slug, the smallest JSON that renders a working
instance, and the most-used settings keys.

Conventions used in this file:

- `{unit, size, sizes}` is Elementor's range-with-unit shape — `{"unit":"px","size":40,"sizes":[]}`.
- `{url, id}` for images — populate both after upload; `id` enables srcset.
- `{value, library}` for icons — Font Awesome v5 by default, e.g. `{"value":"fas fa-check","library":"fa-solid"}`.
- `__globals__` references a kit slug (`globals/colors?id=primary`).
  Add bindings here so the resolver in `optimize.py` doesn't have to
  back-fill them from inline hex.

---

## heading
- **Slug**: `heading`
- **Tier**: Free
- **Use when**: Single line of bold/oversized text introducing a section. Detect: text node whose font-size is ≥ 24px AND length ≤ 120 chars AND not preceded by another text node in the same row.
- **Min JSON**:
  ```jsonc
  { "widgetType": "heading", "elType": "widget",
    "settings": { "title": "We build SaaS dashboards", "header_size": "h1" } }
  ```
- **Common settings**: `title`, `header_size` (`h1..h6|div|span|p`), `align`, `title_color`, `link.url`, `typography_*` block.
- **Pitfalls**: `header_size` defaults to `h2`. Always set it explicitly when the design has a clear hierarchy — Elementor honors it for SEO + outline.

## text-editor
- **Slug**: `text-editor`
- **Tier**: Free
- **Use when**: Paragraph(s) of body copy, including inline `<a>`, `<strong>`, `<em>`. Detect: text node whose content contains spaces AND length > 120 chars OR has line breaks.
- **Min JSON**:
  ```jsonc
  { "widgetType": "text-editor", "elType": "widget",
    "settings": { "editor": "<p>Our platform helps SaaS founders ship faster.</p>" } }
  ```
- **Common settings**: `editor` (HTML string — wrap plain text in `<p>`), `align`, `text_color`, `typography_*`, `drop_cap` (`yes`/empty).
- **Pitfalls**: `editor` content is wp_kses'd by the bridge. Allowed tags: p, br, strong, em, a, ul, ol, li, h1-h6, blockquote.

## image
- **Slug**: `image`
- **Tier**: Free
- **Use when**: Standalone image with no associated heading/caption (those use `image-box`). Detect: image node whose siblings aren't a heading/text.
- **Min JSON**:
  ```jsonc
  { "widgetType": "image", "elType": "widget",
    "settings": { "image": {"url":"https://.../hero.jpg","id":42}, "image_size":"full" } }
  ```
- **Common settings**: `image.url` + `image.id` (both required for srcset), `image_size` (`thumbnail|medium|large|full|custom`), `caption_source` (`none|attachment|custom`), `caption`, `link_to` (`none|file|custom`), `link.url`, `align`.
- **Pitfalls**: Set BOTH `url` and `id`. Without `id`, Elementor renders the raw URL with no responsive variants — slow on mobile and looks broken on Retina.

## video
- **Slug**: `video`
- **Tier**: Free
- **Use when**: Embedded video (YouTube/Vimeo/MP4) OR an image with a play-overlay icon. Detect: image + child icon containing `fa-play` OR `_figma_name` matching `video|player`.
- **Min JSON**:
  ```jsonc
  { "widgetType": "video", "elType": "widget",
    "settings": { "video_type":"youtube", "youtube_url":"https://www.youtube.com/watch?v=…" } }
  ```
- **Common settings**: `video_type` (`youtube|vimeo|dailymotion|hosted`), `youtube_url`/`vimeo_url`/`dailymotion_url`/`hosted_url.url`, `image_overlay.url`, `show_image_overlay` (`yes`), `play_icon.value`, `lightbox` (`yes`), `aspect_ratio` (`169|43|219|11`), `autoplay`, `mute`, `loop`.
- **Pitfalls**: Hosted (self-uploaded) MP4 needs the WP attachment id. YouTube/Vimeo URLs are passed through verbatim — Elementor's `on_import` builds the player.

## button
- **Slug**: `button`
- **Tier**: Free
- **Use when**: Solid or outlined call-to-action. Detect: container with a single text child, 24-96px tall, ≤480px wide, OR `_figma_name` containing `button|cta|btn`.
- **Min JSON**:
  ```jsonc
  { "widgetType": "button", "elType": "widget",
    "settings": { "text":"Get started",
      "link":{"url":"#","is_external":"","nofollow":""} } }
  ```
- **Common settings**: `text`, `link.url|is_external|nofollow`, `align` (`left|center|right|justify`), `size` (`xs|sm|md|lg|xl`), `button_type` (`info|success|warning|danger` — adds a class), `selected_icon.value`, `icon_align` (`left|right`), `button_text_color`, `background_color`, `hover_color`, `button_background_hover_color`, `border_radius`.
- **Pitfalls**: Don't author hover colors when the Figma design has no hover variant — let Elementor's defaults apply. Setting `hover_color = background_color` disables the hover effect entirely.

## star-rating
- **Slug**: `star-rating`
- **Tier**: Free
- **Use when**: Row of star glyphs representing a rating. Detect: ≥ 3 icon widgets whose icon-class contains `star`.
- **Min JSON**:
  ```jsonc
  { "widgetType": "star-rating", "elType": "widget",
    "settings": { "rating_scale":"5", "rating":"4.5" } }
  ```
- **Common settings**: `rating_scale` (`5|10`), `rating` (string, decimals allowed: `"4.5"`), `unmarked_style` (`solid|outline`), `title`, `align`.
- **Pitfalls**: `rating` MUST be a string, not a number, in JSON — Elementor's control rejects numeric.

## divider
- **Slug**: `divider`
- **Tier**: Free
- **Use when**: Thin horizontal line (≤4px tall) used as a visual separator. Detect: container with a single thin colored shape, no children.
- **Min JSON**:
  ```jsonc
  { "widgetType": "divider", "elType": "widget",
    "settings": { "color":"#cccccc",
      "weight":{"unit":"px","size":2,"sizes":[]} } }
  ```
- **Common settings**: `color`, `weight.size`, `gap.size` (vertical space above+below), `style` (`solid|double|dotted|dashed`), `width.size`, `align`, `text`, `icon.value` (turns it into a divider-with-content).
- **Pitfalls**: For vertical dividers, set `_element_custom_width` and rotate via CSS — Elementor's divider widget is horizontal-only.

## google-maps
- **Slug**: `google-maps`
- **Tier**: Free
- **Use when**: A map graphic with a pin and address text. Detect: map illustration + adjacent address-like text.
- **Min JSON**:
  ```jsonc
  { "widgetType": "google_maps", "elType": "widget",
    "settings": { "address":"1 Infinite Loop, Cupertino, CA",
      "zoom":{"unit":"px","size":10,"sizes":[]},
      "height":{"unit":"px","size":300,"sizes":[]} } }
  ```
- **Common settings**: `address`, `zoom`, `height`, `prevent_scroll` (`yes`/empty).
- **Pitfalls**: The widget slug is `google_maps` with an UNDERSCORE — one of the few non-kebab slugs. Without a Google Maps API key configured site-wide, the embed falls back to OpenStreetMap-style; tell the developer when the key is missing.

## icon
- **Slug**: `icon`
- **Tier**: Free
- **Use when**: Static SVG/FA icon with no surrounding text. Detect: `widgetType == "icon"` per ai-layout OR isolated small SVG ≤ 64px.
- **Min JSON**:
  ```jsonc
  { "widgetType": "icon", "elType": "widget",
    "settings": { "selected_icon": {"value":"fas fa-rocket","library":"fa-solid"} } }
  ```
- **Common settings**: `selected_icon.value` + `selected_icon.library` (`fa-solid|fa-regular|fa-brands|svg`), `view` (`default|stacked|framed`), `shape` (`square|circle`), `primary_color`, `secondary_color`, `size.size`, `link.url`, `align`.
- **Pitfalls**: For custom SVG, use `library: "svg"` and `selected_icon.value: {"url":"...","id":<attachment_id>}` shape.

## image-box
- **Slug**: `image-box`
- **Tier**: Free
- **Use when**: Image + heading + (optional) description packed as one card. Detect: container with exactly `image + heading + text-editor` children (≤ 4 children total). VERY common in feature grids.
- **Min JSON**:
  ```jsonc
  { "widgetType": "image-box", "elType": "widget",
    "settings": { "image":{"url":"https://.../feature.png","id":7,"source":"library"},
      "title_text":"Real-time sync", "description_text":"Updates land instantly.",
      "title_size":"h3", "position":"top" } }
  ```
- **Common settings**: `image`, `title_text`, `description_text`, `title_size` (`h1..h6|div|span|p`), `position` (`top|left|right`), `image_space`, `title_color`, `description_color`, `text_align`, `link.url`.
- **Pitfalls**: `image.source` should be `"library"` for uploaded media — without it the editor can swap to a placeholder.

## icon-box
- **Slug**: `icon-box`
- **Tier**: Free
- **Use when**: Icon + heading + (optional) description packed as one card. Detect: container with `icon + heading + text-editor` children.
- **Min JSON**:
  ```jsonc
  { "widgetType": "icon-box", "elType": "widget",
    "settings": { "selected_icon":{"value":"fas fa-check","library":"fa-solid"},
      "title_text":"Secure by default", "description_text":"AES-256 at rest.",
      "title_size":"h3", "position":"top" } }
  ```
- **Common settings**: `selected_icon`, `title_text`, `description_text`, `title_size`, `position`, `view` (`default|stacked|framed`), `shape`, `primary_color`, `link.url`.
- **Pitfalls**: When the icon should be a colored circle background, use `view: "framed"` or `view: "stacked"` + `shape: "circle"`.

## basic-gallery
- **Slug**: `image-gallery` (legacy `gallery` works too)
- **Tier**: Free
- **Use when**: Grid of ≥ 4 images that opens to a lightbox on click. Detect: container with ≥ 4 sibling image children AND no surrounding text.
- **Min JSON**:
  ```jsonc
  { "widgetType": "image-gallery", "elType": "widget",
    "settings": { "wp_gallery":[
        {"id":11,"url":"https://.../1.jpg"},
        {"id":12,"url":"https://.../2.jpg"} ],
      "gallery_columns":"3", "thumbnail_size":"medium" } }
  ```
- **Common settings**: `wp_gallery` (array of `{id,url}` — id is required), `gallery_columns` (`1..12` as string), `thumbnail_size`, `gallery_link` (`file|attachment|none`), `gallery_rand` (`rand` / empty).
- **Pitfalls**: `gallery_columns` is a STRING, not a number. For Pro-style hover effects, use `gallery` (Pro widget) instead.

## image-carousel
- **Slug**: `image-carousel`
- **Tier**: Free
- **Use when**: Horizontal scroller of images — logo strip, product showcase, screenshot rotator. Detect: row of ≥ 3 images with `flex_wrap: nowrap` OR `_figma_name` containing `carousel|slider|gallery|logo cloud`.
- **Min JSON**:
  ```jsonc
  { "widgetType": "image-carousel", "elType": "widget",
    "settings": { "carousel":[
        {"id":11,"url":"https://.../logo-a.svg"},
        {"id":12,"url":"https://.../logo-b.svg"} ],
      "slides_to_show":"5", "navigation":"both", "autoplay":"yes" } }
  ```
- **Common settings**: `carousel`, `slides_to_show` (string), `slides_to_scroll`, `navigation` (`both|arrows|dots|none`), `pause_on_hover`, `autoplay`, `autoplay_speed`, `infinite`, `speed`, `image_stretch` (`yes`/empty).
- **Pitfalls**: For mixed media (image + video) use `media-carousel` (Pro). For text overlays per slide, use `slides` (Pro) — image-carousel has no per-slide text.

## icon-list
- **Slug**: `icon-list`
- **Tier**: Free
- **Use when**: Vertical or horizontal list of icon + label rows. Detect: ≥ 2 rows of `icon + heading/text` shape, same icon pattern (e.g. all checkmarks). Also good for **feature lists** and **specs**.
- **Min JSON**:
  ```jsonc
  { "widgetType": "icon-list", "elType": "widget",
    "settings": { "icon_list":[
        {"text":"Unlimited seats",
         "selected_icon":{"value":"fas fa-check","library":"fa-solid"}},
        {"text":"24/7 support",
         "selected_icon":{"value":"fas fa-check","library":"fa-solid"}} ],
      "view":"traditional" } }
  ```
- **Common settings**: `icon_list[]` (each: `text`, `link.url`, `selected_icon`), `view` (`traditional|inline`), `space_between.size`, `icon_color`, `text_color`, `divider` (`yes`/empty), `divider_style`.
- **Pitfalls**: For per-row link targets, set `icon_list[i].link.url` — the widget renders each row as an `<a>` when `link.url` is set.

## counter
- **Slug**: `counter`
- **Tier**: Free
- **Use when**: Animated number stat (e.g. "10,000+ customers"). Detect: heading with mostly-numeric content, optionally followed by a label.
- **Min JSON**:
  ```jsonc
  { "widgetType": "counter", "elType": "widget",
    "settings": { "starting_number":0, "ending_number":10000,
      "title":"Customers", "thousand_separator":"yes", "suffix":"+" } }
  ```
- **Common settings**: `starting_number`, `ending_number`, `prefix`, `suffix`, `title`, `duration` (ms), `thousand_separator`, `thousand_separator_char`, `title_color`, `number_color`.
- **Pitfalls**: `ending_number` is a NUMBER, but Elementor's control rejects float strings. Round at emit time.

## spacer
- **Slug**: `spacer`
- **Tier**: Free
- **Use when**: Explicit vertical breathing room between sections. Detect: empty container ≥ 8px tall with NO background color, NO children.
- **Min JSON**:
  ```jsonc
  { "widgetType": "spacer", "elType": "widget",
    "settings": { "space":{"unit":"px","size":40,"sizes":[]} } }
  ```
- **Common settings**: `space.size`, `space.unit`.
- **Pitfalls**: Don't emit `spacer` for every gap — Elementor's container `flex_gap` is the proper layout primitive. Reserve `spacer` for between-section breathing room.

## testimonial
- **Slug**: `testimonial`
- **Tier**: Free
- **Use when**: Single quote + person attribution. Detect: text node with quote glyph OR italic + name + (optional) avatar + (optional) job title.
- **Min JSON**:
  ```jsonc
  { "widgetType": "testimonial", "elType": "widget",
    "settings": { "testimonial_content":"This changed our workflow.",
      "testimonial_name":"Alex Rivera", "testimonial_job":"CTO, Acme",
      "testimonial_image":{"url":"https://.../alex.jpg","id":33} } }
  ```
- **Common settings**: `testimonial_content`, `testimonial_name`, `testimonial_job`, `testimonial_image`, `testimonial_image_position` (`aside|top`), `testimonial_alignment`.
- **Pitfalls**: For multiple testimonials, use `testimonial-carousel` (Pro) or row of `testimonial` widgets — not nested testimonials.

## tabs
- **Slug**: `tabs`
- **Tier**: Free
- **Use when**: Tab strip + N panels below. Detect: 2 sibling containers — first has ≥ 2 buttons/headings inline, second has matching N panels.
- **Min JSON**:
  ```jsonc
  { "widgetType": "tabs", "elType": "widget",
    "settings": { "tabs":[
        {"tab_title":"Features","tab_content":"<p>Built for scale.</p>"},
        {"tab_title":"Pricing", "tab_content":"<p>Three plans.</p>"} ] } }
  ```
- **Common settings**: `tabs[]` (each: `tab_title`, `tab_content` HTML), `type` (`horizontal|vertical`), `tab_align` (`flex-start|center|flex-end`).
- **Pitfalls**: `tab_content` is HTML — wrap plain text in `<p>`. Per-panel images/buttons require embedded HTML; for richer per-panel layout use `nested-carousel` (Pro) or separate sections.

## accordion
- **Slug**: `accordion`
- **Tier**: Free
- **Use when**: ≥ 2 expandable rows, each with a heading + body. Detect: vertical stack of containers, each with `heading + text-editor` children, optionally with a chevron icon.
- **Min JSON**:
  ```jsonc
  { "widgetType": "accordion", "elType": "widget",
    "settings": { "tabs":[
        {"tab_title":"How long is onboarding?","tab_content":"<p>About 10 minutes.</p>"},
        {"tab_title":"Do you offer SSO?",       "tab_content":"<p>Yes, via SAML.</p>"} ],
      "selected_icon":{"value":"fas fa-caret-down","library":"fa-solid"} } }
  ```
- **Common settings**: `tabs[]` (`tab_title`, `tab_content`), `selected_icon` (closed state), `selected_active_icon` (open state), `tab_active_id` (which is open initially), `border_width`, `title_background`, `title_color`.
- **Pitfalls**: `tabs[]` key is shared with `toggle` and `tabs` widgets — same shape, different widget. For a single Q+A pair use `toggle`.

## toggle
- **Slug**: `toggle`
- **Tier**: Free
- **Use when**: Single expand/collapse pair (one Q+A). Detect: same as accordion but exactly 1 row.
- **Min JSON**: same as `accordion` but `tabs` has one entry; `widgetType: "toggle"`.
- **Common settings**: same as `accordion`.
- **Pitfalls**: When users have ≥ 2 Q+A pairs, prefer `accordion` — it's the same widget infrastructure with multi-open behavior available via `tab_active_id`.

## social-icons
- **Slug**: `social-icons`
- **Tier**: Free
- **Use when**: Row of small icons linking to social networks. Detect: ≥ 2 icons whose `selected_icon.value` matches a social brand OR whose `link.url` matches a social host (twitter.com, facebook.com, …).
- **Min JSON**:
  ```jsonc
  { "widgetType": "social-icons", "elType": "widget",
    "settings": { "social_icon_list":[
        {"social_icon":{"value":"fab fa-twitter","library":"fa-brands"},
         "link":{"url":"https://twitter.com/acme","is_external":"true","nofollow":""}},
        {"social_icon":{"value":"fab fa-linkedin","library":"fa-brands"},
         "link":{"url":"https://linkedin.com/company/acme","is_external":"true"}} ],
      "shape":"rounded" } }
  ```
- **Common settings**: `social_icon_list[]`, `shape` (`square|rounded|circle`), `columns` (`0` = auto), `align`, `icon_color` (`default|custom`), `icon_primary_color`, `icon_secondary_color`.
- **Pitfalls**: Each item has `social_icon` (not `selected_icon`). For "share on X" style buttons that pre-fill the share URL, use `share-buttons` (Pro).

## progress-bar
- **Slug**: `progress`
- **Tier**: Free
- **Use when**: Horizontal bar showing a percentage. Detect: bar shape + percentage text OR `_figma_name` matching `progress|bar`.
- **Min JSON**:
  ```jsonc
  { "widgetType": "progress", "elType": "widget",
    "settings": { "title":"Storage used",
      "percent":{"unit":"%","size":68,"sizes":[]},
      "display_percentage":"show" } }
  ```
- **Common settings**: `title`, `percent.size`, `display_percentage` (`show|hide`), `inner_text`, `progress_type` (`info|success|warning|danger`), `bar_color`, `bar_bg_color`, `bar_height`, `bar_border_radius`.
- **Pitfalls**: For multi-step indicators (1/3 → 2/3 → 3/3), use `progress-tracker` (Pro).

## sound-cloud
- **Slug**: `soundcloud`
- **Tier**: Free
- **Use when**: SoundCloud / audio embed. Detect: SoundCloud URL in plugin metadata OR audio waveform graphic with track title.
- **Min JSON**:
  ```jsonc
  { "widgetType": "soundcloud", "elType": "widget",
    "settings": { "link":{"url":"https://soundcloud.com/.../track"},
      "visual":"yes", "auto_play":"" } }
  ```
- **Common settings**: `link.url`, `visual` (`yes` = waveform, `""` = mini player), `auto_play`, `buying`, `liking`, `download`, `show_comments`, `color`.
- **Pitfalls**: Slug is `soundcloud` (one word, no hyphen). For self-hosted audio, use `video` with `video_type: "hosted"` — there's no native audio widget.

## shortcode
- **Slug**: `shortcode`
- **Tier**: Free
- **Use when**: A `[shortcode]` literal appears in the Figma copy. Detect: text node containing `[...]` square-bracket syntax.
- **Min JSON**:
  ```jsonc
  { "widgetType": "shortcode", "elType": "widget",
    "settings": { "shortcode":"[gravityform id=\"1\" title=\"false\"]" } }
  ```
- **Common settings**: just `shortcode`.
- **Pitfalls**: Used by `form-intelligence` skill when Gravity Forms is active — the form section is replaced with a `shortcode` widget pointing at the new GF form id.

## html
- **Slug**: `html`
- **Tier**: Free
- **Use when**: Custom HTML / embed code that doesn't map to any widget. Last resort.
- **Min JSON**:
  ```jsonc
  { "widgetType": "html", "elType": "widget",
    "settings": { "html":"<iframe src=\"https://...\" width=\"600\" height=\"400\"></iframe>" } }
  ```
- **Common settings**: just `html`.
- **Pitfalls**: `optimize.py::replace_html_widgets()` aggressively converts `widgetType: html` → `text-editor` when the content looks paragraph-like. Only emit `html` for genuinely-not-text content (iframes, custom scripts).

## menu-anchor
- **Slug**: `menu-anchor`
- **Tier**: Free
- **Use when**: An invisible jump-target for in-page `#anchor` links. Detect: rare in Figma — emit when a Pro Nav Menu link has `url: "#features"`.
- **Min JSON**:
  ```jsonc
  { "widgetType": "menu-anchor", "elType": "widget",
    "settings": { "anchor":"features" } }
  ```
- **Common settings**: just `anchor` (no `#` prefix in the value).

## alert
- **Slug**: `alert`
- **Tier**: Free
- **Use when**: Notice box (info/success/warn/danger). Detect: rounded container with colored bg + icon + heading + description, often with a close button.
- **Min JSON**:
  ```jsonc
  { "widgetType": "alert", "elType": "widget",
    "settings": { "alert_type":"info", "alert_title":"Heads up",
      "alert_description":"This feature is in beta.", "show_dismiss":"show" } }
  ```
- **Common settings**: `alert_type` (`info|success|warning|danger`), `alert_title`, `alert_description`, `show_dismiss`, `alert_notice_icon.value`.

## sidebar
- **Slug**: `sidebar`
- **Tier**: Free
- **Use when**: Embeds a WordPress sidebar (legacy theme widget area) inside the page. Detect: rare in Figma — only when a section literally is "show theme's sidebar here".
- **Min JSON**:
  ```jsonc
  { "widgetType": "sidebar", "elType": "widget",
    "settings": { "sidebar":"sidebar-1" } }
  ```
- **Common settings**: just `sidebar` (the sidebar slug as registered by the active theme).
- **Pitfalls**: Requires the active theme to register a sidebar with that slug. The bridge logs a warning when the slug doesn't exist.

## text-path
- **Slug**: `text-path`
- **Tier**: Free
- **Use when**: Text bent along an SVG curve (logo lockups, decorative headings). Detect: text on an SVG path in Figma.
- **Min JSON**:
  ```jsonc
  { "widgetType": "text-path", "elType": "widget",
    "settings": { "text":"Bend me around",
      "path":"M 10 50 Q 100 0 200 50 T 400 50",
      "text_path_direction":"" } }
  ```
- **Common settings**: `text`, `path` (SVG path data), `text_path_direction` (`""` = forward, `rtl` = reverse), `start_point.size`, `link.url`.

## container
- **Element type**: container (NOT a widget)
- **Use when**: Layout wrapper around children. Detect: any Figma frame with Auto Layout. The agent emits this as `elType: "container"`, not `widget`.
- **Min JSON**:
  ```jsonc
  { "elType":"container", "isInner":false,
    "settings":{ "flex_direction":"row", "flex_gap":{"unit":"px","size":24,"sizes":[]} },
    "elements":[ /* widgets or nested containers */ ] }
  ```
- **Common settings**: `flex_direction` (`row|column`), `flex_gap`, `flex_justify_content` (`flex-start|center|flex-end|space-between|space-around|space-evenly`), `flex_align_items`, `flex_wrap` (`nowrap|wrap`), `padding`, `margin`, `width`, `min_height`, `content_width` (`boxed|full`), `boxed_width`, `background_*`, `border_*`, `box_shadow_*`, `flex_direction_mobile`, `flex_wrap_mobile`, `padding_mobile`.
- **Pitfalls**: Containers replace the legacy `section > column > widget` tree on Elementor 3.16+. For top-level containers `isInner: false`; for nested `isInner: true`. The `apply_responsive_defaults()` pass stamps `_mobile` overrides automatically.

## link-in-bio
- **Slug**: `link-in-bio`
- **Tier**: Free
- **Use when**: Linktree-style mini landing page (avatar + name + bio + stacked link buttons). Detect: vertical stack with avatar, short name heading, bio paragraph, ≥3 full-width buttons.
- **Min JSON**:
  ```jsonc
  { "widgetType":"link-in-bio", "elType":"widget",
    "settings":{ "identity_image":{"url":"...","id":1},
      "heading":"Alex Rivera", "description":"Building dashboards",
      "bio_links":[
        {"link_text":"Twitter","link":{"url":"https://twitter.com/alex"}},
        {"link_text":"GitHub", "link":{"url":"https://github.com/alex"}} ] } }
  ```
- **Common settings**: `identity_image`, `heading`, `description`, `bio_links[]`, `cta_links[]`, `icons[]` (social row).
- **Pitfalls**: Newer widget (Elementor 3.21+). Falls back to a plain container of buttons on older Elementor — version-check `health.elementor` before emitting.

## inner-section
- **Slug**: `section` with `isInner: true` (legacy)
- **Use when**: NEVER for new content. Only encountered when reading existing pages that pre-date Flexbox Container. Detect: `elType: "section"` with `isInner: true`.
- **Migration**: Replace with `elType: "container"` `isInner: true`. The agent's pipeline emits Container by default for new pages.
- **Pitfalls**: Sections + columns + widgets is the legacy three-tier tree. Don't author new content into it.
