"""Component library + tokens-hash on ProjectState.

Covers the multi-page state additions: storing a component fingerprint
maps to a template id we can look up later; tokens_hash distinguishes
re-runs of the same global.json from a divergent ZIP; and an older
project-state.json (without these fields) still loads cleanly.
"""
import json

import pytest


@pytest.fixture
def fresh_state(tmp_path):
    from project_state import load_state
    return load_state(tmp_path)


def test_record_and_find_component_roundtrips(tmp_path, fresh_state):
    fresh_state.record_component(
        "fp-abc123",
        template_id=187,
        slug="acme--reuse--accordion-faq",
        kind="section",
        title="FAQ",
        page_slug="home",
        widget_count=8,
    )
    fresh_state.save()

    from project_state import load_state
    reloaded = load_state(tmp_path)
    match = reloaded.find_component("fp-abc123")
    assert match is not None
    assert match["template_id"] == 187
    assert match["first_page_slug"] == "home"
    assert match["widget_count"] == 8
    assert match["template_slug"] == "acme--reuse--accordion-faq"


def test_find_component_returns_none_for_unknown(fresh_state):
    assert fresh_state.find_component("never-seen") is None


def test_record_tokens_first_call_is_not_changed(fresh_state):
    new_hash, changed = fresh_state.record_tokens({"colors": ["#000"]})
    assert isinstance(new_hash, str) and len(new_hash) == 64
    assert changed is False  # nothing stored yet → not "changed"


def test_record_tokens_detects_diff_after_persist(tmp_path, fresh_state):
    h1, _ = fresh_state.record_tokens({"colors": ["#000"]})
    fresh_state.tokens_hash = h1
    fresh_state.save()

    from project_state import load_state
    reloaded = load_state(tmp_path)
    same_hash, changed = reloaded.record_tokens({"colors": ["#000"]})
    assert changed is False
    assert same_hash == h1

    diff_hash, changed = reloaded.record_tokens({"colors": ["#fff"]})
    assert changed is True
    assert diff_hash != h1


def test_tokens_hash_is_order_insensitive(fresh_state):
    h1, _ = fresh_state.record_tokens({"a": 1, "b": 2})
    h2, _ = fresh_state.record_tokens({"b": 2, "a": 1})
    assert h1 == h2


def test_backward_compat_loads_old_state_without_new_fields(tmp_path):
    # Simulate a project-state.json written by a previous version of the
    # agent — no component_library, no tokens_hash.
    legacy = tmp_path / "project-state.json"
    legacy.write_text(json.dumps({
        "kit_applied": True,
        "kit_id": 12,
        "template_ids_by_slug": {"acme--header": {"id": 142, "template_type": "header"}},
        "form_ids_by_title": {},
        "asset_map_by_filename": {},
        "asset_map_by_hash": {},
        "pages_imported": [{"slug": "home", "page_id": 99}],
    }))
    from project_state import load_state
    s = load_state(tmp_path)
    assert s.kit_applied is True
    assert s.kit_id == 12
    assert s.component_library == {}
    assert s.tokens_hash is None
    assert s.is_first_run is False  # kit + header satisfy the predicate
