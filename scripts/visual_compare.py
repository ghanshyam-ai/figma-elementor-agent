"""
Capture the live page with Playwright and pixel-diff against the Figma screenshot.

Multi-breakpoint by default — captures desktop / tablet / mobile and diffs
the desktop result against the Figma export screenshot. The tablet and
mobile captures are saved (for visual review) but only diffed when a
matching baseline exists in the export.

Outputs:
    build/diff/live.desktop.png  → live page screenshot (full page)
    build/diff/live.tablet.png   → tablet capture
    build/diff/live.mobile.png   → mobile capture
    build/diff/expected.png      → Figma full-page screenshot (resized to live width)
    build/diff/diff.png          → red-pixel diff image (desktop)
    build/diff/report.json       → numeric drift report

Usage:
    python3 scripts/visual_compare.py --config project-config.json
    python3 scripts/visual_compare.py --threshold 0.05 --width 1920
    python3 scripts/visual_compare.py --viewports desktop-only      # legacy single shot
"""
from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCRIPTS = ROOT / "scripts"
BUILD_DIFF = ROOT / "build" / "diff"


# Names the plugin uses for the full-page screenshot, in priority order.
# We refuse to diff against anything else — a section-sized thumbnail
# resized to live width is meaningless. If none of these are present we
# capture the live page but report `no-baseline` and skip the diff.
FULL_PAGE_CANDIDATES = (
    "page.png", "home.png", "full.png", "full-page.png",
)


def load_state() -> dict:
    state_path = ROOT / "build" / "state.json"
    if not state_path.exists():
        raise SystemExit("build/state.json not found. Run import_elementor.py first.")
    return json.loads(state_path.read_text())


def find_full_page_baseline(export_dir: Path) -> Path | None:
    """Return the path to a real full-page screenshot, or None.

    We accept *only* explicit full-page filenames. A "first PNG in the
    folder" fallback is a foot-gun — section thumbnails were producing
    fabricated PASS verdicts in the audit. If nothing matches, we emit
    a `no-baseline` warning instead of pretending to diff.
    """
    shots_dir = export_dir / "screenshots"
    if not shots_dir.exists():
        return None
    for name in FULL_PAGE_CANDIDATES:
        p = shots_dir / name
        if p.exists():
            return p
    # Plugin exports also use slug variants like `home@2x.png`.
    for suffix in (".png", "@2x.png", "@3x.png"):
        for prefix in ("page", "home", "full", "full-page"):
            p = shots_dir / f"{prefix}{suffix}"
            if p.exists():
                return p
    return None


def ensure_node_helper_installed() -> None:
    if not (SCRIPTS / "node_modules" / "playwright").exists():
        print("→ npm install (one-time, may take a minute)")
        subprocess.run(["npm", "install"], cwd=str(SCRIPTS), check=True)
    subprocess.run(
        ["npx", "playwright", "install", "chromium"],
        cwd=str(SCRIPTS),
        check=False,
    )


def capture_live(url: str, out_path: Path, width: int, viewports: str | None) -> list[str]:
    """Run the Playwright capture. Returns the list of breakpoint names captured."""
    ensure_node_helper_installed()
    out_path.parent.mkdir(parents=True, exist_ok=True)
    print(f"→ Capture {url} (viewports={viewports or 'single'})")
    cmd = [
        "node", str(SCRIPTS / "playwright_capture.js"),
        "--url", url,
        "--out", str(out_path),
        "--width", str(width),
    ]
    if viewports:
        cmd += ["--viewports", viewports]
    subprocess.run(cmd, check=True)
    if not viewports:
        return [""]
    if viewports == "desktop-only":
        return ["desktop"]
    if viewports == "default":
        return ["desktop", "tablet", "mobile"]
    # Custom JSON viewport spec
    try:
        return [v.get("name", f"vp{i}") for i, v in enumerate(json.loads(viewports))]
    except (ValueError, AttributeError):
        return ["desktop"]


def run_diff(live: Path, expected: Path, diff_out: Path) -> dict:
    print(f"→ Diff {live.name} ↔ {expected.name}")
    result = subprocess.run(
        [
            "node", str(SCRIPTS / "pixelmatch_compare.js"),
            "--live", str(live),
            "--expected", str(expected),
            "--diff", str(diff_out),
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    return json.loads(result.stdout.strip().splitlines()[-1])


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", default=str(ROOT / "project-config.json"))
    ap.add_argument("--width", type=int, default=1920)
    ap.add_argument("--threshold", type=float, default=0.05,
                    help="Drift fraction above which the page is considered failing.")
    ap.add_argument("--page-slug", help="Override page slug from config.")
    ap.add_argument("--viewports", default="default",
                    help="'default' (desktop+tablet+mobile), 'desktop-only', or "
                         "JSON viewport list. Use 'desktop-only' for legacy single-shot.")
    args = ap.parse_args()

    cfg = json.loads(Path(args.config).read_text())
    state = load_state()
    export_dir = Path(state["export_dir"])
    page_slug = args.page_slug or state.get("page_slug") or cfg.get("page_slug", "home")
    page_url = f"{cfg['wp_url'].rstrip('/')}/{page_slug}/"

    BUILD_DIFF.mkdir(parents=True, exist_ok=True)
    live_base = BUILD_DIFF / "live.png"
    expected = BUILD_DIFF / "expected.png"
    diff = BUILD_DIFF / "diff.png"

    breakpoints = capture_live(page_url, live_base, args.width, args.viewports)

    figma_shot = find_full_page_baseline(export_dir)
    report: dict = {
        "url": page_url,
        "threshold": args.threshold,
        "breakpoints": breakpoints,
    }
    if not figma_shot:
        # No usable baseline — DO NOT diff against a section thumbnail.
        # The agent's previous code fell through to `screenshots/*.png[0]`
        # which produced fabricated PASS verdicts in the audit.
        report["passed"] = False
        report["no_baseline"] = True
        report["drift"] = None
        report["regions"] = []
        report["note"] = (
            "No full-page baseline screenshot found in export "
            f"({export_dir / 'screenshots'}). Expected one of "
            f"{FULL_PAGE_CANDIDATES}. Refusing to fabricate a pass."
        )
        (BUILD_DIFF / "report.json").write_text(json.dumps(report, indent=2))
        print("\nFAIL  no-baseline — plugin export is missing a full-page screenshot")
        print(f"  searched: {[str(export_dir / 'screenshots' / n) for n in FULL_PAGE_CANDIDATES]}")
        print(f"  live captures saved under {BUILD_DIFF}")
        return 1

    shutil.copy2(figma_shot, expected)

    # Diff desktop primarily. Tablet/mobile diffs are reported when the
    # export contains matching baselines (`page.tablet.png`, `page.mobile.png`).
    per_breakpoint: dict[str, dict] = {}
    primary_drift: float | None = None
    for bp in breakpoints:
        live_path = live_base.parent / (
            f"{live_base.stem}.{bp}{live_base.suffix}" if bp else live_base.name
        )
        if not live_path.exists():
            continue
        baseline_for_bp = (
            figma_shot if bp in ("", "desktop") else
            _find_breakpoint_baseline(export_dir, bp)
        )
        if baseline_for_bp is None:
            per_breakpoint[bp] = {"diff": None, "drift": None, "skipped": "no baseline"}
            continue
        diff_path = BUILD_DIFF / f"diff.{bp}.png" if bp else diff
        r = run_diff(live_path, baseline_for_bp, diff_path)
        per_breakpoint[bp] = r
        if bp in ("", "desktop") and primary_drift is None:
            primary_drift = r.get("drift")
            report.update(r)

    report["per_breakpoint"] = per_breakpoint

    # PASSING rules:
    #   • Desktop drift ≤ threshold
    #   • No manual_review_regions (fallback bands)
    #   • If tablet/mobile baseline exists, those must also be ≤ threshold
    fallback_indices = state.get("fallback_indices") or []
    dynamic_count = state.get("dynamic_count") or 0
    manual_bands: list[dict] = []
    if fallback_indices or dynamic_count:
        manual_bands = _annotate_manual_review(report, state)
        report["manual_review_regions"] = manual_bands

    desktop_pass = (primary_drift is not None) and (primary_drift <= args.threshold)
    other_pass = True
    for bp, r in per_breakpoint.items():
        if bp in ("", "desktop"):
            continue
        d = r.get("drift")
        if d is not None and d > args.threshold:
            other_pass = False
            break
    report["passed"] = bool(desktop_pass and other_pass and not manual_bands)
    if manual_bands:
        report["passed_with_manual_review"] = False

    (BUILD_DIFF / "report.json").write_text(json.dumps(report, indent=2))

    status = "PASS" if report["passed"] else "FAIL"
    pct = (primary_drift or 0) * 100
    print(f"\n{status}  desktop_drift={pct:.2f}%  "
          f"diffPixels={report.get('diffPixels')}  size={report.get('width')}x{report.get('height')}")
    for bp, r in per_breakpoint.items():
        if bp in ("", "desktop"):
            continue
        d = r.get("drift")
        msg = f"{d*100:.2f}%" if isinstance(d, (int, float)) else r.get("skipped", "—")
        print(f"  {bp}: {msg}")
    if manual_bands:
        print(f"  manual-review bands ({len(manual_bands)}):")
        for b in manual_bands[:5]:
            print(f"    y={b['y0']}..{b['y1']}  reason={b['reason']}")
    print(f"  live:     {live_base.parent}")
    print(f"  expected: {expected}")
    print(f"  diff:     {diff}")
    return 0 if report["passed"] else 1


def _find_breakpoint_baseline(export_dir: Path, breakpoint: str) -> Path | None:
    shots = export_dir / "screenshots"
    for prefix in ("page", "home", "full", "full-page"):
        for suffix in ("", "@2x", "@3x"):
            p = shots / f"{prefix}.{breakpoint}{suffix}.png"
            if p.exists():
                return p
    return None


def _annotate_manual_review(report: dict, state: dict) -> list[dict]:
    """Estimate y-bands for fallback / dynamic sections in the live capture."""
    placements_summary = state.get("placements_summary") or {}
    fallback_indices = set(state.get("fallback_indices") or [])
    n_total = sum(placements_summary.values()) or 0
    if n_total == 0:
        return []
    height = report.get("height") or 0
    if height == 0:
        return []
    band_h = height / n_total
    bands = []
    cursor = 0
    for i in range(n_total):
        y0 = int(cursor)
        y1 = int(cursor + band_h)
        if i in fallback_indices:
            bands.append({"y0": y0, "y1": y1, "reason": "screenshot fallback"})
        cursor += band_h
    return bands


if __name__ == "__main__":
    sys.exit(main())
