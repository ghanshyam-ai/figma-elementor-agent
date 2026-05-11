---
name: dynamic-content
description: Detects Figma sections that should render as a dynamic WordPress query (blog grids, archive lists) and substitutes a Posts widget instead of N hard-coded card containers. Falls back to the core Recent Posts widget when Elementor Pro isn't active.
---

# Dynamic content detection

Lives in `scripts/dynamic_content.py`. Runs after the optimization
passes and confidence fallbacks but before architecture routing.

## What it solves

A "Recent Articles" section in Figma renders as 3 cards, each with
an image, title, date, and excerpt. If the agent ships those as
3 hard-coded containers, the live page shows the same 3 stories
forever — adding a new blog post doesn't update the page.

The fix is a Posts widget that queries the WordPress post type and
renders cards dynamically. The blog post added next week appears
automatically.

## Detection: any 2 of 3 signals

### Signal 1 — sectionPurpose + cards

`ai-layout.json` says `sectionPurpose: "feature-grid"` and the section
has at least 3 children whose `role == "card"`.

### Signal 2 — structural blog shape

Each top-level child container has:
* an image widget
* a heading widget
* a text-editor widget whose stripped content is ≥ 60 chars (excerpt-ish)

…and there are at least 3 such cards.

### Signal 3 — layer name

Section name matches `\b(blog|posts?|articles?|news|stories|insights)\b`.

If 2 of 3 fire, the section is a candidate.

## Replacement

The original section's container is replaced with a fresh container
holding a single `posts` widget (Pro) or `wp-widget-recent-posts`
(Free):

```jsonc
{
  "elType": "container", "isInner": false,
  "settings": { "_dynamic_section": true, "_dynamic_reason": "..." },
  "elements": [{
    "elType": "widget",
    "widgetType": "posts",            // or wp-widget-recent-posts
    "settings": {
      "posts_per_page": 3,
      "posts_post_type": "post",
      "posts_columns": 3,
      "show_image": "yes", "show_title": "yes",
      "show_excerpt": "yes", "excerpt_length": 25
    }
  }]
}
```

The `_design_reference_id` setting points back at the original Figma
node id so designers can cross-reference the intended visual.

## Why heuristic not rule-based

A single signal triggers too many false positives:
* "feature-grid" alone catches every 3-card section, including
  "How it works" steps and "Pricing tiers" — neither should become
  a Posts widget.
* Structural blog shape alone catches testimonial sections (image +
  heading + long quote).
* Name "blog" alone catches "Blog header" hero sections.

Two of three is the inflection point where the cost of a false
positive (designer manually replaces back) is balanced against
the cost of a false negative (page never updates with new posts).

## Disabling

There's no dedicated CLI flag because dynamic detection is
opportunistic — if no candidates fire, nothing happens. To force
opt-out for a specific section, rename its layer in Figma to avoid
the regex (`Featured Articles` → `Featured Insights Showcase`) or
switch its sectionPurpose intent.

## Pro vs Free

The bridge `/health` reports `elementor_pro`:
* **Pro present** → `posts` widget (full styling, columns, ordering).
* **Pro absent** → `wp-widget-recent-posts` (core WordPress widget,
  basic but functional).

Both can be replaced post-import with the developer's preferred query
solution.
