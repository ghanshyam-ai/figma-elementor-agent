"""
Prompt-driven template generator.

Some templates have no Figma source — search results, 404 pages,
maintenance / coming-soon screens, simple popups. The user provides a
short prompt; the orchestrator reasons over it, and this script
materialises the resulting structure as an Elementor library template.

Public entry points:
    build_404(spec)       → Elementor tree for a 404 template
    build_search(spec)    → Elementor tree for search results
    build_maintenance(spec) → coming-soon / under-construction
    build_popup(spec)     → simple promo / newsletter popup

CLI:
    python3 scripts/prompt_template.py --type 404 --spec-file spec.json
        → posts the template via the bridge and prints the edit URL.

The spec is an ordinary dict the agent fills in based on the user's
prompt. All keys are optional with sensible defaults; the spec is
meant to read like "here's what the user asked for in shorthand"
rather than a full Elementor schema dump.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))


# ---------------------------------------------------------------------------
# Defaults — kept conservative so a bare-minimum spec still produces a
# usable template
# ---------------------------------------------------------------------------

DEFAULT_404 = {
    "headline": "404",
    "title": "We can't find that page",
    "body": "The link may be broken, or the page may have been moved.",
    "primary_cta": {"text": "Back to home", "url": "/"},
    "secondary_cta": {"text": "Contact support", "url": "/contact/"},
    "show_search": True,
}

DEFAULT_SEARCH = {
    "title": "Search results",
    "search_box_placeholder": "Search the site…",
    "show_recent_posts": True,
    "recent_posts_limit": 5,
    "no_results_text": "No results matched your search. Try different keywords.",
}

DEFAULT_MAINTENANCE = {
    "title": "We'll be back soon",
    "body": "We're polishing things up and will be online again shortly.",
    "show_countdown": False,
    "show_subscribe": True,
}

DEFAULT_POPUP = {
    "title": "Stay in the loop",
    "body": "Get product updates straight to your inbox.",
    "primary_cta": {"text": "Subscribe", "url": "#"},
    "show_dismiss": True,
}


# ---------------------------------------------------------------------------
# Builders — each returns a list[dict] (Elementor `_elementor_data` shape)
# ---------------------------------------------------------------------------

def build_404(spec: dict) -> list[dict]:
    s = {**DEFAULT_404, **(spec or {})}
    elements = [
        _heading(s["headline"], "h1", align="center", extra_class="dt-radius-md"),
        _heading(s["title"], "h2", align="center"),
        _text(s["body"], align="center"),
    ]
    cta_row: list[dict] = []
    if s.get("primary_cta"):
        cta_row.append(_button(s["primary_cta"]["text"], s["primary_cta"].get("url", "#"), style="primary"))
    if s.get("secondary_cta"):
        cta_row.append(_button(s["secondary_cta"]["text"], s["secondary_cta"].get("url", "#"), style="secondary"))
    if cta_row:
        elements.append(_row_container(cta_row, gap=16))
    if s.get("show_search"):
        elements.append(_search_widget(s.get("search_box_placeholder", "Search the site…")))
    return [_section_container(elements, min_height=560, padding=80, align="center")]


def build_search(spec: dict) -> list[dict]:
    s = {**DEFAULT_SEARCH, **(spec or {})}
    elements = [
        _heading(s["title"], "h1", align="left"),
        _search_widget(s.get("search_box_placeholder", "Search the site…")),
        # Inline shortcode renders the actual search-results loop.
        _shortcode("[wp_search_results]"),
    ]
    if s.get("show_recent_posts"):
        elements.append(_heading("Recent posts", "h3"))
        elements.append({
            "id": "rcntps",
            "elType": "widget",
            "widgetType": "wp-widget-recent-posts",
            "settings": {"title": "", "number": s.get("recent_posts_limit", 5)},
            "elements": [],
        })
    return [_section_container(elements, padding=80)]


def build_maintenance(spec: dict) -> list[dict]:
    s = {**DEFAULT_MAINTENANCE, **(spec or {})}
    elements = [
        _heading(s["title"], "h1", align="center"),
        _text(s["body"], align="center"),
    ]
    if s.get("show_subscribe"):
        elements.append(_subscribe_form())
    return [_section_container(elements, min_height=560, padding=120, align="center")]


def build_popup(spec: dict) -> list[dict]:
    s = {**DEFAULT_POPUP, **(spec or {})}
    elements = [
        _heading(s["title"], "h2", align="center"),
        _text(s["body"], align="center"),
    ]
    if s.get("primary_cta"):
        elements.append(_button(s["primary_cta"]["text"], s["primary_cta"].get("url", "#"), style="primary"))
    if s.get("show_dismiss"):
        elements.append(_button("No thanks", "#", style="secondary"))
    return [_section_container(elements, padding=40, align="center", min_height=320)]


# ---------------------------------------------------------------------------
# Element factories — each returns a dict that Elementor's iterate_data
# accepts directly. ids are short + meaningful so re-runs produce stable
# output.
# ---------------------------------------------------------------------------

def _section_container(children: list, *, min_height: int = 0, padding: int = 60,
                       align: str = "left") -> dict:
    settings: dict[str, Any] = {
        "content_width": "boxed",
        "flex_direction": "column",
        "flex_gap": {"unit": "px", "size": 24, "sizes": []},
        "padding": {"unit": "px", "top": str(padding), "right": str(padding), "bottom": str(padding), "left": str(padding), "isLinked": True},
    }
    if min_height:
        settings["min_height"] = {"unit": "px", "size": min_height, "sizes": []}
        settings["flex_justify_content"] = "center"
    if align == "center":
        settings["flex_align_items"] = "center"
    return {
        "id": "scsec1",
        "elType": "container",
        "isInner": False,
        "settings": settings,
        "elements": children,
    }


def _row_container(children: list, *, gap: int = 16) -> dict:
    return {
        "id": "scrow1",
        "elType": "container",
        "isInner": True,
        "settings": {
            "flex_direction": "row",
            "flex_gap": {"unit": "px", "size": gap, "sizes": []},
            "flex_justify_content": "center",
            "flex_align_items": "center",
            "flex_wrap": "wrap",
        },
        "elements": children,
    }


def _heading(title: str, tag: str = "h2", align: str = "left", extra_class: str = "") -> dict:
    s: dict[str, Any] = {
        "title": title,
        "header_size": tag,
        "align": align,
    }
    if extra_class:
        s["css_classes"] = extra_class
    return {"id": "phead" + tag, "elType": "widget", "widgetType": "heading", "settings": s, "elements": []}


def _text(body: str, align: str = "left") -> dict:
    html = body if body.startswith("<") else f"<p>{body}</p>"
    return {
        "id": "ptext1",
        "elType": "widget",
        "widgetType": "text-editor",
        "settings": {"editor": html, "align": align},
        "elements": [],
    }


def _button(text: str, url: str, style: str = "primary") -> dict:
    return {
        "id": f"pbtn{style[:3]}",
        "elType": "widget",
        "widgetType": "button",
        "settings": {
            "text": text,
            "link": {"url": url, "is_external": "", "nofollow": ""},
            "size": "md",
            "css_classes": f"prompt-cta-{style}",
        },
        "elements": [],
    }


def _shortcode(code: str) -> dict:
    return {
        "id": "pshort",
        "elType": "widget",
        "widgetType": "shortcode",
        "settings": {"shortcode": code},
        "elements": [],
    }


def _search_widget(placeholder: str = "Search…") -> dict:
    """Core WP search form via shortcode — works on Free Elementor."""
    return _shortcode("[wp_search_form]")


def _subscribe_form() -> dict:
    """Conservative fallback: invite GF if installed, otherwise hint."""
    return {
        "id": "psubsc",
        "elType": "widget",
        "widgetType": "shortcode",
        "settings": {"shortcode": "[gravityform id=\"newsletter\" title=\"false\" description=\"false\"]"},
        "elements": [],
    }


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

BUILDERS = {
    "404": build_404,
    "search": build_search,
    "maintenance": build_maintenance,
    "popup": build_popup,
}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--type", required=True, choices=list(BUILDERS.keys()),
                    help="Template kind to generate.")
    ap.add_argument("--spec", help="JSON object with the structured fields.")
    ap.add_argument("--spec-file", help="Path to a JSON file containing the spec.")
    ap.add_argument("--prompt", help="Original user prompt (stored as a note).")
    ap.add_argument("--title", help="Override the resulting template title.")
    ap.add_argument("--config", default=str(ROOT / "project-config.json"))
    ap.add_argument("--dry-run", action="store_true",
                    help="Print the Elementor tree to stdout and exit; don't post.")
    args = ap.parse_args()

    spec: dict[str, Any] = {}
    if args.spec_file:
        spec = json.loads(Path(args.spec_file).read_text())
    elif args.spec:
        spec = json.loads(args.spec)
    if args.prompt:
        spec.setdefault("_prompt", args.prompt)

    builder = BUILDERS[args.type]
    tree = builder(spec)

    if args.dry_run:
        json.dump({"type": args.type, "tree": tree}, sys.stdout, indent=2)
        sys.stdout.write("\n")
        return 0

    from wp_client import WPClient, load_config
    cfg = load_config(args.config)
    password = cfg.get("wp_password") or cfg.get("wp_app_password")
    client = WPClient(cfg["wp_url"], cfg["wp_user"], password)
    client.login()

    title = args.title or spec.get("title") or f"{args.type.title()} (prompt)"
    template_kind = "404" if args.type == "404" else (
        "search" if args.type == "search" else
        "popup" if args.type in ("popup", "maintenance") else
        "section"
    )
    site_slug = (cfg.get("page_slug") or "site").split("/")[0]
    template_slug = f"{site_slug}--prompt--{args.type}"
    r = client.create_template(
        template_type=template_kind,
        title=title,
        elementor_data=tree,
        slug=template_slug,
    )
    verb = "Updated" if r.get("updated") else "Created"
    print(f"✓ {verb} {args.type} template id={r['id']} slug={template_slug}")
    print(f"  edit: {r['edit_url']}")
    if not r.get("pro_active") and args.type in ("404", "search"):
        print("  ⚠ Elementor Pro not detected — auto-assignment unavailable.")
        print("    Apply the template manually in wp-admin → Templates → Theme Builder → Other.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
