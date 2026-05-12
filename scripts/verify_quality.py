"""
Quality gate — assert a build is genuinely complete before declaring success.

The orchestrator calls this between Phase J (review) and printing the
final summary. It exits non-zero (and prints a structured FAIL line)
when any of the following fail:

    • diff/report.json::drift > --drift-threshold   (default 0.05)
    • diff/report.json::passed is False             (covers no-baseline, manual_review)
    • state.json::placements_summary lacks header   (with --require-header)
    • state.json::placements_summary lacks footer   (with --require-footer)
    • import-report.json::global_coverage < --min-global-coverage (default 0.7)

Usage:
    python3 scripts/verify_quality.py
    python3 scripts/verify_quality.py --drift-threshold 0.05 --min-global-coverage 0.7

The orchestrator must call this BEFORE printing "Build complete"; if it
exits non-zero the run is reported as FAILED with the offending metric.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BUILD = ROOT / "build"


def _load(p: Path) -> dict:
    if not p.exists():
        return {}
    try:
        return json.loads(p.read_text())
    except json.JSONDecodeError:
        return {}


def verify(
    drift_threshold: float = 0.05,
    require_header: bool = True,
    require_footer: bool = True,
    min_global_coverage: float = 0.7,
) -> tuple[bool, list[dict]]:
    """Return (passed, failures). Each failure is {check, expected, actual, detail}."""
    failures: list[dict] = []

    state = _load(BUILD / "state.json")
    import_report = _load(BUILD / "import-report.json")
    diff_report = _load(BUILD / "diff" / "report.json")

    # --- check 1: drift ---
    if diff_report.get("no_baseline"):
        failures.append({
            "check": "drift",
            "expected": f"≤ {drift_threshold}",
            "actual": "no-baseline",
            "detail": "Plugin export missing full-page screenshot — cannot verify drift.",
        })
    elif diff_report:
        drift = diff_report.get("drift")
        if drift is None:
            failures.append({
                "check": "drift",
                "expected": f"≤ {drift_threshold}",
                "actual": "missing",
                "detail": "diff/report.json has no `drift` value.",
            })
        elif drift > drift_threshold:
            failures.append({
                "check": "drift",
                "expected": f"≤ {drift_threshold * 100:.1f}%",
                "actual": f"{drift * 100:.2f}%",
                "detail": "Live page does not match the design within threshold.",
            })
        if diff_report.get("manual_review_regions"):
            failures.append({
                "check": "manual_review_regions",
                "expected": "0 bands",
                "actual": f"{len(diff_report['manual_review_regions'])} bands",
                "detail": "Screenshot-fallback bands present — sections are non-functional.",
            })
        # Multi-breakpoint check: if per_breakpoint exists, all must pass.
        for bp, r in (diff_report.get("per_breakpoint") or {}).items():
            if bp in ("", "desktop"):
                continue
            d = r.get("drift")
            if isinstance(d, (int, float)) and d > drift_threshold:
                failures.append({
                    "check": f"drift_{bp}",
                    "expected": f"≤ {drift_threshold * 100:.1f}%",
                    "actual": f"{d * 100:.2f}%",
                    "detail": f"{bp} breakpoint exceeds drift threshold.",
                })
    else:
        failures.append({
            "check": "drift",
            "expected": "diff report present",
            "actual": "missing",
            "detail": "build/diff/report.json not found — run visual_compare.py.",
        })

    # --- check 2 + 3: header + footer placements ---
    placements = state.get("placements_summary") or {}
    if require_header and placements.get("header", 0) < 1:
        failures.append({
            "check": "header_placement",
            "expected": "≥ 1 header template",
            "actual": str(placements.get("header", 0)),
            "detail": "Header was not detected — it will render inline on the page "
                      "instead of as a Theme Builder template. "
                      "Add `header_pattern` to project-config.json or improve "
                      "the Figma layer naming.",
        })
    if require_footer and placements.get("footer", 0) < 1:
        failures.append({
            "check": "footer_placement",
            "expected": "≥ 1 footer template",
            "actual": str(placements.get("footer", 0)),
            "detail": "Footer was not detected — see header note above.",
        })

    # --- check 4: global coverage ---
    coverage = (import_report.get("global_coverage") or {})
    color_cov = coverage.get("colors")
    if isinstance(color_cov, (int, float)) and color_cov < min_global_coverage:
        failures.append({
            "check": "global_coverage_colors",
            "expected": f"≥ {min_global_coverage * 100:.0f}%",
            "actual": f"{color_cov * 100:.1f}%",
            "detail": "Widgets still hold inline hex values instead of `globals/colors?id=…` refs.",
        })
    type_cov = coverage.get("typography")
    if isinstance(type_cov, (int, float)) and type_cov < min_global_coverage:
        failures.append({
            "check": "global_coverage_typography",
            "expected": f"≥ {min_global_coverage * 100:.0f}%",
            "actual": f"{type_cov * 100:.1f}%",
            "detail": "Widgets still hold inline typography instead of `globals/typography?id=…` refs.",
        })

    return (not failures, failures)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--drift-threshold", type=float, default=0.05)
    ap.add_argument("--min-global-coverage", type=float, default=0.7)
    ap.add_argument("--no-require-header", action="store_true",
                    help="Skip the header-placement check (use for partial runs).")
    ap.add_argument("--no-require-footer", action="store_true")
    ap.add_argument("--json", action="store_true", help="Emit JSON instead of human output.")
    args = ap.parse_args()

    passed, failures = verify(
        drift_threshold=args.drift_threshold,
        require_header=not args.no_require_header,
        require_footer=not args.no_require_footer,
        min_global_coverage=args.min_global_coverage,
    )

    if args.json:
        out = {"passed": passed, "failures": failures}
        json.dump(out, sys.stdout, indent=2)
        sys.stdout.write("\n")
        return 0 if passed else 1

    if passed:
        print("✓ Quality gate PASSED — drift, placements, and globalization all within threshold.")
        return 0

    print("✗ Quality gate FAILED — the build is NOT complete:")
    for f in failures:
        print(f"  • {f['check']:30s}  expected={f['expected']!s:>10}  actual={f['actual']!s:>10}")
        print(f"      {f['detail']}")
    return 1


if __name__ == "__main__":
    sys.exit(main())
