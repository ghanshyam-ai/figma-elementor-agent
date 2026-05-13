"""
Build-plan generator (read-only, no WordPress writes).

Reads the extracted Figma export under `build/<export>/` and emits a
human-readable plan of what `import_elementor.py` *would* do — without
touching the WP site. The plan lists every detected section, the chosen
widget per heuristic, the confidence, and the design tokens it expects
to bind.

Two output files:

  • `build/build-plan.json`        — full plan, one entry per section
  • `build/widget-review-queue.json` — subset of sections with widget
    confidence below `--widget-confidence-floor` (default 0.7). The
    orchestrator dispatches a single Claude lookup against the
    `elementor-widgets` skill for everything in this queue, BEFORE the
    import runs. Catching widget-choice mistakes at plan time is
    drastically cheaper than catching them post-import via visual diff.

The script is deterministic and runs all the same inference functions
that `import_elementor.py` uses, but on a deep copy of the tree so
nothing mutates. The plan is what would land on the page if the
developer accepted it.

Usage:
    python3 scripts/build_plan.py                              # plan from build/export/
    python3 scripts/build_plan.py --widget-confidence-floor 0.6
    python3 scripts/build_plan.py --print                      # echo the plan as a table
"""
from __future__ import annotations

import argparse
import copy
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BUILD = ROOT / "build"
sys.path.insert(0, str(ROOT / "scripts"))

# Default queue threshold. Mirrors the value the orchestrator uses for
# Claude-as-Author dispatch — keeps plan-time and review-time consistent.
DEFAULT_WIDGET_CONFIDENCE_FLOOR = 0.7


def _load(p: Path) -> dict:
    if not p.exists():
        return {}
    try:
        return json.loads(p.read_text())
    except json.JSONDecodeError:
        return {}


def _resolve_export_dir(build_dir: Path) -> Path | None:
    """Return the directory under build/ that contains data.json."""
    for p in build_dir.rglob("data.json"):
        return p.parent
    return None


def _detect_widget(node: dict) -> tuple[str | None, float, str]:
    """Run the widget_inference detectors *without* mutating the tree.

    Returns (chosen_widget, confidence, reason). When no detector matches,
    returns (None, 0.0, "no-match").
    """
    from widget_inference import INFERENCE_PIPELINE
    if not isinstance(node, dict) or node.get("elType") != "container":
        return None, 0.0, "not-a-container"
    if not (node.get("elements") or []):
        return None, 0.0, "empty-container"
    for kind, detector, _converter in INFERENCE_PIPELINE:
        try:
            if detector(node):
                # Detectors are binary — they don't carry a confidence
                # score. We synthesize one based on signal strength:
                # the more specific the detector is, the higher the
                # score (tabs/slides are very narrow, image/text-editor
                # are not in this list at all).
                return kind, _detector_confidence(kind), f"detector={kind}"
        except Exception:  # pragma: no cover — defensive
            continue
    # Last resort: fall back to plugin's `preferredWidget` if present
    settings = node.get("settings") or {}
    pref = settings.get("_preferred_widget") or settings.get("preferredWidget")
    if pref:
        return pref, 0.55, f"plugin.preferredWidget={pref}"
    return None, 0.0, "no-match"


def _detector_confidence(kind: str) -> float:
    """Heuristic confidence per detector. Tabs/slides/accordion are
    structurally distinctive (high). Icon-box / image-box are common
    patterns that frequently mis-classify against generic containers
    (medium). Spacer/divider trivially match (high)."""
    high = {"tabs", "slides", "accordion", "image-carousel",
            "divider", "spacer", "social-icons"}
    medium = {"icon-list", "icon-box", "image-box", "video",
              "counter", "progress", "star-rating", "toggle"}
    if kind in high:
        return 0.85
    if kind in medium:
        return 0.65
    return 0.6


def _slice_tokens_for_section(section_node: dict, global_json: dict) -> dict:
    """Return the subset of design tokens visible inside this section.

    A section's "token budget" is informational — it tells the developer
    which colors / fonts the section will consume from the active kit.
    """
    used_colors: set[str] = set()
    used_fonts: set[str] = set()

    def walk(n):
        if isinstance(n, dict):
            s = n.get("settings") or {}
            # color refs: inline hex
            for key in ("title_color", "color", "background_color", "text_color"):
                val = s.get(key)
                if isinstance(val, str) and val.startswith("#"):
                    used_colors.add(val.lower())
            # typography: font family
            fam = (s.get("typography_font_family") or {})
            if isinstance(fam, dict):
                v = fam.get("value")
                if v: used_fonts.add(v)
            elif isinstance(fam, str) and fam:
                used_fonts.add(fam)
            for c in n.get("elements") or []:
                walk(c)
        elif isinstance(n, list):
            for it in n:
                walk(it)

    walk(section_node)

    # Pair against global.json tokens we know about
    colors_map = global_json.get("colors") or []
    if isinstance(colors_map, dict):
        colors_map = [{"name": k, "value": v} for k, v in colors_map.items()]
    matched_colors = []
    for c in colors_map:
        if not isinstance(c, dict):
            continue
        val = (c.get("value") or c.get("hex") or "").lower()
        if val in used_colors:
            matched_colors.append(c.get("name") or val)

    return {
        "colors": matched_colors,
        "fonts": sorted(used_fonts),
        "inline_color_count": len(used_colors),
    }


def _walk_section_widgets(section_node: dict) -> list[dict]:
    """Enumerate descendant widgets inside a section with their inferred kind.

    Walks ONLY children — the section root is already reported by the
    section-level `_detect_widget(el)` call, so including it here would
    duplicate that pick in `child_widgets`.
    """
    widgets: list[dict] = []

    def walk(n, depth):
        if not isinstance(n, dict):
            return
        if depth > 0 and n.get("elType") == "container":
            kind, conf, reason = _detect_widget(n)
            if kind is not None:
                widgets.append({
                    "kind": kind,
                    "confidence": conf,
                    "reason": reason,
                    "node_id": n.get("id"),
                    "figma_name": (n.get("settings") or {}).get("_figma_name") or "",
                    "depth": depth,
                })
        for c in n.get("elements") or []:
            walk(c, depth + 1)

    walk(section_node, 0)
    return widgets


def build_plan(
    export_dir: Path,
    widget_floor: float = DEFAULT_WIDGET_CONFIDENCE_FLOOR,
) -> dict:
    """Build the plan and the widget-review queue from the extracted export.

    Returns a dict with keys:
      • plan: list[Section]            — full ordered plan
      • widget_review_queue: list      — items needing Claude widget review
      • preflight: dict                — preflight check result
      • stats: dict                    — counts for the orchestrator banner
    """
    from enrich import load_enrichment
    from section_finder import (
        find_real_sections, summarize as sf_summary, filter_hidden,
    )

    data_path = export_dir / "data.json"
    if not data_path.exists():
        raise SystemExit(f"data.json not found at {data_path}")
    data = json.loads(data_path.read_text())
    global_json = _load(export_dir / "global.json")
    enrichment = load_enrichment(export_dir)

    # Deep-copy so we never mutate the source tree.
    # The live import filters hidden / zero-opacity layers BEFORE section
    # finding — mirror that here so the plan matches what the import
    # will see.
    content = copy.deepcopy(data.get("content") or [])
    filter_hidden(content)
    real_sections = find_real_sections(content, enrichment.section_by_index)

    plan_entries: list[dict] = []
    widget_queue: list[dict] = []

    # Top-level sections from real_sections + anything else that's top-level
    top_level = [{"el": el, "section": rs} for el, rs in _pair_top_level(content, real_sections)]

    for i, item in enumerate(top_level):
        el = item["el"]
        rs = item["section"]
        s = el.get("settings") or {}
        figma_name = s.get("_figma_name") or (rs.figma_name if rs else "") or f"section[{i}]"
        kind = rs.kind if rs else "section"
        section_conf = rs.confidence if rs else 0.5
        reason = rs.reason if rs else "top-level container (no role detected)"

        widget_kind, widget_conf, widget_reason = _detect_widget(el)
        widgets_in_section = _walk_section_widgets(el)
        tokens_used = _slice_tokens_for_section(el, global_json)

        needs_review = (
            section_conf < widget_floor
            or any(w["confidence"] < widget_floor for w in widgets_in_section)
        )

        entry = {
            "index": i,
            "figma_name": figma_name,
            "kind": kind,
            "section_confidence": round(section_conf, 2),
            "section_reason": reason,
            "widget": widget_kind,
            "widget_confidence": round(widget_conf, 2) if widget_kind else None,
            "widget_reason": widget_reason,
            "child_widgets": [
                {"kind": w["kind"], "confidence": round(w["confidence"], 2),
                 "figma_name": w["figma_name"], "node_id": w["node_id"]}
                for w in widgets_in_section
            ],
            "tokens_used": tokens_used,
            "needs_review": needs_review,
        }
        plan_entries.append(entry)

        if needs_review:
            widget_queue.append({
                "section_index": i,
                "figma_name": figma_name,
                "section_confidence": entry["section_confidence"],
                "current_widget_pick": widget_kind,
                "widget_confidence": entry["widget_confidence"],
                "low_confidence_children": [
                    c for c in entry["child_widgets"] if c["confidence"] < widget_floor
                ],
                "instructions": (
                    "Consult the `elementor-widgets` skill catalog. For this "
                    f"section (kind={kind}, figma name=\"{figma_name}\"), the "
                    "deterministic detectors returned low confidence. Decide "
                    "whether the chosen widget (`current_widget_pick`) is "
                    "correct, or recommend a different one. Reply with a JSON "
                    "object: {\"widget\": \"<elementor-widget-name>\", "
                    "\"confidence\": 0.0..1.0, \"reason\": \"...\"} or "
                    "{\"keep\": true, \"reason\": \"...\"} to accept the "
                    "current pick. Reference the section's expected screenshot "
                    "if present at build/export/screenshots/sections/<figma_id>.png."
                ),
            })

    # Preflight check (sparse tokens, missing fonts, etc.)
    from preflight import run as preflight_run
    preflight = preflight_run(export_dir / "global.json")

    stats = {
        "total_sections": len(plan_entries),
        "needs_review": len(widget_queue),
        "kinds": sf_summary(real_sections) if real_sections else {},
    }

    return {
        "plan": plan_entries,
        "widget_review_queue": widget_queue,
        "preflight": preflight,
        "stats": stats,
        "source": {
            "export_dir": str(export_dir),
            "title": data.get("title"),
        },
    }


def _pair_top_level(content: list, real_sections: list) -> list[tuple]:
    """Pair top-level content entries with their RealSection (when one exists)."""
    rs_by_id = {id(rs.elementor_node): rs for rs in real_sections}
    out = []
    for el in content:
        out.append((el, rs_by_id.get(id(el))))
    return out


def write_plan(plan: dict) -> tuple[Path, Path]:
    """Persist the plan and the widget-review queue to build/."""
    BUILD.mkdir(parents=True, exist_ok=True)
    plan_path = BUILD / "build-plan.json"
    queue_path = BUILD / "widget-review-queue.json"
    plan_path.write_text(json.dumps(plan, indent=2, default=str))
    queue_path.write_text(json.dumps({
        "total": len(plan["widget_review_queue"]),
        "items": plan["widget_review_queue"],
    }, indent=2, default=str))
    return plan_path, queue_path


def _print_plan(plan: dict) -> None:
    print(f"\nBuild plan for: {plan['source'].get('title') or 'untitled'}")
    print(f"Source: {plan['source']['export_dir']}")
    print(f"Sections: {plan['stats']['total_sections']}  "
          f"review queue: {plan['stats']['needs_review']}")
    if plan['preflight']['issues']:
        print(f"\nPre-flight: {len(plan['preflight']['issues'])} issue(s)")
        for i in plan['preflight']['issues']:
            marker = "✗" if i['severity'] == 'error' else "⚠"
            print(f"  {marker} {i['kind']}: {i['detail']}")
    print(f"\n{'#':>3}  {'Kind':12s}  {'Conf':>5s}  {'Widget':18s}  {'WConf':>5s}  Name")
    print("─" * 90)
    for e in plan["plan"]:
        review_marker = "*" if e["needs_review"] else " "
        widget = e.get("widget") or "(none)"
        wconf = f"{e['widget_confidence']:.2f}" if e['widget_confidence'] is not None else "  — "
        print(f"{e['index']:3d}{review_marker} {e['kind']:12s}  "
              f"{e['section_confidence']:.2f}   {widget:18s}  {wconf}   {e['figma_name'][:40]}")
    if plan["widget_review_queue"]:
        print(f"\n* = needs Claude widget review (queued in build/widget-review-queue.json)")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--export-dir", help="Override path to extracted Figma export (default: build/export)")
    ap.add_argument("--widget-confidence-floor", type=float,
                    default=DEFAULT_WIDGET_CONFIDENCE_FLOOR,
                    help="Sections / widgets below this confidence go to the review queue.")
    ap.add_argument("--print", dest="print_table", action="store_true",
                    help="Print the plan as a human-readable table")
    args = ap.parse_args()

    if args.export_dir:
        export_dir = Path(args.export_dir)
    else:
        export_dir = _resolve_export_dir(BUILD)
        if not export_dir:
            print("✗ No data.json found under build/. Extract the ZIP first "
                  "(run import_elementor.py through Phase B, then retry).",
                  file=sys.stderr)
            return 2

    plan = build_plan(export_dir, args.widget_confidence_floor)
    plan_path, queue_path = write_plan(plan)
    print(f"✓ Plan written → {plan_path.relative_to(ROOT)}")
    print(f"✓ Widget review queue → {queue_path.relative_to(ROOT)} "
          f"({plan['stats']['needs_review']} item(s))")

    if args.print_table:
        _print_plan(plan)
    return 0


if __name__ == "__main__":
    sys.exit(main())
