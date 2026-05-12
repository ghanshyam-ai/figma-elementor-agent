"""
Produce a prioritized fix plan from import-report.json + diff/report.json.

The auto-fixer agent calls this between iterations; it returns the next
batch of patches to attempt as a JSON list ordered by impact.

Schema of each fix candidate:
    {
      "priority": 1..N,            # 1 = highest impact
      "kind": "color"|"spacing"|"typography"|"asset_missing"|"structural"|"manual_review"|"unknown",
      "source": "import-report"|"visual-diff",
      "node_id": "el00012",        # Elementor element id when known
      "y_band": [y0, y1],          # rendered y-band on the live page (visual-diff only)
      "drift": 0.18,               # 0..1 — only set for visual-diff sources
      "severity": "error"|"warn"|"info",
      "detail": "human-readable",
      "auto_patchable": true       # false → emit a hint, don't try patch_elementor.py
    }

Usage:
    python3 scripts/fix_plan.py                           # print top-N as table
    python3 scripts/fix_plan.py --json                    # emit as JSON
    python3 scripts/fix_plan.py --json --top 5            # cap output
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BUILD = ROOT / "build"


# Severity → numeric weight (higher = more important).
SEVERITY_WEIGHT = {"error": 0.30, "warn": 0.10, "info": 0.0}


def build_fix_plan(top: int | None = None) -> list[dict]:
    import_report = _load(BUILD / "import-report.json")
    diff_report = _load(BUILD / "diff" / "report.json")

    plan: list[dict] = []

    # --- Source 1: import-report risk areas (confidence + validation) ---
    for risk in (import_report.get("riskAreas") or []):
        sev = risk.get("severity") or "warn"
        kind_raw = risk.get("kind") or ""
        # Map kind to a patch type. "low-confidence" sections aren't
        # auto-patchable (they're a Claude-review prompt). Validation
        # warnings depend on their code.
        if kind_raw == "low-confidence":
            kind = "manual_review"
            auto = False
        elif "color" in (risk.get("detail") or "").lower():
            kind = "color"
            auto = True
        elif "font" in (risk.get("detail") or "").lower() or "typography" in (risk.get("detail") or "").lower():
            kind = "typography"
            auto = True
        elif "spacing" in (risk.get("detail") or "").lower() or "padding" in (risk.get("detail") or "").lower():
            kind = "spacing"
            auto = True
        else:
            kind = "unknown"
            auto = False
        plan.append({
            "kind": kind,
            "source": "import-report",
            "node_id": risk.get("nodeId"),
            "y_band": None,
            "drift": None,
            "severity": sev,
            "detail": risk.get("detail") or "",
            "auto_patchable": auto,
            "_score": SEVERITY_WEIGHT.get(sev, 0.0) + (0.05 if auto else 0.0),
        })

    # --- Source 2: visual-diff regions ----------------------------------
    manual_bands = {
        (b.get("y0"), b.get("y1"))
        for b in (diff_report.get("manual_review_regions") or [])
    }
    for region in (diff_report.get("regions") or []):
        drift = region.get("drift") or 0.0
        if drift < 0.05:
            continue
        y_band = (region.get("y0"), region.get("y1"))
        if y_band in manual_bands:
            kind = "manual_review"
            auto = False
        elif drift >= 0.15:
            # High-drift regions should go to Claude review, not heuristic
            # spacing/typography patches. Auto-patching a structural mismatch
            # by tweaking padding burns iteration budget and rarely converges.
            kind = "manual_review"
            auto = False
        else:
            kind = "spacing" if drift < 0.10 else "typography"
            auto = True
        plan.append({
            "kind": kind,
            "source": "visual-diff",
            "node_id": None,
            "y_band": list(y_band),
            "drift": drift,
            "severity": "error" if drift > 0.5 else ("warn" if drift > 0.15 else "info"),
            "detail": f"y={y_band[0]}..{y_band[1]}  drift={drift*100:.1f}%",
            "auto_patchable": auto,
            "_score": min(1.0, drift) + SEVERITY_WEIGHT.get("error" if drift > 0.5 else "warn", 0.0),
        })

    # Sort by score desc; assign priority in that order.
    plan.sort(key=lambda x: -x["_score"])
    for i, item in enumerate(plan, start=1):
        item["priority"] = i
        item.pop("_score", None)
    if top:
        plan = plan[:top]
    return plan


def _load(path: Path) -> dict:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text())
    except json.JSONDecodeError:
        return {}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--top", type=int, default=None)
    args = ap.parse_args()

    plan = build_fix_plan(args.top)
    if args.json:
        json.dump(plan, sys.stdout, indent=2)
        sys.stdout.write("\n")
        return 0

    if not plan:
        print("✓ No fix candidates — page is at acceptable drift.")
        return 0

    print(f"Fix plan ({len(plan)} candidates, ordered by impact):\n")
    print(f"{'Pri':>3}  {'Kind':12s}  {'Sev':5s}  {'Source':14s}  {'Auto':4s}  Detail")
    print("─" * 80)
    for item in plan:
        auto_label = "yes" if item["auto_patchable"] else "no"
        print(f"{item['priority']:3d}  {item['kind']:12s}  {item['severity']:5s}  {item['source']:14s}  {auto_label:4s}  {item['detail'][:60]}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
