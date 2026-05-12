# Elementor Pro general widgets — JSON reference

All Pro widgets that aren't Theme Elements or WooCommerce. The agent
emits these only when `health.elementor_pro` is truthy (set by
`wp-setup`'s health check). When Pro isn't active, use the Free
fallback listed in each entry — the page will not be pixel-identical
but it will be editable.

Same conventions as `basic-widgets.md`:
- `{unit,size,sizes}` shape for ranges
- `{url,id}` for images (id required for srcset)
- `{value,library}` for icons (`fa-solid|fa-regular|fa-brands|svg`)
- Slug = `widgetType` value the agent must emit
- `__globals__` references kit slugs

---

## form
- **Slug**: `form`
- **Tier**: Pro
- **Use when**: ≥ 2 input fields + a submit button. Detect: container with `text-editor` + `input`-shaped children (label above + bordered rectangle) + a button.
- **Min JSON**:
  ```jsonc
  { "widgetType":"form", "elType":"widget",
    "settings":{ "form_name":"Contact",
      "form_fields":[
        {"_id":"name",    "field_type":"text",     "field_label":"Name",    "required":"true", "width":"100"},
        {"_id":"email",   "field_type":"email",    "field_label":"Email",   "required":"true", "width":"100"},
        {"_id":"message", "field_type":"textarea", "field_label":"Message", "required":"true", "width":"100", "rows":"4"} ],
      "button_text":"Send", "button_size":"md",
      "submit_actions":["email"], "email_to":"admin@example.com",
      "email_subject":"New form submission" } }
  ```
- **Common settings**:
  - `form_fields[]` per field: `_id`, `field_type` (`text|email|textarea|tel|url|number|date|time|select|radio|checkbox|acceptance|hidden|html|password|file|step`), `field_label`, `placeholder`, `required` (`"true"`/`""`), `width` (`100|66|50|33|25`), `field_options` (newline-separated for select/radio/checkbox), `rows`, `min`, `max`.
  - `button_text`, `button_size`, `button_align`, `button_width` (`100|66|50|33|25`), `button_icon`.
  - `submit_actions` (`["email","redirect","webhook","mailchimp","activecampaign","slack","discord","getresponse","convertkit","drip","hubspot","zapier","popup"]`), per action settings: `email_to`, `email_subject`, `email_from`, `email_content`, `email_content_type` (`html|plain`), `redirect_to`, `webhook_url`.
  - Messaging: `success_message`, `error_message`, `required_field_message`.
- **Pitfalls**: For Free Elementor, this skill defers to `form-intelligence` which creates a Gravity Form and inserts a `shortcode` widget instead. The Form widget cannot read a Gravity form — emit one or the other, never both.

## posts
- **Slug**: `posts`
- **Tier**: Pro
- **Use when**: "Latest posts" / blog grid pattern. Detect: row of cards each with `image + heading + text-editor + button` shape, OR ai-layout's `sectionPurpose` indicates `blog-grid|archive`.
- **Min JSON**:
  ```jsonc
  { "widgetType":"posts", "elType":"widget",
    "settings":{ "posts_per_page":"3", "columns":"3",
      "post_type":"post", "orderby":"date", "order":"desc",
      "show_image":"yes", "show_title":"yes", "show_excerpt":"yes",
      "show_read_more":"yes", "read_more_text":"Read more" } }
  ```
- **Common settings**: `posts_per_page`, `columns` (`1..6` string), `post_type`, `posts_post_ids` (array of post IDs for hand-picked), `posts_categories` (array of term IDs), `posts_tags`, `posts_authors`, `orderby` (`date|title|menu_order|rand|comment_count`), `order` (`asc|desc`), `show_*` flags for each part, `excerpt_length`, `meta_separator`, `pagination` (`numbers|prev_next|load_more|none`).
- **Pitfalls**: Without Pro, fall back to `wp-widget-recent-posts` which renders a basic UL of titles. To target a CPT, set `post_type` to the CPT slug — verify it exists via `health.custom_post_types` first.

## portfolio
- **Slug**: `portfolio`
- **Tier**: Pro
- **Use when**: Filterable image grid with category tabs (case-study showcase, work samples). Detect: image grid + category filter row above it.
- **Min JSON**:
  ```jsonc
  { "widgetType":"portfolio", "elType":"widget",
    "settings":{ "posts_per_page":"9", "columns":"3",
      "show_filter":"yes", "post_type":"portfolio" } }
  ```
- **Common settings**: `posts_per_page`, `columns`, `show_filter`, `filter_text_all`, `filter_taxonomy`, `image_size`, `item_ratio`, `overlay_color`, `caption_position`.
- **Pitfalls**: Requires the `portfolio` CPT (registered by Elementor Pro automatically). For a static portfolio with no CPT, use a row of `image-box` widgets instead.

## slides
- **Slug**: `slides`
- **Tier**: Pro
- **Use when**: Full-bleed hero rotator — ≥ 2 slides each with its own background, heading, description, and button(s). Detect: ≥ 2 sibling containers with image background + heading + button.
- **Min JSON**:
  ```jsonc
  { "widgetType":"slides", "elType":"widget",
    "settings":{ "slides":[
        {"_id":"s1", "background_image":{"url":"https://.../bg1.jpg","id":10},
         "heading":"Ship faster", "description":"With our SaaS toolkit.",
         "button_text":"Get started", "link":{"url":"#"}},
        {"_id":"s2", "background_image":{"url":"https://.../bg2.jpg","id":11},
         "heading":"Built for teams","description":"Real-time collaboration.",
         "button_text":"See features","link":{"url":"#features"}} ],
      "navigation":"both", "autoplay":"yes", "infinite":"yes" } }
  ```
- **Common settings**: `slides[]` (per slide: `heading`, `description`, `button_text`, `link.url`, `background_image`, `background_overlay_color`, `text_align`, `content_position` (`top|middle|bottom`), `vertical_position` for content placement), `slides_height`, `navigation`, `pause_on_hover`, `autoplay`, `autoplay_speed`, `transition` (`slide|fade`).
- **Pitfalls**: Falls back to `image-carousel` without Pro (text overlay is lost). For a single non-rotating hero, use a Container with image background + heading + button.

## nav-menu
- **Slug**: `nav-menu`
- **Tier**: Pro
- **Use when**: Site navigation in a header template. Detect: row of inline text/button widgets in a navbar section.
- **Min JSON**:
  ```jsonc
  { "widgetType":"nav-menu", "elType":"widget",
    "settings":{ "menu":"primary-menu", "layout":"horizontal",
      "align":"right", "pointer":"underline" } }
  ```
- **Common settings**: `menu` (the WP menu slug — agent creates this via `figma_importer_create_or_update_menu`), `layout` (`horizontal|vertical|dropdown`), `align` (`left|center|right|justify`), `pointer` (`none|underline|overline|double-line|framed|background|text`), `pointer_animation` (`fade|slide|grow|drop-in|drop-out|none`), `indicator` (`classic|chevron|chevron-down|angle|angle-down|plus|none`), `submenu_icon`.
- **Pitfalls**: Free fallback is `wp-widget-nav_menu` (core WP widget). The agent's `make_wp_menu_widget()` emits this. For mobile, set `mobile_layout: "dropdown"` and `toggle: "burger"`.

## mega-menu
- **Slug**: `mega-menu`
- **Tier**: Pro (Elementor 3.18+)
- **Use when**: Header nav with full-width dropdown panels that contain images / icons / multi-column link lists.
- **Min JSON**:
  ```jsonc
  { "widgetType":"mega-menu", "elType":"widget",
    "settings":{ "menu_items":[
        {"item_label":"Products",
         "item_content_source":"elementor_template",
         "item_content":"42"  /* elementor_library post id */} ] } }
  ```
- **Common settings**: `menu_items[]` (each: `item_label`, `item_link.url`, `item_content_source` (`elementor_template|text|html`), `item_content`), `layout`, `align`, `pointer`, `submenu_position`.
- **Pitfalls**: Dropdown contents are Elementor library templates referenced by post id — the agent must create those templates separately (one per dropdown) before referencing them.

## off-canvas
- **Slug**: `off-canvas`
- **Tier**: Pro (Elementor 3.21+)
- **Use when**: Hidden side panel triggered by a button (mobile hamburger menu, cart drawer). Detect: rare in Figma exports — only when the design explicitly shows a drawer panel.
- **Min JSON**:
  ```jsonc
  { "widgetType":"off-canvas", "elType":"widget",
    "settings":{ "open_button_label":"Menu",
      "open_button_icon":{"value":"fas fa-bars","library":"fa-solid"},
      "content_template":"42", "position":"left",
      "content_width":{"unit":"px","size":320,"sizes":[]} } }
  ```
- **Common settings**: `open_button_label`, `open_button_icon`, `content_template` (library template id), `position` (`left|right|top|bottom`), `content_width`, `overlay_color`, `close_on_overlay_click` (`yes`/empty).

## login
- **Slug**: `login`
- **Tier**: Pro
- **Use when**: Login form section. Detect: 2 inputs (user + password) + login button OR `_figma_name` matching `login|signin`.
- **Min JSON**:
  ```jsonc
  { "widgetType":"login", "elType":"widget",
    "settings":{ "show_logged_in_message":"hide",
      "button_text":"Log in",
      "redirect_after_login":"yes", "redirect_url":"/dashboard" } }
  ```
- **Common settings**: `button_text`, `show_labels`, `show_placeholder`, `show_lost_password`, `show_register`, `redirect_after_login`, `redirect_url`, `logged_in_message`, `field_size` (`xs|sm|md|lg|xl`).
- **Pitfalls**: Without Pro, link to `wp-login.php` directly. The widget renders the login *user-facing* — not the admin login.

## animated-headline
- **Slug**: `animated-headline`
- **Tier**: Pro
- **Use when**: Heading with one or more rotating/animated words (e.g. "We build {dashboards|websites|apps}"). Detect: heading whose text contains a rotating pattern OR `_figma_name` matching `animated|rotating headline`.
- **Min JSON**:
  ```jsonc
  { "widgetType":"animated-headline", "elType":"widget",
    "settings":{ "before_text":"We build ",
      "animated_text":"dashboards, websites, apps",
      "after_text":" for teams.",
      "animation":"typing", "header_size":"h2" } }
  ```
- **Common settings**: `before_text`, `animated_text` (comma-separated list of words to cycle), `after_text`, `animation` (`typing|highlight|rotate|clip|drop-in|drop-out|fade-in-down|stretch|float|swirl|wave|flip`), `header_size`, `highlight_animation_*` (for `highlight`), `loop` (`yes`/empty).

## price-table
- **Slug**: `price-table`
- **Tier**: Pro
- **Use when**: Pricing card with header + price + feature list + CTA. Detect: container with heading + numeric price + icon-list + button.
- **Min JSON**:
  ```jsonc
  { "widgetType":"price-table", "elType":"widget",
    "settings":{ "heading":"Pro", "sub_heading":"For growing teams",
      "currency_symbol":"$", "price":"29", "period":"per month",
      "features_list":[
        {"item_text":"10 users", "selected_icon":{"value":"fas fa-check","library":"fa-solid"}},
        {"item_text":"24/7 support", "selected_icon":{"value":"fas fa-check","library":"fa-solid"}} ],
      "button_text":"Get started", "link":{"url":"#"} } }
  ```
- **Common settings**: `heading`, `sub_heading`, `currency_symbol`, `currency_symbol_custom`, `currency_format` (`,` or `.`), `price`, `period`, `original_price` (strike-through), `features_list[]`, `button_text`, `link`, `footer_additional_info`, `ribbon_title` (e.g. "Most popular"), `ribbon_horizontal_position`.

## price-list
- **Slug**: `price-list`
- **Tier**: Pro
- **Use when**: Menu-style list (item title + description on left, price on right). Detect: vertical list of rows each with text on the left and a numeric (price) on the right.
- **Min JSON**:
  ```jsonc
  { "widgetType":"price-list", "elType":"widget",
    "settings":{ "price_list":[
        {"title":"Espresso", "item_description":"Single shot", "price":"$3.50"},
        {"title":"Cappuccino","item_description":"Espresso with steamed milk","price":"$4.50"} ] } }
  ```
- **Common settings**: `price_list[]` (each: `title`, `item_description`, `price`, `image.url`, `link.url`), `heading_tag`.

## gallery
- **Slug**: `gallery` (Pro — distinct from `image-gallery` / `basic-gallery`)
- **Tier**: Pro
- **Use when**: Image grid with advanced hover effects, multi-source galleries, or per-image links. Detect: large image grid where the design shows hover overlays OR labels per image.
- **Min JSON**:
  ```jsonc
  { "widgetType":"gallery", "elType":"widget",
    "settings":{ "gallery_type":"single",
      "galleries":[{ "_id":"g1", "gallery_title":"Work",
        "multiple_gallery":[
          {"id":11,"url":"https://.../1.jpg"},
          {"id":12,"url":"https://.../2.jpg"}] }],
      "columns":"3", "aspect_ratio":"169" } }
  ```
- **Common settings**: `gallery_type` (`single|multiple`), `galleries[]`, `columns`, `aspect_ratio` (`11|169|43|32|219`), `image_spacing`, `lazyload`, `lightbox` (`yes`/empty), `overlay_color`, `caption_title_source`.

## flip-box
- **Slug**: `flip-box`
- **Tier**: Pro
- **Use when**: Card that flips on hover to reveal back content. Detect: rare in static Figma — only when the design literally shows both front + back states adjacent OR layer name contains `flip|flipbox|hover-card`.
- **Min JSON**:
  ```jsonc
  { "widgetType":"flip-box", "elType":"widget",
    "settings":{ "graphic_element":"icon",
      "selected_icon":{"value":"fas fa-lock","library":"fa-solid"},
      "title_text_a":"Secure", "description_text_a":"Hover to learn more.",
      "title_text_b":"AES-256 at rest", "description_text_b":"All your data, encrypted by default.",
      "button_text":"Read docs", "link":{"url":"/docs"},
      "flip_effect":"flip", "flip_direction":"right" } }
  ```
- **Common settings**: `graphic_element` (`image|icon|none`), front: `title_text_a`/`description_text_a`, back: `title_text_b`/`description_text_b`/`button_text`/`link`, `flip_effect` (`flip|slide|push|zoom-in|zoom-out|fade`), `flip_direction`, `background_a_*`, `background_b_*`.

## call-to-action
- **Slug**: `call-to-action`
- **Tier**: Pro
- **Use when**: Large promotional banner — image/icon + headline + description + button, often with a ribbon. Detect: full-width section with `image + heading + text-editor + button` + optional ribbon shape.
- **Min JSON**:
  ```jsonc
  { "widgetType":"call-to-action", "elType":"widget",
    "settings":{ "graphic_element":"image",
      "image":{"url":"https://.../promo.jpg","id":33,"source":"library"},
      "title":"Save 20% this week", "description":"Limited offer for new customers.",
      "button":"Claim now", "link":{"url":"/sale"},
      "ribbon_title":"Limited", "ribbon_horizontal_position":"right",
      "skin":"classic", "layout":"image-above" } }
  ```
- **Common settings**: `graphic_element` (`image|none`), `skin` (`classic|cover`), `layout` (`image-above|image-side`), `title`, `description`, `button`, `link`, `ribbon_title`, `bg_image`, `bg_color`, `hover_animation`.

## media-carousel
- **Slug**: `media-carousel`
- **Tier**: Pro
- **Use when**: Mixed-media slider — images AND videos in the same carousel. Detect: row carousel where some children are image, some are video.
- **Min JSON**:
  ```jsonc
  { "widgetType":"media-carousel", "elType":"widget",
    "settings":{ "slides":[
        {"type":"image", "image":{"url":"https://.../1.jpg","id":1}},
        {"type":"youtube","youtube_url":"https://www.youtube.com/watch?v=…"} ],
      "slides_to_show":"1", "navigation":"both" } }
  ```
- **Common settings**: `slides[]` (each: `type` (`image|youtube|vimeo|hosted`), `image`, `youtube_url`, `vimeo_url`, `caption`), `slides_to_show`, `navigation`, `autoplay`, `infinite`, `pause_on_hover`.

## testimonial-carousel
- **Slug**: `testimonial-carousel`
- **Tier**: Pro
- **Use when**: ≥ 3 testimonials in a rotating carousel. Detect: row of cards each with quote + author + avatar, or `_figma_name` matching `testimonial.*carousel`.
- **Min JSON**:
  ```jsonc
  { "widgetType":"testimonial-carousel", "elType":"widget",
    "settings":{ "slides":[
        {"content":"Game-changer.","name":"Alex","title":"CTO","image":{"url":"https://.../a.jpg","id":1}},
        {"content":"Best decision we made.","name":"Maya","title":"Head of Product","image":{"url":"https://.../m.jpg","id":2}},
        {"content":"Saved us months.","name":"Sam","title":"Founder","image":{"url":"https://.../s.jpg","id":3}} ],
      "slides_to_show":"3", "navigation":"both", "skin":"default" } }
  ```
- **Common settings**: `slides[]` (`content`, `name`, `title`, `image`, `rating`), `slides_to_show`, `slides_to_scroll`, `navigation`, `pause_on_hover`, `autoplay`, `skin` (`default|bubble`).

## nested-carousel
- **Slug**: `nested-carousel`
- **Tier**: Pro (Elementor 3.16+)
- **Use when**: Slider where each slide is a custom Container the editor authors visually (mix of images, headings, buttons, columns). Detect: complex per-slide layouts that don't fit `slides` or `media-carousel`.
- **Min JSON**: Structurally complex — each child is an `elType: container` slide. The widget itself just owns the carousel mechanics; slide contents are normal Elementor elements.
  ```jsonc
  { "widgetType":"nested-carousel", "elType":"widget",
    "settings":{ "carousel":[{"_id":"s1"},{"_id":"s2"}],
      "slides_to_show":"1", "navigation":"both" },
    "elements":[
      { "elType":"container", "isInner":true, "settings":{...},
        "elements":[ /* slide 1 widgets */ ] },
      { "elType":"container", "isInner":true, "settings":{...},
        "elements":[ /* slide 2 widgets */ ] } ] }
  ```
- **Pitfalls**: The structural nesting (Container slides inside the widget's `elements`) is non-standard — most widgets have an empty `elements`. Elementor's nested widgets are a special case.

## loop-carousel
- **Slug**: `loop-carousel`
- **Tier**: Pro (Elementor 3.21+)
- **Use when**: Carousel of dynamically-queried posts (each slide is a Loop template applied to a post). Detect: rare in static Figma — appears in dynamic-content sections.
- **Min JSON**:
  ```jsonc
  { "widgetType":"loop-carousel", "elType":"widget",
    "settings":{ "template_id":"42", "post_type":"post",
      "posts_per_page":"5", "slides_to_show":"3" } }
  ```
- **Common settings**: `template_id` (Loop Item template post id), `post_type`, `posts_per_page`, `slides_to_show`, all standard carousel + posts-query settings.
- **Pitfalls**: Requires a Loop Item template to be created first. Until that's authored, fall back to `posts` widget.

## table-of-contents
- **Slug**: `table-of-contents`
- **Tier**: Pro
- **Use when**: Sticky sidebar listing all `h2`/`h3` headings on the page. Detect: layer named `toc|table.*content|on.this.page` OR a sticky element on the side showing heading hierarchy.
- **Min JSON**:
  ```jsonc
  { "widgetType":"table-of-contents", "elType":"widget",
    "settings":{ "title":"On this page",
      "headings_by_tags":["h2","h3"],
      "marker_view":"numbers", "min_height":{"unit":"px","size":40,"sizes":[]} } }
  ```
- **Common settings**: `title`, `headings_by_tags` (array), `exclude_headings_by_selector`, `marker_view` (`bullets|numbers`), `hierarchical_view` (`yes`/empty), `collapse_subitems` (`yes`/empty).

## countdown
- **Slug**: `countdown`
- **Tier**: Pro
- **Use when**: Event countdown timer (days/hours/minutes/seconds tiles). Detect: 4 number tiles with labels `Days|Hours|Minutes|Seconds`.
- **Min JSON**:
  ```jsonc
  { "widgetType":"countdown", "elType":"widget",
    "settings":{ "due_date":"2026-12-31 23:59",
      "label_display":"block", "show_days":"yes", "show_hours":"yes",
      "show_minutes":"yes", "show_seconds":"yes", "expire_actions":[] } }
  ```
- **Common settings**: `due_date` (UTC string `YYYY-MM-DD HH:MM`), per-unit `show_*` + `label_*`, `expire_actions` (`["redirect","hide","message","template"]`), `message_after_expire`, `expire_redirect_url`.
- **Pitfalls**: `due_date` is timezone-naive — Elementor applies the site's WP timezone. Confirm `wp_timezone()` matches the design intent.

## blockquote
- **Slug**: `blockquote`
- **Tier**: Pro
- **Use when**: Large pull-quote with attribution + share buttons. Detect: oversized quoted text + name + (optional) tweet/share icons.
- **Min JSON**:
  ```jsonc
  { "widgetType":"blockquote", "elType":"widget",
    "settings":{ "blockquote_content":"The best UX is invisible.",
      "author_name":"Don Norman", "skin":"border",
      "tweet_button_view":"icon-text", "tweet_button_label":"Tweet" } }
  ```
- **Common settings**: `blockquote_content`, `author_name`, `skin` (`none|border|quotation|boxed|clean`), `align`, `tweet_button_view` (`text|icon|icon-text|none`), `tweet_button_label`, `tweet_button_url`, `tweet_button_username`.

## template
- **Slug**: `template`
- **Tier**: Pro
- **Use when**: Reuse a saved library template inline on a page. Detect: rare — emit when `template-reuse` skill identifies a duplicate section and creates a library template.
- **Min JSON**:
  ```jsonc
  { "widgetType":"template", "elType":"widget",
    "settings":{ "template_id":"42" } }
  ```
- **Common settings**: just `template_id` (the elementor_library post id).
- **Pitfalls**: The `template-reuse` skill already uses this widget. For 100% page-level reuse use Elementor Pro's Theme Builder conditions instead.

## reviews
- **Slug**: `reviews`
- **Tier**: Pro
- **Use when**: Star-rating + review-count aggregate (e.g. "4.8 / 5 from 200 reviews"). Detect: stars + numeric rating + count.
- **Min JSON**:
  ```jsonc
  { "widgetType":"reviews", "elType":"widget",
    "settings":{ "slides":[
        {"name":"Alex","title":"CTO","content":"Solid product.","rating":"5"} ],
      "slides_to_show":"3" } }
  ```
- **Pitfalls**: Pro's `reviews` widget overlaps with `testimonial-carousel`. For most designs `testimonial-carousel` is the right pick; reserve `reviews` for product-review aggregates.

## lottie
- **Slug**: `lottie`
- **Tier**: Pro
- **Use when**: Animated illustration powered by a Lottie/Bodymovin JSON file. Detect: animated SVG sequence OR layer named `lottie|animation`.
- **Min JSON**:
  ```jsonc
  { "widgetType":"lottie", "elType":"widget",
    "settings":{ "source":"media_file",
      "source_json":{"url":"https://.../anim.json","id":99},
      "loop":"yes", "play_on_hover":"" } }
  ```
- **Common settings**: `source` (`media_file|external_url`), `source_json.url|id` for media_file, `source_external_url.url` for external, `loop`, `reverse_animation`, `play_on_hover`, `play_on_scroll`, `play_speed`.
- **Pitfalls**: Falls back to a static image when Pro is missing. Lottie JSON files must be served from the same origin or with CORS headers.

## hotspot
- **Slug**: `hotspot`
- **Tier**: Pro
- **Use when**: Image with clickable interactive markers (product feature callouts). Detect: image with overlaid small numbered/icon markers + tooltip popups.
- **Min JSON**:
  ```jsonc
  { "widgetType":"hotspots", "elType":"widget",
    "settings":{ "main_image":{"url":"https://.../product.jpg","id":50},
      "hotspots":[
        {"_id":"h1", "tooltip_text":"Pinch-to-zoom display",
         "x_position":{"unit":"%","size":35,"sizes":[]},
         "y_position":{"unit":"%","size":40,"sizes":[]}} ] } }
  ```
- **Common settings**: `main_image`, `hotspots[]` (per hotspot: `tooltip_text`, `x_position`, `y_position`, `icon`, `text`, `link.url`, `tooltip_position`).
- **Pitfalls**: Widget slug is `hotspots` (plural) — common typo.

## progress-tracker
- **Slug**: `progress-tracker`
- **Tier**: Pro
- **Use when**: Multi-step indicator (Step 1 → 2 → 3). Detect: row of numbered circles connected by lines, with step labels.
- **Min JSON**:
  ```jsonc
  { "widgetType":"progress-tracker", "elType":"widget",
    "settings":{ "steps":[
        {"step_title":"Sign up","step_icon":{"value":"fas fa-user","library":"fa-solid"}},
        {"step_title":"Configure","step_icon":{"value":"fas fa-cog","library":"fa-solid"}},
        {"step_title":"Launch","step_icon":{"value":"fas fa-rocket","library":"fa-solid"}} ],
      "active_step":"2", "view":"horizontal" } }
  ```
- **Common settings**: `steps[]` (each: `step_title`, `step_icon`, `step_link.url`), `active_step`, `view` (`horizontal|vertical`), `marker_shape` (`circle|square|rounded`), `connector_style`.

## code-highlight
- **Slug**: `code-highlight`
- **Tier**: Pro
- **Use when**: Syntax-highlighted code block in docs / developer-facing pages. Detect: monospace code block with Figma's "Code" style applied.
- **Min JSON**:
  ```jsonc
  { "widgetType":"code-highlight", "elType":"widget",
    "settings":{ "code":"const x = 1;\nconsole.log(x);",
      "language":"javascript", "show_line_numbers":"yes",
      "show_copy_button":"yes", "theme":"github-dark" } }
  ```
- **Common settings**: `code`, `language` (Prism-supported: `javascript|typescript|python|php|html|css|sass|jsx|tsx|json|yaml|bash|markdown|sql|...`), `show_line_numbers`, `show_copy_button`, `theme`.

## video-playlist
- **Slug**: `video-playlist`
- **Tier**: Pro
- **Use when**: Multiple videos with a thumbnail rail to switch between them. Detect: large video frame + sidebar/strip of video thumbnails.
- **Min JSON**:
  ```jsonc
  { "widgetType":"video-playlist", "elType":"widget",
    "settings":{ "playlist":[
        {"type":"youtube","video_url":"https://www.youtube.com/watch?v=…",
         "image_overlay":{"url":"https://.../t1.jpg","id":1},"title":"Intro"},
        {"type":"vimeo","video_url":"https://vimeo.com/…",
         "image_overlay":{"url":"https://.../t2.jpg","id":2},"title":"Demo"} ],
      "tabs_position":"right", "show_image_overlay":"yes" } }
  ```
- **Common settings**: `playlist[]` (per item: `type` (`youtube|vimeo|hosted`), `video_url`, `hosted_url.url`, `image_overlay`, `title`, `duration`), `tabs_position` (`left|right|bottom|top`), `tab_width`, `show_image_overlay`, `lazy_load`, `autoplay`.

## share-buttons
- **Slug**: `share-buttons`
- **Tier**: Pro
- **Use when**: Native styled "Share on Facebook / Twitter / WhatsApp / LinkedIn" buttons (different from `social-icons`, which just links to your profile). Detect: row of icons with share-arrow style OR "Share" labels.
- **Min JSON**:
  ```jsonc
  { "widgetType":"share-buttons", "elType":"widget",
    "settings":{ "share_buttons":[
        {"button":"twitter","text":""},
        {"button":"facebook","text":""},
        {"button":"linkedin","text":""},
        {"button":"whatsapp","text":""} ],
      "view":"icon-text", "skin":"flat", "columns":"4" } }
  ```
- **Common settings**: `share_buttons[]` (each: `button` (`facebook|twitter|google|linkedin|pinterest|reddit|whatsapp|telegram|email|skype|tumblr|vk|xing|delicious|stumbleupon|digg|pocket|...`), `text`, `visible_label`), `view` (`icon|text|icon-text`), `skin` (`flat|gradient|framed|boxed`), `columns`, `share_url` (override; default = current page).

## paypal-button
- **Slug**: `paypal-button`
- **Tier**: Pro
- **Use when**: PayPal pay button. Detect: button styled with PayPal logo OR `_figma_name` matching `paypal`.
- **Min JSON**:
  ```jsonc
  { "widgetType":"paypal-button", "elType":"widget",
    "settings":{ "transaction_type":"checkout",
      "merchant_email":"merchant@example.com",
      "price":"19.99", "currency":"USD", "item_name":"Pro plan",
      "button_layout":"vertical", "button_color":"gold" } }
  ```
- **Common settings**: `transaction_type` (`checkout|subscribe|donate`), `merchant_email`, `price`, `currency`, `item_name`, `sandbox` (`yes`/empty), `button_layout`, `button_color`, `button_size`, `button_label`.
- **Pitfalls**: Requires a configured PayPal merchant account. For Stripe instead use `stripe-button`.

## stripe-button
- **Slug**: `stripe-button`
- **Tier**: Pro
- **Use when**: Stripe Checkout pay button. Detect: button labeled "Pay" or "Subscribe" with Stripe styling.
- **Min JSON**:
  ```jsonc
  { "widgetType":"stripe-button", "elType":"widget",
    "settings":{ "stripe_button_action":"checkout",
      "price":"29.00", "currency":"USD", "item_name":"Pro plan",
      "button_label":"Pay now" } }
  ```
- **Common settings**: `stripe_button_action`, `price`, `currency`, `item_name`, `description`, `quantity`, `success_url`, `cancel_url`, `button_label`, `button_size`, `button_color`.
- **Pitfalls**: Requires Stripe publishable + secret keys set in Elementor → Site Settings → Integrations.

## facebook-page
- **Slug**: `facebook-page`
- **Tier**: Pro
- **Use when**: Embedded Facebook page widget (timeline / events / messages). Detect: layer literally named `Facebook|FB Page`.
- **Min JSON**:
  ```jsonc
  { "widgetType":"facebook-page", "elType":"widget",
    "settings":{ "url":"https://www.facebook.com/acme",
      "tabs":["timeline"], "small_header":"", "hide_cover":"",
      "show_facepile":"yes" } }
  ```
- **Common settings**: `url`, `tabs` (array of `timeline|events|messages`), `width`, `height`, `small_header`, `hide_cover`, `show_facepile`, `adapt_container_width`.

## facebook-button
- **Slug**: `facebook-button`
- **Tier**: Pro
- **Use when**: Facebook Like / Recommend / Share / Follow button. Detect: small FB-branded button.
- **Min JSON**:
  ```jsonc
  { "widgetType":"facebook-button", "elType":"widget",
    "settings":{ "type":"like", "url":"https://example.com",
      "action":"like", "show_share":"yes", "show_faces":"" } }
  ```

## facebook-embed
- **Slug**: `facebook-embed`
- **Tier**: Pro
- **Use when**: Embed of a single Facebook post or video.
- **Min JSON**:
  ```jsonc
  { "widgetType":"facebook-embed", "elType":"widget",
    "settings":{ "type":"post", "url":"https://www.facebook.com/.../posts/123",
      "show_text":"yes" } }
  ```

## facebook-comments
- **Slug**: `facebook-comments`
- **Tier**: Pro
- **Use when**: Facebook-powered comments box at the bottom of a blog post.
- **Min JSON**:
  ```jsonc
  { "widgetType":"facebook-comments", "elType":"widget",
    "settings":{ "url":"", "num_posts":"5", "order_by":"social" } }
  ```
- **Pitfalls**: `url` empty = use current page URL.

---

## Notes on Pro detection

`scripts/wp_client.py::bridge_health()` returns `health.elementor_pro` —
a version string when Pro is active, `null` otherwise. The agent must
gate every entry in this file on that value. The Free fallback column
in `SKILL.md` is the substitute table.

## Notes on widget version compatibility

A few widgets above require minimum Elementor versions:
- `link-in-bio` — Elementor 3.21+
- `mega-menu` — Pro 3.18+
- `off-canvas` — Pro 3.21+
- `nested-carousel` — Pro 3.16+
- `loop-carousel` — Pro 3.21+

`health.elementor` reports the installed version; compare before emitting
these widgets, and fall back to the substitute when the version is older.
