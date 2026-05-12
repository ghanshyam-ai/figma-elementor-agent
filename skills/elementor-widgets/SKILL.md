---
name: elementor-widgets
description: Complete reference for all Elementor Free Basic + Pro general widgets — what each does, when to pick it from a Figma section, the minimum JSON to emit, the most-used settings, and Pro-vs-Free pitfalls. Consult this skill BEFORE the agent invents a generic image/text fallback for any section it can't classify with the other skills.
---

# Elementor widgets catalog

Two reference files load with this skill:

- **`basic-widgets.md`** — 32 Free Basic widgets (heading, text-editor, image, button, image-box, icon-box, icon-list, accordion, tabs, image-carousel, video, social-icons, counter, progress-bar, star-rating, testimonial, divider, spacer, html, alert, google-maps, sound-cloud, basic-gallery, menu-anchor, sidebar, shortcode, text-path, container, link-in-bio, toggle, icon, inner-section).
- **`pro-widgets.md`** — 35 Pro general widgets (form, posts, portfolio, slides, nav-menu, mega-menu, off-canvas, login, animated-headline, price-table, price-list, gallery, flip-box, call-to-action, media-carousel, testimonial-carousel, nested-carousel, loop-carousel, table-of-contents, countdown, blockquote, template, reviews, lottie, hotspot, progress-tracker, code-highlight, video-playlist, share-buttons, paypal-button, stripe-button, facebook-page, facebook-button, facebook-embed, facebook-comments).

> **Out of scope for now** (separate skills): the 15 Theme-Elements widgets
> (Post Title, Post Excerpt, Author Box, Featured Image, Site Logo, Search
> Bar, Loop Grid, etc.) and the 24 WooCommerce widgets. Add them when the
> agent starts building single-post / archive / shop templates.

## Why this skill exists

The previous agent versions documented only 5 widgets in the data-schema
skill and hard-coded ~17 detectors in `optimize.py`. When a Figma section
didn't match one of those detectors, the agent fell back to a flat
image + text-editor wall — destroying ~30% of structural accuracy on
real-world designs. This skill is the **deliberate-widget-selection
dictionary** Claude consults before authoring a section.

## How to use this skill

When the agent (or a Claude-as-Author sub-agent dispatched by
`scripts/claude_review.py`) needs to pick a widget for a Figma section:

1. **Run the detection table below** — match the visible pattern in the
   section's expected crop / ai-layout subtree against the "Section
   pattern" column. Pick the first row that matches.
2. **Open the referenced widget's detail block** in `basic-widgets.md`
   or `pro-widgets.md` and read its `Min JSON`. Emit those keys at
   minimum.
3. **Add the section's actual content** (headings, body text, image
   URLs, button labels, link targets) into the emitted JSON.
4. **Layer in `Common settings`** only where the design demands it.
   Resist the urge to set every key — Elementor's defaults are usually
   correct, and our pipeline binds colors / typography via
   `__globals__` afterward.

## Detection table — Figma pattern → widget

Run top to bottom; first match wins. "Pro:" rows fall back to a Free
substitute when Elementor Pro isn't active (the agent reads
`health.elementor_pro` from `wp-setup`).

| Section pattern in Figma | Widget | Tier | Fallback when no Pro |
|---|---|---|---|
| Single line, 24-100px font, sits at top of a section | `heading` | Free | — |
| Paragraph block, multiple sentences | `text-editor` | Free | — |
| Standalone image, no surrounding label | `image` | Free | — |
| Image + heading + paragraph inside one card | `image-box` | Free | — |
| Icon + heading + paragraph inside one card | `icon-box` | Free | — |
| Vertical list of icon + label rows (≥2 rows, same icon shape) | `icon-list` | Free | — |
| Solid-fill button shape (24-96px tall, ≤480px wide, has text or arrow) | `button` | Free | — |
| Number + suffix + small label (e.g. "10K+ users") | `counter` | Free | — |
| Horizontal/vertical bar with % label | `progress` | Free | — |
| Row of 3-7 small icons matching social brand glyphs OR linking to social URLs | `social-icons` | Free | — |
| Row of 5 stars or star pattern + numeric label | `star-rating` | Free | — |
| Single quote + author name + (optional) avatar | `testimonial` | Free | — |
| Horizontal/vertical line, < 4px thick, no children | `divider` | Free | — |
| Empty vertical space ≥ 8px, no fill | `spacer` | Free | — |
| Tab strip (row of N buttons) + N panels below | `tabs` | Free | — |
| ≥ 2 expandable rows, each with heading + body | `accordion` | Free | — |
| Single expandable Q+A pair | `toggle` | Free | — |
| Embedded YouTube/Vimeo/MP4 reference OR poster image + play overlay | `video` | Free | — |
| Row of ≥ 3 images with `nowrap` flex OR carousel-named container | `image-carousel` | Free | — |
| Grid/masonry of images (≥4, lightbox-style) | `basic-gallery` | Free | — |
| Map pin / map illustration with address | `google-maps` | Free | — |
| Static FA icon or SVG icon, no text | `icon` | Free | — |
| Custom HTML/embed block (rare in Figma — only via `_figma_html`) | `html` | Free | — |
| `[shortcode]` literal in text | `shortcode` | Free | — |
| Notice / banner with icon + dismissable styling | `alert` | Free | — |
| Audio waveform reference | `sound-cloud` | Free | — |
| Curved text along path | `text-path` | Free | — |
| Card with multiple inline links (creator bio link tree) | `link-in-bio` | Free | — |
| Layout wrapper, no widget content of its own | `container` (elType, not widget) | Free | — |
| **Pro:** Form fields (≥ 2 inputs + submit) | `form` | Pro | Gravity Forms via `form-intelligence` skill |
| **Pro:** Blog grid / "Latest Posts" pattern | `posts` | Pro | `wp-widget-recent-posts` (basic substitute) |
| **Pro:** Image grid filterable by category | `portfolio` | Pro | `basic-gallery` |
| **Pro:** Full-bleed hero rotator (≥ 2 slides each with heading+button) | `slides` | Pro | `image-carousel` (text overlay lost) |
| **Pro:** Site nav menu (row of nav links in header) | `nav-menu` | Pro | `wp-widget-nav_menu` |
| **Pro:** Mega menu with dropdown columns | `mega-menu` | Pro | `nav-menu` collapsed |
| **Pro:** Hidden side panel / hamburger drawer | `off-canvas` | Pro | inline `nav-menu` |
| **Pro:** Login / register form | `login` | Pro | `wp-login.php` link |
| **Pro:** Rotating-word heading ("We build [X]") | `animated-headline` | Pro | `heading` (static) |
| **Pro:** Pricing column with header, price, features list, CTA | `price-table` | Pro | `image-box` |
| **Pro:** Menu / spec list with price column on right | `price-list` | Pro | `icon-list` (price as suffix) |
| **Pro:** Advanced gallery with hover effects / lightbox / lazy-load | `gallery` (Pro) | Pro | `basic-gallery` |
| **Pro:** Card that flips to reveal back content on hover | `flip-box` | Pro | `image-box` (no flip) |
| **Pro:** Large promotional banner: image + headline + CTA + ribbon | `call-to-action` | Pro | `image-box` |
| **Pro:** Carousel of mixed media (image + video) | `media-carousel` | Pro | `image-carousel` |
| **Pro:** Carousel of testimonials (≥ 3, with quote+avatar+name) | `testimonial-carousel` | Pro | row of `testimonial` |
| **Pro:** Carousel with inner Container per slide (custom slide layout) | `nested-carousel` | Pro | `image-carousel` |
| **Pro:** Loop-driven carousel of queried posts | `loop-carousel` | Pro | none (drop) |
| **Pro:** Sticky TOC of headings on the side | `table-of-contents` | Pro | manual anchor list |
| **Pro:** Countdown timer (event date) | `countdown` | Pro | `heading` (date text) |
| **Pro:** Large pull-quote with attribution | `blockquote` | Pro | `text-editor` styled |
| **Pro:** "Insert template here" reference | `template` | Pro | manual paste |
| **Pro:** Star + count review aggregate | `reviews` | Pro | `star-rating` + `text-editor` |
| **Pro:** Lottie / Bodymovin JSON animation | `lottie` | Pro | static image |
| **Pro:** Image with clickable interactive hotspots | `hotspot` | Pro | `image` + overlay containers |
| **Pro:** Multi-step progress indicator (1 → 2 → 3) | `progress-tracker` | Pro | `progress` × N |
| **Pro:** Code snippet with syntax highlighting | `code-highlight` | Pro | `html` block |
| **Pro:** Video playlist with thumbnail rail | `video-playlist` | Pro | first video only |
| **Pro:** Row of "Share on X / Facebook / LinkedIn" with native styling | `share-buttons` | Pro | `social-icons` |
| **Pro:** PayPal pay button | `paypal-button` | Pro | external link |
| **Pro:** Stripe pay button | `stripe-button` | Pro | external link |
| **Pro:** Embedded Facebook page | `facebook-page` | Pro | `html` iframe |
| **Pro:** Facebook share / like button | `facebook-button` | Pro | `social-icons` |
| **Pro:** Embed of a Facebook post | `facebook-embed` | Pro | `html` iframe |
| **Pro:** Facebook Comments box | `facebook-comments` | Pro | `html` iframe |

## Choosing the right widget — rules

1. **Prefer compound widgets over container + leaves**. If a section is
   image + heading + paragraph, emit `image-box`, not a container with
   three children. Compound widgets get correct semantic HTML, hover
   states, and responsive defaults that we'd otherwise have to author.
2. **Prefer Pro widgets when Pro is detected**. The agent reads
   `health.elementor_pro` at startup. When Pro is active, use `nav-menu`
   over `wp-widget-nav_menu`, `form` over Gravity Forms shortcode,
   `posts` over `wp-widget-recent-posts`, `slides` over hand-built
   carousel containers.
3. **Don't manufacture rich data**. If the section is a `posts` widget
   but the Figma export shows hardcoded post titles, emit `posts` with
   a query for the latest 3 posts — let WP fill in real content. Don't
   inline the placeholder titles as static text.
4. **Don't pick a Pro widget when Pro is missing**. The Free fallback
   column above is the substitution table. The page will not be
   pixel-identical but at least it will be editable.
5. **Skip the widget entirely** when:
   - The Figma element is purely decorative (gradient blobs, divider
     dots) — absorb into the parent container's background.
   - The element is `_visible: false` or opacity 0 (already filtered
     by `section_finder.filter_hidden`).
   - The element is a baked screenshot of a section (low-confidence
     fallback) — leave it as an `image` widget.

## Container is an elType, not a widget

Every entry above with `widgetType` lives inside a `container` (with
flex layout) or — for legacy templates — a `section > column` tree.
The agent's `auto_layout_inference.py` already promotes absolutely-
positioned children into proper flex containers. When a Figma frame
is just a layout wrapper with no visual identity of its own, emit a
plain `container` and let its children carry the widgets.

## Sources for this catalog

- Public Elementor widget inventory (https://elementor.com/widgets/) —
  the 117-widget canonical list, retrieved 2026-05-12.
- Elementor PHP widget source (`elementor/includes/widgets/*.php` and
  `elementor-pro/modules/*/widgets/*.php`) — the authoritative settings
  key reference. JSON examples here follow that source's `_register_*`
  control declarations.
- The agent's own converters in `scripts/optimize.py` (verified shapes
  for the 17 widgets the converter pipeline already produces).
- The Elementor Document API used by `scripts/wp-bridge/figma-importer-
  bridge.php::figma_importer_iterate_data()` — every widget's
  `on_import` hook runs automatically, so the agent doesn't need to
  emit every key Elementor would derive (e.g. image `srcset`, button
  hover defaults).

When in doubt, the `Min JSON` block in `basic-widgets.md` /
`pro-widgets.md` is the smallest payload that produces a working
widget; the bridge's `on_import` hook fills the rest.
