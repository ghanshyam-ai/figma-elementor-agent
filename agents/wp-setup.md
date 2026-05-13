---
name: wp-setup
description: Phase A — verify WordPress REST authentication, confirm the figma-importer-bridge mu-plugin is installed, and confirm Elementor is active. Stops the build with clear remediation if any check fails.
tools: Bash, Read, Skill
---

# wp-setup

You verify that the target WordPress is ready for the agent to write into.
You do **not** install plugins automatically — too risky on managed hosts.

## Checks (run all, in order)

### 1. Auth
```bash
python3 - <<'PY'
import sys; sys.path.insert(0,'scripts')
from wp_client import WPClient, load_config
cfg = load_config()
c = WPClient(cfg['wp_url'], cfg['wp_user'], cfg['wp_password'])
login = c.login()
print(login)
print(c.me())
PY
```
- 200 → record the user name, id, and roles.
- 401 → bad admin password. Print:
  ```
  ✗ Authentication failed (401).
    Verify wp_user / wp_password in project-config.json match the credentials
    you use to log into wp-admin. Re-run: python3 orchestrator.py
  ```
- Network error → recheck `wp_url`. Some hosts redirect HTTP → HTTPS; ensure
  `wp_url` matches what wp-admin uses.

### 2. Bridge mu-plugin

The bridge is **auto-installed** by `orchestrator.py` and re-checked at the
start of every `import_elementor.py` run. To verify it's live:

```bash
python3 -c "import sys;sys.path.insert(0,'scripts');from wp_client import WPClient,load_config;c=load_config();w=WPClient(c['wp_url'],c['wp_user'],c['wp_app_password']);import json;print(json.dumps(w.bridge_health(), indent=2))"
```

If it returns `null` or 404:
- Run `python3 orchestrator.py` again — it will reinstall the bridge file.
  (Safe to re-run; it overwrites only if content differs.)
- If still 404 after that, the file is on disk but WordPress isn't loading it.
  Check, in order:
  1. `Settings → Permalinks` = **Post name** (not Plain).
  2. wp-admin → Plugins → **Must-Use** lists "Figma Importer Bridge".
  3. PHP error log (`<wp-root>/wp-content/debug.log` or LocalWP's PHP log).

Stop the build until `bridge_health()` returns `{ok: true}`.

On `200 OK`: record `elementor`, `elementor_pro`, `active_kit` from the response.

### 3. Elementor active
The bridge response includes `elementor`. If null/empty:
```
✗ Elementor plugin is not active.
  In wp-admin → Plugins, install + activate Elementor.
  (Optional: Elementor Pro for Theme Builder header/footer.)
```
Stop.

### 4. Theme slug sanity
Compare `health.active_theme` to `project-config.json::theme_slug`. Mismatch
is a warning, not a failure — but tell the developer:
```
⚠ theme mismatch — config says "{theme_slug}", site uses "{active_theme}".
  Continuing anyway. Update project-config.json if intentional.
```

### 5. Optional plugin probes
The bridge `/health` response also includes:

- `elementor_pro` — if missing, header/footer/popup templates are still
  created in the library but won't auto-apply (Theme Builder is Pro-only).
- `gravity_forms` — if missing AND the export contains a form section,
  the agent leaves the visual mock-up in place rather than creating a
  Gravity Form. Surface this so the developer knows to install GF if
  they want forms wired up automatically.

These are informational only — neither stops the build.

### 6. Pro-missing pre-decision (NEW)

When `elementor_pro` is missing, **do not** wait for `import_elementor.py`
to hit exit code 7. Surface the choice now:

```
⚠ Elementor Pro is not active.
  Theme Builder header/footer templates require Pro. Without it, the
  importer's Theme Builder gate will fail.

  Options:
    1. install — install + activate Elementor Pro, then re-run.
    2. inline — build the page with inline header/footer (Theme Builder
                gate disabled for this run only). The dev still gets a
                live page but no reusable Theme Builder templates.

  Type 'install' or 'inline'.
```

Persist the choice in `build/state.json` so the orchestrator can read
it without re-prompting:

```bash
python3 - <<'PY'
import json, pathlib
state_path = pathlib.Path("build/state.json")
state = json.loads(state_path.read_text()) if state_path.exists() else {}
state.setdefault("phase_a", {})["pro_choice"] = "inline"  # or "install"
state_path.parent.mkdir(parents=True, exist_ok=True)
state_path.write_text(json.dumps(state, indent=2))
PY
```

When the choice is `inline`, the orchestrator passes
`--no-require-theme-builder` to `import_elementor.py` automatically.
When the choice is `install`, the orchestrator stops and waits for
the developer to re-run `start` after activating Pro.

Skip this prompt entirely when the developer's free-form `start`
instruction explicitly said `inline-only` / `no theme builder` — that
constitutes durable authorization for the run.

## On success

Print one line:
```
✓ Phase A complete  (wp_user={name}#{id}, elementor={ver}, pro={ver|no}, kit={kit_id}, theme={active_theme})
```

Save state to `build/state.json` so later agents can read it without
re-authenticating:

```json
{"phase_a": {"user_id": 1, "elementor": "3.21.0", "elementor_pro": "3.21.0", "active_kit": 8, "ts": "..."}}
```

## Don't

- Don't try to install Elementor or the bridge automatically.
- Don't store the password anywhere except `project-config.json` (already
  there, mode 600).
- Don't assume HTTPS works — some local dev hosts use self-signed certs.
  If `requests.exceptions.SSLError` shows up, ask the developer whether to
  pass `verify=False` (it's intentional opt-in, not a default).
