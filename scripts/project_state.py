"""
Persistent project state — survives `build/` wipes between page imports.

The project workflow is intentionally page-by-page (start with the home
page so header / footer / globals get created once, then run the agent
per page after that). To keep the second + third + Nth runs fast and
non-destructive, we cache:

    • kit_applied + kit_id            — globals were already written
    • template_ids_by_slug            — header / footer / popup / section
                                        templates created on prior runs
    • form_ids_by_title               — Gravity Forms we've already created
    • asset_map_by_filename           — media library entries (so re-uploads
                                        are skipped when the same image is
                                        referenced from a second page's ZIP)
    • pages_imported                  — slug → page_id audit trail
    • component_library               — fingerprint → library template, so
                                        identical accordions / testimonials
                                        across pages collapse to one source
    • tokens_hash                     — fingerprint of the applied global.json;
                                        lets us warn when a later ZIP brings
                                        different brand colors/typography

Shape: `<repo_root>/project-state.json`. Gitignored (mode 600).
This file is the orchestrator's "what's already on the WP site". It
complements `build/state.json`, which is per-run scratch space.
"""
from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path


_DEFAULT_FILENAME = "project-state.json"


@dataclass
class ProjectState:
    path: Path
    first_run_at: str | None = None
    last_run_at: str | None = None
    kit_applied: bool = False
    kit_id: int | None = None
    template_ids_by_slug: dict[str, dict] = field(default_factory=dict)
    form_ids_by_title: dict[str, int] = field(default_factory=dict)
    asset_map_by_filename: dict[str, dict] = field(default_factory=dict)
    # Content-hash → upload meta. Survives filename churn: the Figma plugin
    # often regenerates hashed filenames per export ("img_<hash>.png"), so
    # the same actual file lands under a different name. Dedup keyed by
    # SHA-256 of file content skips re-uploads in that case.
    asset_map_by_hash: dict[str, dict] = field(default_factory=dict)
    pages_imported: list[dict] = field(default_factory=list)
    # Fingerprint → library-template metadata. Lets the next page's reuse
    # detector short-circuit to a stored template_id instead of recreating
    # an identical accordion / testimonial / card-grid. Structural-only:
    # matching ignores copy + image content (see template_reuse._structural_hash).
    component_library: dict[str, dict] = field(default_factory=dict)
    # SHA-256 of the canonicalized global.json that was applied to the kit.
    # Subsequent runs compare against this to warn when brand tokens diverge.
    tokens_hash: str | None = None

    # ---- Convenience properties --------------------------------------

    @property
    def is_first_run(self) -> bool:
        """True until the kit has been applied AND a header/footer template exists."""
        if not self.kit_applied:
            return True
        kinds = {meta.get("template_type") for meta in self.template_ids_by_slug.values()}
        return not (kinds & {"header", "footer"})

    @property
    def header_template(self) -> dict | None:
        return self._first_template_of("header")

    @property
    def footer_template(self) -> dict | None:
        return self._first_template_of("footer")

    def _first_template_of(self, kind: str) -> dict | None:
        for slug, meta in self.template_ids_by_slug.items():
            if meta.get("template_type") == kind:
                return {"slug": slug, **meta}
        return None

    # ---- Mutators ----------------------------------------------------

    def record_kit_applied(self, kit_id: int) -> None:
        self.kit_applied = True
        self.kit_id = kit_id

    def record_template(self, slug: str, template_type: str, template_id: int, title: str = "") -> None:
        self.template_ids_by_slug[slug] = {
            "id": template_id,
            "template_type": template_type,
            "title": title,
            "saved_at": _now_iso(),
        }

    def record_form(self, title: str, form_id: int) -> None:
        self.form_ids_by_title[title] = form_id

    def record_assets(self, asset_map: dict[str, dict]) -> None:
        for fname, meta in (asset_map or {}).items():
            if isinstance(meta, dict) and meta.get("id") and meta.get("url"):
                entry = {"id": meta["id"], "url": meta["url"]}
                if meta.get("sha256"):
                    entry["sha256"] = meta["sha256"]
                    self.asset_map_by_hash[meta["sha256"]] = entry
                self.asset_map_by_filename[fname] = entry

    def record_page(self, slug: str, page_id: int, permalink: str) -> None:
        self.pages_imported = [p for p in self.pages_imported if p.get("slug") != slug]
        self.pages_imported.append({
            "slug": slug,
            "page_id": page_id,
            "permalink": permalink,
            "imported_at": _now_iso(),
        })

    def record_component(
        self,
        fingerprint: str,
        template_id: int,
        *,
        slug: str,
        kind: str,
        title: str,
        page_slug: str,
        widget_count: int = 0,
    ) -> None:
        self.component_library[str(fingerprint)] = {
            "template_id": template_id,
            "template_slug": slug,
            "kind": kind,
            "title": title,
            "first_page_slug": page_slug,
            "widget_count": widget_count,
            "recorded_at": _now_iso(),
        }

    def find_component(self, fingerprint: str) -> dict | None:
        return self.component_library.get(str(fingerprint))

    def record_tokens(self, global_dict: dict) -> tuple[str, bool]:
        """Hash the global.json and report whether it differs from stored.

        Returns ``(new_hash, changed)``. Does **not** persist the new hash —
        the caller decides whether to keep it (e.g. after a successful kit
        POST) or leave the stored one alone (warn-and-skip path).
        """
        new_hash = _tokens_hash(global_dict)
        changed = bool(self.tokens_hash) and self.tokens_hash != new_hash
        return new_hash, changed

    def remember_run(self) -> None:
        if not self.first_run_at:
            self.first_run_at = _now_iso()
        self.last_run_at = _now_iso()

    # ---- Persistence -------------------------------------------------

    def save(self) -> None:
        payload = {
            "first_run_at": self.first_run_at,
            "last_run_at": self.last_run_at,
            "kit_applied": self.kit_applied,
            "kit_id": self.kit_id,
            "tokens_hash": self.tokens_hash,
            "template_ids_by_slug": self.template_ids_by_slug,
            "form_ids_by_title": self.form_ids_by_title,
            "asset_map_by_filename": self.asset_map_by_filename,
            "asset_map_by_hash": self.asset_map_by_hash,
            "pages_imported": self.pages_imported,
            "component_library": self.component_library,
        }
        self.path.write_text(json.dumps(payload, indent=2, default=str))
        try:
            os.chmod(self.path, 0o600)
        except OSError:
            pass


def load_state(repo_root: Path, filename: str = _DEFAULT_FILENAME) -> ProjectState:
    """Load `<repo_root>/project-state.json`, or return a fresh state."""
    p = repo_root / filename
    if not p.exists():
        return ProjectState(path=p)
    try:
        raw = json.loads(p.read_text())
    except json.JSONDecodeError:
        return ProjectState(path=p)
    s = ProjectState(path=p)
    s.first_run_at = raw.get("first_run_at")
    s.last_run_at = raw.get("last_run_at")
    s.kit_applied = bool(raw.get("kit_applied"))
    s.kit_id = raw.get("kit_id")
    s.tokens_hash = raw.get("tokens_hash")
    s.template_ids_by_slug = raw.get("template_ids_by_slug") or {}
    s.form_ids_by_title = raw.get("form_ids_by_title") or {}
    s.asset_map_by_filename = raw.get("asset_map_by_filename") or {}
    s.asset_map_by_hash = raw.get("asset_map_by_hash") or {}
    s.pages_imported = raw.get("pages_imported") or []
    s.component_library = raw.get("component_library") or {}
    return s


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _tokens_hash(global_dict: dict) -> str:
    """SHA-256 of a canonicalized global.json — order-insensitive."""
    canon = json.dumps(global_dict or {}, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(canon.encode("utf-8")).hexdigest()
