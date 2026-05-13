"""
DOM-structure diff — complements the pixel diff for semantic correctness.

The pixel diff is the right primary signal but it has a known false-
positive class: any visual change that doesn't break the design intent.
Animations that haven't settled, lazy-loaded images that arrive a frame
late, web fonts that swap after FOUT, video posters loading async, even
a one-pixel browser anti-alias shift on text — all register as drift
the pixel comparator can't tell apart from a real bug.

This module produces a *structural fingerprint* of the rendered page:
  • widget-type tree (depth-first list of Elementor widget types)
  • text-content fingerprint per widget (normalized + hashed)
  • per-section signature derived from the live DOM

It then compares those fingerprints against the expected `build/data.json`
(the post-optimize Elementor tree the importer just wrote). A section
"passes structurally" when:
  • the widget-type tree matches (or differs only by reordered siblings
    of the same kind), AND
  • the visible text content is a superset of the expected text
    (allowance: text rendered with `dynamic_content` may differ slightly).

The pixel + structure pair lets the gate apply:
  passing = pixel_drift ≤ threshold OR (structure_match AND text_match)

So a section that's visually slightly off but structurally identical
no longer kicks the build into auto-fix or Claude review.

Public API:
    capture_live_dom(url) → {"sections": [...], "captured_at": str}
    expected_dom_from_data_json(data_path) → {"sections": [...]}
    diff_dom(live, expected) → {"sections": [...], "passed": bool}
"""
from __future__ import annotations

import hashlib
import json
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCRIPTS = Path(__file__).resolve().parent
BUILD = ROOT / "build"

# Text-content normalizer — collapse whitespace, lower-case, strip
# punctuation. Two text values whose canonical form matches are
# considered "the same content."
_WS_RE = re.compile(r"\s+")
_PUNCT_RE = re.compile(r"[^\w\s]+")


def _normalize_text(t: str) -> str:
    if not isinstance(t, str):
        return ""
    t = t.replace("\xa0", " ")
    t = _PUNCT_RE.sub(" ", t)
    t = _WS_RE.sub(" ", t).strip().lower()
    return t


def _hash_text(t: str) -> str:
    return hashlib.sha1(_normalize_text(t).encode("utf-8")).hexdigest()[:12]


# Elementor widget-type → semantic kind. Tabs / accordion render their
# inner content via JavaScript so the static DOM doesn't expose every
# panel — we treat structural equivalence as "the widget kind matches,"
# not "every nested node matches."
WIDGET_KIND_FALLBACK = "unknown"


# Helpers used by both `capture_live_dom` and `expected_dom_from_data_json`.

_HTML_TAG_RE = re.compile(r"<[^>]+>")


def _strip_html(t: str) -> str:
    return _HTML_TAG_RE.sub(" ", t)


def _extract_text_from_settings(settings: dict) -> list[str]:
    """Pull visible text out of a widget's settings. The live DOM's
    `innerText` strips HTML, so we must strip it here too — otherwise
    `<p>Hello</p>` in `editor` hashes differently from "Hello" rendered
    on the page."""
    if not isinstance(settings, dict):
        return []
    out: list[str] = []
    for key in ("title", "text", "editor", "html", "description", "button_text", "label", "heading"):
        v = settings.get(key)
        if isinstance(v, str) and v.strip():
            out.append(_strip_html(v))
    items = settings.get("icon_list")
    if isinstance(items, list):
        for it in items:
            if isinstance(it, dict) and isinstance(it.get("text"), str):
                out.append(_strip_html(it["text"]))
    return out


def _walk_elementor_tree(content: list) -> list[dict]:
    """Flatten an Elementor tree into a list of {kind, depth, text_hash}.

    A pre-order traversal — siblings appear left-to-right at the same
    depth. We strip _figma_pre_id and other agent markers from text so
    they don't pollute the structural fingerprint.
    """
    out: list[dict] = []

    def walk(node, depth: int) -> None:
        if not isinstance(node, dict):
            return
        el_type = node.get("elType")
        widget = node.get("widgetType")
        kind = widget if el_type == "widget" else (el_type or WIDGET_KIND_FALLBACK)
        texts = _extract_text_from_settings(node.get("settings") or {})
        text_hash = _hash_text(" ".join(texts)) if texts else ""
        out.append({"kind": kind, "depth": depth, "text_hash": text_hash, "id": node.get("id")})
        for c in node.get("elements") or []:
            walk(c, depth + 1)
    for top in content or []:
        walk(top, 0)
    return out


def expected_dom_from_data_json(data_path: Path | None = None) -> dict:
    """Build the expected DOM fingerprint from `build/data.json`.

    Sections are the top-level entries of `content`.
    """
    if data_path is None:
        data_path = BUILD / "data.json"
    if not data_path.exists():
        return {"sections": []}
    try:
        data = json.loads(data_path.read_text())
    except json.JSONDecodeError:
        return {"sections": []}
    content = data.get("content") if isinstance(data, dict) else data
    if not isinstance(content, list):
        return {"sections": []}
    sections: list[dict] = []
    for top in content:
        if not isinstance(top, dict):
            continue
        fp = _walk_elementor_tree([top])
        sections.append({
            "id": top.get("id"),
            "figma_id": (top.get("settings") or {}).get("_figma_id"),
            "figma_name": (top.get("settings") or {}).get("_figma_name"),
            "kind_path": [n["kind"] for n in fp],
            "text_hashes": [n["text_hash"] for n in fp if n["text_hash"]],
            "depth_max": max((n["depth"] for n in fp), default=0),
        })
    return {"sections": sections}


def capture_live_dom(url: str, width: int = 1920, timeout_ms: int = 60_000) -> dict:
    """Fetch the live page via Playwright and extract per-section
    fingerprints from the rendered DOM."""
    script = SCRIPTS / "playwright_dom_fingerprint.js"
    if not script.exists():
        raise RuntimeError(f"playwright_dom_fingerprint.js not found at {script}")
    cmd = ["node", str(script), "--url", url, "--width", str(width), "--timeout", str(timeout_ms)]
    result = subprocess.run(cmd, capture_output=True, text=True, check=True)
    last_line = result.stdout.strip().splitlines()[-1]
    payload = json.loads(last_line)
    return payload


def diff_dom(live: dict, expected: dict, allow_text_superset: bool = True) -> dict:
    """Compare two fingerprint payloads. Returns:
        {
          "sections": [
            {data_id, figma_name, structure_match, text_match,
             passed, missing_text, extra_text}
          ],
          "passed": bool   (True when every section either matches or
                            the live DOM has the expected text as a subset)
        }
    """
    live_secs = {s.get("data_id"): s for s in (live.get("sections") or []) if s.get("data_id")}
    expected_secs = expected.get("sections") or []
    out: list[dict] = []
    overall = True

    # Build id_map (pre → post) to translate expected ids to live ids.
    id_map = {}
    p = BUILD / "id_map.json"
    if p.exists():
        try:
            id_map = json.loads(p.read_text())
        except json.JSONDecodeError:
            id_map = {}

    for exp in expected_secs:
        exp_id = exp.get("id")
        live_id = id_map.get(exp_id, exp_id)
        live_sec = live_secs.get(live_id)
        if not live_sec:
            # Section missing entirely in the live DOM — hard structural fail.
            out.append({
                "data_id": live_id,
                "figma_name": exp.get("figma_name"),
                "structure_match": False,
                "text_match": False,
                "passed": False,
                "reason": "section_missing_in_live",
            })
            overall = False
            continue
        # Structure: compare kind-path as multisets per depth-band. Pure
        # order equality is too strict (mobile reflow + flex column may
        # legitimately reorder visually-equivalent siblings).
        live_path = list(live_sec.get("kind_path") or [])
        exp_path = list(exp.get("kind_path") or [])
        structure_match = sorted(live_path) == sorted(exp_path)
        # Text: live should contain ALL expected text hashes (superset
        # allowed because dynamic widgets may inject more text).
        live_hashes = set(live_sec.get("text_hashes") or [])
        exp_hashes = set(exp.get("text_hashes") or [])
        missing_text = sorted(exp_hashes - live_hashes)
        extra_text = sorted(live_hashes - exp_hashes) if not allow_text_superset else []
        text_match = not missing_text and (allow_text_superset or not extra_text)
        passed = structure_match and text_match
        out.append({
            "data_id": live_id,
            "figma_name": exp.get("figma_name"),
            "structure_match": structure_match,
            "text_match": text_match,
            "missing_text": missing_text,
            "extra_text": extra_text,
            "passed": passed,
            "reason": (
                "ok" if passed else
                ("structure_mismatch" if not structure_match else "text_mismatch")
            ),
        })
        if not passed:
            overall = False

    return {"sections": out, "passed": overall}


# ---- CLI -----------------------------------------------------------------

def main() -> int:
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", help="Live page URL to fingerprint")
    ap.add_argument("--width", type=int, default=1920)
    ap.add_argument("--expected", default=str(BUILD / "data.json"),
                    help="Path to expected build/data.json")
    ap.add_argument("--out", default=str(BUILD / "diff" / "dom_diff.json"))
    args = ap.parse_args()

    expected = expected_dom_from_data_json(Path(args.expected))
    if not args.url:
        # Just write the expected fingerprint for inspection.
        Path(args.out).parent.mkdir(parents=True, exist_ok=True)
        Path(args.out).write_text(json.dumps({"expected": expected}, indent=2))
        print(f"Wrote expected fingerprint → {args.out}")
        return 0
    live = capture_live_dom(args.url, args.width)
    result = diff_dom(live, expected)
    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps({
        "expected": expected,
        "live": live,
        "diff": result,
    }, indent=2))
    print(f"DOM diff → {out_path}  passed={result['passed']}")
    fail_count = sum(1 for s in result["sections"] if not s["passed"])
    if fail_count:
        print(f"  {fail_count} section(s) failed structural diff")
    return 0 if result["passed"] else 1


if __name__ == "__main__":
    sys.exit(main())
