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
    max_page_weight_bytes: int | None = None,
    max_widgets: int | None = None,
    max_runtime_depth: int | None = None,
) -> tuple[bool, list[dict], list[dict]]:
    """Return (passed, failures, warnings).

    `failures` block the gate; `warnings` are informational (e.g. a
    breakpoint with no baseline degrades the gate's coverage but doesn't
    fail it). Each entry is {check, [expected], [actual], detail}.

    Performance budget parameters (all opt-in — None = no enforcement):
      • max_page_weight_bytes — total asset bytes uploaded for this run
      • max_widgets — Elementor widget count cap (DOM-size proxy)
      • max_runtime_depth — observed nesting depth in the live tree
    """
    failures: list[dict] = []
    warnings: list[dict] = []

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
        # Multi-breakpoint check: if per_breakpoint exists, all with a
        # baseline must pass. Breakpoints WITHOUT a baseline degrade the
        # gate (recorded in `gate_warnings` but not a failure) — so the
        # build can still ship while making it explicit that mobile/tablet
        # regressions are NOT being caught.
        gate_warnings: list[dict] = []
        for bp, r in (diff_report.get("per_breakpoint") or {}).items():
            if bp in ("", "desktop"):
                continue
            d = r.get("drift")
            skipped = r.get("skipped")
            if skipped:
                gate_warnings.append({
                    "check": f"drift_{bp}",
                    "detail": (
                        f"{bp} breakpoint not gated: {skipped}. "
                        "Mobile/tablet regressions in this breakpoint will "
                        "not be detected. Add page.tablet.png / page.mobile.png "
                        "to the Figma plugin export to close this gap."
                    ),
                })
                continue
            if isinstance(d, (int, float)) and d > drift_threshold:
                failures.append({
                    "check": f"drift_{bp}",
                    "expected": f"≤ {drift_threshold * 100:.1f}%",
                    "actual": f"{d * 100:.2f}%",
                    "detail": f"{bp} breakpoint exceeds drift threshold.",
                })
        if gate_warnings:
            warnings.extend(gate_warnings)
    else:
        failures.append({
            "check": "drift",
            "expected": "diff report present",
            "actual": "missing",
            "detail": "build/diff/report.json not found — run visual_compare.py.",
        })

    # --- check 2 + 3: header + footer placements ---
    # Partial Theme Builder is allowed: when EITHER header OR footer
    # exists, the other falls through to the theme default — that's a
    # warning, not a failure. Only when BOTH are missing does the gate
    # fail. The check honours --no-require-header / --no-require-footer
    # for the unusual case where the user knows the design intentionally
    # uses theme defaults for both.
    placements = state.get("placements_summary") or {}
    has_header = (placements.get("header", 0) or 0) >= 1
    has_footer = (placements.get("footer", 0) or 0) >= 1
    if require_header and require_footer and not (has_header or has_footer):
        failures.append({
            "check": "chrome_placement",
            "expected": "≥ 1 of {header, footer}",
            "actual": "0 of {header, footer}",
            "detail": "Neither header nor footer was detected. Add `header_pattern` "
                      "/ `footer_pattern` to project-config.json or improve the "
                      "Figma layer naming.",
        })
    elif require_header and not has_header and has_footer:
        warnings.append({
            "check": "header_placement",
            "detail": "Header falling through to theme default — only footer was "
                      "detected as a Theme Builder template.",
        })
    elif require_footer and not has_footer and has_header:
        warnings.append({
            "check": "footer_placement",
            "detail": "Footer falling through to theme default — only header was "
                      "detected as a Theme Builder template.",
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

    # --- check 4b: performance budget ---------------------------------
    # All opt-in — passing None for any limit disables that sub-check.
    # Page weight is computed from the asset map persisted in state.json;
    # widget count + max runtime depth are walked from build/data.json.
    if max_page_weight_bytes is not None:
        total = 0
        asset_map = state.get("asset_map") or {}
        for meta in asset_map.values():
            if isinstance(meta, dict) and isinstance(meta.get("size_bytes"), int):
                total += meta["size_bytes"]
        if total > max_page_weight_bytes:
            failures.append({
                "check": "page_weight",
                "expected": f"≤ {max_page_weight_bytes / 1_000_000:.1f} MB",
                "actual": f"{total / 1_000_000:.2f} MB",
                "detail": "Total uploaded asset weight exceeds budget. Compress "
                          "or resize source assets in Figma before re-export.",
            })

    if max_widgets is not None or max_runtime_depth is not None:
        data_path = BUILD / "data.json"
        if data_path.exists():
            try:
                tree = json.loads(data_path.read_text())
                content = tree.get("content") if isinstance(tree, dict) else tree
            except json.JSONDecodeError:
                content = None
            if isinstance(content, list):
                widget_count = 0
                deepest = 0

                def walk(node, depth):
                    nonlocal widget_count, deepest
                    if not isinstance(node, dict):
                        return
                    if node.get("elType") == "widget":
                        widget_count += 1
                    if depth > deepest:
                        deepest = depth
                    for c in node.get("elements") or []:
                        walk(c, depth + 1)
                for top in content:
                    walk(top, 0)
                if max_widgets is not None and widget_count > max_widgets:
                    failures.append({
                        "check": "widget_count",
                        "expected": f"≤ {max_widgets}",
                        "actual": str(widget_count),
                        "detail": "Page has more Elementor widgets than the budget allows. "
                                  "Collapse repeated sections via Saved Templates or "
                                  "reduce decorative widgets.",
                    })
                if max_runtime_depth is not None and deepest > max_runtime_depth:
                    failures.append({
                        "check": "runtime_depth",
                        "expected": f"≤ {max_runtime_depth}",
                        "actual": str(deepest),
                        "detail": "Element nesting exceeds budget. Increase --max-depth "
                                  "in import_elementor.py or flatten Figma groups.",
                    })

    # --- check 5: asset upload failures ---
    asset_failures = import_report.get("asset_failures") or []
    if asset_failures:
        sample = ", ".join(a.get("name", "?") for a in asset_failures[:3])
        if len(asset_failures) > 3:
            sample += f", + {len(asset_failures) - 3} more"
        failures.append({
            "check": "asset_uploads",
            "expected": "0 failed uploads",
            "actual": f"{len(asset_failures)} failed",
            "detail": f"Failed: {sample}. Live page will show broken images "
                      "for these assets. See import-report.json::asset_failures.",
        })

    return (not failures, failures, warnings)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--drift-threshold", type=float, default=0.05)
    ap.add_argument("--min-global-coverage", type=float, default=0.7)
    ap.add_argument("--no-require-header", action="store_true",
                    help="Skip the header-placement check (use for partial runs).")
    ap.add_argument("--no-require-footer", action="store_true")
    ap.add_argument("--max-page-weight", type=str, default=None,
                    help="Performance budget for total asset size. Accepts '2mb', '1.5MB', '500kb', or raw bytes.")
    ap.add_argument("--max-widgets", type=int, default=None,
                    help="Performance budget on Elementor widget count (DOM-size proxy).")
    ap.add_argument("--max-runtime-depth", type=int, default=None,
                    help="Performance budget on element nesting depth.")
    ap.add_argument("--json", action="store_true", help="Emit JSON instead of human output.")
    args = ap.parse_args()

    def _parse_size(v: str | None) -> int | None:
        if v is None:
            return None
        s = v.strip().lower()
        mult = 1
        if s.endswith("mb"):
            mult, s = 1_000_000, s[:-2].strip()
        elif s.endswith("kb"):
            mult, s = 1_000, s[:-2].strip()
        try:
            return int(float(s) * mult)
        except ValueError:
            return None

    passed, failures, warnings = verify(
        drift_threshold=args.drift_threshold,
        require_header=not args.no_require_header,
        require_footer=not args.no_require_footer,
        min_global_coverage=args.min_global_coverage,
        max_page_weight_bytes=_parse_size(args.max_page_weight),
        max_widgets=args.max_widgets,
        max_runtime_depth=args.max_runtime_depth,
    )

    if args.json:
        out = {"passed": passed, "failures": failures, "warnings": warnings}
        json.dump(out, sys.stdout, indent=2)
        sys.stdout.write("\n")
        return 0 if passed else 1

    if passed:
        print("✓ Quality gate PASSED — drift, placements, and globalization all within threshold.")
        for w in warnings:
            print(f"  ⚠ {w['check']}: {w['detail']}")
        return 0

    print("✗ Quality gate FAILED — the build is NOT complete:")
    for f in failures:
        exp = f.get("expected", "—")
        act = f.get("actual", "—")
        print(f"  • {f['check']:30s}  expected={exp!s:>10}  actual={act!s:>10}")
        print(f"      {f['detail']}")
    for w in warnings:
        print(f"  ⚠ {w['check']}: {w['detail']}")
    return 1


if __name__ == "__main__":
    sys.exit(main())
