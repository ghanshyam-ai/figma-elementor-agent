---
name: form-intelligence
description: Detects form sections in Figma exports, creates real Gravity Forms via the bridge plugin, and replaces the form node in the Elementor tree with a `[gravityform]` shortcode. Falls back to leaving the visual mock-up in place when Gravity Forms isn't installed.
---

# Form intelligence

Lives in `scripts/form_intelligence.py`. Runs after the optimization
passes and before architecture routing.

## Why hand off to Gravity Forms

Rebuilding a form's visual styling in Elementor rarely matches what
Gravity Forms outputs. Submission handling, validation, spam filtering,
and CRM integrations are non-trivial — Gravity Forms handles them
correctly out of the box. The agent's job is to **detect, map, and
delegate**, not re-implement form rendering.

## Detection

A section is a form candidate when **either**:
* `role == "form"` or `sectionPurpose == "lead-capture"` on the
  ai-layout section, OR
* a structural child of the section has `role == "form"` (handles
  the common case of "Contact Us" hero with form embedded inside).

For each candidate, descendants with `role == "input"` become form
fields. The first button in the section's `content.buttons` becomes
the submit button label.

## Field-type inference

Field labels run through regex rules to map to Gravity Forms types:

| Regex on label (case-insensitive) | GF type |
|-----------------------------------|---------|
| `email` | `email` |
| `phone\|mobile\|tel` | `phone` |
| `website\|url\|domain` | `website` |
| `message\|comment\|details\|enquiry\|notes` | `textarea` |
| `address\|street\|city\|state\|zip\|postcode` | `address` |
| `full[- ]?name\|name` | `name` |
| `number\|amount\|qty\|quantity\|age` | `number` |
| `company\|organisation\|business` | `text` |
| `subject\|topic` | `text` |
| _(anything else)_ | `text` |

`isRequired` is auto-set on email fields; everything else defaults
to optional. The developer can tighten requirements in
`wp-admin → Forms`.

## Bridge endpoints

The bridge plugin (`figma-importer-bridge.php`) adds:
* **GET** `/figma-importer/v1/forms/gravity` — lists existing forms by
  title (the agent skips creation if a form with the same title
  already exists, so re-running doesn't duplicate).
* **POST** `/figma-importer/v1/forms/gravity` — creates a form from a
  compact spec via Gravity Forms' `GFAPI::add_form()`.

`health` reports `gravity_forms` so the agent knows whether to even
attempt detection.

## Replacement

A successful form creation replaces the entire form section with:

```jsonc
{
  "elType": "container", "isInner": false,
  "settings": {
    "content_width": "boxed",
    "_form_source": "gravity-forms",
    "_form_ai_node_id": "234:11"
  },
  "elements": [{
    "elType": "widget", "widgetType": "shortcode",
    "settings": { "shortcode": "[gravityform id=\"3\" title=\"false\" description=\"false\"]" }
  }]
}
```

Gravity Forms renders the form server-side; styling inherits from the
GF theme settings. The developer can later wrap the shortcode in a
narrower container or add Elementor padding/background as wanted.

## Fallback (Gravity Forms not active)

When `health.gravity_forms` is null, the agent leaves the original
form visual mock-up in the page tree and surfaces a one-line note:

```
(form section(s) detected; install Gravity Forms to convert them automatically)
```

The mock-up renders with non-functional inputs — visually correct,
not submittable. Re-running the orchestrator after installing GF
will detect and convert.

## Idempotency

`list_gravity_forms()` is consulted before creation. If a form with
the candidate's title already exists, the agent reuses the existing
form id rather than creating a duplicate. Editing the form in
`wp-admin → Forms` and re-running the orchestrator preserves your
changes.

## Disabling

```bash
python3 scripts/import_elementor.py --skip-forms
```

The agent will print a hint that forms were detected but skipped, so
you don't lose the signal.

## Why not the Elementor Form widget (Pro)?

Two reasons:
1. The Elementor Form widget is Pro-only; many of our targets won't
   have Pro. Gravity Forms works on Free WP installs.
2. Even with Pro, re-implementing GF's spam protection / multi-step
   logic / addon ecosystem is a non-starter. Better to delegate.

If a project specifically wants Elementor Form (e.g. tight design
system control), the developer can replace the shortcode widget
manually after import.
