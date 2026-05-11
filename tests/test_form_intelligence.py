"""Coverage for form detection + Gravity Forms field-type inference."""


def test_detect_forms_extracts_typed_fields(fake_enrichment_factory):
    from form_intelligence import detect_forms
    section = {
        "id": "s", "role": "form", "name": "Contact Us",
        "sectionPurpose": "lead-capture",
        "confidence": 0.6,
        "content": {"buttons": [{"text": "Send"}], "paragraph": "Drop us a line"},
        "children": [
            {"id": "f1", "role": "input", "name": "Email"},
            {"id": "f2", "role": "input", "name": "Full Name"},
            {"id": "f3", "role": "input", "name": "Phone"},
            {"id": "f4", "role": "input", "name": "Message"},
            {"id": "f5", "role": "input", "name": "Website"},
        ],
    }
    e = fake_enrichment_factory([section])
    content = [{"id": "c", "elType": "container", "settings": {}, "elements": []}]
    cands = detect_forms(content, e)
    assert len(cands) == 1
    types_by_label = {f["label"]: f["type"] for f in cands[0].fields}
    assert types_by_label == {
        "Email": "email",
        "Full Name": "name",
        "Phone": "phone",
        "Message": "textarea",
        "Website": "website",
    }
    # Email is auto-required.
    email_field = next(f for f in cands[0].fields if f["label"] == "Email")
    assert email_field["isRequired"] is True
    assert cands[0].button_text == "Send"


def test_detect_forms_falls_back_to_default_three_fields(fake_enrichment_factory):
    """When the form has no inputs in ai-layout, we still produce a usable spec."""
    from form_intelligence import detect_forms
    section = {"id": "s", "role": "form", "name": "Bare form", "children": []}
    e = fake_enrichment_factory([section])
    content = [{"id": "c", "elType": "container", "settings": {}, "elements": []}]
    cands = detect_forms(content, e)
    assert cands and len(cands[0].fields) == 3
    assert {f["type"] for f in cands[0].fields} == {"name", "email", "textarea"}
