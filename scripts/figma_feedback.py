"""
Generate `figma-suggestions.md` — what to fix in Figma to raise the next
build's quality gate.

The agent's reports (import-report.json, build/data.json, diff/report.json,
build/state.json) carry every signal needed to tell a designer:
  • which inline colors appear N+ times and should become Figma tokens
  • which typography combinations are repeated and should become Figma
    text styles
  • which sections were the worst pixel drift (suggest re-export with
    cleaner auto-layout)
  • which low-confidence sections were ambiguous (suggest renaming
    layers with clearer purpose names like "Hero / CTA")
  • whether responsive baselines are missing (suggest enabling them in
    the plugin export options)

Output is a plain Markdown file the developer can paste into a Figma
comment or ticket. Zero formatting heuristics — pure data → text.
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
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


def _walk_widgets(content):
    if isinstance(content, list):
        for it in content:
            yield from _walk_widgets(it)
    elif isinstance(content, dict):
        if content.get("elType") == "widget":
            yield content
        for c in content.get("elements") or []:
            yield from _walk_widgets(c)


def _inline_color_occurrences(content) -> Counter:
    """Tally every inline hex color value (settings keys that look like
    `*_color`, `background_color`, etc.). Skip values already bound to
    a global ref via __globals__."""
    counter: Counter = Counter()
    for w in _walk_widgets(content):
        s = w.get("settings") or {}
        globals_map = s.get("__globals__") or {}
        for k, v in s.items():
            if not isinstance(v, str):
                continue
            if not v.startswith("#"):
                continue
            if k in globals_map:
                continue  # already a token
            counter[v.lower()] += 1
    return counter


def _typography_occurrences(content) -> Counter:
    """Tally (family, size, weight) triples on widgets that don't already
    use a typography global."""
    counter: Counter = Counter()
    for w in _walk_widgets(content):
        s = w.get("settings") or {}
        if s.get("typography_typography") == "globals":
            continue
        family = s.get("typography_font_family")
        size = s.get("typography_font_size")
        weight = s.get("typography_font_weight")
        if not family:
            continue
        size_norm = None
        if isinstance(size, dict):
            size_norm = size.get("size")
        elif isinstance(size, (int, float)):
            size_norm = size
        if size_norm is None:
            continue
        counter[(family, round(float(size_norm)), str(weight) if weight else "regular")] += 1
    return counter


def _worst_sections(diff_report: dict, top: int = 5) -> list[dict]:
    sections = diff_report.get("sections") or []
    sections = [s for s in sections if isinstance(s.get("drift"), (int, float))]
    sections.sort(key=lambda s: -s["drift"])
    return sections[:top]


def _low_confidence_sections(import_report: dict, top: int = 5) -> list[dict]:
    risks = import_report.get("riskAreas") or []
    lc = [r for r in risks if r.get("kind") == "low-confidence"]
    return lc[:top]


def build_suggestions() -> str:
    """Compose the Markdown report from the current build outputs."""
    data = _load(BUILD / "data.json")
    content = data.get("content") if isinstance(data, dict) else []
    import_report = _load(BUILD / "import-report.json")
    diff_report = _load(BUILD / "diff" / "report.json")

    coverage = (import_report.get("global_coverage") or {})
    color_cov = coverage.get("colors")
    type_cov = coverage.get("typography")

    lines: list[str] = []
    lines.append("# Figma Suggestions — raise the next build's gate")
    lines.append("")
    lines.append(
        "These are the highest-leverage edits in the Figma file that "
        "would improve the next build automatically. Each section names "
        "*what to change* and *why* — not a manual fixup, but a source "
        "fix that compounds across pages."
    )
    lines.append("")

    # --- 1. Inline colors that should become Figma tokens ---
    color_counter = _inline_color_occurrences(content)
    repeated = [(c, n) for c, n in color_counter.most_common(20) if n >= 3]
    if repeated:
        lines.append("## 1. Promote repeated inline colors to Figma tokens")
        lines.append("")
        lines.append(
            f"Global-color coverage is **{(color_cov or 0) * 100:.0f}%** "
            "(target ≥ 70%). The colors below are used inline 3+ times "
            "in the live page — defining them as Figma color tokens "
            "(File → Colors → Create local style) would lift coverage "
            "with zero Elementor-side changes."
        )
        lines.append("")
        lines.append("| Hex | Occurrences | Suggested token name |")
        lines.append("| --- | ---: | --- |")
        for hex_v, n in repeated[:10]:
            lines.append(f"| `{hex_v}` | {n} | _name this in Figma_ |")
        lines.append("")
    else:
        lines.append("## 1. Inline colors ✓")
        lines.append("")
        lines.append("No inline colors are repeated enough to suggest tokenizing.")
        lines.append("")

    # --- 2. Typography combinations to promote ---
    typo_counter = _typography_occurrences(content)
    repeated_typo = [(k, n) for k, n in typo_counter.most_common(10) if n >= 3]
    if repeated_typo:
        lines.append("## 2. Promote repeated typography to Figma text styles")
        lines.append("")
        lines.append(
            f"Typography coverage is **{(type_cov or 0) * 100:.0f}%** "
            "(target ≥ 70%). The (family, size, weight) combinations "
            "below are used inline 3+ times — define them as Figma text "
            "styles to lift coverage."
        )
        lines.append("")
        lines.append("| Family | Size | Weight | Occurrences |")
        lines.append("| --- | ---: | ---: | ---: |")
        for (fam, size, weight), n in repeated_typo:
            lines.append(f"| {fam} | {size}px | {weight} | {n} |")
        lines.append("")

    # --- 3. Worst-drift sections ---
    worst = _worst_sections(diff_report)
    if worst:
        lines.append("## 3. Worst-drift sections — review the Figma layout")
        lines.append("")
        lines.append(
            "These sections had the highest visual difference from the "
            "design. Common upstream causes: missing auto-layout, "
            "non-standard absolute positioning, custom components the "
            "plugin can't decompose. Re-check these in Figma:"
        )
        lines.append("")
        for s in worst:
            d = (s.get("drift") or 0) * 100
            name = s.get("figma_name") or s.get("data_id")
            lines.append(f"- **{name}** — {d:.1f}% drift")
        lines.append("")

    # --- 4. Low-confidence sections ---
    low = _low_confidence_sections(import_report)
    if low:
        lines.append("## 4. Ambiguous sections — clarify layer names")
        lines.append("")
        lines.append(
            "The agent couldn't decide what these sections are. Renaming "
            "the Figma layer to a clear, purpose-prefixed name "
            "(`Hero / Banner`, `Pricing / Tier`, `Footer / Newsletter`) "
            "is the cheapest fix:"
        )
        lines.append("")
        for r in low:
            name = r.get("nodeName") or r.get("nodeId")
            lines.append(f"- **{name}** — {r.get('detail') or 'low confidence'}")
        lines.append("")

    # --- 5. Plugin export options ---
    screenshots = BUILD / "export" / "screenshots"
    missing_breakpoints = []
    if screenshots.exists():
        for bp_name, fname in [("tablet", "page.tablet.png"), ("mobile", "page.mobile.png")]:
            if not (screenshots / fname).exists():
                missing_breakpoints.append(bp_name)
    if missing_breakpoints:
        lines.append("## 5. Plugin export — enable responsive screenshots")
        lines.append("")
        lines.append(
            "Your Figma plugin export is missing per-breakpoint baseline "
            "screenshots: "
            f"**{', '.join(missing_breakpoints)}**. The visual diff can "
            "only gate breakpoints with baselines, so right now "
            f"{', '.join(missing_breakpoints)} regressions are invisible "
            "to the quality gate. In the plugin's export options, enable "
            "'Per-breakpoint screenshots' before exporting."
        )
        lines.append("")

    if len(lines) <= 4:
        lines.append("✅ Nothing to suggest — the build looks healthy from the "
                     "designer-side perspective.")
        lines.append("")

    return "\n".join(lines)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=None, help="Override output path. Default: build/figma-suggestions.md")
    ap.add_argument("--stdout", action="store_true")
    args = ap.parse_args()

    md = build_suggestions()
    if args.stdout:
        sys.stdout.write(md)
        return 0
    out = Path(args.out) if args.out else BUILD / "figma-suggestions.md"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(md)
    print(f"✓ Figma suggestions → {out.relative_to(ROOT) if str(out).startswith(str(ROOT)) else out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
