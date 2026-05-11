"""Coverage for confidence scoring + screenshot fallback decisions."""


def test_compute_report_aggregates_confidence(fake_enrichment_factory):
    from validation_layer import compute_report
    e = fake_enrichment_factory([
        {"id": "1", "role": "hero", "confidence": 0.9},
        {"id": "2", "role": "card", "confidence": 0.4},
        {"id": "3", "role": "section", "confidence": 0.95},
    ])
    e.validation = {
        "warnings": [
            {"level": "warn", "code": "absolute-layout", "nodeId": "2", "message": "x"},
            {"level": "info", "code": "unnamed-layer", "nodeId": "3", "message": "y"},
        ],
        "summary": {"info": 1, "warn": 1, "error": 0},
    }
    report = compute_report(e, [])
    assert 0.6 < report.confidence < 0.85  # mean ~0.75 minus 0.02 warn
    assert report.summary["sections_low_confidence"] == 1
    assert report.fallback_section_indices == [1]
    # Warnings of level=warn become risk areas; info ones do not.
    severities = sorted(r.severity for r in report.risk_areas)
    assert "warn" in severities
    assert all(s != "info" for s in severities)


def test_compute_report_handles_empty_enrichment(empty_enrichment):
    """Confidence defaults to 1.0 when no ai-layout is loaded."""
    from validation_layer import compute_report
    report = compute_report(empty_enrichment, [])
    assert report.confidence == 1.0
    assert report.risk_areas == []


def test_apply_screenshot_fallbacks_skips_when_no_asset(fake_enrichment_factory, tmp_path):
    """When the screenshot wasn't uploaded, leave the section intact and tag
    it with `_low_confidence` instead of crashing."""
    from validation_layer import apply_screenshot_fallbacks
    e = fake_enrichment_factory([
        {"id": "node-1", "role": "section", "confidence": 0.4, "name": "guess"},
    ])
    container = {"id": "c", "elType": "container", "settings": {}, "elements": []}
    swapped = apply_screenshot_fallbacks([container], e, tmp_path, asset_map={}, threshold=0.5)
    assert swapped == []
    assert container["settings"]["_low_confidence"] is True
