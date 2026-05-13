"""
Build Claude-as-Author review bundles.

This script does NOT call any LLM itself — it prepares the JSON bundle
that the orchestrator agent dispatches via the Agent tool, and writes
the bundles to `build/claude-review/` so the agent can read them at
review time.

Bundle schema (per section):
    {
      "section_kind":  "hero" | "feature-grid" | "footer-column" | ...,
      "node_id":       "<post-regen elementor id>",
      "figma_node_id": "<original figma node id>",
      "elementor_json": <current subtree from data.json — the live tree>,
      "ai_subtree":     <matching ai-layout subtree>,
      "tokens":         <slice of global.json relevant to this section>,
      "expected_crop":  "build/<export>/screenshots/sections/<id>.png",
      "live_crop":      "build/diff/sections/<id>.png" (created later by the reviewer),
      "kit_globals":    {colors, typography} (selected fields only),
      "reason":         "confidence=0.35" | "drift=22%" | "no-image-widgets",
      "confidence":     0.0..1.0,
      "drift":          0.0..1.0  (when sourced from visual-diff),
      "instructions":   "Compare expected_crop to live_crop. Return a JSON patch ..."
    }

Output files: `build/claude-review/section-<idx>.json` (one per dispatch).
A top-level `build/claude-review/queue.json` lists every bundle the agent
should process, in priority order, plus the cap (5 dispatches per build).

Usage from CLI (for debugging):
    python3 scripts/claude_review.py --build --confidence 0.6 --drift 0.15
    python3 scripts/claude_review.py --list
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
BUILD = ROOT / "build"
REVIEW_DIR = BUILD / "claude-review"

# Cap per CLAUDE.md line 154: at most 5 Claude-as-Author dispatches per build.
MAX_DISPATCHES = 5

# Default thresholds — caller can override.
DEFAULT_CONFIDENCE_FLOOR = 0.6
DEFAULT_DRIFT_CEILING = 0.15

# Section-purpose weights — higher means "more likely to be worth a
# dispatch slot when budget is tight". Tuned so hero/pricing always
# beat generic feature-grid sections; chrome (header/footer) gets a
# huge boost because errors there propagate to every page.
SECTION_PURPOSE_WEIGHTS: dict[str, float] = {
    "header":         1.6,
    "footer":         1.6,
    "hero":           1.4,
    "pricing":        1.4,
    "cta":            1.3,
    "navigation":     1.3,
    "form":           1.2,
    "testimonial":    1.1,
    "feature-grid":   1.0,
    "feature":        1.0,
    "trust-row":      0.9,
    "stats":          0.9,
    "logo-strip":     0.7,
    "spacer":         0.5,
    "unknown":        1.0,
}

# Severity score weights — drift and confidence-deficit contribute
# directly; combined with purpose weight at the end.
WEIGHT_DRIFT = 0.6
WEIGHT_CONFIDENCE_DEFICIT = 0.4

# First-run vs Nth-run budgets — the orchestrator passes `prior_runs`
# (count of successful archived runs for this slug). The first build
# of a fresh design gets a higher dispatch budget; later runs lean on
# fix_history and need fewer slots.
FIRST_RUN_DISPATCHES = 8
INCREMENTAL_DISPATCHES = 3
FIRST_RUN_CONFIDENCE_FLOOR = 0.7
INCREMENTAL_CONFIDENCE_FLOOR = 0.55
FIRST_RUN_DRIFT_CEILING = 0.12
INCREMENTAL_DRIFT_CEILING = 0.18


def adaptive_budget(prior_runs: int) -> tuple[int, float, float]:
    """Return (max_dispatches, confidence_floor, drift_ceiling) tuned to
    whether this is the first build of the design or an incremental run.

    First runs: more aggressive review (high budget, tighter floors)
    because we don't yet have a fix_history cache or prior gate to lean
    on. Nth runs: smaller budget, looser thresholds — the cache handles
    most of what would otherwise need review."""
    if prior_runs <= 0:
        return (FIRST_RUN_DISPATCHES, FIRST_RUN_CONFIDENCE_FLOOR, FIRST_RUN_DRIFT_CEILING)
    return (INCREMENTAL_DISPATCHES, INCREMENTAL_CONFIDENCE_FLOOR, INCREMENTAL_DRIFT_CEILING)


def build_queue_from_plan(
    confidence_floor: float = DEFAULT_CONFIDENCE_FLOOR,
    max_dispatches: int = MAX_DISPATCHES,
) -> list[dict]:
    """Build Claude review bundles from `build/build-plan.json` directly.

    Use this BEFORE `import_elementor.py` runs — it lets the orchestrator
    dispatch Claude-as-Author at plan stage, eliminating one round of
    import + render when a section's widget choice is obviously low
    confidence. The bundles produced here carry the plan entry (no live
    page yet), so Claude's job is "pick the right widget" rather than
    "explain a visual diff".

    Returns the same shape as build_queue() so the orchestrator's
    dispatch loop is identical.
    """
    plan_path = BUILD / "build-plan.json"
    if not plan_path.exists():
        return []
    plan = _load(plan_path)
    queue: list[dict] = []

    items = (plan.get("widget_review_queue") or [])
    if not items:
        # Fall back to walking the full plan and picking entries flagged
        # as needs_review — handy when the plan was generated by an
        # older build_plan.py that didn't pre-compute the queue.
        items = [
            {
                "section_index": e["index"],
                "figma_name": e["figma_name"],
                "section_confidence": e.get("section_confidence"),
                "current_widget_pick": e.get("widget"),
                "widget_confidence": e.get("widget_confidence"),
            }
            for e in (plan.get("plan") or [])
            if e.get("needs_review")
        ]

    items = items[:max_dispatches]
    REVIEW_DIR.mkdir(parents=True, exist_ok=True)
    export_dir = Path(plan.get("source", {}).get("export_dir") or BUILD / "export")

    for i, item in enumerate(items):
        figma_name = item.get("figma_name")
        # Try to locate an expected screenshot — the plan-stage bundle
        # has no live render to diff against, only the Figma reference.
        expected_crop = None
        shots = export_dir / "screenshots" / "sections"
        if shots.exists() and figma_name:
            safe = str(figma_name).replace(":", "_").replace("/", "_").replace(" ", "-")
            for cand in (shots / f"{safe}.png", shots / f"{safe}@2x.png"):
                if cand.exists():
                    expected_crop = cand
                    break
        bundle = {
            "_path": str((REVIEW_DIR / f"plan-section-{i:02d}.json").resolve()),
            "stage": "plan",
            "section_kind": None,
            "figma_name": figma_name,
            "section_index": item.get("section_index"),
            "section_confidence": item.get("section_confidence"),
            "current_widget_pick": item.get("current_widget_pick"),
            "widget_confidence": item.get("widget_confidence"),
            "low_confidence_children": item.get("low_confidence_children") or [],
            "expected_crop": str(expected_crop) if expected_crop else None,
            "live_crop": None,            # no live render yet at plan stage
            "kit_globals": _selected_kit_globals(),
            "instructions": item.get("instructions") or (
                "Plan-stage widget review. Consult the `elementor-widgets` "
                "skill catalog (skills/elementor-widgets/). For the section "
                f"named \"{figma_name}\", decide whether `current_widget_pick` "
                "is the right Elementor widget. Reply with a JSON object: "
                "`{\"widget\": \"<name>\", \"confidence\": 0..1, \"reason\": \"...\"}` "
                "to override the pick, or `{\"keep\": true, \"reason\": \"...\"}` "
                "to accept it. Reference `expected_crop` (the Figma screenshot) "
                "to make the call."
            ),
        }
        Path(bundle["_path"]).write_text(json.dumps(bundle, indent=2, default=str))
        queue.append(bundle)

    (REVIEW_DIR / "plan-queue.json").write_text(json.dumps({
        "total": len(queue),
        "stage": "plan",
        "bundles": [b["_path"] for b in queue],
    }, indent=2))
    return queue


def build_queue(
    confidence_floor: float = DEFAULT_CONFIDENCE_FLOOR,
    drift_ceiling: float = DEFAULT_DRIFT_CEILING,
    max_dispatches: int = MAX_DISPATCHES,
    prior_runs: int | None = None,
) -> list[dict]:
    """Compile the list of sections that need a Claude review.

    Inputs (all under `build/`):
      • import-report.json — confidence scores + risk areas
      • diff/report.json — per-region + per-section drift
      • state.json — export_dir, placements_summary
      • id_map.json — pre-regen → post-regen Elementor ids

    Scoring is a priority queue, not a hard threshold:
      severity = w_drift * drift + w_conf * (1 - confidence)
      score    = severity * section_purpose_weight

    This eliminates the "14% drift / 0.72 confidence dead band" — every
    section gets a score, the top N (where N is the budget) win the
    dispatch slots. A section with drift just under the ceiling that
    happens to be a hero will still get reviewed; a 0.5%-drift logo
    strip will not.

    When `prior_runs` is provided, the budget and thresholds adapt to
    incremental builds — see `adaptive_budget()`.

    Returns a list of bundle dicts (highest priority first), capped at
    the budget. Each bundle is also written to disk.
    """
    state = _load(BUILD / "state.json")
    import_report = _load(BUILD / "import-report.json")
    diff_report = _load(BUILD / "diff" / "report.json")
    export_dir = Path(state.get("export_dir") or BUILD / "export")

    if prior_runs is not None:
        ab = adaptive_budget(prior_runs)
        max_dispatches = ab[0]
        confidence_floor = ab[1]
        drift_ceiling = ab[2]

    REVIEW_DIR.mkdir(parents=True, exist_ok=True)

    # Build a {node_id → section_kind} lookup once. Per-section drift
    # entries carry data_id (post-regen) + figma_name; ai-layout carries
    # role / sectionPurpose. Stitch the two so each candidate gets a
    # section_kind for purpose weighting.
    section_kind_by_node = _build_section_kind_lookup(export_dir, state)

    candidates: dict[str, dict] = {}  # keyed by node_id (or y_band str)

    def upsert(key: str, **fields) -> dict:
        existing = candidates.get(key)
        if existing is None:
            existing = candidates[key] = fields
        else:
            # Merge — drift wins on max, confidence on min.
            if fields.get("drift") is not None:
                cur = existing.get("drift")
                existing["drift"] = max(cur or 0.0, fields["drift"])
            if fields.get("confidence") is not None:
                cur = existing.get("confidence")
                existing["confidence"] = min(cur if cur is not None else 1.0, fields["confidence"])
            for k, v in fields.items():
                if k in ("drift", "confidence"):
                    continue
                existing.setdefault(k, v)
        return existing

    # --- Source 1: low-confidence risk areas ---
    for risk in (import_report.get("riskAreas") or []):
        if risk.get("kind") != "low-confidence":
            continue
        detail = risk.get("detail") or ""
        conf = _parse_confidence(detail)
        node_id = risk.get("nodeId") or f"_risk_{len(candidates)}"
        upsert(
            node_id,
            source="low-confidence",
            node_id=risk.get("nodeId"),
            node_name=risk.get("nodeName"),
            confidence=conf,
            drift=None,
            severity=risk.get("severity") or "warn",
        )

    # --- Source 2: per-section drift (preferred) ---
    sections = diff_report.get("sections") or []
    for sec in sections:
        # Skip sections that the DOM-structure diff rescued — pixel
        # drift is misleading there (animation / FOUT / lazy load).
        if sec.get("dom_rescued") or sec.get("passed") is True:
            continue
        drift = sec.get("drift")
        if drift is None:
            continue
        node_id = sec.get("data_id") or f"_section_{len(candidates)}"
        upsert(
            node_id,
            source="per-section-diff",
            node_id=sec.get("data_id"),
            node_name=sec.get("figma_name"),
            drift=drift,
            confidence=None,
            severity="error" if drift > 0.5 else "warn",
        )

    # --- Source 3: legacy band-level regions (fallback only) ---
    if not sections:
        for region in (diff_report.get("regions") or []):
            drift = region.get("drift") or 0.0
            key = f"_y{region.get('y0')}-{region.get('y1')}"
            upsert(
                key,
                source="visual-diff",
                node_id=None,
                y_band=[region.get("y0"), region.get("y1")],
                drift=drift,
                confidence=None,
                severity="error" if drift > 0.5 else "warn",
            )

    # --- Filter to candidates that exceed at least one threshold ---
    pool: list[dict] = []
    for c in candidates.values():
        drift = c.get("drift") or 0.0
        conf = c.get("confidence")
        passes_drift = drift >= drift_ceiling
        passes_conf = conf is not None and conf < confidence_floor
        if not (passes_drift or passes_conf):
            continue
        pool.append(c)

    # --- Score and prioritize ---
    for c in pool:
        drift = c.get("drift") or 0.0
        conf = c.get("confidence")
        deficit = (1.0 - conf) if conf is not None else 0.0
        severity_score = WEIGHT_DRIFT * drift + WEIGHT_CONFIDENCE_DEFICIT * deficit
        kind = (
            section_kind_by_node.get(c.get("node_id"))
            or _purpose_from_name(c.get("node_name"))
            or "unknown"
        )
        purpose_weight = SECTION_PURPOSE_WEIGHTS.get(kind, 1.0)
        c["_score"] = severity_score * purpose_weight
        c["_kind_inferred"] = kind
        # Reason string for telemetry.
        bits = []
        if drift:
            bits.append(f"drift={drift*100:.1f}%")
        if conf is not None:
            bits.append(f"confidence={conf:.2f}")
        bits.append(f"kind={kind}")
        bits.append(f"weight={purpose_weight}")
        c["reason"] = " ".join(bits)

    candidates_list = sorted(pool, key=lambda c: -c["_score"])
    candidates_list = candidates_list[:max_dispatches]
    for c in candidates_list:
        c.pop("_score", None)
        c.pop("_kind_inferred", None)
    candidates = candidates_list  # type: ignore[assignment]

    # --- Materialize each candidate as a bundle file ---
    bundles: list[dict] = []
    for i, c in enumerate(candidates):
        bundle = _materialize_bundle(c, i, export_dir)
        if bundle:
            bundles.append(bundle)
            bundle_path = REVIEW_DIR / f"section-{i:02d}.json"
            bundle_path.write_text(json.dumps(bundle, indent=2, default=str))

    queue_path = REVIEW_DIR / "queue.json"
    queue_path.write_text(json.dumps({
        "total": len(bundles),
        "cap": max_dispatches,
        "bundles": [b["_path"] for b in bundles],
    }, indent=2))

    return bundles


def _materialize_bundle(candidate: dict, idx: int, export_dir: Path) -> dict | None:
    """Build the full review bundle for one candidate.

    Looks up the elementor subtree, ai-layout subtree, and matching
    expected/live crops. Returns None if essential inputs are missing.
    """
    node_id = candidate.get("node_id")
    elementor_json = _load_elementor_subtree(node_id) if node_id else None
    ai_subtree = _load_ai_subtree(export_dir, node_id, candidate.get("node_name"))
    tokens = _load_tokens(export_dir)

    expected_crop = _find_expected_crop(export_dir, node_id, ai_subtree)
    live_crop = _find_live_crop(node_id, candidate.get("y_band"))

    bundle = {
        "_path": str((REVIEW_DIR / f"section-{idx:02d}.json").resolve()),
        "section_kind": (ai_subtree or {}).get("role") or (ai_subtree or {}).get("sectionPurpose") or "unknown",
        "node_id": node_id,
        "figma_node_id": (ai_subtree or {}).get("id"),
        "elementor_json": elementor_json,
        "ai_subtree": ai_subtree,
        "tokens": tokens,
        "expected_crop": str(expected_crop) if expected_crop else None,
        "live_crop": str(live_crop) if live_crop else None,
        "kit_globals": _selected_kit_globals(),
        "reason": candidate.get("reason"),
        "confidence": candidate.get("confidence"),
        "drift": candidate.get("drift"),
        "y_band": candidate.get("y_band"),
        "source": candidate.get("source"),
        "instructions": (
            "You are reviewing a single section of an Elementor page that "
            "was auto-generated from a Figma export. The pixel diff or "
            "confidence score suggests this section is wrong. Compare "
            "`expected_crop` (the Figma design) against `live_crop` (the "
            "rendered page) — or, when no live crop is available, against "
            "`elementor_json` directly. Decide whether the right Elementor "
            "widget is used (e.g. nav-menu, icon-list, image-box, accordion, "
            "tabs, posts) and whether layout / spacing / colors match. "
            "Return a JSON object: "
            "`{\"replace_subtree\": <new elementor json>}` to rewrite the "
            "section, or `{\"patches\": [{op,path,value}, ...]}` for "
            "targeted RFC-6902 changes. Prefer minimal patches unless the "
            "widget choice itself is wrong. Reference `kit_globals` to use "
            "`__globals__` bindings instead of inline hex / px values."
        ),
    }
    return bundle


def apply_review_result(bundle_path: Path, result: dict) -> dict:
    """Apply a Claude review result back to the Elementor tree.

    Result schema (one of):
      • {"replace_subtree": <node>}     — wholesale replacement
      • {"patches": [{op,path,value}]}  — RFC-6902-style patches
      • {"skip": true, "reason": "..."} — Claude abstained

    Returns {"applied": bool, "kind": "replace"|"patch"|"skip", "node_id": ...}.
    The caller is responsible for re-running `import_elementor.py
    --skip-bootstrap --replay-claude-review` or POSTing the patched tree.
    """
    bundle = json.loads(Path(bundle_path).read_text())
    out = {"applied": False, "kind": "skip", "node_id": bundle.get("node_id")}
    if result.get("skip"):
        out["reason"] = result.get("reason")
        return out
    target_path = Path(bundle["_path"]).with_suffix(".applied.json")
    target_path.write_text(json.dumps(result, indent=2))
    if "replace_subtree" in result:
        out["kind"] = "replace"
        out["applied"] = True
    elif result.get("patches"):
        out["kind"] = "patch"
        out["applied"] = True
        out["patch_count"] = len(result["patches"])
    return out


# --- internal helpers -----------------------------------------------------

def _load(p: Path) -> dict:
    if not p.exists():
        return {}
    try:
        return json.loads(p.read_text())
    except json.JSONDecodeError:
        return {}


def _build_section_kind_lookup(export_dir: Path, state: dict) -> dict[str, str]:
    """{node_id (post-regen) → section_kind} so per-section drift entries
    can be weighted by their purpose. The lookup chains:
        live data-id (post-regen)
          → id_map (post → pre)
          → build/data.json _figma_id
          → ai-layout role / sectionPurpose
    Best-effort; sections without a recoverable kind get "unknown".
    """
    id_map = _load(BUILD / "id_map.json")
    if not isinstance(id_map, dict):
        return {}
    # id_map is {pre: post} after my marker fix — invert.
    post_to_pre = {post: pre for pre, post in id_map.items()}

    data = _load(BUILD / "data.json")
    content = data.get("content") if isinstance(data, dict) else data
    figma_id_by_pre: dict[str, str] = {}
    figma_name_by_pre: dict[str, str] = {}

    def collect(n):
        if isinstance(n, dict):
            nid = n.get("id")
            s = n.get("settings") or {}
            if nid:
                fid = s.get("_figma_id")
                fname = s.get("_figma_name") or s.get("_figma_section_name")
                if fid:
                    figma_id_by_pre[nid] = fid
                if fname:
                    figma_name_by_pre[nid] = fname
            for c in n.get("elements") or []:
                collect(c)
    if isinstance(content, list):
        for top in content:
            collect(top)

    # Walk ai-layout once to build {figma_id: role} and {name_lower: role}.
    ai_path = export_dir / "ai-layout.json"
    role_by_figma_id: dict[str, str] = {}
    role_by_name: dict[str, str] = {}
    if ai_path.exists():
        try:
            ai = json.loads(ai_path.read_text())
        except json.JSONDecodeError:
            ai = {}

        def walk_ai(sec):
            if not isinstance(sec, dict):
                return
            role = sec.get("role") or sec.get("sectionPurpose") or sec.get("_figma_section_purpose")
            if role:
                if sec.get("id"):
                    role_by_figma_id[sec["id"]] = role
                if sec.get("name"):
                    role_by_name[sec["name"].strip().lower()] = role
            for c in sec.get("children") or []:
                walk_ai(c)
        for top in ai.get("sections") or []:
            walk_ai(top)

    out: dict[str, str] = {}
    for post, pre in post_to_pre.items():
        fid = figma_id_by_pre.get(pre)
        fname = (figma_name_by_pre.get(pre) or "").strip().lower()
        role = role_by_figma_id.get(fid or "") or role_by_name.get(fname)
        if not role:
            role = _purpose_from_name(fname)
        if role:
            out[post] = role
    return out


def _purpose_from_name(name: str | None) -> str | None:
    """Heuristic name → purpose mapping as a last resort. Only fires when
    ai-layout doesn't carry an explicit role for the section."""
    if not name:
        return None
    n = name.lower()
    keywords = [
        ("header", "header"), ("navbar", "header"), ("navigation", "navigation"),
        ("footer", "footer"),
        ("hero", "hero"), ("banner", "hero"), ("intro", "hero"),
        ("pricing", "pricing"), ("plans", "pricing"), ("tiers", "pricing"),
        ("cta", "cta"), ("call to action", "cta"), ("call-to-action", "cta"),
        ("testimonial", "testimonial"), ("review", "testimonial"),
        ("feature", "feature-grid"), ("services", "feature-grid"),
        ("trust", "trust-row"), ("partners", "trust-row"), ("clients", "trust-row"),
        ("stats", "stats"), ("metrics", "stats"), ("numbers", "stats"),
        ("logo", "logo-strip"),
        ("form", "form"), ("contact", "form"), ("signup", "form"),
    ]
    for needle, role in keywords:
        if needle in n:
            return role
    return None


def _parse_confidence(detail: str) -> float | None:
    import re
    m = re.search(r"confidence=([\d.]+)", detail or "")
    if not m:
        return None
    try:
        return float(m.group(1))
    except ValueError:
        return None


def _load_elementor_subtree(node_id: str | None) -> dict | None:
    """Look up the current Elementor subtree by id from the live data.

    Uses the `id_map.json` produced by the bridge after iterate_data
    regenerates ids — see `import_elementor.py` post-page-write block.
    Falls back to a scan of `build/data.json` if id_map isn't present.
    """
    if not node_id:
        return None
    id_map = _load(BUILD / "id_map.json")
    target_id = id_map.get(node_id, node_id) if isinstance(id_map, dict) else node_id
    data_path = BUILD / "data.json"
    if not data_path.exists():
        return None
    tree = _load(data_path)
    content = tree.get("content") if isinstance(tree, dict) else tree

    def walk(n):
        if not isinstance(n, dict):
            return None
        if n.get("id") == target_id:
            return n
        for c in n.get("elements") or []:
            r = walk(c)
            if r is not None:
                return r
        return None

    if isinstance(content, list):
        for top in content:
            r = walk(top)
            if r is not None:
                return r
    return None


def _load_ai_subtree(export_dir: Path, node_id: str | None, name: str | None) -> dict | None:
    ai_path = export_dir / "ai-layout.json"
    if not ai_path.exists():
        return None
    ai = _load(ai_path)
    target_name = (name or "").strip().lower()
    target_id = (node_id or "").strip()

    def walk(sec):
        if not isinstance(sec, dict):
            return None
        if target_id and sec.get("id") == target_id:
            return sec
        if target_name and (sec.get("name") or "").strip().lower() == target_name:
            return sec
        for c in sec.get("children") or []:
            r = walk(c)
            if r is not None:
                return r
        return None

    for top in ai.get("sections") or []:
        r = walk(top)
        if r is not None:
            return r
    return None


def _load_tokens(export_dir: Path) -> dict:
    p = export_dir / "global.json"
    if not p.exists():
        p = export_dir / "tokens.json"
    return _load(p)


def _selected_kit_globals() -> dict:
    """Return the slim slice of kit globals Claude needs: color slugs + typography slugs."""
    state = _load(BUILD / "state.json")
    return state.get("kit_globals") or {}


def _find_expected_crop(export_dir: Path, node_id: str | None, ai_subtree: dict | None) -> Path | None:
    if not node_id and not ai_subtree:
        return None
    shots = export_dir / "screenshots" / "sections"
    if not shots.exists():
        return None
    candidate_ids = []
    if node_id:
        candidate_ids.append(node_id)
    if ai_subtree and ai_subtree.get("id"):
        candidate_ids.append(ai_subtree["id"])
    for cid in candidate_ids:
        for suffix in (".png", "@2x.png", ".jpg"):
            p = shots / f"{cid}{suffix}"
            if p.exists():
                return p
    return None


def _find_live_crop(node_id: str | None, y_band: list | None) -> Path | None:
    if not node_id and not y_band:
        return None
    crops_dir = BUILD / "diff" / "sections"
    if not crops_dir.exists():
        return None
    if node_id:
        p = crops_dir / f"{node_id}.png"
        if p.exists():
            return p
    if y_band and len(y_band) == 2:
        p = crops_dir / f"y{y_band[0]}-{y_band[1]}.png"
        if p.exists():
            return p
    return None


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--build", action="store_true", help="Build the review queue from post-import data")
    ap.add_argument("--from-plan", action="store_true",
                    help="Build a plan-stage review queue from build/build-plan.json. "
                         "Use this BEFORE the import runs to catch wrong widget picks "
                         "without paying for a render cycle.")
    ap.add_argument("--list", action="store_true", help="List existing bundles")
    ap.add_argument("--confidence", type=float, default=DEFAULT_CONFIDENCE_FLOOR)
    ap.add_argument("--drift", type=float, default=DEFAULT_DRIFT_CEILING)
    ap.add_argument("--max", type=int, default=MAX_DISPATCHES)
    ap.add_argument("--prior-runs", type=int, default=None,
                    help="Number of prior successful runs for this slug. "
                         "When supplied, overrides --max / --confidence / --drift "
                         "with the adaptive budget (first-run aggressive, "
                         "Nth-run conservative — fix_history covers the rest).")
    args = ap.parse_args()

    if args.list:
        queue = _load(REVIEW_DIR / "queue.json")
        bundles = queue.get("bundles", [])
        if not bundles:
            print("(no claude-review queue — run --build first)")
            return 0
        for b in bundles:
            payload = _load(Path(b))
            print(f"  • {Path(b).name}  kind={payload.get('section_kind')}  "
                  f"reason={payload.get('reason')}  "
                  f"expected_crop={payload.get('expected_crop')}")
        return 0

    if args.from_plan:
        bundles = build_queue_from_plan(args.confidence, args.max)
        if not bundles:
            print("✓ No plan-stage widget reviews needed (all picks above confidence floor).")
            return 0
        print(f"✓ Built {len(bundles)} plan-stage review bundle(s) under {REVIEW_DIR}")
        for b in bundles:
            print(f"  • {Path(b['_path']).name}  figma={b.get('figma_name')}  "
                  f"pick={b.get('current_widget_pick')} "
                  f"(conf={b.get('widget_confidence')})")
        return 0

    if args.build:
        bundles = build_queue(args.confidence, args.drift, args.max, prior_runs=args.prior_runs)
        if not bundles:
            print("✓ No sections need Claude review (all above confidence + below drift).")
            return 0
        print(f"✓ Built {len(bundles)} review bundle(s) under {REVIEW_DIR}")
        for b in bundles:
            print(f"  • {Path(b['_path']).name}  reason={b.get('reason')}  "
                  f"kind={b.get('section_kind')}")
        return 0

    ap.print_help()
    return 1


if __name__ == "__main__":
    sys.exit(main())
