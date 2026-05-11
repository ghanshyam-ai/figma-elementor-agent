"""
Design-token CSS bridge.

Elementor's globals system covers colours and typography but NOT spacing,
border-radius, or other numeric tokens. This module injects a `:root` CSS
block of `--token-*` variables into the active kit's custom CSS, plus
matching utility classes (`.dt-radius-md`, `.dt-gap-lg`, …) and tags
widgets/containers whose inline values match a token with the right class.

Result: changing `--token-radius-md` in the kit's custom CSS rewrites
every widget that uses it — the parity feature missing from Elementor's
core globals.

Public API:
  • build_token_css(global_json)            → (css_text, radius_map, gap_map)
  • apply_design_token_classes(content, radius_map, gap_map) → counts
"""
from __future__ import annotations

from typing import Iterator


# ---------------------------------------------------------------------------
# Naming
# ---------------------------------------------------------------------------

# Token tier names. The Figma plugin emits raw spacing arrays like
# [4, 8, 12, 16, 24, 32, 48, 64, 96]; we map sorted unique values onto
# t-shirt sizes so the CSS reads "--token-gap-md" not "--token-gap-16".
RADIUS_TIERS = ("xs", "sm", "md", "lg", "xl", "2xl")
GAP_TIERS    = ("xs", "sm", "md", "lg", "xl", "2xl", "3xl")


def build_token_css(global_json: dict) -> tuple[str, dict[float, str], dict[float, str]]:
    """Build the kit-level custom CSS and {value → class-name} maps.

    `global_json` is the same dict the kit-mapping consumes —
    `{spacing: [...], radii: [...]}`.

    Returns: (css_text, radius_map, gap_map). When neither block is
    populated, returns ("", {}, {}) so the caller can skip the kit
    custom-css update entirely.
    """
    radii = sorted(set(_finite_floats(global_json.get("radii"))))
    spacings = sorted(set(_finite_floats(global_json.get("spacing"))))

    radius_map: dict[float, str] = {}
    gap_map: dict[float, str] = {}

    radius_block = ""
    if radii:
        names = _assign_tiers(radii, RADIUS_TIERS)
        radius_lines = []
        radius_class_lines = []
        for v, tier in names:
            cls = f"dt-radius-{tier}"
            radius_map[v] = cls
            radius_lines.append(f"  --token-radius-{tier}: {_px(v)};")
            radius_class_lines.append(
                f".elementor-element.{cls},"
                f" .elementor-element.{cls} > .elementor-widget-container,"
                f" .elementor-element.{cls} > .e-con-inner"
                f" {{ border-radius: var(--token-radius-{tier}) !important; }}"
            )
        radius_block = "\n".join(radius_lines)

    gap_block = ""
    if spacings:
        names = _assign_tiers(spacings, GAP_TIERS)
        gap_lines = []
        gap_class_lines = []
        for v, tier in names:
            cls = f"dt-gap-{tier}"
            gap_map[v] = cls
            gap_lines.append(f"  --token-gap-{tier}: {_px(v)};")
            gap_class_lines.append(
                f".elementor-element.{cls}.e-con-full,"
                f" .elementor-element.{cls}.e-con,"
                f" .elementor-element.{cls}"
                f" {{ gap: var(--token-gap-{tier}) !important; }}"
            )
        gap_block = "\n".join(gap_lines)

    if not radius_block and not gap_block:
        return "", {}, {}

    parts = [
        "/* figma-elementor-agent: design tokens (do not edit by hand) */",
        ":root {",
        radius_block,
        gap_block,
        "}",
    ]
    if radii:
        parts.extend(radius_class_lines)
    if spacings:
        parts.extend(gap_class_lines)
    return "\n".join(p for p in parts if p), radius_map, gap_map


def apply_design_token_classes(
    content: list,
    radius_map: dict[float, str],
    gap_map: dict[float, str],
) -> dict[str, int]:
    """Walk the tree; tag matching widgets/containers with their token class.

    Returns counts: `{"radius": n, "gap": n}`.
    """
    counts = {"radius": 0, "gap": 0}
    for node in _walk_all(content):
        s = node.get("settings") or {}
        # Border radius — inline value lives at `border_radius.top` (since
        # it's typically a {top, right, bottom, left, isLinked} dict).
        br = s.get("border_radius")
        if isinstance(br, dict):
            v = _coerce_float(br.get("top"))
            if v is not None and v in radius_map:
                _add_class(s, radius_map[v])
                counts["radius"] += 1
        # Flex gap (containers only) — `flex_gap.size`
        gap = s.get("flex_gap")
        if isinstance(gap, dict):
            v = _coerce_float(gap.get("size"))
            if v is not None and v in gap_map:
                _add_class(s, gap_map[v])
                counts["gap"] += 1
    return counts


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _walk_all(content) -> Iterator[dict]:
    def walk(n):
        if isinstance(n, dict) and n.get("elType") in ("container", "widget"):
            yield n
            for c in n.get("elements") or []:
                yield from walk(c)
        elif isinstance(n, list):
            for it in n:
                yield from walk(it)
    yield from walk(content)


def _add_class(settings: dict, cls: str) -> None:
    existing = settings.get("css_classes") or ""
    parts = [p for p in str(existing).split() if p]
    if cls not in parts:
        parts.append(cls)
    settings["css_classes"] = " ".join(parts)


def _assign_tiers(values: list[float], tiers: tuple[str, ...]) -> list[tuple[float, str]]:
    """Map sorted unique values onto t-shirt tiers, dropping extras.

    If we have more values than tiers, evenly subsample so the smallest
    and largest values still anchor `xs` and the last tier.
    """
    if not values:
        return []
    if len(values) <= len(tiers):
        return list(zip(values, tiers))
    step = (len(values) - 1) / (len(tiers) - 1)
    indices = [round(i * step) for i in range(len(tiers))]
    picked = [values[idx] for idx in indices]
    return list(zip(picked, tiers))


def _finite_floats(seq) -> list[float]:
    out: list[float] = []
    if not seq:
        return out
    for x in seq:
        try:
            v = float(x)
            if v == v and v >= 0:  # NaN guard
                out.append(v)
        except (TypeError, ValueError):
            continue
    return out


def _coerce_float(v) -> float | None:
    if isinstance(v, (int, float)):
        return float(v)
    if isinstance(v, str):
        try:
            return float(v)
        except ValueError:
            return None
    return None


def _px(v: float) -> str:
    if v == int(v):
        return f"{int(v)}px"
    return f"{v}px"
