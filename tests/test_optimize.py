"""Coverage for the post-mapping optimization passes."""
import pytest


def test_resolve_global_tokens_against_plugin_sample(sample_content, kit_settings):
    """The plugin's sample hero has 7 inline colour values + 2 typography
    blocks the resolver should rewrite to global references."""
    from optimize import resolve_global_tokens
    stats = resolve_global_tokens(sample_content, kit_settings)
    assert stats["colors"] >= 5, stats
    assert stats["typography"] >= 1, stats

    # Confirm the heading widget got linked
    heading = sample_content[0]["elements"][0]
    assert heading["widgetType"] == "heading"
    assert heading["settings"]["typography_typography"] == "globals"
    assert heading["settings"]["__globals__"]["typography_typography"].startswith(
        "globals/typography?id="
    )


def test_resolve_global_tokens_idempotent(sample_content, kit_settings):
    """Running the resolver twice produces zero new changes."""
    from optimize import resolve_global_tokens
    resolve_global_tokens(sample_content, kit_settings)
    second = resolve_global_tokens(sample_content, kit_settings)
    assert second["colors"] == 0
    assert second["typography"] == 0


def test_replace_html_widgets_wraps_bare_text():
    from optimize import replace_html_widgets
    content = [{
        "id": "c1", "elType": "container", "settings": {}, "elements": [
            {"id": "w1", "elType": "widget", "widgetType": "html",
             "settings": {"html": "Hello world"}, "elements": []},
        ],
    }]
    n = replace_html_widgets(content)
    assert n == 1
    swapped = content[0]["elements"][0]
    assert swapped["widgetType"] == "text-editor"
    assert swapped["settings"]["editor"] == "<p>Hello world</p>"


def test_collapse_single_child_containers_flattens_chain():
    """A → A → A (each with no layout settings) collapses to one container."""
    from optimize import collapse_single_child_containers
    leaf_widget = {"id": "w", "elType": "widget", "widgetType": "spacer", "settings": {}, "elements": []}
    inner = {"id": "c3", "elType": "container", "settings": {}, "elements": [leaf_widget]}
    middle = {"id": "c2", "elType": "container", "settings": {}, "elements": [inner]}
    outer = {"id": "c1", "elType": "container", "settings": {}, "elements": [middle]}
    content = [outer]
    n = collapse_single_child_containers(content)
    assert n >= 2
    # Two collapses should leave one container with the spacer as its child.
    assert content[0]["elements"][0] is leaf_widget


def test_collapse_preserves_layout_bearing_parents():
    """A container with a background colour shouldn't be collapsed away."""
    from optimize import collapse_single_child_containers
    inner = {"id": "c2", "elType": "container", "settings": {}, "elements": []}
    outer = {"id": "c1", "elType": "container",
             "settings": {"background_background": "classic", "background_color": "#fff"},
             "elements": [inner]}
    n = collapse_single_child_containers([outer])
    # The outer has visible style; collapse should leave it as outer with c2 inside.
    assert n == 0
    assert outer["elements"][0] is inner


def test_cap_nesting_depth_collapses_excess_container_levels():
    """Containers nested past max_depth get their structural shells dropped."""
    from optimize import cap_nesting_depth
    deep_widget = {"id": "w", "elType": "widget", "widgetType": "spacer", "settings": {}, "elements": []}
    chain = deep_widget
    for d in range(6):
        chain = {"id": f"c{d}", "elType": "container", "settings": {}, "elements": [chain]}
    content = [chain]
    n = cap_nesting_depth(content, max_depth=4)
    assert n >= 1
    # After hoisting, the maximum container-nesting depth in the tree
    # should be <= max_depth. We measure container depth (skip widgets).
    max_container_depth = 0
    def walk(node, depth):
        nonlocal max_container_depth
        if isinstance(node, dict) and node.get("elType") == "container":
            max_container_depth = max(max_container_depth, depth)
            for c in node.get("elements") or []:
                walk(c, depth + 1)
    walk(content[0], 0)
    assert max_container_depth <= 4


def test_widget_pref_handlers_registry_covers_full_enum():
    from optimize import WIDGET_PREF_HANDLERS
    expected = {
        "icon-list", "accordion", "tabs", "slides", "image-carousel",
        "testimonial-carousel", "counter", "progress", "star-rating",
        "social-icons", "video", "image-box", "icon-box", "toggle",
        "divider", "spacer", "nav-menu",
    }
    missing = expected - set(WIDGET_PREF_HANDLERS.keys())
    assert not missing, f"missing handlers: {missing}"


def test_enforce_widget_preferences_converts_icon_list(fake_enrichment_factory):
    """A row of icon+text containers becomes a single icon-list widget."""
    from optimize import enforce_widget_preferences
    rows = []
    for i in range(3):
        rows.append({
            "id": f"r{i}", "elType": "container", "settings": {}, "elements": [
                {"id": f"ic{i}", "elType": "widget", "widgetType": "icon",
                 "settings": {"selected_icon": {"value": "fas fa-check", "library": "fa-solid"}},
                 "elements": []},
                {"id": f"tx{i}", "elType": "widget", "widgetType": "heading",
                 "settings": {"title": f"Feature {i}"}, "elements": []},
            ],
        })
    section = {"id": "sec", "elType": "container", "settings": {}, "elements": rows}
    e = fake_enrichment_factory([{"id": "0", "role": "section", "preferredWidget": "icon-list"}])
    n = enforce_widget_preferences([section], e)
    assert n == 1
    assert section["widgetType"] == "icon-list"
    assert len(section["settings"]["icon_list"]) == 3
