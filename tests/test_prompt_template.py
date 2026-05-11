"""Coverage for the prompt-driven template generator (#10)."""


def test_build_404_minimum_spec_emits_section_with_widgets():
    from prompt_template import build_404
    tree = build_404({})
    assert len(tree) == 1
    section = tree[0]
    assert section["elType"] == "container"
    types = [c.get("widgetType") for c in section["elements"] if c.get("elType") == "widget"]
    assert "heading" in types and "text-editor" in types


def test_build_404_includes_search_when_requested():
    from prompt_template import build_404
    tree = build_404({"show_search": True})
    found_search = any(
        c.get("widgetType") == "shortcode" and "[wp_search_form]" in c["settings"]["shortcode"]
        for c in tree[0]["elements"]
    )
    assert found_search


def test_build_search_with_recent_posts_emits_widget():
    from prompt_template import build_search
    tree = build_search({"show_recent_posts": True, "recent_posts_limit": 7})
    posts_widget = next(
        c for c in tree[0]["elements"]
        if c.get("widgetType") == "wp-widget-recent-posts"
    )
    assert posts_widget["settings"]["number"] == 7


def test_build_popup_omits_dismiss_when_disabled():
    from prompt_template import build_popup
    tree = build_popup({"show_dismiss": False})
    button_texts = [
        c["settings"].get("text") for c in tree[0]["elements"]
        if c.get("widgetType") == "button"
    ]
    assert "No thanks" not in button_texts
