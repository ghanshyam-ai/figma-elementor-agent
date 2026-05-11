"""
End-to-end importer: Figma plugin export ZIP → live Elementor page.

Usage:
    python3 scripts/import_elementor.py --config project-config.json
    python3 scripts/import_elementor.py --dry-run                       # don't write to WP
    python3 scripts/import_elementor.py --skip-globals --skip-page      # only header/footer
    python3 scripts/import_elementor.py --only-globals                  # only kit settings

Phases:
    A. Auth                 → /wp/v2/users/me
    B. Bridge               → /figma-importer/v1/health
    C. Extract ZIP          → build/<export>/
    D. Upload assets        → /wp/v2/media (one per image)
    E. Rewrite asset URLs   → in-memory transform of data.json.content
    F. Apply globals        → /figma-importer/v1/kit
    G. Header / Footer      → /figma-importer/v1/template
    H. Page                 → /figma-importer/v1/page
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
import sys
import zipfile
from copy import deepcopy
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BRIDGE_SRC = ROOT / "scripts" / "wp-bridge" / "figma-importer-bridge.php"
sys.path.insert(0, str(ROOT / "scripts"))

# New optimization / enrichment modules — imported lazily inside main() so
# `--help` works on a fresh checkout without installing anything yet.


def _wp_client_module():
    """Defer wp_client import so --help works without `requests` installed."""
    from wp_client import WPClient, WPError, load_config  # noqa: F401
    return WPClient, WPError, load_config


def ensure_bridge_installed(wp_root: Path) -> tuple[bool, Path]:
    """Copy the bridge mu-plugin into wp-content/mu-plugins/ if missing or stale.

    Returns (was_installed_or_updated, dst_path). Idempotent.
    """
    if not BRIDGE_SRC.exists():
        raise SystemExit(f"Bridge source missing: {BRIDGE_SRC}")
    if not wp_root.exists():
        raise SystemExit(f"wp_root does not exist: {wp_root}")

    mu_dir = wp_root / "wp-content" / "mu-plugins"
    dst = mu_dir / "figma-importer-bridge.php"
    mu_dir.mkdir(parents=True, exist_ok=True)

    if dst.exists() and dst.read_bytes() == BRIDGE_SRC.read_bytes():
        return (False, dst)

    shutil.copy2(BRIDGE_SRC, dst)
    return (True, dst)


# ---------------------------------------------------------------------------
# ZIP extraction
# ---------------------------------------------------------------------------

def extract_zip(zip_path: str, dest: Path) -> Path:
    zip_p = Path(zip_path)
    if not zip_p.is_absolute():
        zip_p = ROOT / zip_p
    if not zip_p.exists():
        raise SystemExit(f"ZIP not found: {zip_p}")

    dest.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(zip_p) as z:
        z.extractall(dest)

    for p in dest.rglob("data.json"):
        return p.parent
    raise SystemExit(f"No data.json found inside {zip_p}")


# ---------------------------------------------------------------------------
# Asset upload + URL rewriting
# ---------------------------------------------------------------------------

ASSET_REF_RE = re.compile(
    r"^(?:assets/images/|build/[^/]+/assets/images/)?([^/]+\.(?:png|jpg|jpeg|gif|svg|webp))$",
    re.IGNORECASE,
)


def _upload_subset(client, files: list[Path], dry_run: bool = False) -> dict[str, dict]:
    """Upload a specific list of files. Variant of upload_assets that takes
    files as input rather than scanning a directory — used by the project-
    state-aware path that only uploads files not already on the site."""
    asset_map: dict[str, dict] = {}
    if dry_run:
        for img in files:
            print(f"  [dry] would upload {img.name}")
            asset_map[img.name] = {"url": f"STUB://media/{img.name}", "id": 0}
        return asset_map
    _, WPError, _ = _wp_client_module()
    for img in files:
        try:
            result = client.upload_media(img)
        except WPError as exc:
            print(f"  ✗ upload {img.name}: {exc}", file=sys.stderr)
            continue
        asset_map[img.name] = {"url": result["source_url"], "id": result["id"]}
        print(f"  ✓ {img.name} → id={result['id']}")
    return asset_map


def upload_assets(client, assets_dir: Path, dry_run: bool = False) -> dict[str, dict]:
    asset_map: dict[str, dict] = {}
    if not assets_dir.exists():
        return asset_map

    images = sorted(p for p in assets_dir.iterdir() if p.is_file())
    if dry_run:
        for img in images:
            print(f"  [dry] would upload {img.name}")
            asset_map[img.name] = {"url": f"STUB://media/{img.name}", "id": 0}
        return asset_map

    _, WPError, _ = _wp_client_module()
    for img in images:
        try:
            result = client.upload_media(img)
        except WPError as exc:
            print(f"  ✗ upload {img.name}: {exc}", file=sys.stderr)
            continue
        asset_map[img.name] = {"url": result["source_url"], "id": result["id"]}
        print(f"  ✓ {img.name} → id={result['id']}")
    return asset_map


def _maybe_rewrite_string(value: str, asset_map: dict[str, dict]) -> str | None:
    m = ASSET_REF_RE.match(value.strip())
    if not m:
        return None
    name = m.group(1)
    return asset_map[name]["url"] if name in asset_map else None


def rewrite_asset_urls(node, asset_map: dict[str, dict]) -> None:
    """Recursively rewrite asset references in an Elementor node tree (in place)."""
    if isinstance(node, dict):
        url = node.get("url")
        if isinstance(url, str):
            m = ASSET_REF_RE.match(url)
            if m and m.group(1) in asset_map:
                mapped = asset_map[m.group(1)]
                node["url"] = mapped["url"]
                node["id"] = mapped["id"]
        for k, v in list(node.items()):
            if isinstance(v, str) and k != "url":
                new_str = _maybe_rewrite_string(v, asset_map)
                if new_str:
                    node[k] = new_str
            elif isinstance(v, (dict, list)):
                rewrite_asset_urls(v, asset_map)
    elif isinstance(node, list):
        for item in node:
            rewrite_asset_urls(item, asset_map)


# ---------------------------------------------------------------------------
# Globals → kit settings
# ---------------------------------------------------------------------------

SYSTEM_COLOR_SLUGS = ("primary", "secondary", "text", "accent")


def _slug(s: str) -> str:
    return re.sub(r"[^a-z0-9_-]+", "_", s.lower()).strip("_") or "x"


def map_global_to_kit_settings(global_json: dict) -> dict:
    colors = list(global_json.get("colors", []))
    typography = list(global_json.get("typography", []))

    system_colors: list[dict] = []
    remaining_slots = list(SYSTEM_COLOR_SLUGS)
    # First pass: claim slots for colors whose name matches a canonical slug.
    claimed: dict[int, str] = {}
    for idx, c in enumerate(colors[:4]):
        name = c.get("name", "")
        if name in remaining_slots:
            claimed[idx] = name
            remaining_slots.remove(name)
    # Second pass: assign remaining canonical slots in order.
    for idx, c in enumerate(colors[:4]):
        if idx in claimed:
            slug = claimed[idx]
        else:
            slug = remaining_slots.pop(0) if remaining_slots else _slug(c.get("name", f"c{idx}"))
        title = (c.get("name") or slug).title()
        system_colors.append({"_id": slug, "title": title, "color": c["value"]})

    custom_colors: list[dict] = []
    for c in colors[4:]:
        h = hashlib.md5(c["value"].encode()).hexdigest()[:7]
        custom_colors.append({"_id": h, "title": c["name"], "color": c["value"]})

    # System typography — first instance per name slot
    seen: set[str] = set()
    system_typography: list[dict] = []
    primary_font: str | None = None
    for t in typography:
        name = t.get("name") or ""
        if name in seen:
            continue
        if name not in ("display", "h1", "h2", "h3", "h4", "body", "small", "caption", "caption-strong"):
            continue
        seen.add(name)
        ts: dict = {
            "_id": _slug(name),
            "title": name.title(),
            "typography_typography": "custom",
        }
        ff = t.get("fontFamily")
        if ff:
            ts["typography_font_family"] = ff
            primary_font = primary_font or ff
        if t.get("fontWeight"):
            ts["typography_font_weight"] = str(int(t["fontWeight"]))
        if t.get("fontSize"):
            ts["typography_font_size"] = {"unit": "px", "size": float(t["fontSize"]), "sizes": []}
        lh = t.get("lineHeight")
        if isinstance(lh, (int, float)):
            ts["typography_line_height"] = {"unit": "px", "size": float(lh), "sizes": []}
        ls = t.get("letterSpacing")
        if isinstance(ls, (int, float)) and ls != 0:
            ts["typography_letter_spacing"] = {"unit": "px", "size": float(ls), "sizes": []}
        system_typography.append(ts)

    settings: dict = {
        "system_colors": system_colors,
        "custom_colors": custom_colors,
        "system_typography": system_typography,
    }
    if primary_font:
        settings["default_generic_fonts"] = primary_font

    spacings = global_json.get("spacing", [])
    if spacings:
        settings["space_between_widgets"] = {
            "unit": "px",
            "size": min((s for s in spacings if 8 <= s <= 32), default=20),
            "sizes": [],
        }
    return settings


# ---------------------------------------------------------------------------
# Header / footer extraction
# ---------------------------------------------------------------------------

def find_section(content: list, name_pattern: str) -> dict | None:
    if not name_pattern:
        return None
    pat = re.compile(name_pattern, re.IGNORECASE)

    def walk(node):
        if not isinstance(node, dict):
            return None
        name = (node.get("settings") or {}).get("_figma_name", "") or ""
        if pat.search(name):
            return node
        for c in node.get("elements") or []:
            r = walk(c)
            if r is not None:
                return r
        return None

    for top in content:
        r = walk(top)
        if r is not None:
            return r
    return None


def remove_node_by_id(content: list, target_id: str) -> bool:
    def walk(node) -> bool:
        if not isinstance(node, dict):
            return False
        children = node.get("elements") or []
        for i, c in enumerate(children):
            if isinstance(c, dict) and c.get("id") == target_id:
                children.pop(i)
                return True
            if walk(c):
                return True
        return False

    for top in content:
        if walk(top):
            return True
    return False


# ---------------------------------------------------------------------------
# Stats
# ---------------------------------------------------------------------------

def build_default_menu_items(slug_to_url: dict[str, str]) -> list[dict]:
    """Generate placeholder menu items. If we already created pages, link to them;
    otherwise use # placeholders the developer can wire up later in wp-admin → Menus."""
    items = []
    for label, slug in (("Home", "home"), ("About", "about"), ("Services", "services"),
                       ("Blog", "blog"), ("Contact", "contact")):
        url = slug_to_url.get(slug, "#")
        items.append({"title": label, "url": url})
    return items


def make_nav_menu_widget(menu_slug: str, layout: str = "horizontal") -> dict:
    """Return an Elementor `nav-menu` widget node (Pro) bound to a WP menu by slug."""
    import secrets
    return {
        "id": secrets.token_hex(4),
        "elType": "widget",
        "widgetType": "nav-menu",
        "settings": {
            "menu": menu_slug,
            "layout": layout,
            "align": "right",
            "pointer": "underline",
            "indicator": "none",
            "submenu_icon": {"value": "fas fa-caret-down", "library": "fa-solid"},
        },
        "elements": [],
    }


def make_wp_menu_widget(menu_slug: str) -> dict:
    """Core 'WordPress Menu' widget — fallback when Elementor Pro is not available."""
    import secrets
    return {
        "id": secrets.token_hex(4),
        "elType": "widget",
        "widgetType": "wp-widget-nav_menu",
        "settings": {
            "title": "",
            "nav_menu": menu_slug,
        },
        "elements": [],
    }


def inject_nav_menu_into_template(template_node: dict, menu_widget: dict) -> bool:
    """Append the menu widget to the deepest container in the template tree.

    Conservative — does NOT replace any existing widgets, only adds the menu
    widget so it's available for the developer to position in the editor.
    Returns True on success.
    """
    if not isinstance(template_node, dict) or template_node.get("elType") != "container":
        return False

    # find the deepest container that has at least one widget child
    deepest = template_node
    def walk(n, depth):
        nonlocal deepest, _depth
        if not isinstance(n, dict):
            return
        if n.get("elType") == "container" and depth > _depth:
            children = n.get("elements", [])
            if any(isinstance(c, dict) and c.get("elType") == "widget" for c in children):
                deepest = n
                _depth = depth
        for c in n.get("elements", []) or []:
            walk(c, depth + 1)
    _depth = 0
    walk(template_node, 0)

    deepest.setdefault("elements", []).append(menu_widget)
    return True


def widget_stats(content: list) -> dict:
    stats = {"containers": 0, "widgets_total": 0, "widgets": {}}

    def walk(n):
        if not isinstance(n, dict):
            return
        if n.get("elType") == "container":
            stats["containers"] += 1
        elif n.get("elType") == "widget":
            stats["widgets_total"] += 1
            wt = n.get("widgetType", "?")
            stats["widgets"][wt] = stats["widgets"].get(wt, 0) + 1
        for c in n.get("elements") or []:
            walk(c)

    for top in content:
        walk(top)
    return stats


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", default=str(ROOT / "project-config.json"))
    ap.add_argument("--zip", dest="zip_override",
                    help="Override zip_path from config (for multi-page imports).")
    ap.add_argument("--page-slug", dest="page_slug_override",
                    help="Override page_slug from config (for multi-page imports).")
    ap.add_argument("--dry-run", action="store_true",
                    help="Don't write to WordPress; print intended actions.")
    ap.add_argument("--skip-globals", action="store_true",
                    help="Don't touch kit settings (useful for 2nd+ page on same site).")
    ap.add_argument("--skip-menus", action="store_true",
                    help="Don't create/update WordPress nav menus.")
    ap.add_argument("--reset-menus", action="store_true",
                    help="Wipe and rebuild Primary/Footer menu items (destructive).")
    ap.add_argument("--skip-header-footer", action="store_true",
                    help="Don't recreate header/footer templates.")
    ap.add_argument("--skip-page", action="store_true")
    ap.add_argument("--skip-assets", action="store_true",
                    help="Skip media upload (URLs in data.json will be left as-is).")
    ap.add_argument("--only-globals", action="store_true",
                    help="Shortcut: only apply globals.")
    ap.add_argument("--skip-optimize", action="store_true",
                    help="Skip token resolver, container collapse, widget enforcement, depth cap.")
    ap.add_argument("--skip-forms", action="store_true",
                    help="Don't create Gravity Forms for detected form sections.")
    ap.add_argument("--skip-template-reuse", action="store_true",
                    help="Don't deduplicate identical sections via library templates.")
    ap.add_argument("--skip-fallbacks", action="store_true",
                    help="Don't replace low-confidence sections with screenshot images.")
    ap.add_argument("--max-depth", type=int, default=6,
                    help="Maximum container nesting depth (default: 6).")
    ap.add_argument("--low-confidence-threshold", type=float, default=0.5,
                    help="Sections below this confidence get screenshot fallback (default: 0.5).")
    ap.add_argument("--page-only", action="store_true",
                    help="Page-by-page mode: skip globals + header/footer + design tokens. "
                         "Use this for the 2nd, 3rd, … page after the home page already created them.")
    ap.add_argument("--reset-media", action="store_true",
                    help="Before uploading, delete agent-uploaded attachments from prior runs "
                         "(matches by figma-export filename prefix). Destructive — confirm first.")
    args = ap.parse_args()

    if args.only_globals:
        args.skip_header_footer = True
        args.skip_page = True
        args.skip_assets = True

    # Page-by-page mode: explicit way to say "this is the 2nd / 3rd / Nth
    # page; the home page already created globals + header + footer."
    if args.page_only:
        args.skip_globals = True
        args.skip_header_footer = True
        args.skip_template_reuse = True  # only run reuse on first run for now

    if args.dry_run:
        # Dry run: read config without needing `requests`.
        cfg = json.loads(Path(args.config).read_text())
        client = None
    else:
        WPClient, WPError, load_config = _wp_client_module()
        cfg = load_config(args.config)
        # Backward compatibility — accept old `wp_app_password` field too.
        password = cfg.get("wp_password") or cfg.get("wp_app_password")
        if not password:
            print("✗ project-config.json missing wp_password. Re-run python3 orchestrator.py.")
            return 5
        client = WPClient(cfg["wp_url"], cfg["wp_user"], password)

    # CLI overrides for multi-page workflow
    if args.zip_override:
        cfg["zip_path"] = args.zip_override
    if args.page_slug_override:
        cfg["page_slug"] = args.page_slug_override
    print(f"→ Target: {cfg['wp_url']}  (theme: {cfg.get('theme_slug', '?')})")
    print(f"  zip:   {cfg['zip_path']}")
    print(f"  slug:  {cfg.get('page_slug', 'home')}")

    # --- Project state — auto-detect first run vs subsequent runs --------
    from project_state import load_state
    project_state = load_state(ROOT)
    if not args.page_only and not project_state.is_first_run:
        # Implicit page-only: kit + theme-builder templates already exist.
        # The dev didn't pass --page-only, but state says we don't need to
        # re-do those phases. Surface the auto-skip so it isn't surprising.
        print(f"• prior run detected (kit_id={project_state.kit_id}, "
              f"templates={len(project_state.template_ids_by_slug)}) → "
              f"auto-skip globals + header/footer (override with --skip-globals=false in env)")
        if not args.skip_globals:
            args.skip_globals = True
        if not args.skip_header_footer:
            args.skip_header_footer = True
    project_state.remember_run()

    # --- Auth + bridge ----------------------------------------------------
    if not args.dry_run:
        # The bridge mu-plugin must respond to /health BEFORE login (login is
        # itself a bridge endpoint). Auto-install if missing.
        wp_root_pre = cfg.get("wp_root")
        if wp_root_pre:
            installed, _ = ensure_bridge_installed(Path(wp_root_pre))
            if installed:
                print("+ Bridge plugin installed/updated (pre-login)")
        try:
            login = client.login()
            print(f"✓ Logged in as {login.get('display_name') or login.get('username')} "
                  f"(id={login.get('user_id')}, roles={','.join(login.get('roles', []))})")
        except WPError as exc:
            print(f"✗ Login failed: {exc}", file=sys.stderr)
            print("  Check that wp_user / wp_password in project-config.json are correct.")
            print("  (use the same credentials you log into wp-admin with)")
            return 2

        health = client.bridge_health()
        if not health:
            print("\n✗ figma-importer-bridge mu-plugin route not responding.")
            if wp_root:
                print(f"  Plugin file: {Path(wp_root) / 'wp-content/mu-plugins/figma-importer-bridge.php'}")
                print("  Possible causes:")
                print("    - Pretty permalinks not enabled (Settings → Permalinks → Post name)")
                print("    - PHP fatal in the plugin (check the PHP error log)")
                print("    - Caching plugin holding a stale REST index")
            else:
                print("  No wp_root in project-config.json — re-run python3 orchestrator.py")
            return 3
        print(
            f"✓ Bridge healthy: elementor={health.get('elementor')}, "
            f"pro={health.get('elementor_pro') or 'no'}, kit={health.get('active_kit')}"
        )
        if not health.get("elementor"):
            print("\n✗ Elementor plugin is not active. Activate it before importing.")
            return 4
    else:
        print("• Dry run — skipping auth and bridge checks")

    # --- Extract + parse --------------------------------------------------
    build_dir = ROOT / "build"
    export_dir = extract_zip(cfg["zip_path"], build_dir)
    print(f"✓ Extracted to {export_dir.relative_to(ROOT)}")

    data = json.loads((export_dir / "data.json").read_text())
    global_json = json.loads((export_dir / "global.json").read_text())
    metadata = json.loads((export_dir / "metadata.json").read_text())

    s = widget_stats(data["content"])
    widget_summary = ", ".join(f"{k}={v}" for k, v in sorted(s["widgets"].items(), key=lambda x: -x[1]))
    print(f"  page: \"{data.get('title')}\"  containers={s['containers']}  widgets={s['widgets_total']}  ({widget_summary})")
    print(f"  assets={metadata['counts']['assets']}  screenshots={metadata['counts']['screenshots']}")

    # --- Enrichment (ai-layout + tokens + validation + assets manifest) --
    from enrich import load_enrichment
    enrichment = load_enrichment(export_dir)
    if enrichment.has_ai_layout:
        page_type = enrichment.ai_layout.get("pageType", "page")
        print(f"✓ ai-layout: {len(enrichment.section_by_index)} top-level sections (pageType={page_type})")
    if enrichment.validation.get("warnings"):
        sm = enrichment.validation.get("summary", {})
        print(f"  validation: {sm.get('warn',0)} warn, {sm.get('error',0)} error, {sm.get('info',0)} info")

    # --- Reset media (destructive, opt-in) -------------------------------
    if args.reset_media and not args.dry_run:
        print("→ Resetting media library (deletes agent-uploaded attachments)…")
        rr = client.reset_media()
        print(f"  removed {rr.get('deleted',0)} attachment(s) (matched {rr.get('matched',0)})")
        # Once we delete, any prior asset_map_by_filename entries are stale.
        project_state.asset_map_by_filename = {}

    # --- Assets -----------------------------------------------------------
    asset_map: dict[str, dict] = {}
    screenshot_map: dict[str, dict] = {}
    assets_dir = export_dir / "assets" / "images"
    screenshots_dir = export_dir / "screenshots"
    if not args.skip_assets:
        # Seed from prior runs so a 2nd page doesn't re-upload the logo/hero
        # assets that already exist on the WP media library.
        if project_state.asset_map_by_filename:
            asset_map.update(project_state.asset_map_by_filename)
        if assets_dir.exists():
            existing = set(asset_map.keys())
            new_files = [p for p in assets_dir.iterdir() if p.is_file() and p.name not in existing]
            print(f"→ Upload {len(new_files)} new asset(s) (skipping {len(existing)} already on site)")
            if new_files:
                # Upload only the genuinely new files.
                asset_map.update(_upload_subset(client, new_files, dry_run=args.dry_run))
        else:
            print("  (no assets/images directory)")
        # Upload screenshots too — they're the source for low-confidence
        # fallback (replace a section with the screenshot it came from).
        if screenshots_dir.exists() and not args.skip_fallbacks:
            screenshot_map = upload_assets(client, screenshots_dir, dry_run=args.dry_run)

    # --- Rewrite ----------------------------------------------------------
    content = deepcopy(data["content"])
    if asset_map:
        rewrite_asset_urls(content, asset_map)
        print(f"✓ Rewrote {len(asset_map)} asset URLs in elementor data")

    # --- Globals ----------------------------------------------------------
    page_settings: dict = {}
    radius_map: dict = {}
    gap_map: dict = {}
    if not args.skip_globals:
        page_settings = map_global_to_kit_settings(global_json)
        # Design-token CSS for spacing + radius (Elementor doesn't natively
        # support these as globals — we inject :root vars + utility classes).
        from design_tokens import build_token_css
        token_css, radius_map, gap_map = build_token_css(global_json)
        if token_css:
            existing_css = page_settings.get("custom_css") or ""
            # Replace any prior agent-managed block so re-runs stay idempotent.
            existing_css = _strip_token_block(existing_css)
            page_settings["custom_css"] = (existing_css + "\n\n" + token_css).strip()

        n_colors = len(page_settings.get("system_colors", [])) + len(page_settings.get("custom_colors", []))
        n_type = len(page_settings.get("system_typography", []))
        n_rad = len(radius_map)
        n_gap = len(gap_map)
        if args.dry_run:
            print(f"  [dry] would update kit: {n_colors} colors, {n_type} typography, {n_rad} radius tokens, {n_gap} gap tokens")
        else:
            r = client.update_kit_settings(page_settings)
            print(f"✓ Kit {r['kit_id']} updated: {n_colors} colors, {n_type} typography, {n_rad} radius tokens, {n_gap} gap tokens")
            project_state.record_kit_applied(r["kit_id"])

    # --- Per-section screenshot crops -----------------------------------
    # Slice the full-page Figma screenshot into per-section PNGs (using
    # bounds from ai-layout). Crops feed two downstream consumers:
    #   • screenshot fallbacks for low-confidence sections
    #   • Claude visual review (per-section diffing)
    try:
        from section_crops import crop_sections as _crop_sections
        # crop_sections needs a real_sections list; we don't have it yet —
        # call it again later with proper sections, or thread it through.
        # For now, this initialisation is deferred; the actual call happens
        # below once `real_sections` is built.
    except ImportError:
        _crop_sections = None  # PIL missing; fallback path will skip crops

    # --- Recursive section finder (any depth) ----------------------------
    # Replaces the top-level-only architecture routing that broke on
    # designs wrapped in a single root frame.
    from section_finder import find_real_sections, by_kind as sf_by_kind, summarize as sf_summary, detach as sf_detach
    real_sections = find_real_sections(content, enrichment.section_by_index)
    structural_node_ids = {id(s.elementor_node) for s in real_sections}
    if real_sections:
        print(f"✓ Sections found (any depth): " + ", ".join(
            f"{k}={v}" for k, v in sorted(sf_summary(real_sections).items())
        ))
        for rs in real_sections:
            tag = " [inferred]" if rs.inferred else ""
            print(f"    {rs.kind:8s} depth={rs.depth} conf={rs.confidence:.2f}{tag} — {rs.figma_name or '?'}")

    # Now we have real_sections — slice per-section crops into the
    # screenshots/ dir so the existing upload pass picks them up.
    if _crop_sections is not None:
        try:
            crops = _crop_sections(export_dir, real_sections)
            if crops:
                print(f"  + cropped {len(crops)} per-section screenshot(s) for fallback + review")
        except Exception as exc:
            print(f"  (skipped section crops: {exc})")

    # --- Token resolver + optimization passes ----------------------------
    optimize_stats = {"colors": 0, "typography": 0, "collapsed": 0, "hoisted": 0,
                      "widgets_swapped": 0, "html_replaced": 0,
                      "radius_tagged": 0, "gap_tagged": 0,
                      "auto_layout_inferred": 0, "widgets_inferred": 0}
    if not args.skip_optimize:
        from optimize import (
            resolve_global_tokens, collapse_single_child_containers,
            cap_nesting_depth, enforce_widget_preferences, replace_html_widgets,
        )
        if page_settings:
            tok = resolve_global_tokens(content, page_settings)
            optimize_stats["colors"] = tok["colors"]
            optimize_stats["typography"] = tok["typography"]
        # Tag widgets/containers whose inline radius/gap matches a token
        # class so the kit-level CSS owns those values from now on.
        if radius_map or gap_map:
            from design_tokens import apply_design_token_classes
            tok2 = apply_design_token_classes(content, radius_map, gap_map)
            optimize_stats["radius_tagged"] = tok2["radius"]
            optimize_stats["gap_tagged"] = tok2["gap"]
        optimize_stats["html_replaced"] = replace_html_widgets(content)
        optimize_stats["widgets_swapped"] = enforce_widget_preferences(content, enrichment)
        # Recursive widget inference — runs at ANY depth, not just top level
        from widget_inference import infer_and_swap
        inferred = infer_and_swap(content, structural_node_ids)
        optimize_stats["widgets_inferred"] = sum(inferred.values())
        if inferred:
            print(f"  + inferred widgets: " + ", ".join(f"{k}={v}" for k, v in sorted(inferred.items())))
        # Auto-layout inference — promote absolutely-positioned children
        # into proper flex containers when their geometry tells us how
        from auto_layout_inference import infer_auto_layout
        al = infer_auto_layout(content)
        optimize_stats["auto_layout_inferred"] = al["converted"]
        if al["converted"]:
            print(f"  + inferred auto-layout on {al['converted']} container(s) (skipped {al['skipped']})")
        # Collapse + depth cap respect structural sections — never destroy
        # a header / footer / hero just because its settings look pass-through.
        optimize_stats["collapsed"] = collapse_single_child_containers(
            content, protected_ids=structural_node_ids,
        )
        optimize_stats["hoisted"] = cap_nesting_depth(content, args.max_depth)
        print(
            f"✓ Optimize: {optimize_stats['colors']} colors→globals, "
            f"{optimize_stats['typography']} typo→globals, "
            f"{optimize_stats['radius_tagged']}+{optimize_stats['gap_tagged']} token classes, "
            f"{optimize_stats['collapsed']} containers collapsed, "
            f"{optimize_stats['hoisted']} hoisted (depth≤{args.max_depth}), "
            f"{optimize_stats['widgets_swapped']} widgets swapped, "
            f"{optimize_stats['html_replaced']} html→text-editor"
        )

    # --- Confidence + screenshot fallbacks -------------------------------
    from validation_layer import compute_report, apply_screenshot_fallbacks
    fallback_indices: list[int] = []
    if not args.skip_fallbacks:
        if args.dry_run:
            from enrich import low_confidence_node_ids
            low = low_confidence_node_ids(enrichment, args.low_confidence_threshold)
            if low:
                print(f"  [dry] would replace {len(low)} low-confidence section(s) with screenshot fallback")
        else:
            fallback_indices = apply_screenshot_fallbacks(
                content, enrichment, export_dir, screenshot_map,
                threshold=args.low_confidence_threshold,
            )
            if fallback_indices:
                print(f"  fallback: {len(fallback_indices)} low-confidence section(s) replaced with screenshot")

    # --- Dynamic content (Posts widget for blog grids) -------------------
    has_pro = bool(health.get("elementor_pro")) if not args.dry_run else False
    has_gforms = bool(health.get("gravity_forms")) if not args.dry_run else False
    from dynamic_content import detect_dynamic_sections, replace_with_posts_widget
    dyn_candidates = detect_dynamic_sections(content, enrichment, has_elementor_pro=has_pro)
    if dyn_candidates:
        n = replace_with_posts_widget(content, dyn_candidates)
        kind = "Pro Posts widget" if has_pro else "WP Recent Posts"
        print(f"✓ Dynamic: {n} blog-grid section(s) → {kind}")

    # --- Menus ------------------------------------------------------------
    primary_menu_info = footer_menu_info = None
    if not args.skip_menus:
        primary_name = cfg.get("primary_menu_name", "Primary Menu")
        primary_loc  = cfg.get("primary_menu_location", "menu-1")
        footer_name  = cfg.get("footer_menu_name", "Footer Menu")
        footer_loc   = cfg.get("footer_menu_location", "menu-2")
        slug_to_url = {cfg.get("page_slug", "home"):
                       f"{cfg['wp_url'].rstrip('/')}/{cfg.get('page_slug', 'home')}/"}
        items = build_default_menu_items(slug_to_url)

        if args.dry_run:
            print(f"  [dry] would create/update menus: '{primary_name}' (location={primary_loc}) "
                  f"and '{footer_name}' (location={footer_loc}) with {len(items)} placeholder items each")
            primary_menu_info = {"slug": "primary-menu"}
            footer_menu_info  = {"slug": "footer-menu"}
        else:
            print(f"→ Create/update WP menus")
            primary_menu_info = client.create_or_update_menu(
                name=primary_name, location=primary_loc, items=items, reset=args.reset_menus
            )
            footer_menu_info = client.create_or_update_menu(
                name=footer_name, location=footer_loc, items=items, reset=args.reset_menus
            )
            print(f"✓ {primary_name}  id={primary_menu_info['id']}  slug={primary_menu_info['slug']}  "
                  f"items={primary_menu_info['item_count']}  "
                  f"({'created' if primary_menu_info['created'] else 'existing'})")
            print(f"✓ {footer_name}   id={footer_menu_info['id']}  slug={footer_menu_info['slug']}  "
                  f"items={footer_menu_info['item_count']}  "
                  f"({'created' if footer_menu_info['created'] else 'existing'})")

    # --- Form intelligence (Gravity Forms) -------------------------------
    form_results: list[dict] = []
    if not args.skip_forms:
        from form_intelligence import detect_forms, materialize_forms
        form_candidates = detect_forms(content, enrichment)
        if args.dry_run:
            for fc in form_candidates:
                print(f"  [dry] would create Gravity Form '{fc.title}' "
                      f"({len(fc.fields)} fields, button='{fc.button_text}') "
                      f"→ shortcode at section {fc.section_index}")
        elif has_gforms:
            if form_candidates:
                form_results = materialize_forms(client, content, form_candidates)
                for fr in form_results:
                    print(f"✓ Form: id={fr['form_id']} \"{fr['title']}\" ({fr['fields']} fields) → shortcode placed")
                    project_state.record_form(fr["title"], fr["form_id"])
        elif form_candidates:
            print("  (form section(s) detected; install Gravity Forms to convert them automatically)")

    # --- Architecture routing — uses section_finder.real_sections --------
    is_pro = has_pro
    from architecture import Placement
    placements: list = []
    for rs in real_sections:
        # Header/footer/popup/etc. become Theme Builder templates and are
        # detached from the page tree. Other kinds (hero, etc.) stay on
        # the page. We only route the kinds the bridge knows how to make.
        if rs.kind in ("header", "footer", "popup", "archive", "single", "search", "404"):
            placements.append(Placement(
                kind=rs.kind,
                section_index=rs.parent_index,
                elementor_node=rs.elementor_node,
                ai_section=rs.ai_section,
                reason=rs.reason,
            ))
            sf_detach(rs)

    # Top-level entries that aren't templates simply go on the page.
    from architecture import by_kind, summary as arch_summary
    if placements:
        print(f"✓ Architecture: " + ", ".join(f"{k}={v}" for k, v in sorted(arch_summary(placements).items())))
    else:
        print("  (no header/footer/popup/archive/single/search/404 sections detected)")

    # Header / Footer / Popup / Archive / Single / Search / 404 — by placements
    if not args.skip_header_footer:
        for p in by_kind(placements, "header"):
            _create_template_from_placement(
                p, client, args, data, kind="header", is_pro=is_pro,
                menu_info=primary_menu_info, project_state=project_state,
            )
        for p in by_kind(placements, "footer"):
            _create_template_from_placement(
                p, client, args, data, kind="footer", is_pro=is_pro,
                menu_info=footer_menu_info, menu_layout="horizontal",
                project_state=project_state,
            )
        for kind in ("popup", "archive", "single", "search", "404"):
            for p in by_kind(placements, kind):
                _create_template_from_placement(
                    p, client, args, data, kind=kind, is_pro=is_pro,
                    project_state=project_state,
                )

    # After section_finder.detach() removed each header/footer/popup/etc.
    # subtree from its parent's elements list, `content` itself IS the
    # page-bound tree. No separate "page_content" filter needed — what
    # remains in `content` is precisely what should land on the page.
    page_only = content

    # --- Template reuse — fingerprint-based deduplication ----------------
    if args.skip_template_reuse:
        pass
    elif args.dry_run:
        from template_reuse import detect_reuse_groups
        for g in detect_reuse_groups(page_only, enrichment):
            print(f"  [dry] would hoist {len(g.sites)} duplicate(s) of \"{g.title}\" into a section template")
    else:
        from template_reuse import detect_reuse_groups, replace_duplicates_with_shortcodes
        reuse_groups = detect_reuse_groups(page_only, enrichment)
        for g in reuse_groups:
            canonical_node = g.canonical.node
            site_slug = (data.get("title") or "site").lower().replace(" ", "-")
            site_slug = "".join(ch for ch in site_slug if ch.isalnum() or ch == "-").strip("-") or "site"
            template_slug = f"{site_slug}--reuse--{g.template_slug}"
            # Reuse an existing template if we already created one with this slug.
            cached = project_state.template_ids_by_slug.get(template_slug)
            if cached:
                g.template_id = cached["id"]
                print(f"✓ Reuse: {len(g.sites)} instance(s) of \"{g.title}\" → existing template id={g.template_id}")
            else:
                r = client.create_template(
                    template_type="section",
                    title=f"{data.get('title','Site')} — {g.title}",
                    elementor_data=[canonical_node],
                    slug=template_slug,
                )
                g.template_id = r.get("id")
                project_state.record_template(template_slug, "section", r["id"], g.title)
                print(f"✓ Reuse: {len(g.sites)} instance(s) of \"{g.title}\" → template id={g.template_id}")
        replaced = replace_duplicates_with_shortcodes(page_only, reuse_groups)
        if replaced:
            print(f"  swapped {replaced} instance(s) for shortcode references")

    # --- Page -------------------------------------------------------------
    if not args.skip_page:
        slug = cfg.get("page_slug") or "home"
        title = data.get("title", slug)
        # Pick the right page template:
        #   • elementor_canvas — only when WE created Theme Builder header/footer
        #     templates (otherwise the page renders with no chrome at all)
        #   • elementor_header_footer — Full Width, theme's header/footer wrap
        #     the Elementor content. Right default when no Figma header/footer
        #     was detected.
        #   • cfg.page_template — explicit override for advanced users
        created_chrome = bool(by_kind(placements, "header") or by_kind(placements, "footer"))
        page_template = (
            cfg.get("page_template")
            or ("elementor_canvas" if created_chrome else "elementor_header_footer")
        )
        # Page-level settings the user always wants:
        #   • hide_title — Elementor's "Hide Title" toggle (page settings)
        #   • container_width — pull from the Figma frame width when known,
        #     so a 1920-design lands at boxed_width=1920 not the kit default
        page_settings: dict = {"hide_title": "yes"}
        # Try to extract a sensible container width from the data.json root
        # frame: if it has boxed_width set we mirror it, capped at 1920.
        try:
            top_settings = (data["content"][0].get("settings") if data.get("content") else {}) or {}
            bw = top_settings.get("boxed_width") or {}
            size = bw.get("size") if isinstance(bw, dict) else None
            if size and size > 0:
                page_settings["container_width"] = {
                    "unit": "px",
                    "size": min(int(size), 1920),
                    "sizes": [],
                }
        except (KeyError, IndexError, TypeError):
            pass

        print(f"→ Create page \"{slug}\" (template={page_template}, hide_title=yes)")
        if args.dry_run:
            print(f"  [dry] would create page with {len(page_only)} top-level container(s)")
        else:
            r = client.create_or_update_page(
                slug=slug,
                title=title,
                elementor_data=page_only,
                template=page_template,
                page_settings=page_settings,
            )
            verb = "Updated" if r.get("updated") else "Created"
            print(f"✓ {verb} page id={r['id']} → {r['permalink']}")
            print(f"  edit: {r['edit_url']}")
            project_state.record_page(slug, r["id"], r["permalink"])

            # Warm Elementor's CSS cache: visit the page so Elementor compiles
            # per-element CSS files. Without this, the first Playwright capture
            # often shows an unstyled page (huge false drift).
            try:
                import time
                print("→ Warm Elementor CSS cache (GET page twice)…", end=" ", flush=True)
                client.session.get(r["permalink"], timeout=30)
                time.sleep(2)
                client.session.get(r["permalink"], timeout=30)
                print("done")
            except Exception as exc:  # network hiccup is non-fatal
                print(f"skipped ({exc})")

    # --- Confidence report -----------------------------------------------
    report = compute_report(enrichment, page_only)
    print(
        f"\n✓ Confidence: {report.confidence:.2f}  "
        f"(sections={report.summary['sections_total']}, "
        f"low={report.summary['sections_low_confidence']}, "
        f"warn={report.summary['warnings_warn']}, "
        f"err={report.summary['warnings_error']})"
    )
    for risk in report.risk_areas[:5]:
        print(f"  ⚠ [{risk.severity}] {risk.kind}: {risk.detail}")
    if len(report.risk_areas) > 5:
        print(f"  … + {len(report.risk_areas) - 5} more (see build/import-report.json)")

    # --- Persist build state ----------------------------------------------
    state = {
        "export_dir": str(export_dir),
        "asset_map": asset_map,
        "screenshot_map": screenshot_map,
        "page_slug": cfg.get("page_slug", "home"),
        "optimize_stats": optimize_stats,
        "fallback_indices": fallback_indices,
        "dynamic_count": len(dyn_candidates),
        "form_results": form_results,
        "placements_summary": arch_summary(placements) if placements else {},
    }
    (build_dir / "state.json").write_text(json.dumps(state, indent=2, default=str))
    (build_dir / "import-report.json").write_text(json.dumps(report.to_dict(), indent=2))

    # Persist cross-run project state (kit + templates + forms + assets +
    # imported pages) so the next page-by-page run can auto-skip work.
    project_state.record_assets(asset_map)
    if not args.dry_run:
        project_state.save()

    print(f"\n✓ Import complete. State → {build_dir / 'state.json'}, report → {build_dir / 'import-report.json'}")
    return 0


_TOKEN_BLOCK_START = "/* figma-elementor-agent: design tokens (do not edit by hand) */"


def _strip_token_block(css: str) -> str:
    """Remove the agent's prior design-token block so re-runs stay idempotent.

    The block starts with the marker comment and runs through the matching
    closing brace plus any utility class lines we appended below it.
    Anything outside the marker is preserved verbatim.
    """
    if _TOKEN_BLOCK_START not in css:
        return css
    head, _sep, rest = css.partition(_TOKEN_BLOCK_START)
    # The block ends at a blank line that precedes a non-token rule, OR at
    # end of string. We approximate: take everything up to the next double
    # newline followed by something that isn't `.dt-` or `:root`.
    lines = rest.splitlines()
    end = len(lines)
    blank_streak = 0
    for i, line in enumerate(lines):
        if not line.strip():
            blank_streak += 1
            continue
        if blank_streak >= 1 and not (line.startswith(".dt-") or line.startswith(":root") or line.startswith("}")):
            end = i
            break
        blank_streak = 0
    tail = "\n".join(lines[end:]).lstrip()
    return (head.rstrip() + ("\n\n" + tail if tail else "")).strip()


def _create_template_from_placement(
    placement,
    client,
    args,
    data,
    kind: str,
    is_pro: bool,
    menu_info=None,
    menu_layout: str = "vertical",
    project_state=None,
):
    """Shared helper for emitting an Elementor library template from one
    architecture placement (header / footer / popup / archive / single).

    Uses slug-based upsert so re-runs update the existing template instead
    of piling up duplicates. Records the resulting id on project_state so
    the next page-by-page import can auto-skip this kind.
    """
    node = placement.elementor_node
    ai = placement.ai_section or {}
    fname = (node.get("settings") or {}).get("_figma_name") or ai.get("name") or kind
    print(f"→ Create {kind} template (figma layer: {fname}, reason: {placement.reason})")

    if menu_info and menu_info.get("slug") and kind in ("header", "footer"):
        widget = (
            make_nav_menu_widget(menu_info["slug"], layout=menu_layout)
            if is_pro else make_wp_menu_widget(menu_info["slug"])
        )
        if inject_nav_menu_into_template(node, widget):
            print(f"  + injected nav-menu widget (menu='{menu_info['slug']}')")

    # Slug shape: site--kind  (e.g. "acme-site--header"). Stable across
    # runs; the bridge upserts on (slug, template_type).
    site_slug = (data.get("title") or "site").lower().replace(" ", "-")
    site_slug = "".join(ch for ch in site_slug if ch.isalnum() or ch == "-").strip("-") or "site"
    template_slug = f"{site_slug}--{kind}"
    title = f"{data.get('title', 'Site')} — {kind.title()}"

    if args.dry_run:
        print(f"  [dry] would upsert {kind} template (slug={template_slug})")
        return

    conditions = None
    popup_settings = None
    if kind in ("header", "footer"):
        conditions = ["include/general"]
    elif kind == "single":
        conditions = ["include/in_singular/post"]
    elif kind == "archive":
        conditions = ["include/post_archive"]
    elif kind == "popup":
        conditions = []  # popup has its own trigger config; leave empty
        from architecture import popup_settings_for_node, popup_hint_for
        popup_settings = popup_settings_for_node(node, ai)
        print(f"  + {popup_hint_for(node, ai, popup_settings)}")
    elif kind in ("search", "404"):
        # Free Elementor has no condition slots for these — Pro's "Other"
        # template type owns assignment. Surface explicit guidance.
        conditions = []
        print(f"  ! {kind} templates require Elementor Pro 'Other' "
              f"template assignment — see wp-admin → Templates → Theme Builder")

    r = client.create_template(
        template_type=kind,
        title=title,
        elementor_data=[node],
        conditions=conditions,
        slug=template_slug,
        popup_settings=popup_settings,
    )
    if project_state is not None:
        project_state.record_template(template_slug, kind, r["id"], title)
    flag = "Theme Builder (auto-applies)" if r.get("pro_active") else "library (Pro not active — assign manually)"
    verb = "Updated" if r.get("updated") else "Created"
    print(f"✓ {verb} {kind.title()} template id={r['id']} ({flag})")


if __name__ == "__main__":
    sys.exit(main())
