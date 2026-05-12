"""
Thin REST wrapper for WordPress + the figma-importer-bridge mu-plugin.

Auth: regular admin username + password (NOT Application Password).
The bridge's /login endpoint validates via wp_signon(), sets the WP auth
cookie on the response, and returns a `wp_rest` nonce. Both are kept on
the requests.Session — every subsequent call carries the cookie + nonce
header automatically.
"""
from __future__ import annotations

import json
import mimetypes
from pathlib import Path
from typing import Any

import requests


class WPError(Exception):
    """Raised when a WP REST call returns >= 400."""


class WPClient:
    def __init__(self, base_url: str, user: str, password: str, timeout: int = 60):
        self.base_url = base_url.rstrip("/")
        self.user = user
        self.password = password
        self.session = requests.Session()
        self.session.headers.update({"Accept": "application/json"})
        self.timeout = timeout
        self._authenticated = False

    # --- low level ----------------------------------------------------------

    def _url(self, path: str) -> str:
        if path.startswith(("http://", "https://")):
            return path
        return f"{self.base_url}/wp-json/{path.lstrip('/')}"

    def request(self, method: str, path: str, **kwargs) -> Any:
        url = self._url(path)
        kwargs.setdefault("timeout", self.timeout)
        try:
            resp = self.session.request(method, url, **kwargs)
        except requests.RequestException as exc:
            raise WPError(f"{method} {url} → network error: {exc}") from exc

        if resp.status_code >= 400:
            snippet = resp.text[:600] if resp.text else ""
            raise WPError(f"{method} {url} → {resp.status_code}: {snippet}")
        if not resp.content:
            return None
        ctype = resp.headers.get("content-type", "")
        if "json" in ctype:
            return resp.json()
        return resp.content

    def get(self, path: str, params: dict | None = None) -> Any:
        return self.request("GET", path, params=params)

    def post(self, path: str, json_body: Any = None, **kwargs) -> Any:
        if json_body is not None:
            kwargs["json"] = json_body
        return self.request("POST", path, **kwargs)

    def patch(self, path: str, json_body: Any = None, **kwargs) -> Any:
        if json_body is not None:
            kwargs["json"] = json_body
        return self.request("PATCH", path, **kwargs)

    # --- auth ---------------------------------------------------------------

    def login(self) -> dict:
        """POST /figma-importer/v1/login with username + password.

        Stores the WP auth cookie (set by wp_signon) on the session and adds
        the X-WP-Nonce header so subsequent REST writes are accepted.
        """
        result = self.post(
            "figma-importer/v1/login",
            json_body={"username": self.user, "password": self.password},
        )
        if not result or "nonce" not in result:
            raise WPError("Login response did not include a nonce")
        self.session.headers["X-WP-Nonce"] = result["nonce"]
        self._authenticated = True
        return result

    def ensure_authenticated(self) -> None:
        if not self._authenticated:
            self.login()

    # --- WP core ------------------------------------------------------------

    def me(self) -> dict:
        self.ensure_authenticated()
        return self.get("wp/v2/users/me")

    def list_plugins(self) -> list[dict]:
        self.ensure_authenticated()
        return self.get("wp/v2/plugins") or []

    def find_plugin(self, slug_fragment: str) -> dict | None:
        for p in self.list_plugins():
            slug = p.get("plugin", "")
            name = p.get("name", "")
            if slug_fragment.lower() in slug.lower() or slug_fragment.lower() in name.lower():
                return p
        return None

    def upload_media(self, file_path: str | Path, alt_text: str | None = None) -> dict:
        self.ensure_authenticated()
        path = Path(file_path)
        if not path.exists():
            raise WPError(f"upload_media: file not found {path}")
        mime, _ = mimetypes.guess_type(str(path))
        mime = mime or "application/octet-stream"
        with path.open("rb") as f:
            data = f.read()
        headers = {
            "Content-Disposition": f'attachment; filename="{path.name}"',
            "Content-Type": mime,
        }
        result = self.request("POST", "wp/v2/media", data=data, headers=headers)
        if alt_text and result and "id" in result:
            self.post(f"wp/v2/media/{result['id']}", json_body={"alt_text": alt_text})
        return result

    # --- Bridge plugin ------------------------------------------------------

    def bridge_health(self) -> dict | None:
        # Public endpoint — no auth required.
        try:
            return self.get("figma-importer/v1/health")
        except WPError:
            return None

    def create_or_update_page(
        self,
        slug: str,
        title: str,
        elementor_data: list,
        template: str = "elementor_canvas",
        page_settings: dict | None = None,
    ) -> dict:
        """Create or update a page.

        page_settings is forwarded as Elementor's `_elementor_page_settings`
        and merged with the existing meta — typical fields:
          • hide_title: 'yes' / ''
          • custom_css: '...'
          • container_width: {unit:'px', size:1440, sizes:[]}
        """
        self.ensure_authenticated()
        body = {
            "slug": slug,
            "title": title,
            "elementor_data": elementor_data,
            "template": template,
        }
        if page_settings:
            body["page_settings"] = page_settings
        return self.post("figma-importer/v1/page", json_body=body)

    def create_template(
        self,
        template_type: str,
        title: str,
        elementor_data: list,
        conditions: list | None = None,
        slug: str | None = None,
        popup_settings: dict | None = None,
    ) -> dict:
        """Create or upsert an elementor_library template.

        When `slug` is provided, the bridge upserts: an existing template
        with the same slug + same template type is updated in place
        instead of duplicated. Returns `updated=True` in that case.

        `popup_settings` is forwarded only when template_type == 'popup'.
        See architecture.popup_settings_for_node().
        """
        self.ensure_authenticated()
        body = {
            "template_type": template_type,
            "title": title,
            "elementor_data": elementor_data,
        }
        if conditions is not None:
            body["conditions"] = conditions
        if slug:
            body["slug"] = slug
        if popup_settings is not None:
            body["popup_settings"] = popup_settings
        return self.post("figma-importer/v1/template", json_body=body)

    def reset_media(self, prefixes: list[str] | None = None, dry_run: bool = False) -> dict:
        """Delete media library attachments uploaded by prior agent runs.

        Matches by filename prefix; defaults to the prefixes the Figma
        plugin produces (`img_`, `node_`, `frame_`, `screenshot_`). Pass
        a stricter list to scope the cleanup. Set `dry_run=True` to see
        what would be deleted without touching anything.
        """
        self.ensure_authenticated()
        body: dict = {"dry_run": dry_run}
        if prefixes:
            body["prefixes"] = prefixes
        return self.post("figma-importer/v1/media/reset", json_body=body)

    def update_kit_settings(self, page_settings: dict) -> dict:
        self.ensure_authenticated()
        return self.post(
            "figma-importer/v1/kit",
            json_body={"page_settings": page_settings},
        )

    def get_elementor_data(self, post_id: int) -> list:
        self.ensure_authenticated()
        result = self.get(f"figma-importer/v1/elementor-data/{post_id}")
        return result.get("elementor_data", [])

    def patch_elementor_data(self, post_id: int, elementor_data: list) -> dict:
        self.ensure_authenticated()
        return self.patch(
            f"figma-importer/v1/elementor-data/{post_id}",
            json_body={"elementor_data": elementor_data},
        )

    def list_gravity_forms(self) -> list[dict]:
        """[{id, title, is_active, fields}, ...] — empty list when GF is missing."""
        self.ensure_authenticated()
        try:
            result = self.get("figma-importer/v1/forms/gravity")
        except WPError:
            return []
        return result.get("forms", []) if isinstance(result, dict) else []

    def create_gravity_form(self, spec: dict) -> dict:
        """Create a Gravity Form from the agent's compact spec.

        Returns {id, title, shortcode, edit_url}. Raises WPError if Gravity
        Forms is not active on the target site.
        """
        self.ensure_authenticated()
        return self.post("figma-importer/v1/forms/gravity", json_body=spec)

    def list_theme_nav_locations(self) -> list[dict]:
        """Return the active theme's registered nav-menu locations.

        Each entry: `{slug, label, assigned_menu_id}`. Used by the agent
        so it can pick a real location slug instead of guessing
        `menu-1` / `menu-2` (which only happen to exist on hello-elementor).
        Returns an empty list when the bridge or theme doesn't support it.
        """
        self.ensure_authenticated()
        try:
            r = self.get("figma-importer/v1/theme/locations")
        except WPError:
            return []
        return (r or {}).get("locations", [])

    def create_or_update_menu(
        self,
        name: str,
        items: list[dict],
        location: str | None = None,
        reset: bool = False,
    ) -> dict:
        """Create or update a WP nav menu with `items` and optionally bind to a theme location.

        `items` is a list of `{title, url, object_id?}` dicts.
        `reset=True` wipes existing items before re-adding (use with care).
        """
        self.ensure_authenticated()
        return self.post(
            "figma-importer/v1/menu",
            json_body={
                "name": name,
                "location": location or "",
                "items": items,
                "reset": reset,
            },
        )


def load_config(path: str = "project-config.json") -> dict:
    p = Path(path)
    if not p.is_absolute():
        p = Path.cwd() / p
    with p.open() as f:
        cfg = json.load(f)
    # Backward-compat: support old `wp_app_password` field as the password.
    if "wp_password" not in cfg and "wp_app_password" in cfg:
        cfg["wp_password"] = cfg["wp_app_password"]
    return cfg


def client_from_config(cfg: dict) -> WPClient:
    return WPClient(cfg["wp_url"], cfg["wp_user"], cfg["wp_password"])
