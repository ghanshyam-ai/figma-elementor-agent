"""
Patch history cache for the auto-fixer loop.

After the auto-fixer (Phase H) successfully reduces drift below
threshold, the patches it applied are recorded in
`build/fix_history.json`. On the NEXT run of the same Figma file
(re-export → re-import), the orchestrator pre-applies any matching
patches BEFORE the first visual diff, so the auto-fixer doesn't have
to rediscover them.

Matching is keyed by `_figma_name + kind`, not by Elementor node id —
node ids change on every regen, so name-based matching is the only
stable handle across re-exports. We also store the figma_id when the
plugin stamped one, as a stronger secondary key.

Schema:
    {
      "version": 1,
      "page_slug": "home",
      "recorded_at": "2026-05-13T10:42:00",
      "patches": [
        {
          "figma_name": "Hero Heading",
          "figma_id": "123:45",        # optional
          "kind": "color"|"spacing"|"typography",
          "key": "title_color",
          "value": "#0F172A",
          "drift_before": 0.18,
          "drift_after": 0.03,
          "iterations": 1
        },
        ...
      ]
    }

Public API:
    record_patch(figma_name, kind, key, value, drift_before, drift_after)
    save_history(page_slug)
    load_history() → list[dict]
    apply_cached_patches(client, slug) → {applied: N, skipped: N}
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BUILD = ROOT / "build"
HISTORY_PATH = BUILD / "fix_history.json"

CURRENT_VERSION = 1


def _load(p: Path) -> dict:
    if not p.exists():
        return {}
    try:
        return json.loads(p.read_text())
    except json.JSONDecodeError:
        return {}


def load_history() -> dict:
    """Return the current fix-history payload (or empty)."""
    h = _load(HISTORY_PATH)
    if not h or h.get("version") != CURRENT_VERSION:
        return {"version": CURRENT_VERSION, "patches": []}
    return h


def record_patch(
    figma_name: str,
    kind: str,
    key: str,
    value,
    drift_before: float | None,
    drift_after: float | None,
    figma_id: str | None = None,
    iterations: int | None = None,
) -> None:
    """Append a patch to the in-memory history (caller calls save_history later)."""
    h = load_history()
    h.setdefault("patches", []).append({
        "figma_name": figma_name,
        "figma_id": figma_id,
        "kind": kind,
        "key": key,
        "value": value,
        "drift_before": drift_before,
        "drift_after": drift_after,
        "iterations": iterations,
    })
    HISTORY_PATH.write_text(json.dumps(h, indent=2, default=str))


def save_history(page_slug: str) -> Path:
    """Stamp the history with metadata and persist."""
    h = load_history()
    h["page_slug"] = page_slug
    h["recorded_at"] = dt.datetime.now().isoformat(timespec="seconds")
    HISTORY_PATH.write_text(json.dumps(h, indent=2, default=str))
    return HISTORY_PATH


def _find_node_by_figma_name(tree: list, name: str, figma_id: str | None = None) -> dict | None:
    """Walk the post-regen tree to find the node with matching _figma_name
    (or matching _figma_id when provided)."""
    target_name = (name or "").strip().lower()

    def walk(n):
        if not isinstance(n, dict):
            return None
        s = n.get("settings") or {}
        if figma_id and (s.get("_figma_id") or s.get("figma_id")) == figma_id:
            return n
        if target_name and (s.get("_figma_name") or "").strip().lower() == target_name:
            return n
        for c in n.get("elements") or []:
            r = walk(c)
            if r is not None:
                return r
        return None

    for top in tree:
        r = walk(top)
        if r is not None:
            return r
    return None


def apply_cached_patches(client, slug: str) -> dict:
    """Pre-apply every patch in the history file to the live page.

    Returns {"applied": N, "skipped": N, "missing": [list of names]}.
    Each skip is logged so the developer knows why the cache wasn't
    fully reusable (e.g. section was removed in the new Figma export).
    """
    h = load_history()
    patches = h.get("patches") or []
    if not patches:
        return {"applied": 0, "skipped": 0, "missing": []}

    # Resolve post id from slug
    pages = client.get("wp/v2/pages", params={"slug": slug, "status": "publish,draft,private"})
    if not pages:
        return {"applied": 0, "skipped": len(patches), "missing": [], "error": f"page '{slug}' not found"}
    post_id = int(pages[0]["id"])
    tree = client.get_elementor_data(post_id)

    applied = 0
    missing: list[str] = []
    for patch in patches:
        node = _find_node_by_figma_name(tree, patch.get("figma_name"), patch.get("figma_id"))
        if not node:
            missing.append(patch.get("figma_name") or patch.get("figma_id") or "?")
            continue
        settings = node.setdefault("settings", {})
        settings[patch["key"]] = patch["value"]
        applied += 1

    if applied:
        client.patch_elementor_data(post_id, tree)

    return {
        "applied": applied,
        "skipped": len(patches) - applied,
        "missing": missing,
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("show", help="Print current fix history as JSON.")
    apply_p = sub.add_parser("apply", help="Pre-apply cached patches to the live page.")
    apply_p.add_argument("--slug", required=True, help="Page slug to patch.")
    apply_p.add_argument("--config", default=str(ROOT / "project-config.json"))
    args = ap.parse_args()

    if args.cmd == "show":
        json.dump(load_history(), sys.stdout, indent=2)
        sys.stdout.write("\n")
        return 0

    if args.cmd == "apply":
        sys.path.insert(0, str(ROOT / "scripts"))
        from wp_client import WPClient, load_config
        cfg = load_config(args.config)
        password = cfg.get("wp_password") or cfg.get("wp_app_password")
        client = WPClient(cfg["wp_url"], cfg["wp_user"], password)
        client.login()
        result = apply_cached_patches(client, args.slug)
        print(f"✓ Pre-applied cached patches: applied={result['applied']}, "
              f"skipped={result['skipped']}")
        if result.get("missing"):
            print(f"  missing nodes (figma layer renamed/removed?): "
                  f"{', '.join(result['missing'][:5])}")
        return 0

    return 1


if __name__ == "__main__":
    sys.exit(main())
