"""Cross-page reuse via the component_library on ProjectState.

The key invariants under test:
  • A single-instance accordion on Page 2 is shortcoded when its fingerprint
    matches a template recorded during Page 1's run — even though it would
    NOT qualify under the within-page ≥2-instance rule.
  • Different copy (title / text / editor) doesn't break the match.
  • `replace_duplicates_with_shortcodes` rewrites that single site to the
    pre-existing template id.
"""
import pytest


def _accordion(item_titles: list[str]) -> dict:
    """Synthetic accordion-shaped container. Item bodies differ by copy
    only — the structure is identical between calls so structural hashing
    should treat them as the same fingerprint."""
    return {
        "id": "acc",
        "elType": "container",
        "settings": {"flex_direction": "column", "padding": {"unit": "px", "size": 24}},
        "elements": [
            {
                "id": f"i{i}",
                "elType": "widget",
                "widgetType": "heading",
                "settings": {"title": t, "header_size": "h3"},
                "elements": [],
            }
            for i, t in enumerate(item_titles)
        ],
    }


def test_within_page_reuse_records_into_component_library(tmp_path, empty_enrichment):
    """Page 1 builds an accordion that appears 2+ times → it becomes a
    library template. We record the fingerprint into project_state so a
    later page can find it."""
    from project_state import load_state
    from template_reuse import detect_reuse_groups

    state = load_state(tmp_path)
    content = [_accordion(["Q1", "Q2"]), _accordion(["Q1", "Q2"])]
    groups = detect_reuse_groups(content, empty_enrichment, state=state)
    assert len(groups) == 1, f"expected 1 within-page group, got {len(groups)}"
    g = groups[0]
    assert g.reused_from_state is False
    # Simulate what import_elementor.py does after creating the template:
    state.record_component(
        g.fingerprint,
        template_id=200,
        slug="site--reuse--accordion",
        kind="section",
        title="Accordion",
        page_slug="home",
        widget_count=2,
    )
    state.save()
    assert state.find_component(g.fingerprint)["template_id"] == 200


def test_cross_page_single_instance_matches_prior_library(tmp_path, empty_enrichment):
    """Page 2 has the accordion exactly once. The within-page rule would
    reject it (needs ≥2), but cross-page reuse should fire."""
    from project_state import load_state
    from template_reuse import detect_reuse_groups, _structural_hash

    # Compute the structural hash the way detect_reuse_groups would,
    # so the library entry uses a matching key.
    page1_accordion = _accordion(["Q1 home", "Q2 home"])
    fp = _structural_hash(page1_accordion)

    state = load_state(tmp_path)
    state.record_component(
        fp,
        template_id=200,
        slug="site--reuse--accordion",
        kind="section",
        title="Accordion",
        page_slug="home",
        widget_count=2,
    )
    state.save()

    # Page 2: same structure, different content. Only one instance.
    page2_content = [_accordion(["A different question?", "And another"])]
    groups = detect_reuse_groups(page2_content, empty_enrichment, state=state)

    assert len(groups) == 1, f"expected 1 cross-page group, got {len(groups)}"
    g = groups[0]
    assert g.reused_from_state is True
    assert g.pre_existing_template_id == 200
    assert g.pre_existing_page_slug == "home"
    assert g.template_id == 200
    assert len(g.sites) == 1


def test_cross_page_replace_swaps_single_instance_to_shortcode(tmp_path, empty_enrichment):
    from project_state import load_state
    from template_reuse import (
        detect_reuse_groups,
        replace_duplicates_with_shortcodes,
        _structural_hash,
    )

    page1_accordion = _accordion(["Q1", "Q2"])
    fp = _structural_hash(page1_accordion)

    state = load_state(tmp_path)
    state.record_component(
        fp,
        template_id=200,
        slug="site--reuse--accordion",
        kind="section",
        title="Accordion",
        page_slug="home",
        widget_count=2,
    )

    page2_content = [_accordion(["X", "Y"])]
    groups = detect_reuse_groups(page2_content, empty_enrichment, state=state)
    replaced = replace_duplicates_with_shortcodes(page2_content, groups)
    assert replaced == 1
    sw = page2_content[0].get("elements", [None])[0]
    assert sw and sw.get("widgetType") == "shortcode"
    assert 'id="200"' in sw["settings"]["shortcode"]


def test_no_state_means_no_cross_page_groups(tmp_path, empty_enrichment):
    """Sanity: without state, a single-instance accordion never groups."""
    from template_reuse import detect_reuse_groups
    content = [_accordion(["only"])]
    groups = detect_reuse_groups(content, empty_enrichment, state=None)
    assert groups == []


def test_different_structure_does_not_match(tmp_path, empty_enrichment):
    """Library entry for an accordion shouldn't match a hero with a button."""
    from project_state import load_state
    from template_reuse import detect_reuse_groups, _structural_hash

    state = load_state(tmp_path)
    page1_accordion = _accordion(["Q1", "Q2"])
    fp = _structural_hash(page1_accordion)
    state.record_component(
        fp, template_id=200, slug="x", kind="section",
        title="Accordion", page_slug="home", widget_count=2,
    )

    hero = {
        "id": "hero",
        "elType": "container",
        "settings": {"flex_direction": "row"},
        "elements": [
            {"id": "b", "elType": "widget", "widgetType": "button",
             "settings": {"text": "Click"}, "elements": []},
        ],
    }
    groups = detect_reuse_groups([hero], empty_enrichment, state=state)
    # No within-page match (single instance) and no cross-page match
    # (different fingerprint) → empty.
    assert groups == []
