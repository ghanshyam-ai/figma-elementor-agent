"""Coverage for nested template-reuse detection + replacement."""


def _card(title: str, body: str = "Body text long enough to look excerpt-y") -> dict:
    return {
        "id": title.replace(" ", "")[:6],
        "elType": "container",
        "settings": {"flex_direction": "column"},
        "elements": [
            {"id": "i", "elType": "widget", "widgetType": "image",
             "settings": {"image": {"url": "/x.png"}}, "elements": []},
            {"id": "h", "elType": "widget", "widgetType": "heading",
             "settings": {"title": title, "header_size": "h3"}, "elements": []},
            {"id": "t", "elType": "widget", "widgetType": "text-editor",
             "settings": {"editor": f"<p>{body}</p>"}, "elements": []},
        ],
    }


def test_detect_reuse_groups_finds_top_level_duplicates(empty_enrichment):
    from template_reuse import detect_reuse_groups
    content = [_card("Card"), _card("Card"), _card("Card")]
    groups = detect_reuse_groups(content, empty_enrichment)
    assert len(groups) == 1
    g = groups[0]
    assert len(g.sites) == 3
    assert g.template_slug


def test_detect_reuse_groups_finds_nested_duplicates(empty_enrichment):
    """Three identical cards inside a single container should still group."""
    from template_reuse import detect_reuse_groups
    parent = {
        "id": "p", "elType": "container", "settings": {}, "elements": [
            _card("Card"), _card("Card"), _card("Card"),
        ],
    }
    groups = detect_reuse_groups([parent], empty_enrichment)
    assert len(groups) == 1
    assert all(s.depth >= 1 for s in groups[0].sites)


def test_detect_reuse_groups_skips_trivial_subtrees(empty_enrichment):
    """Single-widget wrappers don't justify a template (MIN_SUBTREE_NODES=3)."""
    from template_reuse import detect_reuse_groups
    trivial = {"id": "t", "elType": "container", "settings": {}, "elements": [
        {"id": "w", "elType": "widget", "widgetType": "spacer", "settings": {}, "elements": []},
    ]}
    groups = detect_reuse_groups([trivial, trivial.copy()], empty_enrichment)
    assert groups == []


def test_replace_duplicates_with_shortcodes_swaps_nodes(empty_enrichment):
    from template_reuse import detect_reuse_groups, replace_duplicates_with_shortcodes
    content = [_card("Card"), _card("Card"), _card("Card")]
    groups = detect_reuse_groups(content, empty_enrichment)
    for g in groups:
        g.template_id = 99
    n = replace_duplicates_with_shortcodes(content, groups)
    assert n == 3
    for el in content:
        widget = (el.get("elements") or [None])[0]
        assert widget and widget.get("widgetType") == "shortcode"
        assert "[elementor-template" in widget["settings"]["shortcode"]
