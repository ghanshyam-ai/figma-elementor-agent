"""
Accessibility audit — static checks against build/data.json.

This is a *source* audit, not a live-page crawl. We inspect the
Elementor tree we just wrote and surface issues a designer can fix in
Figma + propagate via the next build. Three classes of check:

  1. Image alt-text presence — every `image` widget must have an alt
     attribute. Decorative-only images can be tagged
     `_decorative: true` in plugin output to opt out.
  2. Heading hierarchy — H1 → H2 → H3 should not skip levels within a
     section. A page should have exactly one H1.
  3. Text-on-background contrast — when both the widget's text color
     and its container's background color resolve to a hex value, we
     compute the WCAG contrast ratio. Body text < 4.5:1 fails AA,
     headings (≥18pt or ≥14pt bold) < 3:1 fails AA-Large.

Output: appends `a11y_issues` to `build/import-report.json` and writes
`build/a11y-report.json` with the full detail. Surfaces as warnings —
the quality gate doesn't fail on a11y today, but the issues are
visible to the developer and the Figma feedback loop.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BUILD = ROOT / "build"

_HEX_RE = re.compile(r"^#?([0-9a-fA-F]{3}|[0-9a-fA-F]{6}|[0-9a-fA-F]{8})$")


def _load(p: Path) -> dict:
    if not p.exists():
        return {}
    try:
        return json.loads(p.read_text())
    except json.JSONDecodeError:
        return {}


def _walk(node, ancestors=None):
    ancestors = ancestors or []
    if isinstance(node, dict):
        yield node, ancestors
        for c in node.get("elements") or []:
            yield from _walk(c, ancestors + [node])
    elif isinstance(node, list):
        for it in node:
            yield from _walk(it, ancestors)


def _parse_hex(v) -> tuple[int, int, int] | None:
    if not isinstance(v, str):
        return None
    s = v.strip().lstrip("#").lower()
    if len(s) == 3:
        s = "".join(c * 2 for c in s)
    if len(s) == 8:
        s = s[:6]
    if len(s) != 6 or not all(c in "0123456789abcdef" for c in s):
        return None
    return int(s[0:2], 16), int(s[2:4], 16), int(s[4:6], 16)


def _relative_luminance(rgb: tuple[int, int, int]) -> float:
    def chan(c: float) -> float:
        c /= 255.0
        return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4
    r, g, b = (chan(c) for c in rgb)
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def _contrast(a: tuple[int, int, int], b: tuple[int, int, int]) -> float:
    la, lb = _relative_luminance(a), _relative_luminance(b)
    lighter, darker = max(la, lb), min(la, lb)
    return (lighter + 0.05) / (darker + 0.05)


def _resolve_color_from_kit(ref: str, kit_globals: dict) -> str | None:
    """Resolve `globals/colors?id=primary` → hex by walking kit_globals."""
    if not isinstance(ref, str) or "globals/colors?id=" not in ref:
        return None
    slug = ref.rsplit("=", 1)[-1]
    for bucket in ("system_colors", "custom_colors"):
        for c in kit_globals.get(bucket) or []:
            if c.get("_id") == slug:
                return c.get("color")
    return None


def _settings_color(settings: dict, key: str, kit_globals: dict) -> str | None:
    """Pull a color value out of settings, resolving __globals__ refs."""
    if not isinstance(settings, dict):
        return None
    val = settings.get(key)
    if isinstance(val, str) and val.startswith("#"):
        return val
    g = (settings.get("__globals__") or {}).get(key)
    if g:
        return _resolve_color_from_kit(g, kit_globals)
    return None


def audit_a11y(content: list, kit_globals: dict | None = None) -> list[dict]:
    """Return a list of accessibility issues found in `content`."""
    issues: list[dict] = []
    kit_globals = kit_globals or {}

    # --- Image alt text ---
    for node, ancestors in _walk(content):
        if not isinstance(node, dict):
            continue
        if node.get("widgetType") != "image":
            continue
        s = node.get("settings") or {}
        if s.get("_decorative") is True:
            continue  # opt-out
        image = s.get("image") or {}
        alt = (image.get("alt") or s.get("alt") or "").strip()
        if not alt:
            issues.append({
                "kind": "missing_alt_text",
                "severity": "error",
                "node_id": node.get("id"),
                "detail": "Image widget has no alt attribute. Set it in Figma "
                          "(layer name acts as alt by default) or mark the layer "
                          "as decorative.",
            })

    # --- Heading hierarchy ---
    h1_count = 0
    last_level: int | None = None
    for node, _ in _walk(content):
        if node.get("widgetType") != "heading":
            continue
        s = node.get("settings") or {}
        tag = (s.get("header_size") or "h2").lower()
        if not re.match(r"^h[1-6]$", tag):
            continue
        level = int(tag[1])
        if level == 1:
            h1_count += 1
        if last_level is not None and level - last_level > 1:
            issues.append({
                "kind": "heading_hierarchy_skip",
                "severity": "warn",
                "node_id": node.get("id"),
                "detail": f"Heading jumped from h{last_level} to h{level} (skipped a level). "
                          "Adjust the Figma text style to keep semantic order.",
            })
        last_level = level
    if h1_count == 0:
        issues.append({
            "kind": "no_h1",
            "severity": "warn",
            "node_id": None,
            "detail": "Page has no H1. Promote the hero / page-title heading to H1 in Figma.",
        })
    elif h1_count > 1:
        issues.append({
            "kind": "multiple_h1",
            "severity": "warn",
            "node_id": None,
            "detail": f"Page has {h1_count} H1 elements. A page should have exactly one H1.",
        })

    # --- Contrast ---
    # For each text widget, find the nearest ancestor container with a
    # resolvable background color and compute contrast. This is a
    # conservative check — many backgrounds come from images or
    # gradients we can't compute, so we just skip those.
    for node, ancestors in _walk(content):
        if node.get("elType") != "widget":
            continue
        if node.get("widgetType") not in ("heading", "text-editor", "button"):
            continue
        s = node.get("settings") or {}
        title_color = (
            _settings_color(s, "title_color", kit_globals)
            or _settings_color(s, "color", kit_globals)
            or _settings_color(s, "text_color", kit_globals)
            or _settings_color(s, "button_text_color", kit_globals)
        )
        fg = _parse_hex(title_color or "")
        if fg is None:
            continue
        # Find nearest ancestor background
        bg_rgb = None
        for anc in reversed(ancestors):
            anc_s = anc.get("settings") or {}
            bg_color = _settings_color(anc_s, "background_color", kit_globals)
            bg_rgb = _parse_hex(bg_color or "")
            if bg_rgb:
                break
        if bg_rgb is None:
            continue
        ratio = _contrast(fg, bg_rgb)
        # Determine threshold: AA-large for headings ≥18pt or ≥14pt bold
        is_heading = node.get("widgetType") == "heading"
        size = (s.get("typography_font_size") or {}).get("size", 0) if isinstance(s.get("typography_font_size"), dict) else 0
        weight = str(s.get("typography_font_weight") or "")
        large = is_heading or (isinstance(size, (int, float)) and size >= 18) or \
                (isinstance(size, (int, float)) and size >= 14 and weight in ("700", "800", "900", "bold"))
        threshold = 3.0 if large else 4.5
        if ratio < threshold:
            issues.append({
                "kind": "low_contrast",
                "severity": "error" if ratio < threshold * 0.7 else "warn",
                "node_id": node.get("id"),
                "detail": (
                    f"Text-on-background contrast is {ratio:.2f}:1 "
                    f"(threshold {threshold}:1 for {'large' if large else 'body'} text). "
                    "Pick a darker text or lighter background in Figma."
                ),
                "ratio": round(ratio, 2),
                "threshold": threshold,
            })

    return issues


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", default=str(BUILD / "data.json"))
    ap.add_argument("--state", default=str(BUILD / "state.json"))
    ap.add_argument("--out", default=str(BUILD / "a11y-report.json"))
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    data = _load(Path(args.data))
    content = data.get("content") if isinstance(data, dict) else data
    if not isinstance(content, list):
        print("No content to audit.", file=sys.stderr)
        return 1
    state = _load(Path(args.state))
    kit_globals = state.get("kit_globals") or {}

    issues = audit_a11y(content, kit_globals)
    out = {"total": len(issues), "issues": issues}
    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    Path(args.out).write_text(json.dumps(out, indent=2))

    # Mirror to import-report so the orchestrator's downstream tooling
    # (figma_feedback, gate, archive) can see it without loading a
    # second file.
    ir_path = BUILD / "import-report.json"
    if ir_path.exists():
        try:
            ir = json.loads(ir_path.read_text())
            ir["a11y_issues"] = issues
            ir_path.write_text(json.dumps(ir, indent=2))
        except json.JSONDecodeError:
            pass

    if args.json:
        json.dump(out, sys.stdout, indent=2)
        return 0
    if not issues:
        print("✓ Accessibility audit: no issues found.")
        return 0
    err_count = sum(1 for i in issues if i["severity"] == "error")
    print(f"Accessibility audit: {len(issues)} issue(s) ({err_count} error / "
          f"{len(issues) - err_count} warn)")
    for i in issues[:10]:
        print(f"  [{i['severity']}] {i['kind']}: {i['detail']}")
    if len(issues) > 10:
        print(f"  … + {len(issues) - 10} more (see {args.out})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
