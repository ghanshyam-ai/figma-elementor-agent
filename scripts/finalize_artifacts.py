"""
Copy the per-run build artifacts into a permanent, slug-scoped archive.

`build/` is wiped at the start of every run (`importer` agent does
`rm -rf build && mkdir build`), so anything in there is ephemeral. This
script copies the artifacts the developer is most likely to want when
comparing runs or debugging a regression into:

    pages/<page-slug>/<timestamp>/
        build-plan.json
        import-report.json
        widget-review-queue.json
        diff/
            report.json
            diff.png             (only when present)
        state.json               (asset map, kit ids, template ids)
        fix_history.json         (when the auto-fixer ran)

Each run gets its own timestamp directory so re-runs don't overwrite
prior history — useful when the visual diff regresses between iterations
of the Figma file.

Usage:
    python3 scripts/finalize_artifacts.py                 # uses page_slug from project-config.json
    python3 scripts/finalize_artifacts.py --slug about    # explicit slug
    python3 scripts/finalize_artifacts.py --slug home --keep-last 10
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BUILD = ROOT / "build"
PAGES = ROOT / "pages"

# Files we always try to copy. Missing files are tolerated.
COPY_FILES = [
    "build-plan.json",
    "import-report.json",
    "widget-review-queue.json",
    "state.json",
    "fix_history.json",
]

COPY_DIRS_WITH_FILES = {
    # source dir relative to build/ → list of filenames to copy
    "diff": ["report.json", "diff.png", "diff.tablet.png", "diff.mobile.png"],
}


def _load_config() -> dict:
    cfg_path = ROOT / "project-config.json"
    if not cfg_path.exists():
        return {}
    try:
        return json.loads(cfg_path.read_text())
    except json.JSONDecodeError:
        return {}


def finalize(slug: str, keep_last: int | None = None) -> Path:
    """Copy the current build artifacts into pages/<slug>/<timestamp>/.

    Returns the path of the created archive directory.
    """
    if not BUILD.exists():
        raise SystemExit("No build/ directory to archive. Run the importer first.")

    ts = dt.datetime.now().strftime("%Y%m%d-%H%M%S")
    out_dir = PAGES / slug / ts
    out_dir.mkdir(parents=True, exist_ok=True)

    copied: list[str] = []
    for name in COPY_FILES:
        src = BUILD / name
        if src.exists():
            shutil.copy2(src, out_dir / name)
            copied.append(name)

    for sub, files in COPY_DIRS_WITH_FILES.items():
        src_dir = BUILD / sub
        if not src_dir.exists():
            continue
        dst_dir = out_dir / sub
        dst_dir.mkdir(exist_ok=True)
        for fn in files:
            src = src_dir / fn
            if src.exists():
                shutil.copy2(src, dst_dir / fn)
                copied.append(f"{sub}/{fn}")

    # Manifest — small JSON summarizing the run so a human can scan a
    # directory of runs and pick the one to inspect without opening files.
    manifest = {
        "slug": slug,
        "timestamp": ts,
        "files": copied,
        "drift": _peek_drift(),
        "confidence": _peek_confidence(),
        "passed": _peek_passed(),
    }
    (out_dir / "manifest.json").write_text(json.dumps(manifest, indent=2))

    if keep_last is not None and keep_last > 0:
        _prune_old_runs(slug, keep_last)

    return out_dir


def _peek_drift() -> float | None:
    p = BUILD / "diff" / "report.json"
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text()).get("drift")
    except json.JSONDecodeError:
        return None


def _peek_confidence() -> float | None:
    p = BUILD / "import-report.json"
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text()).get("confidence")
    except json.JSONDecodeError:
        return None


def _peek_passed() -> bool | None:
    p = BUILD / "diff" / "report.json"
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text()).get("passed")
    except json.JSONDecodeError:
        return None


def _prune_old_runs(slug: str, keep_last: int) -> None:
    """Drop all but the most recent `keep_last` runs for this slug."""
    slug_dir = PAGES / slug
    if not slug_dir.exists():
        return
    runs = sorted(
        (p for p in slug_dir.iterdir() if p.is_dir()),
        key=lambda p: p.name,
        reverse=True,
    )
    for old in runs[keep_last:]:
        shutil.rmtree(old, ignore_errors=True)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--slug", help="Page slug (defaults to project-config.json::page_slug)")
    ap.add_argument("--keep-last", type=int, default=None,
                    help="Prune older runs for this slug, keeping the N most recent.")
    args = ap.parse_args()

    slug = args.slug
    if not slug:
        cfg = _load_config()
        slug = cfg.get("page_slug") or "home"

    out_dir = finalize(slug, args.keep_last)
    print(f"✓ Archived run → {out_dir.relative_to(ROOT)}")
    manifest = json.loads((out_dir / "manifest.json").read_text())
    if manifest.get("drift") is not None:
        print(f"  drift={manifest['drift']*100:.2f}%  "
              f"confidence={manifest.get('confidence', 'n/a')}  "
              f"passed={manifest.get('passed')}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
