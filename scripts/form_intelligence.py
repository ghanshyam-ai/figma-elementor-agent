"""
Form intelligence — detect form sections in the Figma export, create
real Gravity Forms via the bridge, and replace the form node in the
Elementor tree with the appropriate shortcode widget.

Design intent:
  • Figma forms come in as a container with `semanticRole='form'` and
    nested children with `semanticRole='input'`. Each input's name often
    encodes the field type (e.g. "Email", "Phone", "Message").
  • Rather than rebuilding the form's visual styling in Elementor (which
    rarely matches Gravity Forms output), we substitute the entire form
    container with a single `[gravityform]` shortcode widget. The form
    will inherit Gravity Forms' theme styles, which the developer can
    tune globally afterwards.
  • If Gravity Forms is not active, we leave the original tree in place
    and surface a clear note so the developer knows to install it.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from enrich import Enrichment


# ---------------------------------------------------------------------------
# Field-name → GF type mapping
# ---------------------------------------------------------------------------

FIELD_TYPE_RULES: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"\bemail\b", re.IGNORECASE),               "email"),
    (re.compile(r"\bphone|mobile|tel\b", re.IGNORECASE),    "phone"),
    (re.compile(r"\bwebsite|url|domain\b", re.IGNORECASE),  "website"),
    (re.compile(r"\bmessage|comments?|details|enquiry|inquiry|notes?\b", re.IGNORECASE), "textarea"),
    (re.compile(r"\baddress|street|city|state|zip|postcode\b", re.IGNORECASE), "address"),
    (re.compile(r"\bfull[- ]?name|name\b", re.IGNORECASE),  "name"),
    (re.compile(r"\bnumber|amount|qty|quantity|age\b", re.IGNORECASE), "number"),
    (re.compile(r"\bcompany|organisation|organization|business\b", re.IGNORECASE), "text"),
    (re.compile(r"\bsubject|topic\b", re.IGNORECASE),       "text"),
]


@dataclass
class FormCandidate:
    section_index: int
    title: str
    description: str
    button_text: str
    fields: list[dict]
    ai_node_id: str | None
    elementor_node: dict


def detect_forms(content: list, e: Enrichment) -> list[FormCandidate]:
    """Find every form container the agent should hand off to Gravity Forms.

    Walks the FULL tree (not just top level) using plugin-emitted
    `_ai_role` / `_figma_section_purpose` on each Elementor container's
    settings. Falls back to ai-layout's section walk when those fields
    aren't present. Independent of plugin's top-level shape — finds
    forms nested arbitrarily deep.
    """
    candidates: list[FormCandidate] = []
    seen_ids: set[int] = set()

    # Walk every container, collecting sites where _ai_role == 'form'
    # or _figma_section_purpose == 'lead-capture'. We promote each form
    # to a top-level placement by replacing its enclosing structural
    # container with the shortcode.
    def walk(parent_list: list, parent_idx_offset: int = 0):
        for i, el in enumerate(parent_list):
            if not isinstance(el, dict):
                continue
            settings = el.get("settings") or {}
            role = settings.get("_ai_role")
            purpose = settings.get("_figma_section_purpose")
            if role == "form" or purpose == "lead-capture":
                if id(el) not in seen_ids:
                    seen_ids.add(id(el))
                    ai_section = _find_ai_for(el, e)
                    cand = _build_candidate_from_node(el, ai_section, parent_list, i)
                    if cand and cand.fields:
                        candidates.append(cand)
                    continue  # don't recurse into a form we already captured
            walk(el.get("elements") or [], 0)
    walk(content)

    # Fall-back: if the plugin didn't stamp _ai_role on any node, also do
    # the original ai-layout-only walk so we don't regress.
    if not candidates and e.section_by_index:
        for i, sec in enumerate(e.section_by_index):
            if i >= len(content):
                break
            if sec.get("role") == "form" or sec.get("sectionPurpose") == "lead-capture":
                cand = _build_candidate(content[i], sec, i)
                if cand and cand.fields:
                    candidates.append(cand)
    return candidates


def _find_ai_for(el: dict, e: Enrichment) -> dict | None:
    """Best-effort: find the ai-layout section whose id matches the
    Elementor node's `_figma_id`. Returns None when not found — caller
    falls back to extracting fields from the Elementor subtree itself."""
    target = (el.get("settings") or {}).get("_figma_id")
    if not target:
        return None
    def walk(s: dict):
        if s.get("id") == target:
            return s
        for c in s.get("children") or []:
            r = walk(c)
            if r:
                return r
        return None
    for s in e.section_by_index:
        r = walk(s)
        if r:
            return r
    return None


def _build_candidate_from_node(el: dict, ai: dict | None, parent_list: list, parent_idx: int) -> FormCandidate | None:
    """Build a candidate using ai-layout when present, falling back to
    extracting fields from the Elementor subtree's input widgets."""
    settings = el.get("settings") or {}
    name = settings.get("_figma_name") or (ai or {}).get("name") or "Contact Form"
    description = ""
    button_text = "Submit"
    if ai:
        if ai.get("content"):
            content = ai["content"] if isinstance(ai["content"], dict) else {}
            description = content.get("paragraph") or ""
            if content.get("buttons"):
                button_text = content["buttons"][0].get("text") or button_text
        fields = _extract_fields(ai)
    else:
        # No ai-layout match — derive fields from the Elementor subtree.
        fields = _extract_fields_from_elementor(el)

    cand = FormCandidate(
        section_index=parent_idx,
        title=name,
        description=description,
        button_text=button_text,
        fields=fields,
        ai_node_id=settings.get("_figma_id"),
        elementor_node=el,
    )
    # Stash the parent-list pointer so the materializer can swap in place.
    cand.__dict__["_parent_list"] = parent_list
    return cand


def _extract_fields_from_elementor(el: dict) -> list[dict]:
    """Walk the Elementor subtree looking for inputs.

    The plugin emits inputs as containers/widgets with `_ai_role: input`
    in their settings. Each input's `_figma_name` becomes the field label.
    """
    out: list[dict] = []
    seen: set[str] = set()
    def walk(n):
        if not isinstance(n, dict):
            return
        s = n.get("settings") or {}
        if s.get("_ai_role") == "input":
            label = (s.get("_figma_name") or "").strip() or "Field"
            key = label.lower()
            if key not in seen:
                seen.add(key)
                ftype = _infer_field_type(label)
                out.append({"type": ftype, "label": _humanize(label), "isRequired": ftype == "email"})
        for c in n.get("elements") or []:
            walk(c)
    walk(el)
    if not out:
        out = [
            {"type": "name", "label": "Name", "isRequired": True},
            {"type": "email", "label": "Email", "isRequired": True},
            {"type": "textarea", "label": "Message"},
        ]
    return out


def _build_candidate(el: dict, sec: dict, section_index: int) -> FormCandidate | None:
    title = sec.get("name") or "Contact Form"
    description = ""
    if sec.get("content") and isinstance(sec["content"], dict):
        description = sec["content"].get("paragraph") or ""

    fields = _extract_fields(sec)
    button_text = "Submit"
    if sec.get("content") and sec["content"].get("buttons"):
        first = sec["content"]["buttons"][0]
        button_text = first.get("text") or button_text

    return FormCandidate(
        section_index=section_index,
        title=title,
        description=description,
        button_text=button_text,
        fields=fields,
        ai_node_id=sec.get("id"),
        elementor_node=el,
    )


def _extract_fields(sec: dict) -> list[dict]:
    """Walk the ai-layout subtree and produce a Gravity Forms field spec."""
    out: list[dict] = []
    seen_labels: set[str] = set()

    def visit(node: dict) -> None:
        if not isinstance(node, dict):
            return
        if node.get("role") == "input":
            label = (node.get("name") or "").strip() or "Field"
            label_norm = label.lower()
            if label_norm in seen_labels:
                return
            seen_labels.add(label_norm)
            ftype = _infer_field_type(label)
            out.append({
                "type": ftype,
                "label": _humanize(label),
                "isRequired": ftype == "email",
            })
        for c in node.get("children") or []:
            visit(c)

    for c in sec.get("children") or []:
        visit(c)

    if not out:
        # Fallback: a default 3-field contact form so we still hand over
        # something usable rather than failing the import.
        out = [
            {"type": "name", "label": "Name", "isRequired": True},
            {"type": "email", "label": "Email", "isRequired": True},
            {"type": "textarea", "label": "Message"},
        ]
    return out


def _infer_field_type(label: str) -> str:
    for rx, t in FIELD_TYPE_RULES:
        if rx.search(label):
            return t
    return "text"


def _humanize(s: str) -> str:
    s = re.sub(r"[_\-]+", " ", s).strip()
    if not s:
        return "Field"
    return s[0].upper() + s[1:]


# ---------------------------------------------------------------------------
# Wiring: create GF forms + swap into Elementor tree
# ---------------------------------------------------------------------------

def materialize_forms(
    client,
    content: list,
    candidates: list[FormCandidate],
) -> list[dict]:
    """Create each candidate as a Gravity Form, swap the Elementor node.

    Each candidate carries a `_parent_list` pointer (set by detect_forms)
    so we can swap the node in place no matter how deeply nested it is.
    Falls back to `content[section_index]` for legacy candidates that
    came from the top-level-only path.
    """
    results: list[dict] = []
    if not candidates:
        return results
    existing = {f["title"]: f for f in client.list_gravity_forms()}
    for cand in candidates:
        if cand.title in existing:
            form_id = existing[cand.title]["id"]
            shortcode = f'[gravityform id="{form_id}" title="false" description="false"]'
            edit_url = None
        else:
            spec = {
                "title": cand.title,
                "description": cand.description,
                "button": {"text": cand.button_text},
                "fields": cand.fields,
            }
            created = client.create_gravity_form(spec)
            form_id = created["id"]
            shortcode = created["shortcode"]
            edit_url = created.get("edit_url")
        # Swap in place — handles nested form sites.
        replacement = _make_shortcode_section(shortcode, cand)
        parent_list = cand.__dict__.get("_parent_list")
        if parent_list is not None and 0 <= cand.section_index < len(parent_list):
            parent_list[cand.section_index] = replacement
        elif 0 <= cand.section_index < len(content):
            content[cand.section_index] = replacement
        results.append({
            "section_index": cand.section_index,
            "form_id": form_id,
            "shortcode": shortcode,
            "edit_url": edit_url,
            "title": cand.title,
            "fields": len(cand.fields),
        })
    return results


def _make_shortcode_section(shortcode: str, cand: FormCandidate) -> dict:
    return {
        "id": "gffrm" + str(cand.section_index).zfill(2),
        "elType": "container",
        "isInner": False,
        "settings": {
            "content_width": "boxed",
            "flex_direction": "column",
            "flex_align_items": "center",
            "_form_source": "gravity-forms",
            "_form_ai_node_id": cand.ai_node_id,
        },
        "elements": [
            {
                "id": "gfsht" + str(cand.section_index).zfill(2),
                "elType": "widget",
                "widgetType": "shortcode",
                "settings": {"shortcode": shortcode},
                "elements": [],
            }
        ],
    }
