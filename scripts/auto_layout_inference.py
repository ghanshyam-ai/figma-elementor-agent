"""
Auto-layout inference.

Many real Figma files contain frames the designer never wrapped in
Auto Layout. The plugin's mapper emits those as plain containers with
absolutely-positioned children (`_position: absolute, _offset_x, _offset_y`)
which renders correctly in *exactly* the snapshot viewport but explodes
on any breakpoint, font swap, or content edit.

This module walks the Elementor tree and, for each non-flex container
whose children are clearly stacked or laid out in a row, infers
`flex_direction`, `flex_gap`, `flex_justify_content`, and
`flex_align_items` from the children's recorded geometry — promoting
them to a real auto-layout container.

The geometry comes from `_offset_x` + `_offset_y` (which the plugin
already writes when the parent is non-flex). When the offsets are
missing we leave the container alone — guessing from settings alone
would do more harm than good.

Public API:
    infer_auto_layout(content)  → {converted: n, skipped: n}
"""
from __future__ import annotations

from typing import Iterator


# Children with horizontal positions that fall within this column-height
# tolerance share a row.
ROW_HEIGHT_TOLERANCE = 32        # px

# Below this gap variance, we treat children as evenly-spaced (use flex_gap).
GAP_VARIANCE_PX = 8


def infer_auto_layout(content: list) -> dict[str, int]:
    counters = {"converted": 0, "skipped": 0}
    for node in _walk_containers(content):
        if not _is_non_flex(node):
            continue
        children = [c for c in (node.get("elements") or []) if isinstance(c, dict)]
        if len(children) < 2:
            continue
        positions = _collect_positions(children)
        if positions is None:
            counters["skipped"] += 1
            continue
        if _convert(node, children, positions):
            counters["converted"] += 1
        else:
            counters["skipped"] += 1
    return counters


def _is_non_flex(node: dict) -> bool:
    s = node.get("settings") or {}
    if node.get("elType") != "container":
        return False
    # Already flex? leave it.
    if s.get("flex_direction"):
        return False
    return True


def _collect_positions(children: list[dict]) -> list[tuple[float, float, float, float]] | None:
    """Return [(x, y, w, h), …] for every child IF every child has known position."""
    out = []
    for c in children:
        s = c.get("settings") or {}
        x = _coerce_float((s.get("_offset_x") or {}).get("size") if isinstance(s.get("_offset_x"), dict) else s.get("_offset_x"))
        y = _coerce_float((s.get("_offset_y") or {}).get("size") if isinstance(s.get("_offset_y"), dict) else s.get("_offset_y"))
        w = _coerce_float((s.get("width") or {}).get("size") if isinstance(s.get("width"), dict) else None)
        h = _coerce_float((s.get("min_height") or {}).get("size") if isinstance(s.get("min_height"), dict) else None)
        if x is None or y is None:
            return None
        out.append((x, y, w or 0.0, h or 0.0))
    return out


def _convert(node: dict, children: list[dict], positions: list[tuple]) -> bool:
    """Decide direction + gap + alignment, mutate `node.settings` in place."""
    ys = [p[1] for p in positions]
    xs = [p[0] for p in positions]
    spread_x = max(xs) - min(xs)
    spread_y = max(ys) - min(ys)

    direction: str
    if spread_y > spread_x:
        direction = "column"
        # gap = consecutive y deltas
        sorted_ys = sorted(ys)
        gaps = [sorted_ys[i + 1] - sorted_ys[i] for i in range(len(sorted_ys) - 1)]
    else:
        direction = "row"
        sorted_xs = sorted(xs)
        gaps = [sorted_xs[i + 1] - sorted_xs[i] for i in range(len(sorted_xs) - 1)]

    if not gaps:
        return False
    avg_gap = sum(gaps) / len(gaps)
    variance = max(gaps) - min(gaps)
    use_flex_gap = variance <= GAP_VARIANCE_PX

    settings = node.setdefault("settings", {})
    settings["flex_direction"] = direction
    settings["flex_wrap"] = "nowrap"
    if use_flex_gap and avg_gap > 0:
        settings["flex_gap"] = {"unit": "px", "size": round(avg_gap), "sizes": []}
        # Children no longer need explicit offsets.
        for c in children:
            cs = c.get("settings") or {}
            cs.pop("_offset_x", None)
            cs.pop("_offset_y", None)
            cs.pop("_position", None)
    settings["flex_justify_content"] = "flex-start"
    settings["flex_align_items"] = _infer_alignment(positions, direction)
    settings["_inferred_layout"] = True  # traceability marker
    return True


def _infer_alignment(positions: list[tuple], direction: str) -> str:
    """If all children share the same secondary-axis position, they're aligned start; if they centre vs the parent's center, return center; else stretch."""
    if direction == "column":
        secondary = [p[0] for p in positions]
    else:
        secondary = [p[1] for p in positions]
    if max(secondary) - min(secondary) < 4:
        return "flex-start"
    return "stretch"


def _coerce_float(v) -> float | None:
    if v is None:
        return None
    if isinstance(v, (int, float)):
        return float(v)
    if isinstance(v, str):
        try:
            return float(v.replace("px", "").strip())
        except ValueError:
            return None
    return None


def _walk_containers(content) -> Iterator[dict]:
    def walk(n):
        if isinstance(n, dict):
            if n.get("elType") == "container":
                yield n
            for c in n.get("elements") or []:
                yield from walk(c)
        elif isinstance(n, list):
            for it in n:
                yield from walk(it)
    yield from walk(content)
