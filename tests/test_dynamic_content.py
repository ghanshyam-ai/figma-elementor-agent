"""Coverage for blog-grid → Posts widget detection."""


def _blog_card(title: str = "Story", excerpt_chars: int = 100) -> dict:
    """Image + heading + long-enough excerpt — the structural blog-card shape."""
    return {
        "id": "card", "elType": "container", "settings": {}, "elements": [
            {"id": "i", "elType": "widget", "widgetType": "image",
             "settings": {"image": {"url": "/x.png"}}, "elements": []},
            {"id": "h", "elType": "widget", "widgetType": "heading",
             "settings": {"title": title, "header_size": "h3"}, "elements": []},
            {"id": "t", "elType": "widget", "widgetType": "text-editor",
             "settings": {"editor": "<p>" + ("x " * (excerpt_chars // 2)) + "</p>"},
             "elements": []},
        ],
    }


def test_detect_dynamic_sections_requires_two_signals(fake_enrichment_factory):
    """Three blog-shaped cards alone aren't enough — need name OR purpose too."""
    from dynamic_content import detect_dynamic_sections
    section = {"id": "0", "role": "section", "name": "Generic"}
    container = {"id": "c", "elType": "container", "settings": {"_figma_name": "Generic"},
                 "elements": [_blog_card(), _blog_card(), _blog_card()]}
    e = fake_enrichment_factory([section])
    cands = detect_dynamic_sections([container], e)
    assert cands == []


def test_detect_dynamic_sections_with_layer_name_and_structure(fake_enrichment_factory):
    from dynamic_content import detect_dynamic_sections, replace_with_posts_widget
    section = {"id": "0", "role": "section", "name": "Latest Articles"}
    container = {"id": "c", "elType": "container",
                 "settings": {"_figma_name": "Latest Articles"},
                 "elements": [_blog_card("A"), _blog_card("B"), _blog_card("C"), _blog_card("D")]}
    e = fake_enrichment_factory([section])
    cands = detect_dynamic_sections([container], e, has_elementor_pro=False)
    assert len(cands) == 1
    n = replace_with_posts_widget([container], cands)
    assert n == 1


def test_detect_dynamic_sections_uses_pro_when_available(fake_enrichment_factory):
    from dynamic_content import detect_dynamic_sections, replace_with_posts_widget
    section = {"id": "0", "role": "section", "name": "Blog Grid",
               "sectionPurpose": "feature-grid",
               "children": [{"id": str(i), "role": "card"} for i in range(4)]}
    container = {"id": "c", "elType": "container", "settings": {"_figma_name": "Blog Grid"},
                 "elements": [_blog_card() for _ in range(4)]}
    e = fake_enrichment_factory([section])
    cands = detect_dynamic_sections([container], e, has_elementor_pro=True)
    assert cands and cands[0].has_pro
    content = [container]
    replace_with_posts_widget(content, cands)
    posts_widget = content[0]["elements"][0]
    assert posts_widget["widgetType"] == "posts"
