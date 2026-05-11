"""
Capture the live page with Playwright and pixel-diff against the Figma screenshot.

Outputs:
    build/diff/live.png          → live page screenshot (full page)
    build/diff/expected.png      → Figma screenshot (resized to live width)
    build/diff/diff.png          → red-pixel diff image
    build/diff/report.json       → numeric drift report

Usage:
    python3 scripts/visual_compare.py --config project-config.json
    python3 scripts/visual_compare.py --threshold 0.05 --width 1920
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


def load_state() -> dict:
    state_path = ROOT / "build" / "state.json"
    if not state_path.exists():
        raise SystemExit("build/state.json not found. Run import_elementor.py first.")
    return json.loads(state_path.read_text())


def find_screenshot(export_dir: Path) -> Path:
    shots = list((export_dir / "screenshots").glob("*.png"))
    if not shots:
        raise SystemExit(f"No screenshot found in {export_dir / 'screenshots'}")
    return shots[0]


def ensure_node_helper_installed() -> None:
    if not (SCRIPTS / "node_modules" / "playwright").exists():
        print("→ npm install (one-time, may take a minute)")
        subprocess.run(["npm", "install"], cwd=str(SCRIPTS), check=True)
    # Make sure browsers are installed
    subprocess.run(
        ["npx", "playwright", "install", "chromium"],
        cwd=str(SCRIPTS),
        check=False,  # don't error if already installed
    )


def capture_live(url: str, out_path: Path, width: int) -> None:
    ensure_node_helper_installed()
    out_path.parent.mkdir(parents=True, exist_ok=True)
    print(f"→ Capture {url} at {width}px")
    subprocess.run(
        [
            "node",
            str(SCRIPTS / "playwright_capture.js"),
            "--url", url,
            "--out", str(out_path),
            "--width", str(width),
        ],
        check=True,
    )


def run_diff(live: Path, expected: Path, diff_out: Path) -> dict:
    print(f"→ Diff live ↔ expected")
    result = subprocess.run(
        [
            "node",
            str(SCRIPTS / "pixelmatch_compare.js"),
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
    args = ap.parse_args()

    cfg = json.loads(Path(args.config).read_text())
    state = load_state()
    export_dir = Path(state["export_dir"])
    page_slug = args.page_slug or state.get("page_slug") or cfg.get("page_slug", "home")
    page_url = f"{cfg['wp_url'].rstrip('/')}/{page_slug}/"

    BUILD_DIFF.mkdir(parents=True, exist_ok=True)
    live = BUILD_DIFF / "live.png"
    expected = BUILD_DIFF / "expected.png"
    diff = BUILD_DIFF / "diff.png"

    capture_live(page_url, live, args.width)

    figma_shot = find_screenshot(export_dir)
    shutil.copy2(figma_shot, expected)

    report = run_diff(live, expected, diff)
    report["url"] = page_url
    report["threshold"] = args.threshold
    report["passed"] = report.get("drift", 1.0) <= args.threshold

    # --- Fallback awareness ----------------------------------------------
    # When a section was swapped to a screenshot fallback during import,
    # the live + expected pixels for that y-band ARE the same screenshot
    # so pixel diff returns ~0%. We don't want to call that PASS — the
    # section is non-functional. Mark its band as `manual-review`.
    fallback_indices = state.get("fallback_indices") or []
    dynamic_count = state.get("dynamic_count") or 0
    if fallback_indices or dynamic_count:
        bands = _annotate_manual_review(report, state, live)
        report["manual_review_regions"] = bands
        if bands and report["passed"]:
            # A pure-pass page that contains a fallback is still flagged so
            # the developer manually re-checks those bands.
            report["passed_with_fallbacks"] = True

    (BUILD_DIFF / "report.json").write_text(json.dumps(report, indent=2))

    pct = report.get("drift", 0) * 100
    status = "PASS" if report["passed"] else "FAIL"
    if report.get("passed_with_fallbacks"):
        status = "PASS (with manual-review)"
    print(f"\n{status}  drift={pct:.2f}%  diffPixels={report.get('diffPixels')}  size={report.get('width')}x{report.get('height')}")
    if report.get("manual_review_regions"):
        print(f"  manual-review bands ({len(report['manual_review_regions'])}):")
        for b in report["manual_review_regions"][:5]:
            print(f"    y={b['y0']}..{b['y1']}  reason={b['reason']}")
    print(f"  live:     {live}")
    print(f"  expected: {expected}")
    print(f"  diff:     {diff}")
    return 0 if report["passed"] else 1


def _annotate_manual_review(report: dict, state: dict, live_png: Path) -> list[dict]:
    """Estimate y-bands for fallback / dynamic sections in the live capture.

    Heuristic: divide the live page height proportionally across top-level
    sections (placements). The split factors come from the import state's
    `placements_summary` ordering — exact only if every section renders at
    its mapped height, but good enough to flag bands for triage.
    """
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
