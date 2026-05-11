"""
Per-section screenshot crops.

The Figma plugin emits one full-page PNG per top-level frame. To support
two important features the agent uses separately:

  • screenshot fallbacks — replace a low-confidence section with the
    cropped region of the original screenshot
  • Claude visual review — feed Claude the live render + Figma render of
    a single section so it can reason about what's wrong

…we slice the full screenshot into per-section PNGs based on the bounds
each ai-layout section carries. The crops land under
`build/<export>/screenshots/sections/<node_id>.png` and are uploaded to
the WP media library by the same code path that uploads `assets/images/`.

Public API:
    crop_sections(export_dir, sections) → dict[node_id, Path]
"""
from __future__ import annotations

from pathlib import Path

try:
    from PIL import Image  # noqa: F401  (used inline)
    _HAS_PIL = True
except ImportError:
    _HAS_PIL = False


def crop_sections(
    export_dir: Path,
    real_sections: list,
) -> dict[str, Path]:
    """Slice the full-page screenshot into per-section crops.

    Returns {node_id: crop_path}. When PIL or the screenshot is missing,
    returns an empty dict — the caller must tolerate this.
    """
    if not _HAS_PIL:
        return {}
    shots_dir = export_dir / "screenshots"
    if not shots_dir.exists():
        return {}
    full_pngs = sorted(shots_dir.glob("*.png"))
    if not full_pngs:
        return {}
    full = full_pngs[0]

    # Crops live alongside the full-page screenshot so the existing
    # upload-screenshots pass picks them up automatically. Use a
    # distinctive prefix so they don't shadow the original.
    out_dir = shots_dir
    out_dir.mkdir(parents=True, exist_ok=True)

    from PIL import Image
    img = Image.open(full)
    iw, ih = img.size

    # Plugin screenshots are typically 2× the design width. Figure out
    # the actual scale factor by comparing pixel size to the root
    # section's design-coordinate width.
    scale_x = scale_y = 1.0
    root_bounds = next((s.bounds for s in real_sections if s.bounds), None)
    if root_bounds:
        design_w = max(1, root_bounds.get("width", 0) or 1920)
        scale_x = scale_y = iw / design_w if design_w else 1.0

    out: dict[str, Path] = {}
    for sec in real_sections:
        b = sec.bounds
        if not b:
            continue
        node_id = (sec.elementor_node.get("settings") or {}).get("_figma_id") or sec.figma_name or ""
        if not node_id:
            continue
        x0 = max(0, int((b.get("x", 0)) * scale_x))
        y0 = max(0, int((b.get("y", 0)) * scale_y))
        x1 = min(iw, int((b.get("x", 0) + b.get("width", 0)) * scale_x))
        y1 = min(ih, int((b.get("y", 0) + b.get("height", 0)) * scale_y))
        if x1 <= x0 or y1 <= y0:
            continue
        crop = img.crop((x0, y0, x1, y1))
        safe_id = str(node_id).replace(":", "_").replace("/", "_")
        path = out_dir / f"{safe_id}.png"
        crop.save(path)
        out[str(node_id)] = path
    return out
