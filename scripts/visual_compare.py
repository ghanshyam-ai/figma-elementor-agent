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
    ap.add_argument("--per-section", action="store_true",
                    help="Also crop the live desktop screenshot per top-level "
                         "Elementor section and diff each against the matching "
                         "Figma section screenshot under build/<export>/screenshots/sections/. "
                         "Per-section drift is added to report.json::sections and gives "
                         "the auto-fixer exact node_ids instead of fuzzy y-band guesses.")
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

    # --- Per-section drift (opt-in) ---------------------------------------
    # Crops the desktop live capture by each top-level Elementor section's
    # bounding rect (via Playwright getBoundingClientRect()) and diffs each
    # against the matching Figma section PNG. Per-section drift gives the
    # auto-fixer exact node_id targets, eliminating the y-band → section
    # heuristic that's the main source of false-positive fixes.
    if args.per_section:
        try:
            sections = _per_section_diff(
                page_url, export_dir,
                live_path=(BUILD_DIFF / (live_base.stem + ".desktop" + live_base.suffix)),
                build_diff=BUILD_DIFF,
                width=args.width,
                threshold=args.threshold,
            )
            report["sections"] = sections
            if sections:
                worst = max(sections, key=lambda s: s.get("drift") or 0.0)
                print(f"\nPer-section diff: {len(sections)} section(s) analysed")
                print(f"  worst: {worst.get('figma_name') or worst['data_id']} "
                      f"({(worst.get('drift') or 0)*100:.1f}% drift)")
        except Exception as exc:
            # Per-section is non-blocking — the overall report still
            # determines pass/fail. Log and continue.
            print(f"  (per-section diff skipped: {exc})")
            report["sections_error"] = str(exc)

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


def _per_section_diff(
    page_url: str,
    export_dir: Path,
    live_path: Path,
    build_diff: Path,
    width: int,
    threshold: float,
) -> list[dict]:
    """Compute per-section drift by cropping the live desktop screenshot.

    For each top-level Elementor section on the live page, find its
    bounding rect, crop the matching region from `live_path`, and diff
    it against the corresponding Figma section screenshot under
    `<export>/screenshots/sections/<figma_id>.png`. The pairing uses
    `build/data.json` (the post-regen tree) to map elementor `data-id`
    → `_figma_id` settings.

    Returns a list of section results. Each entry has:
        { data_id, figma_id, figma_name, x, y, width, height,
          drift, live_crop, expected, passed }
    Sections without a matching expected crop get `skipped: "no-baseline"`.
    """
    if not live_path.exists():
        raise RuntimeError(f"desktop live capture missing at {live_path}")

    # 1. Fetch the bounding rects for top-level sections on the live page
    cmd = [
        "node", str(SCRIPTS / "playwright_section_rects.js"),
        "--url", page_url, "--width", str(width),
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, check=True)
    rects = json.loads(result.stdout.strip().splitlines()[-1])
    if not rects:
        return []

    # 2. Build {data_id → settings._figma_id} from build/data.json
    figma_id_by_data_id = _build_figma_id_lookup()

    # 3. Crop each rect from the live screenshot and diff
    try:
        from PIL import Image
    except ImportError:
        raise RuntimeError("Pillow is required for --per-section diffing")

    crops_dir = build_diff / "sections"
    crops_dir.mkdir(parents=True, exist_ok=True)

    live_img = Image.open(live_path)
    iw, ih = live_img.size
    # Live captures use deviceScaleFactor=2 → pixel dims are 2× the CSS dims
    scale = iw / max(1, width)

    sections: list[dict] = []
    for rect in rects:
        data_id = rect.get("data_id")
        figma_id = figma_id_by_data_id.get(data_id, {}).get("figma_id")
        figma_name = figma_id_by_data_id.get(data_id, {}).get("figma_name") or ""

        x0 = max(0, int(rect["x"] * scale))
        y0 = max(0, int(rect["y"] * scale))
        x1 = min(iw, int((rect["x"] + rect["width"]) * scale))
        y1 = min(ih, int((rect["y"] + rect["height"]) * scale))
        if x1 <= x0 or y1 <= y0:
            continue

        live_crop_path = crops_dir / f"{data_id}.png"
        live_img.crop((x0, y0, x1, y1)).save(live_crop_path)

        section_entry: dict = {
            "data_id": data_id,
            "figma_id": figma_id,
            "figma_name": figma_name,
            "x": int(rect["x"]), "y": int(rect["y"]),
            "width": int(rect["width"]), "height": int(rect["height"]),
            "live_crop": str(live_crop_path),
        }

        expected_path = _find_section_expected(export_dir, figma_id, figma_name)
        if not expected_path:
            section_entry["skipped"] = "no-baseline"
            section_entry["drift"] = None
            section_entry["passed"] = None
            sections.append(section_entry)
            continue

        diff_path = crops_dir / f"{data_id}.diff.png"
        try:
            r = run_diff(live_crop_path, expected_path, diff_path)
            section_entry["drift"] = r.get("drift")
            section_entry["diff"] = str(diff_path)
            section_entry["expected"] = str(expected_path)
            section_entry["passed"] = bool(
                (r.get("drift") or 0) <= threshold
            )
        except subprocess.CalledProcessError as exc:
            section_entry["error"] = exc.stderr or str(exc)
            section_entry["drift"] = None
            section_entry["passed"] = False
        sections.append(section_entry)

    return sections


def _build_figma_id_lookup() -> dict[str, dict]:
    """Return {live_data_id: {figma_id, figma_name}} for all containers.

    `build/data.json` carries the agent's PRE-regen ids — Elementor's
    `iterate_data` hook regenerates every id at write time. The live page
    therefore exposes POST-regen `data-id` values. We bridge the two via
    `build/id_map.json` (pre → post), inverted here, so a `data-id` from
    the live DOM can resolve back to its `_figma_id`.
    """
    build = ROOT / "build"
    data_path = build / "data.json"
    if not data_path.exists():
        return {}
    try:
        tree = json.loads(data_path.read_text())
    except json.JSONDecodeError:
        return {}
    content = tree.get("content") if isinstance(tree, dict) else tree

    # post-regen → pre-regen (inverse of the saved id_map). When id_map
    # is missing (first run failed to read back the live tree), assume
    # ids didn't change and fall through to identity matching.
    id_map_path = build / "id_map.json"
    pre_by_post: dict[str, str] = {}
    if id_map_path.exists():
        try:
            saved = json.loads(id_map_path.read_text())
            if isinstance(saved, dict):
                pre_by_post = {post: pre for pre, post in saved.items() if post}
        except json.JSONDecodeError:
            pre_by_post = {}

    # First pass: pre-regen id → figma metadata
    by_pre: dict[str, dict] = {}

    def walk(n):
        if not isinstance(n, dict):
            return
        eid = n.get("id")
        if eid:
            s = n.get("settings") or {}
            by_pre[eid] = {
                "figma_id": s.get("_figma_id") or s.get("figma_id"),
                "figma_name": s.get("_figma_name"),
            }
        for c in n.get("elements") or []:
            walk(c)

    if isinstance(content, list):
        for top in content:
            walk(top)

    # Second pass: project to post-regen ids when id_map is present.
    # Otherwise fall back to identity so the lookup is still useful on
    # first runs.
    if pre_by_post:
        return {post: by_pre[pre] for post, pre in pre_by_post.items() if pre in by_pre}
    return by_pre


def _find_section_expected(export_dir: Path, figma_id: str | None, figma_name: str | None) -> Path | None:
    """Locate the matching Figma section screenshot.

    Search order (most-specific first):
      1. screenshots/sections/<figma_id>.png
      2. screenshots/sections/<sanitized figma_name>.png
      3. screenshots/<figma_id>.png (legacy layout — section_crops.py
         used to write here before the dedicated sections/ dir)
    """
    shots = export_dir / "screenshots"
    if not shots.exists():
        return None

    candidates = []
    if figma_id:
        safe_id = str(figma_id).replace(":", "_").replace("/", "_")
        candidates += [
            shots / "sections" / f"{safe_id}.png",
            shots / "sections" / f"{safe_id}@2x.png",
            shots / f"{safe_id}.png",
        ]
    if figma_name:
        safe_name = str(figma_name).replace(":", "_").replace("/", "_").replace(" ", "-")
        candidates.append(shots / "sections" / f"{safe_name}.png")
    for c in candidates:
        if c.exists():
            return c
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
