"""Coverage for the design-token CSS bridge (spacing + radius)."""


def test_build_token_css_returns_root_block_and_classes():
    from design_tokens import build_token_css
    g = {"radii": [4, 8, 16, 24], "spacing": [8, 16, 24, 32]}
    css, radius_map, gap_map = build_token_css(g)
    assert ":root {" in css
    assert "--token-radius-xs: 4px" in css
    assert "--token-gap-xs: 8px" in css
    # Utility classes generated for each token.
    assert ".dt-radius-xs" in css and ".dt-gap-xs" in css
    # Maps round-trip.
    assert 4 in radius_map and "dt-radius-" in radius_map[4]
    assert 8 in gap_map and "dt-gap-" in gap_map[8]


def test_build_token_css_returns_empty_when_no_input():
    from design_tokens import build_token_css
    css, r, g = build_token_css({})
    assert css == "" and not r and not g


def test_apply_design_token_classes_tags_matching_widgets():
    from design_tokens import apply_design_token_classes
    radius_map = {8.0: "dt-radius-md"}
    gap_map = {16.0: "dt-gap-md"}
    container = {
        "id": "c", "elType": "container",
        "settings": {
            "border_radius": {"unit": "px", "top": "8", "right": "8", "bottom": "8", "left": "8", "isLinked": True},
            "flex_gap": {"unit": "px", "size": 16, "sizes": []},
        },
        "elements": [],
    }
    counts = apply_design_token_classes([container], radius_map, gap_map)
    assert counts == {"radius": 1, "gap": 1}
    classes = container["settings"]["css_classes"].split()
    assert "dt-radius-md" in classes and "dt-gap-md" in classes
