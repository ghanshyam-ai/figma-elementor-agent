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


# Module-level list of asset upload failures captured during this run.
# Each entry: {"path": str, "name": str, "reason": str}. Surfaced into
# build/import-report.json so the quality gate can react instead of the
# only signal being a console line a developer might miss.
ASSET_UPLOAD_FAILURES: list[dict] = []


def _record_upload_failure(path: Path, exc: Exception) -> None:
    ASSET_UPLOAD_FAILURES.append({
        "path": str(path),
        "name": path.name,
        "reason": str(exc),
    })


def _file_sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 16), b""):
            h.update(chunk)
    return h.hexdigest()


def _upload_subset(
    client,
    files: list[Path],
    dry_run: bool = False,
    hash_map: dict[str, dict] | None = None,
) -> dict[str, dict]:
    """Upload a specific list of files. Variant of upload_assets that takes
    files as input rather than scanning a directory — used by the project-
    state-aware path that only uploads files not already on the site.

    `hash_map` is the persisted {sha256: {id,url}} from project_state.
    A file whose hash is already known short-circuits the upload — the
    Figma plugin frequently regenerates hashed filenames per export, so
    matching by content hash prevents re-uploading identical bytes
    under a new name (which would bloat the media library)."""
    asset_map: dict[str, dict] = {}
    hash_map = hash_map or {}
    if dry_run:
        for img in files:
            print(f"  [dry] would upload {img.name}")
            asset_map[img.name] = {"url": f"STUB://media/{img.name}", "id": 0}
        return asset_map
    _, WPError, _ = _wp_client_module()
    for img in files:
        try:
            sha = _file_sha256(img)
        except OSError:
            sha = None
        try:
            size_bytes = img.stat().st_size
        except OSError:
            size_bytes = None
        if sha and sha in hash_map:
            cached = hash_map[sha]
            asset_map[img.name] = {
                "url": cached["url"], "id": cached["id"], "sha256": sha,
                "size_bytes": size_bytes,
            }
            print(f"  ◦ {img.name} → reused by hash (id={cached['id']})")
            continue
        try:
            result = client.upload_media(img)
        except WPError as exc:
            print(f"  ✗ upload {img.name}: {exc}", file=sys.stderr)
            _record_upload_failure(img, exc)
            continue
        asset_map[img.name] = {
            "url": result["source_url"], "id": result["id"], "size_bytes": size_bytes,
        }
        if sha:
            asset_map[img.name]["sha256"] = sha
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
            _record_upload_failure(img, exc)
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


# ---------- Brand-color heuristics ----------
#
# The plugin sorts colors by raw usage count, which means a grey wall
# fill (used as background on every section) wins the "primary" slot.
# That cascades into widgets binding `title_color → grey`. To fix this
# we score each color on:
#   • saturation        (greys score 0; neon brand colors score high)
#   • not-near-grey     (white/black/grey can't be a brand color)
#   • usage context     (buttonBg + textHeading hits beat raw count)
#   • role hint         (plugin's own roleHint when meaningful)

def _hex_to_rgb(hex_v: str) -> tuple[int, int, int] | None:
    s = hex_v.strip().lstrip("#")
    if len(s) not in (3, 6, 8):
        return None
    if len(s) == 3:
        s = "".join(ch * 2 for ch in s)
    try:
        return int(s[0:2], 16), int(s[2:4], 16), int(s[4:6], 16)
    except ValueError:
        return None


def _saturation(rgb: tuple[int, int, int]) -> float:
    r, g, b = rgb
    mx, mn = max(r, g, b), min(r, g, b)
    if mx == 0:
        return 0.0
    return (mx - mn) / mx


def _luminance(rgb: tuple[int, int, int]) -> float:
    r, g, b = (v / 255.0 for v in rgb)
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def _is_near_grey(rgb: tuple[int, int, int]) -> bool:
    """Pure white, pure black, and any color with saturation < 0.10 → grey."""
    if _saturation(rgb) < 0.10:
        return True
    return False


def _is_near_white(rgb: tuple[int, int, int]) -> bool:
    return min(rgb) > 240


def _is_near_black(rgb: tuple[int, int, int]) -> bool:
    return max(rgb) < 30


def _brand_score(c: dict) -> float:
    """Score how likely this color is a brand-primary candidate.

    Higher = more likely a brand color. Greys/whites/blacks score near 0
    even if heavily used.
    """
    rgb = _hex_to_rgb(c.get("value", ""))
    if not rgb:
        return 0.0
    if _is_near_grey(rgb) or _is_near_white(rgb) or _is_near_black(rgb):
        return 0.0
    sat = _saturation(rgb)
    ctx = c.get("usageContext") or {}
    btn_hits = (ctx.get("buttonBg") or 0) + (ctx.get("buttonText") or 0)
    heading_hits = ctx.get("textHeading") or 0
    # Saturation dominates. Button + heading usage are tie-breakers.
    return sat * 100 + btn_hits * 2 + heading_hits * 1


def _surface_score(c: dict) -> float:
    """Score how likely this color is a section surface (background)."""
    rgb = _hex_to_rgb(c.get("value", ""))
    if not rgb:
        return 0.0
    ctx = c.get("usageContext") or {}
    return float(ctx.get("surface") or 0) + (50 if _is_near_white(rgb) else 0)


def _text_score(c: dict) -> float:
    """Score how likely this color is the default text color."""
    rgb = _hex_to_rgb(c.get("value", ""))
    if not rgb:
        return 0.0
    ctx = c.get("usageContext") or {}
    # Dark, low-saturation colors with heavy body-text usage win.
    return (
        (ctx.get("textBody") or 0) * 3
        + (ctx.get("textHeading") or 0) * 2
        + (50 if _is_near_black(rgb) else 0)
        + (1.0 - _luminance(rgb)) * 30
    )


def _pick_brand_colors(colors: list[dict]) -> dict[str, dict]:
    """Choose the four system_colors slots — primary, secondary, text, accent.

    Returns {slot: color_dict_with_index}. Slots are filled by score, not
    by plugin declaration order. Greys/whites can never claim brand slots
    but can still claim `text` (when they're the darkest entry).
    """
    if not colors:
        return {}

    # primary = highest brand score
    brand_ranked = sorted(
        ((c, _brand_score(c)) for c in colors), key=lambda x: -x[1]
    )
    brand_candidates = [c for c, score in brand_ranked if score > 0]

    # text = highest text score
    text_ranked = sorted(
        ((c, _text_score(c)) for c in colors), key=lambda x: -x[1]
    )
    text_candidates = [c for c, score in text_ranked if score > 0]

    slots: dict[str, dict] = {}
    used_ids: set[int] = set()

    def claim(slot: str, candidates: list[dict]) -> None:
        for c in candidates:
            if id(c) in used_ids:
                continue
            slots[slot] = c
            used_ids.add(id(c))
            return

    claim("primary", brand_candidates)
    claim("secondary", brand_candidates)  # second-best brand color
    claim("text", text_candidates)
    claim("accent", brand_candidates)     # third-best brand color
    # Fill remaining slots from anything left, in original order.
    for slot in SYSTEM_COLOR_SLUGS:
        if slot in slots:
            continue
        for c in colors:
            if id(c) in used_ids:
                continue
            slots[slot] = c
            used_ids.add(id(c))
            break
    return slots


def map_global_to_kit_settings(global_json: dict) -> dict:
    colors = list(global_json.get("colors", []))
    typography = list(global_json.get("typography", []))

    # Brand-aware slot assignment instead of declaration order.
    slot_map = _pick_brand_colors(colors)
    system_colors: list[dict] = []
    system_color_ids: set[int] = set()
    for slug in SYSTEM_COLOR_SLUGS:
        c = slot_map.get(slug)
        if not c:
            continue
        title = (c.get("name") or slug).title()
        system_colors.append({"_id": slug, "title": title, "color": c["value"]})
        system_color_ids.add(id(c))

    custom_colors: list[dict] = []
    for c in colors:
        if id(c) in system_color_ids:
            continue
        h = hashlib.md5(c["value"].encode()).hexdigest()[:7]
        custom_colors.append({"_id": h, "title": c.get("name") or h, "color": c["value"]})

    # System typography — pick the *largest* size per slot name so e.g.
    # the three "h2" entries (sizes 30.7, 25, 31) collapse to the largest.
    # Also: skip entries with no fontFamily so we don't ship null-family
    # presets that no widget will ever match.
    by_slot: dict[str, dict] = {}
    primary_font: str | None = None
    VALID_SLOTS = ("display", "h1", "h2", "h3", "h4", "body", "small", "caption", "caption-strong")
    for t in typography:
        name = t.get("name") or ""
        if name not in VALID_SLOTS:
            continue
        if not t.get("fontFamily"):
            continue
        size_v = t.get("fontSize") or 0
        existing = by_slot.get(name)
        if existing and (existing.get("fontSize") or 0) >= size_v:
            continue
        by_slot[name] = t

    system_typography: list[dict] = []
    for name, t in by_slot.items():
        ts: dict = {
            "_id": _slug(name),
            "title": name.title(),
            "typography_typography": "custom",
        }
        ff = t.get("fontFamily")
        ts["typography_font_family"] = ff
        primary_font = primary_font or ff
        if t.get("fontWeight"):
            ts["typography_font_weight"] = str(int(t["fontWeight"]))
        size_v = t.get("fontSize")
        if isinstance(size_v, (int, float)):
            ts["typography_font_size"] = {
                "unit": "px",
                "size": float(round(size_v)),  # round fractional Figma sizes
                "sizes": [],
            }
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


# ---------- Globalization verification ----------

def verify_globalization(content: list, kit_settings: dict) -> dict:
    """Compute coverage = (widget refs to globals) / (widget refs total).

    Returns {"colors": 0.0..1.0, "typography": 0.0..1.0, "details": {...}}.
    Used by the orchestrator's quality gate — when coverage is < 0.7 the
    build is flagged because most widgets still hold inline values.
    """
    from optimize import COLOR_KEYS_BY_WIDGET, CONTAINER_COLOR_KEYS, ADVANCED_COLOR_KEYS

    total_color_refs = 0
    resolved_color_refs = 0
    total_type_refs = 0
    resolved_type_refs = 0

    HEX_RE = re.compile(r"^#?[0-9a-fA-F]{3,8}$")

    def is_hex(v) -> bool:
        return isinstance(v, str) and bool(HEX_RE.match(v.strip()))

    def visit(n: dict) -> None:
        nonlocal total_color_refs, resolved_color_refs, total_type_refs, resolved_type_refs
        if not isinstance(n, dict):
            return
        s = n.get("settings") or {}
        globals_ref = s.get("__globals__") or {}

        if n.get("elType") == "container":
            keys = CONTAINER_COLOR_KEYS
        else:
            keys = COLOR_KEYS_BY_WIDGET.get(n.get("widgetType"), []) + ADVANCED_COLOR_KEYS

        for k in keys:
            v = s.get(k)
            if is_hex(v):
                total_color_refs += 1
            elif k in globals_ref:
                total_color_refs += 1
                resolved_color_refs += 1

        # Typography: any widget with typography_font_family set is a ref.
        if isinstance(s.get("typography_font_family"), str) and s["typography_font_family"]:
            total_type_refs += 1
        if s.get("typography_typography") == "globals" or "typography_typography" in globals_ref:
            total_type_refs += 1
            resolved_type_refs += 1

        for c in n.get("elements") or []:
            visit(c)

    for top in content:
        visit(top)

    return {
        "colors": (resolved_color_refs / total_color_refs) if total_color_refs else 1.0,
        "typography": (resolved_type_refs / total_type_refs) if total_type_refs else 1.0,
        "details": {
            "total_color_refs": total_color_refs,
            "resolved_color_refs": resolved_color_refs,
            "total_type_refs": total_type_refs,
            "resolved_type_refs": resolved_type_refs,
        },
    }


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
    """Generate placeholder menu items. Used ONLY as a last resort when the
    design contains zero parseable nav links (e.g. footer columns baked
    into a single image)."""
    items = []
    for label, slug in (("Home", "home"), ("About", "about"), ("Services", "services"),
                       ("Blog", "blog"), ("Contact", "contact")):
        url = slug_to_url.get(slug, "#")
        items.append({"title": label, "url": url})
    return items


def extract_nav_items_from_sections(sections: list, kind: str) -> list[dict]:
    """Walk all sections of `kind` (header / footer / footer-column) and
    produce a deduped list of `{title, url}` items from the contained
    text/button widgets.

    Returns an empty list when the design has nothing parseable — caller
    decides whether to fall back to placeholders or dispatch Claude to OCR
    the section image.
    """
    from section_finder import extract_nav_items as _extract
    seen: set[str] = set()
    items: list[dict] = []
    for s in sections:
        if s.kind != kind:
            continue
        for it in _extract(s):
            key = (it["title"].lower().strip(), it.get("url", "").lower().strip())
            stable = key[0] + "|" + key[1]
            if stable in seen:
                continue
            seen.add(stable)
            items.append(it)
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


def _check_wp_side_drift(client, page_id: int, slug: str, build_dir) -> dict | None:
    """Return a drift report if the live WP page diverges from the most
    recent archived run.

    Compares the live `_elementor_data` (post-regen ids, fetched via the
    bridge) against `pages/<slug>/<latest>/data.json` (the pre-regen
    tree the agent last wrote). We translate the live tree back to
    pre-regen space via `settings._figma_pre_id` markers so the diff is
    apples-to-apples — no false positives from id regeneration alone.

    Returns None when no archived run exists yet (nothing to diff
    against) or when WP-side drift detection is disabled.
    """
    from pathlib import Path as _Path
    archive_root = _Path(__file__).resolve().parent.parent / "pages" / slug
    if not archive_root.exists():
        return None
    runs = sorted([p for p in archive_root.iterdir() if p.is_dir()])
    if not runs:
        return None
    last_data = runs[-1] / "data.json"
    if not last_data.exists():
        return None
    try:
        archived = json.loads(last_data.read_text())
    except (json.JSONDecodeError, OSError):
        return None
    archived_tree = archived.get("content") if isinstance(archived, dict) else archived
    if not isinstance(archived_tree, list):
        return None

    try:
        live_tree = client.get_elementor_data(page_id)
    except Exception as exc:
        # Fetch failure isn't a drift; just bail and let the write
        # proceed (the user might have a fresh site with the page id
        # stale from project_state).
        return {"drifted": False, "fetch_error": str(exc)}

    # Build {pre_id → live_node_fingerprint} from the marker-stamped
    # live tree. Compare against the same fingerprint built from the
    # archived pre-regen tree.
    def fingerprint(node) -> dict:
        if not isinstance(node, dict):
            return {}
        s = node.get("settings") or {}
        # Drop bookkeeping fields that change on every read-back.
        ignore = {"_figma_pre_id", "_responsive_defaulted", "_vision_authored"}
        filtered = {k: v for k, v in s.items() if k not in ignore}
        return {
            "kind": node.get("widgetType") or node.get("elType"),
            "settings_hash": hashlib.sha1(
                json.dumps(filtered, sort_keys=True, default=str).encode("utf-8")
            ).hexdigest()[:12],
            "child_count": len(node.get("elements") or []),
        }

    def collect_by_pre_id(content, key_attr: str) -> dict[str, dict]:
        out: dict[str, dict] = {}
        def walk(n):
            if isinstance(n, dict):
                key = (n.get("settings") or {}).get(key_attr) if key_attr == "_figma_pre_id" else n.get("id")
                if key:
                    out[key] = fingerprint(n)
                for c in n.get("elements") or []:
                    walk(c)
            elif isinstance(n, list):
                for it in n:
                    walk(it)
        walk(content)
        return out

    archived_fp = collect_by_pre_id(archived_tree, "id")
    live_fp = collect_by_pre_id(live_tree, "_figma_pre_id")
    # Backwards compat: if a pre-fix run is the only archived baseline,
    # the live tree won't have any _figma_pre_id markers yet (this is
    # the first run with stamping enabled). In that case we have no
    # apples-to-apples way to detect drift — skip rather than false-
    # positive every node as "removed".
    if not live_fp:
        return None

    changed_ids = []
    for pre, fp_archived in archived_fp.items():
        fp_live = live_fp.get(pre)
        if fp_live is None:
            changed_ids.append({"id": pre, "change": "removed"})
            continue
        if fp_live != fp_archived:
            changed_ids.append({
                "id": pre,
                "change": "modified",
                "archived": fp_archived,
                "live": fp_live,
            })
    added = [k for k in live_fp.keys() if k not in archived_fp]
    for nid in added:
        changed_ids.append({"id": nid, "change": "added"})

    drifted = bool(changed_ids)
    report = {
        "drifted": drifted,
        "archived_run": str(runs[-1].name),
        "page_id": page_id,
        "changed_node_count": len(changed_ids),
        "changes": changed_ids[:50],  # cap the report size
        "summary": (
            f"{len(changed_ids)} node(s) differ between live page and last archived "
            f"run ({runs[-1].name})."
        ) if drifted else "Live page matches the last archived build.",
    }
    drift_path = build_dir / "wp_drift.json"
    drift_path.write_text(json.dumps(report, indent=2))
    return report


def _stamp_pre_regen_ids(content) -> int:
    """Stamp every node's pre-regen id into settings._figma_pre_id.

    The bridge's `figma_importer_iterate_data` regenerates every node id
    at write time, so a positional zip of pre→post ids breaks the moment
    iterate_data adds/removes/reorders a node (Pro widgets promote
    children, control on_import hooks can mutate the subtree). We instead
    stamp an in-tree marker that survives the round-trip — the bridge
    preserves any settings key beginning with `_figma_`, so the marker
    travels with its node regardless of how the tree is restructured.

    Returns the number of nodes stamped.
    """
    n = 0
    def walk(node):
        nonlocal n
        if isinstance(node, dict):
            nid = node.get("id")
            if nid:
                s = node.setdefault("settings", {})
                if isinstance(s, dict):
                    s["_figma_pre_id"] = nid
                    n += 1
            for c in node.get("elements") or []:
                walk(c)
        elif isinstance(node, list):
            for it in node:
                walk(it)
    walk(content)
    return n


def _build_id_map_from_tree(live_tree) -> dict[str, str]:
    """Walk the post-regen tree and return {pre_regen_id: post_regen_id}.

    Pairs come from the `_figma_pre_id` marker stamped before send. No
    order or count assumption — robust to iterate_data adding wrappers,
    removing empty containers, or reshuffling siblings.
    """
    out: dict[str, str] = {}
    def walk(node):
        if isinstance(node, dict):
            post = node.get("id")
            pre = (node.get("settings") or {}).get("_figma_pre_id")
            if pre and post:
                out[pre] = post
            for c in node.get("elements") or []:
                walk(c)
        elif isinstance(node, list):
            for it in node:
                walk(it)
    walk(live_tree)
    return out


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
    ap.add_argument("--plan-only", action="store_true",
                    help="Extract the ZIP, run section + widget inference read-only, "
                         "emit build/build-plan.json + build/widget-review-queue.json, "
                         "and exit 0 BEFORE touching WordPress. Lets the developer "
                         "review widget choices before any writes.")
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
    ap.add_argument("--low-confidence-threshold", type=float, default=0.3,
                    help="Sections below this confidence get screenshot fallback (default: 0.3). "
                         "Lowered from 0.5 in the previous revision — the agent now dispatches "
                         "Claude-as-Author for marginal sections instead of surrendering to a screenshot.")
    ap.add_argument("--page-only", action="store_true",
                    help="Page-by-page mode: skip globals + header/footer + design tokens. "
                         "Use this for the 2nd, 3rd, … page after the home page already created them.")
    ap.add_argument("--reset-media", action="store_true",
                    help="Before uploading, delete agent-uploaded attachments from prior runs "
                         "(matches by figma-export filename prefix). Destructive — requires --confirm-destructive.")
    ap.add_argument("--confirm-destructive", action="store_true",
                    help="Acknowledge that destructive operations (--reset-media, --reset-menus) "
                         "are intentional. Without this flag, destructive operations abort.")
    ap.add_argument("--require-theme-builder", action="store_true", default=True,
                    help="Fail if header AND footer aren't detected as Theme Builder templates. "
                         "Default ON — pass --no-require-theme-builder for partial builds.")
    ap.add_argument("--no-require-theme-builder", dest="require_theme_builder",
                    action="store_false")
    ap.add_argument("--skip-responsive-defaults", action="store_true",
                    help="Don't stamp _mobile / _tablet overrides on containers + headings.")
    ap.add_argument("--force", action="store_true",
                    help="Overwrite the live page even when WP-side drift is detected. "
                         "WP-side drift means the page on the live site differs from the "
                         "last archived build — typically because someone edited it in "
                         "WP admin. Without --force, the agent refuses to overwrite (exit 5).")
    ap.add_argument("--from-cache", action="store_true",
                    help="Resume from build/data.json: skip ZIP extraction, optimization, "
                         "and asset upload. Only re-applies the cached tree to the live page. "
                         "Use for fast iteration when only the patch loop needs to re-run.")
    args = ap.parse_args()

    # --- Safety gate for destructive flags --------------------------------
    if (args.reset_media or args.reset_menus) and not args.confirm_destructive:
        print("✗ --reset-media / --reset-menus are destructive. Re-run with --confirm-destructive "
              "if this is intentional.", file=sys.stderr)
        return 6

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

    if args.dry_run or args.plan_only:
        # Dry run / plan-only: read config without needing `requests`.
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
    if args.plan_only:
        print("• Plan-only mode — skipping auth, bridge, and all WordPress writes")
    elif not args.dry_run:
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

    # --from-cache short-circuit: skip ZIP extraction + optimization entirely.
    # Useful for fast iteration when the user has manually edited
    # build/data.json (or build_plan.json patched the tree) and wants to
    # just push it without re-running the full pipeline. Skips imply:
    # globals already applied, assets already uploaded, theme builder
    # templates already created — i.e. a build that has previously
    # completed at least once.
    if args.from_cache:
        existing_data = build_dir / "data.json"
        if not existing_data.exists():
            print(
                "✗ --from-cache requires a prior successful build "
                f"(build/data.json not found at {existing_data}).",
                file=sys.stderr,
            )
            return 8
        # Reuse the previous export dir + tree. Find it from the prior state.
        prior_state = json.loads((build_dir / "state.json").read_text()) if (build_dir / "state.json").exists() else {}
        export_dir = Path(prior_state.get("export_dir") or (build_dir / "export"))
        print(f"✓ --from-cache: reusing {existing_data.relative_to(ROOT)} (no extraction)")
        for f in ("skip_assets", "skip_globals", "skip_header_footer",
                  "skip_menus", "skip_forms", "skip_template_reuse",
                  "skip_fallbacks", "skip_optimize"):
            setattr(args, f, True)
        data = {"content": json.loads(existing_data.read_text())["content"], "title": prior_state.get("page_slug", "home")}
        global_json = json.loads((export_dir / "global.json").read_text()) if (export_dir / "global.json").exists() else {}
        metadata = {"counts": {"assets": 0, "screenshots": 0}}
    else:
        export_dir = extract_zip(cfg["zip_path"], build_dir)
        print(f"✓ Extracted to {export_dir.relative_to(ROOT)}")

        data = json.loads((export_dir / "data.json").read_text())
        global_json = json.loads((export_dir / "global.json").read_text())
        metadata = json.loads((export_dir / "metadata.json").read_text())

    s = widget_stats(data["content"])
    widget_summary = ", ".join(f"{k}={v}" for k, v in sorted(s["widgets"].items(), key=lambda x: -x[1]))
    print(f"  page: \"{data.get('title')}\"  containers={s['containers']}  widgets={s['widgets_total']}  ({widget_summary})")
    print(f"  assets={metadata['counts']['assets']}  screenshots={metadata['counts']['screenshots']}")

    # --- Plan-only early exit --------------------------------------------
    # Emits build/build-plan.json + build/widget-review-queue.json (and
    # runs the pre-flight design-system check) WITHOUT any WP writes.
    # The orchestrator wires this in BEFORE the Y/n confirmation so the
    # developer can review widget choices and fix sparse tokens early.
    if args.plan_only:
        from build_plan import build_plan as _build_plan_fn, write_plan as _write_plan
        plan = _build_plan_fn(export_dir)
        plan_path, queue_path = _write_plan(plan)
        print(f"\n✓ Plan written → {plan_path.relative_to(ROOT)}  "
              f"({plan['stats']['total_sections']} sections, "
              f"{plan['stats']['needs_review']} need widget review)")
        print(f"  widget-review queue → {queue_path.relative_to(ROOT)}")
        pf = plan.get("preflight") or {}
        if pf.get("issues"):
            print(f"\nPre-flight: {len(pf['issues'])} design-system issue(s)")
            for issue in pf["issues"]:
                marker = "✗" if issue["severity"] == "error" else "⚠"
                print(f"  {marker} {issue['kind']}: {issue['detail']}")
            if not pf.get("passed"):
                print("\nFix the error(s) above in Figma before running the import.")
                return 8
        return 0

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
                # Upload only the genuinely new files. Hash-keyed dedup
                # catches files the plugin renamed but didn't change.
                asset_map.update(_upload_subset(
                    client, new_files,
                    dry_run=args.dry_run,
                    hash_map=(project_state.asset_map_by_hash if project_state else {}),
                ))
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

    # --- Drop hidden / zero-opacity layers --------------------------------
    # Defensive filter: real-world Figma files leave hidden variant frames
    # and invisible decorations behind. The plugin sometimes leaks them
    # through; we never want them rendering on the live page.
    from section_finder import filter_hidden as _filter_hidden
    n_hidden = _filter_hidden(content)
    if n_hidden:
        print(f"  filtered {n_hidden} hidden / zero-opacity node(s) from page tree")

    # --- Recursive section finder (any depth) ----------------------------
    # Replaces the top-level-only architecture routing that broke on
    # designs wrapped in a single root frame.
    from section_finder import (
        find_real_sections, by_kind as sf_by_kind, summarize as sf_summary,
        detach as sf_detach, extract_footer_columns,
    )
    real_sections = find_real_sections(content, enrichment.section_by_index)

    # Augment with detected footer columns (each becomes its own nav menu).
    for footer_sec in [s for s in real_sections if s.kind == "footer"]:
        real_sections.extend(extract_footer_columns(footer_sec))

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

    # --- Confidence + screenshot fallbacks (BEFORE widget swaps) ---------
    # Runs first so widget-inference doesn't waste effort mutating sections
    # we're about to replace wholesale. Audit caught this ordering bug.
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

    # --- Token resolver + optimization passes ----------------------------
    optimize_stats = {"colors": 0, "typography": 0, "collapsed": 0, "hoisted": 0,
                      "widgets_swapped": 0, "html_replaced": 0,
                      "radius_tagged": 0, "gap_tagged": 0,
                      "auto_layout_inferred": 0, "widgets_inferred": 0,
                      "responsive_containers": 0, "responsive_headings": 0}
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
        # Responsive defaults — stamp `_mobile` / `_tablet` overrides so the
        # page actually stacks on small screens. Elementor reads these
        # natively; without them the page renders horizontal on mobile.
        if not args.skip_responsive_defaults:
            from optimize import apply_responsive_defaults
            rstats = apply_responsive_defaults(content)
            optimize_stats["responsive_containers"] = rstats["containers"]
            optimize_stats["responsive_rows"] = rstats.get("rows", 0)
            optimize_stats["responsive_headings"] = rstats["headings"]
            optimize_stats["responsive_images"] = rstats.get("images", 0)
            optimize_stats["responsive_buttons"] = rstats.get("buttons", 0)
        print(
            f"✓ Optimize: {optimize_stats['colors']} colors→globals, "
            f"{optimize_stats['typography']} typo→globals, "
            f"{optimize_stats['radius_tagged']}+{optimize_stats['gap_tagged']} token classes, "
            f"{optimize_stats['collapsed']} containers collapsed, "
            f"{optimize_stats['hoisted']} hoisted (depth≤{args.max_depth}), "
            f"{optimize_stats['widgets_swapped']} widgets swapped, "
            f"{optimize_stats['html_replaced']} html→text-editor"
        )
        if not args.skip_responsive_defaults:
            print(
                f"  responsive: {optimize_stats['responsive_rows']} rows stacked, "
                f"{optimize_stats['responsive_headings']} headings scaled, "
                f"{optimize_stats['responsive_images']} images→100% mobile, "
                f"{optimize_stats['responsive_buttons']} buttons→full-width mobile"
            )

    # --- Globalization coverage check (gate-friendly) ---------------------
    global_coverage: dict = {"colors": 1.0, "typography": 1.0, "details": {}}
    if page_settings:
        global_coverage = verify_globalization(content, page_settings)
        print(
            f"  global coverage: colors={global_coverage['colors']*100:.1f}%, "
            f"typography={global_coverage['typography']*100:.1f}% "
            f"(refs: colors {global_coverage['details']['resolved_color_refs']}/"
            f"{global_coverage['details']['total_color_refs']}, "
            f"typo {global_coverage['details']['resolved_type_refs']}/"
            f"{global_coverage['details']['total_type_refs']})"
        )

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
    # Real menu items from the Figma design first; placeholders only as a
    # last resort. Footer columns each get their OWN menu so we don't
    # collapse 4 columns of links into one Primary Menu.
    primary_menu_info = footer_menu_info = None
    footer_column_menus: list[dict] = []
    if not args.skip_menus:
        primary_name = cfg.get("primary_menu_name", "Primary Menu")
        primary_loc  = cfg.get("primary_menu_location", "menu-1")
        footer_name  = cfg.get("footer_menu_name", "Footer Menu")
        footer_loc   = cfg.get("footer_menu_location", "menu-2")

        # Validate location slugs against what the active theme actually
        # registers — without this the agent silently binds menus to slots
        # that don't exist on themes other than hello-elementor.
        if not args.dry_run:
            theme_locs = client.list_theme_nav_locations() or []
            registered_slugs = {l.get("slug") for l in theme_locs}
            if registered_slugs:
                def _resolve(preferred: str, *aliases: str) -> str:
                    for s in (preferred, *aliases):
                        if s in registered_slugs:
                            return s
                    # Pick the first registered slug as a sensible fallback.
                    return sorted(registered_slugs)[0]
                primary_loc = _resolve(primary_loc, "primary", "menu-1", "main-menu")
                footer_loc  = _resolve(footer_loc, "footer", "menu-2", "secondary")
                if primary_loc != cfg.get("primary_menu_location", "menu-1"):
                    print(f"  ↪ primary menu location remapped to '{primary_loc}' "
                          f"(theme registers: {sorted(registered_slugs)})")
        slug_to_url = {cfg.get("page_slug", "home"):
                       f"{cfg['wp_url'].rstrip('/')}/{cfg.get('page_slug', 'home')}/"}

        primary_items = extract_nav_items_from_sections(real_sections, "header")
        if not primary_items:
            primary_items = build_default_menu_items(slug_to_url)
            print("  (no parseable nav items in header — using placeholders)")
        else:
            print(f"  extracted {len(primary_items)} nav item(s) from header section(s)")

        footer_items = extract_nav_items_from_sections(real_sections, "footer-column")
        if not footer_items:
            # Fall back to anything in the footer at all (text/buttons).
            footer_items = extract_nav_items_from_sections(real_sections, "footer")
        if not footer_items:
            footer_items = build_default_menu_items(slug_to_url)
            print("  (no parseable nav items in footer — using placeholders)")

        # Build per-column menu specs.
        footer_cols = [s for s in real_sections if s.kind == "footer-column"]
        configured_cols = cfg.get("footer_menus") or []  # optional dev override
        column_specs: list[dict] = []
        for i, col in enumerate(footer_cols, start=1):
            from section_finder import extract_nav_items as _extract
            col_items = _extract(col)
            if not col_items:
                continue
            spec = {
                "name": (configured_cols[i - 1].get("name") if i - 1 < len(configured_cols) and isinstance(configured_cols[i - 1], dict) else None)
                        or f"Footer Column {i}",
                "location": (configured_cols[i - 1].get("location") if i - 1 < len(configured_cols) and isinstance(configured_cols[i - 1], dict) else None)
                            or f"footer-col-{i}",
                "items": col_items,
            }
            column_specs.append(spec)

        if args.dry_run:
            print(f"  [dry] would create/update menus: '{primary_name}' (location={primary_loc}) "
                  f"with {len(primary_items)} items, '{footer_name}' (location={footer_loc}) "
                  f"with {len(footer_items)} items, plus {len(column_specs)} footer-column menus")
            primary_menu_info = {"slug": "primary-menu", "id": 0, "item_count": len(primary_items), "created": True}
            footer_menu_info  = {"slug": "footer-menu", "id": 0, "item_count": len(footer_items), "created": True}
            footer_column_menus = [{"slug": s["location"], **s, "id": 0, "item_count": len(s["items"]), "created": True}
                                    for s in column_specs]
        else:
            print(f"→ Create/update WP menus")
            primary_menu_info = client.create_or_update_menu(
                name=primary_name, location=primary_loc, items=primary_items, reset=args.reset_menus,
            )
            footer_menu_info = client.create_or_update_menu(
                name=footer_name, location=footer_loc, items=footer_items, reset=args.reset_menus,
            )
            print(f"✓ {primary_name}  id={primary_menu_info['id']}  slug={primary_menu_info['slug']}  "
                  f"items={primary_menu_info['item_count']}  "
                  f"({'created' if primary_menu_info['created'] else 'existing'})")
            print(f"✓ {footer_name}   id={footer_menu_info['id']}  slug={footer_menu_info['slug']}  "
                  f"items={footer_menu_info['item_count']}  "
                  f"({'created' if footer_menu_info['created'] else 'existing'})")
            for spec in column_specs:
                info = client.create_or_update_menu(
                    name=spec["name"], location=spec["location"], items=spec["items"],
                    reset=args.reset_menus,
                )
                footer_column_menus.append({**info, "name": spec["name"], "location": spec["location"]})
                print(f"✓ {spec['name']}  id={info['id']}  slug={info['slug']}  "
                      f"items={info['item_count']}  "
                      f"({'created' if info['created'] else 'existing'})")

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

    # --- Theme Builder assertion gate ------------------------------------
    # If --require-theme-builder is on (default), the build must produce a
    # header AND footer template — otherwise they'll render inline on the
    # page, which is exactly what the user explicitly wants to prevent.
    # Print the failing reason and instruct the developer how to fix it
    # in project-config.json before re-running. Honour --skip-header-footer
    # (which says "I know what I'm doing, don't enforce this gate").
    if args.require_theme_builder and not args.skip_header_footer and not args.page_only:
        has_header = bool(by_kind(placements, "header"))
        has_footer = bool(by_kind(placements, "footer"))
        # Partial Theme Builder: if at least one of header/footer is
        # detected, proceed with that template and let the theme's
        # default render the other. The hard failure (exit 7) is now
        # reserved for the case where NEITHER is detected — that's the
        # signal that section detection genuinely failed and the user
        # needs to fix the Figma layer naming.
        if not (has_header or has_footer):
            print("\n✗ Theme Builder gate FAILED — neither header nor footer detected.")
            print("  The agent cannot create Theme Builder templates without at least one")
            print("  chrome section. Fix options:")
            print("    1. Improve the Figma layer name to match the patterns in")
            print("       section_finder.NAME_RX (e.g. \"Header\", \"Navbar\", \"Footer\").")
            print("    2. Set `header_pattern` / `footer_pattern` regex overrides in project-config.json.")
            print("    3. Re-run with --no-require-theme-builder for a fully inline build.")
            return 7
        if not (has_header and has_footer):
            missing = []
            if not has_header: missing.append("header")
            if not has_footer: missing.append("footer")
            print(f"\n⚠ Partial Theme Builder mode — proceeding without {' and '.join(missing)}.")
            print("  The detected chrome will become Theme Builder templates; the missing")
            print("  side will fall through to the theme's default. This is fine for")
            print("  designs that intentionally use only a custom header OR custom footer.")
            print("  To enforce both, fix the Figma layer naming and re-run.")

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
                footer_column_menus=footer_column_menus,
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
        # Page template:
        #   • elementor_header_footer (Full Width) — keeps wp_head/wp_footer
        #     hooks alive so Theme Builder header/footer templates can inject.
        #     Right default when chrome lives in Theme Builder OR theme.
        #   • elementor_canvas — strips wp_head/wp_footer; use ONLY when chrome
        #     is built inline in the page body (start inline-only).
        #   • cfg.page_template — explicit override for advanced users.
        inline_mode = bool(cfg.get("inline_only"))
        page_template = (
            cfg.get("page_template")
            or ("elementor_canvas" if inline_mode else "elementor_header_footer")
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
            # WP-side drift detection: if a prior run imported this slug,
            # the page already exists on the live site. Fetch the live
            # _elementor_data and compare against pages/<slug>/<latest>/
            # data.json. A divergence means someone hand-edited the page
            # in WP admin between runs — silently overwriting would lose
            # their work. The user must opt in via --force to overwrite.
            # project_state.pages_imported is a list[{slug, page_id, ...}],
            # not a dict — look up by iterating.
            existing_page_id = None
            if project_state:
                for entry in (project_state.pages_imported or []):
                    if isinstance(entry, dict) and entry.get("slug") == slug:
                        existing_page_id = entry.get("page_id")
                        break
            drift_check = _check_wp_side_drift(
                client, existing_page_id, slug, build_dir,
            ) if existing_page_id else None
            if drift_check and drift_check.get("drifted") and not args.force:
                print(
                    f"\n✗ WP-side drift detected on page id={existing_page_id} "
                    f"(slug={slug})."
                )
                print(
                    f"  {drift_check.get('summary')}"
                )
                print(
                    f"  Detail: build/wp_drift.json"
                )
                print(
                    "  The live page differs from the last successful build's "
                    "data.json — someone has likely edited it in WP admin since "
                    "the last `start`. Overwriting would lose those changes."
                )
                print(
                    "  → To overwrite anyway, re-run with `start --force` (or "
                    "pull the live changes back into Figma + re-export the ZIP)."
                )
                return 5

            # Stamp every node with its pre-regen id so we can recover the
            # mapping after the bridge's iterate_data regenerates ids. The
            # marker survives the round-trip (bridge preserves `_figma_*`
            # settings keys), so positional drift between pre/post no
            # longer corrupts the map.
            stamped = _stamp_pre_regen_ids(page_only)

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

            # Read back the post-regen tree and rebuild {pre_id → post_id}
            # by walking the markers — no order assumption. Auto-fixer /
            # claude-review / per-section diff all rely on this map being
            # correct; a wrong map silently breaks every downstream lookup.
            try:
                live_tree = client.get_elementor_data(r["id"])
                id_map = _build_id_map_from_tree(live_tree)
                (build_dir / "id_map.json").write_text(json.dumps(id_map, indent=2))
                if stamped and len(id_map) < stamped:
                    print(
                        f"  ! id_map: {len(id_map)}/{stamped} nodes recovered "
                        "(some markers lost in iterate_data — check bridge logs)"
                    )
                print(f"  + persisted id_map for {len(id_map)} node(s) → build/id_map.json")
            except Exception as exc:
                print(f"  (could not build id_map: {exc})")

            # Warm Elementor's CSS cache: visit the page so Elementor compiles
            # per-element CSS files. Without this, the first Playwright capture
            # often shows an unstyled page (huge false drift).
            try:
                import time
                print("→ Warm Elementor CSS cache (GET page twice)…", end=" ", flush=True)
                resp1 = client.session.get(r["permalink"], timeout=30)
                time.sleep(2)
                resp2 = client.session.get(r["permalink"], timeout=30)
                if resp1.status_code >= 400 or resp2.status_code >= 400:
                    # Don't silently swallow — a 404 here means the page is
                    # private/draft or the permalink is wrong; the visual
                    # review will then fail in confusing ways.
                    print(f"warning: status={resp1.status_code}/{resp2.status_code} — "
                          "page may be unpublished or behind auth")
                else:
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
        "kit_globals": {
            "system_colors": (page_settings or {}).get("system_colors") or [],
            "system_typography": (page_settings or {}).get("system_typography") or [],
        },
        "footer_column_menus": footer_column_menus,
        "primary_menu": primary_menu_info or {},
        "footer_menu": footer_menu_info or {},
    }
    (build_dir / "state.json").write_text(json.dumps(state, indent=2, default=str))

    # Attach global coverage to import-report so the quality gate can read it.
    report_dict = report.to_dict()
    report_dict["global_coverage"] = {
        "colors": round(global_coverage["colors"], 3),
        "typography": round(global_coverage["typography"], 3),
        "details": global_coverage.get("details", {}),
    }
    # Surface asset upload failures so the quality gate can react to them
    # instead of them being a console-only signal that developers miss.
    if ASSET_UPLOAD_FAILURES:
        report_dict["asset_failures"] = list(ASSET_UPLOAD_FAILURES)
        risk_areas = report_dict.setdefault("riskAreas", [])
        risk_areas.append({
            "kind": "asset_upload",
            "nodeId": None,
            "nodeName": None,
            "severity": "error",
            "detail": (
                f"{len(ASSET_UPLOAD_FAILURES)} asset(s) failed to upload. "
                "Their URLs in _elementor_data still point at local paths; "
                "the live page will show broken images. See "
                "import-report.json::asset_failures for the full list."
            ),
        })
        print(
            f"  ⚠ {len(ASSET_UPLOAD_FAILURES)} asset upload failure(s) recorded "
            "in import-report.json::asset_failures"
        )
    (build_dir / "import-report.json").write_text(json.dumps(report_dict, indent=2))

    # Save the rewritten `content` so claude_review.py can read the
    # current Elementor tree without going back to the live WP site.
    (build_dir / "data.json").write_text(json.dumps({"content": content}, indent=2, default=str))

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
    footer_column_menus: list | None = None,
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

    # Footer-column menus — when the agent extracted multiple link columns
    # from the footer (e.g. Company / Resources / Legal), append a nav-menu
    # widget for each. They land inside the deepest container of the footer
    # template; the developer can rearrange them in the editor afterwards.
    if kind == "footer" and footer_column_menus:
        for col_menu in footer_column_menus:
            slug = col_menu.get("slug")
            if not slug:
                continue
            w = (
                make_nav_menu_widget(slug, layout="vertical")
                if is_pro else make_wp_menu_widget(slug)
            )
            if inject_nav_menu_into_template(node, w):
                print(f"  + injected footer-column nav-menu (menu='{slug}')")

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
