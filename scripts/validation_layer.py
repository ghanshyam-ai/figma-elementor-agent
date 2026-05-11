"""
Confidence + validation layer.

Reads the plugin's `validation.json` warnings and per-node `confidence`
scores from `ai-layout.json`, surfaces a single import-level report, and
optionally swaps low-confidence sections for an image-widget fallback so
the live page never goes worse than "looks like the screenshot."

Public API:
    compute_report(e, content)             → ImportReport
    apply_screenshot_fallbacks(content, e, export_dir, asset_map, threshold)
                                            → list of patched section indices
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from enrich import Enrichment


# ---------------------------------------------------------------------------
# Report shape
# ---------------------------------------------------------------------------

@dataclass
class RiskArea:
    kind: str            # "low-confidence" | "warning"
    nodeId: str | None
    nodeName: str | None
    detail: str
    severity: str        # "info" | "warn" | "error"


@dataclass
class ImportReport:
    confidence: float = 1.0
    risk_areas: list[RiskArea] = field(default_factory=list)
    fallback_section_indices: list[int] = field(default_factory=list)
    summary: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "confidence": round(self.confidence, 3),
            "riskAreas": [r.__dict__ for r in self.risk_areas],
            "fallbackSectionIndices": self.fallback_section_indices,
            "summary": self.summary,
        }


# ---------------------------------------------------------------------------
# Computation
# ---------------------------------------------------------------------------

def compute_report(
    e: Enrichment,
    content: list,
    low_confidence_threshold: float = 0.5,
) -> ImportReport:
    """Aggregate ai-layout confidence + validation.json into a single report.

    The overall `confidence` is the mean of every section's confidence
    score, with each warning of severity `error` subtracting 0.1 and each
    `warn` subtracting 0.02 (capped at 0).
    """
    report = ImportReport()
    confidences: list[float] = []

    def visit(sec: dict, idx_path: list[int]) -> None:
        c = sec.get("confidence")
        if isinstance(c, (int, float)):
            confidences.append(float(c))
            if c < low_confidence_threshold:
                report.risk_areas.append(RiskArea(
                    kind="low-confidence",
                    nodeId=sec.get("id"),
                    nodeName=sec.get("name"),
                    detail=f"role={sec.get('role')} confidence={c:.2f} reason={sec.get('reason') or '—'}",
                    severity="warn",
                ))
                if len(idx_path) == 1:
                    report.fallback_section_indices.append(idx_path[0])
        for j, child in enumerate(sec.get("children") or []):
            visit(child, idx_path + [j])

    for i, top in enumerate(e.section_by_index):
        visit(top, [i])

    severity_weight = {"error": 0.10, "warn": 0.02, "info": 0.0}
    severity_counts = {"error": 0, "warn": 0, "info": 0}
    for w in (e.validation.get("warnings") or []):
        sev = w.get("level") or "info"
        severity_counts[sev] = severity_counts.get(sev, 0) + 1
        if sev != "info":
            report.risk_areas.append(RiskArea(
                kind="warning",
                nodeId=w.get("nodeId"),
                nodeName=w.get("nodeName"),
                detail=f"[{w.get('code')}] {w.get('message')}",
                severity=sev,
            ))

    base = (sum(confidences) / len(confidences)) if confidences else 1.0
    penalty = (
        severity_counts.get("error", 0) * severity_weight["error"]
        + severity_counts.get("warn", 0) * severity_weight["warn"]
    )
    report.confidence = max(0.0, base - penalty)
    report.summary = {
        "sections_total": len(e.section_by_index),
        "sections_low_confidence": len(report.fallback_section_indices),
        "warnings_error": severity_counts.get("error", 0),
        "warnings_warn": severity_counts.get("warn", 0),
        "warnings_info": severity_counts.get("info", 0),
        "containers": sum(1 for _ in _walk_containers(content)),
        "widgets": sum(1 for _ in _walk_widgets(content)),
    }
    return report


# ---------------------------------------------------------------------------
# Fallback strategy
# ---------------------------------------------------------------------------

def apply_screenshot_fallbacks(
    content: list,
    e: Enrichment,
    export_dir: Path,
    asset_map: dict[str, dict],
    threshold: float = 0.5,
) -> list[int]:
    """Replace each low-confidence top-level section with an image widget.

    Strategy (fail-safe over fail-pretty):
      1. Identify sections at index `i` whose ai-layout confidence < threshold.
      2. If a screenshot exists for that section's `id` (Figma node id),
         replace the Elementor container at `content[i]` with an image
         widget pointing at the (already-uploaded) screenshot.
      3. If no per-section screenshot is available, leave the section in
         place but tag it via `_low_confidence: true` so the developer
         can spot it in the editor.

    Returns the indices that were swapped to image fallbacks.
    """
    if not e.has_ai_layout:
        return []

    swapped: list[int] = []
    screenshots_dir = export_dir / "screenshots"

    for i, sec in enumerate(e.section_by_index):
        if i >= len(content):
            break
        c = sec.get("confidence")
        if not isinstance(c, (int, float)) or c >= threshold:
            continue

        node_id = sec.get("id")
        candidate_files = []
        if node_id:
            candidate_files += list(screenshots_dir.glob(f"{node_id}*.png"))
        if not candidate_files and sec.get("name"):
            safe = sec["name"].replace("/", "_")
            candidate_files += list(screenshots_dir.glob(f"{safe}*.png"))

        if candidate_files:
            shot = candidate_files[0]
            mapped = asset_map.get(shot.name)
            if mapped:
                content[i] = _make_image_section(mapped, sec)
                swapped.append(i)
                continue

        # No screenshot — annotate but keep structure.
        if isinstance(content[i], dict):
            content[i].setdefault("settings", {})["_low_confidence"] = True

    return swapped


def _make_image_section(mapped: dict, sec: dict) -> dict:
    """A full-bleed container holding a single image widget."""
    return {
        "id": "fblck" + (sec.get("id", "") or "x")[-3:],
        "elType": "container",
        "isInner": False,
        "settings": {
            "content_width": "full",
            "padding": {"unit": "px", "top": "0", "right": "0", "bottom": "0", "left": "0", "isLinked": True},
            "_low_confidence": True,
        },
        "elements": [
            {
                "id": "fbimg" + (sec.get("id", "") or "x")[-3:],
                "elType": "widget",
                "widgetType": "image",
                "settings": {
                    "image": {"url": mapped["url"], "id": mapped["id"], "source": "library"},
                    "image_size": "full",
                    "_element_width": "initial",
                    "_element_custom_width": {"unit": "%", "size": 100, "sizes": []},
                },
                "elements": [],
            }
        ],
    }


# ---------------------------------------------------------------------------
# Internal — same helpers as enrich.py but inlined to avoid the import cycle
# ---------------------------------------------------------------------------

def _walk_containers(content):
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


def _walk_widgets(content):
    def walk(n):
        if isinstance(n, dict):
            if n.get("elType") == "widget":
                yield n
            for c in n.get("elements") or []:
                yield from walk(c)
        elif isinstance(n, list):
            for it in n:
                yield from walk(it)
    yield from walk(content)
