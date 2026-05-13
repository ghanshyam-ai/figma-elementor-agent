"""
Design-system pre-flight check.

Runs over `build/<export>/global.json` BEFORE any WordPress writes and
flags conditions that will almost certainly cause the quality gate to
fail later:

  • Fewer than 2 named brand colors → the brand-color heuristic will
    pick a grey (`#E5E5E5`) for `primary` and global-color coverage will
    fall below the 70% gate.
  • Fewer than 2 typography entries with a non-null `fontFamily` →
    typography coverage will fall below the 70% gate.
  • Missing `space_between_widgets` → containers will inherit the kit
    default (20px) regardless of what Figma intended, producing
    inevitable spacing drift in the visual diff.

The script is intentionally read-only — it just prints warnings and
returns a small JSON report. The orchestrator runs it right after
extraction and before Phase C so the developer can fix the Figma side
without having burned a full build run.

Usage:
    python3 scripts/preflight.py                       # uses build/export/global.json
    python3 scripts/preflight.py path/to/global.json   # explicit path
    python3 scripts/preflight.py --json                # machine-readable
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


def _is_named_color(entry: dict) -> bool:
    """A color entry is 'named' when its name signals brand intent — i.e.
    not a generic grey/black/white label. The brand-color heuristic in
    global-styles uses these names to pick `primary`."""
    name = (entry.get("name") or "").strip().lower()
    if not name:
        return False
    generic = {"white", "black", "grey", "gray", "neutral", "bg",
               "background", "surface", "border", "divider", "fg"}
    # Anything that contains "brand", "primary", "accent" etc. is clearly named
    branded_tokens = {"brand", "primary", "accent", "cta", "success",
                      "danger", "warning", "error", "info"}
    if any(b in name for b in branded_tokens):
        return True
    # Otherwise: treat as named only if not in the generic set
    return name not in generic


def check_responsive_baselines(export_dir: Path) -> list[dict]:
    """Warn when the export is missing tablet/mobile full-page baselines.

    The visual diff degrades gracefully (skips a breakpoint when its
    baseline isn't there) but that silently hides mobile regressions —
    the gate can pass on desktop while mobile is broken. Surface the
    gap loudly so developers either re-export with the missing
    breakpoints or accept that the gate is desktop-only.
    """
    issues: list[dict] = []
    if not export_dir.exists():
        return issues
    screenshots = export_dir / "screenshots"
    if not screenshots.exists():
        return issues

    # The plugin emits these flat in screenshots/ (page.png is desktop).
    candidates = {
        "tablet": ["page.tablet.png", "page-tablet.png", "page_tablet.png"],
        "mobile": ["page.mobile.png", "page-mobile.png", "page_mobile.png"],
    }
    missing = []
    for bp, names in candidates.items():
        if not any((screenshots / n).exists() for n in names):
            missing.append(bp)

    if missing:
        issues.append({
            "severity": "warn",
            "kind": "missing-responsive-baselines",
            "detail": (
                f"Plugin export is missing {', '.join(missing)} baseline "
                "screenshot(s) (expected page.tablet.png / page.mobile.png "
                "in the screenshots/ folder). Visual diff will skip those "
                "breakpoints, which means the quality gate cannot detect "
                f"{'/'.join(missing)} regressions. Update the Figma plugin "
                "to export per-breakpoint screenshots, or accept a "
                "desktop-only gate."
            ),
        })
    return issues


def check_global_json(global_json: dict) -> list[dict]:
    """Return a list of issue dicts. Empty list = pre-flight passes."""
    issues: list[dict] = []

    # --- Colors ---
    colors = global_json.get("colors") or []
    if isinstance(colors, dict):
        # Some exports use {slug: hex}, some use list[{name, value}].
        colors = [{"name": k, "value": v} for k, v in colors.items()]
    named = [c for c in colors if isinstance(c, dict) and _is_named_color(c)]
    if len(colors) < 4:
        issues.append({
            "severity": "warn",
            "kind": "sparse-colors",
            "detail": (
                f"Only {len(colors)} color tokens in global.json. Elementor "
                "expects ≥ 4 system colors (primary/secondary/text/accent). "
                "Add more named colors in Figma to improve global-color coverage."
            ),
        })
    if len(named) < 2:
        issues.append({
            "severity": "error",
            "kind": "unnamed-brand-colors",
            "detail": (
                f"Only {len(named)} color(s) have brand-intent names "
                "(e.g. 'Brand Primary', 'Accent'). The brand-color heuristic "
                "will fall back to a grey for `primary` and global-color "
                "coverage will fail the 70% gate. Rename colors in Figma "
                "before running the import — see global-styles.md."
            ),
        })

    # --- Typography ---
    typography = global_json.get("typography") or []
    if isinstance(typography, dict):
        typography = list(typography.values())
    typed = [t for t in typography if isinstance(t, dict) and t.get("fontFamily")]
    if len(typed) < 2:
        issues.append({
            "severity": "error",
            "kind": "sparse-typography",
            "detail": (
                f"Only {len(typed)} typography token(s) carry a non-null "
                "`fontFamily`. Typography coverage will fall below the 70% "
                "gate. Define text styles in Figma's typography panel "
                "(headings + body at minimum) before running the import."
            ),
        })

    # --- Spacing ---
    # The plugin exports `spacing` (dict) or top-level `space_between_widgets`.
    spacing = global_json.get("spacing") or {}
    has_widget_gap = (
        bool(global_json.get("space_between_widgets"))
        or bool(spacing.get("widget_gap"))
        or bool(spacing.get("space_between_widgets"))
    )
    if not has_widget_gap:
        issues.append({
            "severity": "warn",
            "kind": "missing-widget-gap",
            "detail": (
                "No `space_between_widgets` token in global.json. Containers "
                "will inherit Elementor's 20px default, which usually drifts "
                "from the Figma intent. Spacing drift in the visual diff is "
                "expected unless you set a default widget gap in Figma."
            ),
        })

    return issues


def run(global_json_path: Path | None = None) -> dict:
    """Run the check and return {issues, passed}.

    `passed` is True when no `error`-severity issues are present (warns
    are OK).
    """
    if global_json_path is None:
        # Default location after Phase B (importer extracted the ZIP)
        global_json_path = BUILD / "export" / "global.json"

    if not global_json_path.exists():
        return {
            "passed": False,
            "issues": [{
                "severity": "error",
                "kind": "missing-global-json",
                "detail": f"global.json not found at {global_json_path}. "
                          "Run the importer (Phase B) first.",
            }],
        }

    global_json = _load(global_json_path)
    issues = check_global_json(global_json)
    # Same export dir hosts screenshots/ — pull from the global.json parent.
    issues.extend(check_responsive_baselines(global_json_path.parent))
    passed = not any(i["severity"] == "error" for i in issues)
    return {"passed": passed, "issues": issues, "source": str(global_json_path)}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("path", nargs="?", help="Path to global.json (defaults to build/export/global.json)")
    ap.add_argument("--json", action="store_true", help="Emit JSON instead of human-readable output")
    args = ap.parse_args()

    target = Path(args.path) if args.path else None
    result = run(target)

    if args.json:
        json.dump(result, sys.stdout, indent=2)
        sys.stdout.write("\n")
        return 0 if result["passed"] else 1

    issues = result["issues"]
    if not issues:
        print("✓ Pre-flight: design system looks healthy")
        return 0
    print(f"Pre-flight: {len(issues)} issue(s) found in global.json")
    for issue in issues:
        marker = "✗" if issue["severity"] == "error" else "⚠"
        print(f"  {marker} [{issue['severity']}] {issue['kind']}")
        print(f"      {issue['detail']}")
    if not result["passed"]:
        print(
            "\nFix the error(s) above in Figma and re-export the ZIP. "
            "Warnings can be ignored — they just predict drift, not failures."
        )
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
