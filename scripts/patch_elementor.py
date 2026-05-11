"""
Targeted patcher for an existing Elementor page or template.

Operations:
    --get                           print _elementor_data as JSON
    --set-setting <id> <key> <val>  set settings.<key> on the node with that id
    --set-color   <slug> <#hex>     update a system color in the active kit
    --replace-tree                  read full new tree from stdin (JSON array)

Usage examples:

    # dump the current page tree
    python3 scripts/patch_elementor.py --slug home --get > current.json

    # change a heading's color
    python3 scripts/patch_elementor.py --slug home --set-setting el00001 title_color '#000000'

    # change kit primary color
    python3 scripts/patch_elementor.py --set-color primary '#FF0055'
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))


def _wp_client_module():
    from wp_client import WPClient, WPError, load_config  # noqa: F401
    return WPClient, WPError, load_config


def find_post_id_by_slug(client, slug: str) -> int:
    pages = client.get("wp/v2/pages", params={"slug": slug, "status": "publish,draft,private"})
    if pages:
        return int(pages[0]["id"])
    raise SystemExit(f"No page with slug '{slug}'")


def update_node_setting(tree: list, node_id: str, key: str, value) -> bool:
    def walk(n) -> bool:
        if not isinstance(n, dict):
            return False
        if n.get("id") == node_id:
            settings = n.setdefault("settings", {})
            settings[key] = value
            return True
        for c in n.get("elements") or []:
            if walk(c):
                return True
        return False

    for top in tree:
        if walk(top):
            return True
    return False


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", default=str(ROOT / "project-config.json"))
    ap.add_argument("--slug", help="Page slug. Required for page operations.")
    ap.add_argument("--post-id", type=int, help="Post ID. Alternative to --slug.")

    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--get", action="store_true",
                   help="Print _elementor_data tree as JSON.")
    g.add_argument("--set-setting", nargs=3, metavar=("NODE_ID", "KEY", "VALUE"),
                   help="Set a single settings.<KEY> on node NODE_ID.")
    g.add_argument("--set-color", nargs=2, metavar=("SLUG", "HEX"),
                   help="Update kit system color (primary|secondary|text|accent).")
    g.add_argument("--replace-tree", action="store_true",
                   help="Read full elementor_data array from stdin and overwrite.")

    args = ap.parse_args()
    WPClient, WPError, load_config = _wp_client_module()
    cfg = load_config(args.config)
    client = WPClient(cfg["wp_url"], cfg["wp_user"], cfg["wp_app_password"])

    # Kit-only operation
    if args.set_color:
        slug, hexv = args.set_color
        page_settings = {
            "system_colors": [{"_id": slug, "title": slug.title(), "color": hexv}],
        }
        # Note: this overwrites only the matching slot; bridge merges into existing.
        r = client.update_kit_settings(page_settings)
        print(json.dumps(r, indent=2))
        return 0

    # Resolve post id
    if args.post_id:
        post_id = args.post_id
    elif args.slug:
        post_id = find_post_id_by_slug(client, args.slug)
    else:
        raise SystemExit("--slug or --post-id required for this operation")

    if args.get:
        tree = client.get_elementor_data(post_id)
        print(json.dumps(tree, indent=2))
        return 0

    if args.set_setting:
        node_id, key, value = args.set_setting
        # Heuristic: parse value as JSON if it looks like JSON; else string
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError:
            parsed = value
        tree = client.get_elementor_data(post_id)
        if not update_node_setting(tree, node_id, key, parsed):
            print(f"✗ node id={node_id} not found", file=sys.stderr)
            return 4
        client.patch_elementor_data(post_id, tree)
        print(f"✓ patched node {node_id}: settings.{key} = {parsed!r}")
        return 0

    if args.replace_tree:
        new_tree = json.load(sys.stdin)
        if not isinstance(new_tree, list):
            raise SystemExit("stdin must be a JSON array (the elementor_data)")
        client.patch_elementor_data(post_id, new_tree)
        print(f"✓ replaced elementor_data on post {post_id}")
        return 0

    return 0


if __name__ == "__main__":
    sys.exit(main())
