---
name: optimization
description: Post-mapping passes that minimize the imported Elementor tree — collapse single-child wrappers, cap nesting depth, replace HTML widgets, swap matching containers for icon-list / accordion. Goal is editable, lean output rather than just visually-accurate.
---

# Elementor optimization

Lives in `scripts/optimize.py`. Runs after the token resolver (so
collapses don't lose globals) and before architecture routing (so the
router operates on a clean tree).

## Why this exists

Visual accuracy is one half of "good import." The other half is **what
the page looks like in the editor**. A page with 12 nested containers
and 60 inline colour values renders correctly but is hostile to edit.
Each pass below trades a tiny amount of visual fidelity for materially
better editability.

## Passes

### 1. `replace_html_widgets`

Any `widgetType: "html"` is rewritten to `text-editor`. HTML widgets
aren't editable in the visual builder — every change requires a
developer. text-editor accepts the same paragraph + inline markup, is
visually identical, and can be edited by anyone.

If the HTML body has no block-level tag, it's wrapped in `<p>` so
text-editor's TinyMCE doesn't strip leading whitespace.

### 2. `enforce_widget_preferences`

Reads `preferredWidget` from each top-level ai-layout section. Two
unambiguous swaps are wired:

* **icon-list** — when the section is a stack of containers, each with
  one icon + one heading/text-editor, swap the entire section for an
  Elementor `icon-list` widget. One widget instead of (1 container
  + N×3 widgets).
* **accordion** — when the section is a stack of containers, each with
  one heading + one text-editor (the question / answer pattern),
  swap for an `accordion` widget.

Anything ambiguous is left alone — the optimization pass becomes a
no-op for that section. Better to ship a verbose tree than swap
to a widget that loses content.

### 3. `collapse_single_child_containers`

Walks the tree; for each container whose only child is another
container and whose own settings carry no LAYOUT_BEARING_KEYS
(`background_*`, `border_*`, `box_shadow_*`, `min_height`, `width`,
`content_width`), the parent is replaced with the child.

Loops until a pass yields zero collapses (max 4 iterations) so
deep wrapper chains flatten.

### 4. `cap_nesting_depth`

Walks the tree; any container deeper than `max_depth` (default 4) has
its container children replaced with their widget descendants only —
the structural shells are dropped, the content is preserved. This is
a conservative version of "flatten the deep tail."

Why 4? Elementor's flexbox has no hard limit, but >4 levels of
flex-in-flex-in-flex is where layouts start producing surprising
overflow + alignment bugs in the editor.

### 5. `resolve_global_tokens` (the big one)

See [`global-tokens/SKILL.md`](../global-tokens/SKILL.md) for the
detailed walk-through. Replaces inline colour + typography values
with `__globals__` references that point at the kit slugs we just
wrote.

## Order matters

Token resolver runs FIRST. If we collapsed first, container
backgrounds with global colours could disappear into a parent that
has its own background, and we'd lose the global reference. If we
ran HTML replacement after collapse, we might collapse a container
holding only an HTML widget, then can't find it for replacement.

The orchestrator's order is:

```
resolve_global_tokens     ← reads kit slugs, rewrites widgets
replace_html_widgets      ← html → text-editor (independent)
enforce_widget_preferences← icon-list / accordion swap
collapse_single_child_containers
cap_nesting_depth
```

## Stats

Each pass returns a count. The orchestrator logs them:

```
✓ Optimize: 14 colors→globals, 5 typo→globals,
  3 containers collapsed, 0 hoisted (depth≤4),
  2 widgets swapped, 1 html→text-editor
```

## Idempotency

All passes are designed to be safe to re-run. Running the orchestrator
on an already-optimized tree should produce zero new changes.
