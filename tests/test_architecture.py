"""Coverage for architecture routing + popup trigger inference."""


def _container(name=""):
    return {"id": "c", "elType": "container",
            "settings": {"_figma_name": name} if name else {},
            "elements": []}


def test_route_section_purpose_navbar_to_header(fake_enrichment_factory):
    from architecture import route_sections
    e = fake_enrichment_factory([{"id": "0", "role": "navbar", "sectionPurpose": "navbar"}])
    placements = route_sections([_container("Header")], e)
    assert len(placements) == 1
    assert placements[0].kind == "header"


def test_route_layer_name_popup(fake_enrichment_factory):
    from architecture import route_sections
    e = fake_enrichment_factory([{"id": "0", "role": "section", "name": "Newsletter Popup"}])
    placements = route_sections([_container("Newsletter Popup")], e)
    assert placements[0].kind == "popup"


def test_route_falls_back_to_page_for_hero(fake_enrichment_factory):
    from architecture import route_sections
    e = fake_enrichment_factory([{"id": "0", "role": "hero", "sectionPurpose": "hero"}])
    placements = route_sections([_container("Hero")], e)
    assert placements[0].kind == "page"


def test_route_treats_top_level_widget_as_page(fake_enrichment_factory):
    from architecture import route_sections
    widget = {"id": "w", "elType": "widget", "widgetType": "icon-list", "settings": {}, "elements": []}
    e = fake_enrichment_factory([{"id": "0"}])
    placements = route_sections([widget], e)
    assert placements[0].kind == "page"
    assert placements[0].reason == "top-level widget"


def test_popup_trigger_inference_on_exit_intent():
    from architecture import popup_settings_for_node
    s = popup_settings_for_node({"settings": {"_figma_name": "Exit-Intent Modal"}}, None)
    assert s["triggers"] == {"exit_intent": "yes"}


def test_popup_trigger_inference_default_is_page_load():
    from architecture import popup_settings_for_node
    s = popup_settings_for_node({"settings": {"_figma_name": "Newsletter Popup"}}, None)
    assert "on_page_load" in s["triggers"]
    assert s["frequency"] == "session"
