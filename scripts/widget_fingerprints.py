"""
Structural fingerprints — sanity check every widget detector against.

The detector pipeline in `widget_inference.py` is precision-rich but
recall-fragile. A tightened detector for one shape (e.g. progress bar)
doesn't help when a different detector (e.g. tabs) trips on a layout it
shouldn't. Today (2026-05-13) we hit three independent false positives
in one run because each detector reasoned about its own children only.

This module defines a per-kind STRUCTURAL FINGERPRINT — the *minimum
structural facts that must be true* about a subtree before any detector
in INFERENCE_PIPELINE is allowed to collapse it into that widget. The
fingerprint check runs AFTER the detector says "yes" but BEFORE we
mutate the tree. A failure rejects the swap (the node stays a
container) and increments `_rejected_fingerprint` in the counters.

Each fingerprint declares:
  * `max_descendants`  — hard ceiling on dict descendants. Today's #1
    failure (3-col pricing → progress) is fundamentally about this.
  * `allowed_descendant_widget_types` — set of widget types the subtree
    is allowed to contain (after the converter would run). `None` means
    no constraint. Stops a "tabs" detector from collapsing a section
    full of buttons + carousels.
  * `min_text_descendants` / `max_text_descendants` — text-density
    band. A real icon-list has 2-12 short text strings; a section
    classified as icon-list with 30 text descendants is almost
    certainly a feature grid.
  * `forbidden_descendant_widget_types` — explicit blocklist (e.g. a
    counter must NOT contain image widgets).

The fingerprints are deliberately *conservative ceilings* — they don't
need to be tight (which would tank recall), only need to reject the
"obviously wrong" collapses. Hard cases still flow through to
Claude-as-Author.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable


@dataclass(frozen=True)
class Fingerprint:
    """Structural ceiling for a widget kind. All limits are 'must not
    exceed'; None means unbounded."""
    max_descendants: int | None = None
    allowed_descendant_widget_types: frozenset[str] | None = None
    forbidden_descendant_widget_types: frozenset[str] = field(default_factory=frozenset)
    min_text_descendants: int = 0
    max_text_descendants: int | None = None


# Widget types treated as "text-bearing" when counting text density.
TEXT_WIDGETS = frozenset({"heading", "text-editor", "text", "button"})


# Default fingerprints. Tuned conservatively: a real-world variation of
# the widget should stay under the ceiling, but the failure modes we've
# seen in production (pricing grids, logo strips, feature columns) blow
# through these by 3-10x.
FINGERPRINTS: dict[str, Fingerprint] = {
    "progress": Fingerprint(
        max_descendants=4,
        allowed_descendant_widget_types=frozenset({"heading", "text-editor"}),
        max_text_descendants=2,
    ),
    "counter": Fingerprint(
        max_descendants=4,
        allowed_descendant_widget_types=frozenset({"heading", "text-editor"}),
        forbidden_descendant_widget_types=frozenset({"image", "icon"}),
        max_text_descendants=3,
    ),
    "star-rating": Fingerprint(
        max_descendants=4,
        allowed_descendant_widget_types=frozenset({"heading", "text-editor", "icon"}),
        max_text_descendants=2,
    ),
    "divider": Fingerprint(
        max_descendants=2,
        max_text_descendants=0,
    ),
    "spacer": Fingerprint(
        max_descendants=0,
        max_text_descendants=0,
    ),
    "video": Fingerprint(
        max_descendants=4,
        max_text_descendants=1,
    ),
    "icon-box": Fingerprint(
        max_descendants=6,
        allowed_descendant_widget_types=frozenset({"icon", "image", "heading", "text-editor", "button"}),
        max_text_descendants=4,
    ),
    "image-box": Fingerprint(
        max_descendants=6,
        allowed_descendant_widget_types=frozenset({"image", "heading", "text-editor", "button"}),
        max_text_descendants=4,
    ),
    "social-icons": Fingerprint(
        max_descendants=20,
        allowed_descendant_widget_types=frozenset({"icon", "image"}),
        max_text_descendants=0,
    ),
    "icon-list": Fingerprint(
        max_descendants=50,
        allowed_descendant_widget_types=frozenset({"icon", "image", "heading", "text-editor"}),
        # An icon-list has at most ~12 items; 24 text descendants
        # (heading+caption per item) is the realistic upper bound.
        max_text_descendants=24,
    ),
    "image-carousel": Fingerprint(
        max_descendants=60,
        allowed_descendant_widget_types=frozenset({"image"}),
        max_text_descendants=0,
    ),
    "tabs": Fingerprint(
        max_descendants=120,
        # Tabs widgets carry tab-title + tab-content. Reject if it
        # contains widgets that need to stay structural (sliders,
        # carousels, nested galleries).
        forbidden_descendant_widget_types=frozenset({"image-carousel", "slides"}),
        # 30 tabs is the practical cap; each contributes ~6 text refs.
        max_text_descendants=200,
    ),
    "slides": Fingerprint(
        max_descendants=120,
        forbidden_descendant_widget_types=frozenset({"tabs", "image-carousel"}),
        max_text_descendants=200,
    ),
    "accordion": Fingerprint(
        max_descendants=120,
        forbidden_descendant_widget_types=frozenset({"tabs", "image-carousel"}),
        # 20 panels × ~10 text refs each. A "feature grid masquerading
        # as accordion" with 30+ text widgets per panel will exceed
        # this and be rejected.
        max_text_descendants=200,
    ),
    "toggle": Fingerprint(
        max_descendants=120,
        forbidden_descendant_widget_types=frozenset({"tabs", "image-carousel"}),
        max_text_descendants=200,
    ),
}


def _iter_descendants(node) -> Iterable[dict]:
    if isinstance(node, dict):
        for c in node.get("elements") or []:
            if isinstance(c, dict):
                yield c
                yield from _iter_descendants(c)
    elif isinstance(node, list):
        for it in node:
            yield from _iter_descendants(it)


def check_fingerprint(node, kind: str) -> tuple[bool, str]:
    """Return (passes, reason).

    `passes=True` means the structure is plausible for the target
    widget. `reason` is a short tag suitable for telemetry — empty when
    passing, e.g. "max_descendants" / "forbidden:image" when rejecting.
    """
    fp = FINGERPRINTS.get(kind)
    if fp is None:
        # No fingerprint registered → no opinion → allow. Detectors with
        # no FP listed are typically structural ones (tabs/slides above
        # do have FPs, but a future widget might be added without).
        return True, ""

    descendants = list(_iter_descendants(node))
    n_desc = len(descendants)
    if fp.max_descendants is not None and n_desc > fp.max_descendants:
        return False, f"max_descendants({n_desc}>{fp.max_descendants})"

    # Collect descendant widget types once.
    descendant_widgets: list[str] = []
    for d in descendants:
        if d.get("elType") == "widget":
            wt = d.get("widgetType")
            if isinstance(wt, str):
                descendant_widgets.append(wt)

    if fp.allowed_descendant_widget_types is not None:
        bad = [w for w in descendant_widgets if w not in fp.allowed_descendant_widget_types]
        if bad:
            return False, f"unallowed_descendant:{bad[0]}"
    if fp.forbidden_descendant_widget_types:
        bad = [w for w in descendant_widgets if w in fp.forbidden_descendant_widget_types]
        if bad:
            return False, f"forbidden_descendant:{bad[0]}"

    n_text = sum(1 for w in descendant_widgets if w in TEXT_WIDGETS)
    if n_text < fp.min_text_descendants:
        return False, f"too_few_text({n_text}<{fp.min_text_descendants})"
    if fp.max_text_descendants is not None and n_text > fp.max_text_descendants:
        return False, f"too_much_text({n_text}>{fp.max_text_descendants})"

    return True, ""
