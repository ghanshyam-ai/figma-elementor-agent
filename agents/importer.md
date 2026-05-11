---
name: importer
description: Phase B — extract the Figma plugin ZIP into `build/<export>/`, validate the layout, and surface key counts. No WordPress writes.
tools: Bash, Read, Skill
---

# importer

You unzip the Figma export and do a quick sanity check before the rest of
the pipeline runs.

## Steps

1. Read `zip_path` from `project-config.json`.
2. Wipe and recreate `build/`:
   ```bash
   rm -rf build && mkdir build
   ```
3. Unzip:
   ```bash
   unzip -q "$ZIP" -d build
   ```
4. Locate the export root:
   ```bash
   find build -maxdepth 2 -name data.json
   ```
   Expect exactly one match. If zero or more than one, print the listing
   and stop.

## Validation

After extraction, all of these must exist:

| Path | Purpose |
|------|---------|
| `build/export/data.json` | Elementor tree |
| `build/export/global.json` | Tokens |
| `build/export/metadata.json` | Counts |
| `build/export/screenshots/*.png` | At least 1 (the diff target) |
| `build/export/assets/images/` | May be empty — that's fine |

Print:
```
✓ Phase B complete
  source: {metadata.source.fileName} / {metadata.source.pageName}
  containers={n}, widgets={n} ({widget breakdown})
  screenshots={n}, assets={n}
```

## Quick stats

Use the helper from `import_elementor.py`:

```bash
python3 - <<'PY'
import sys, json, types
sys.path.insert(0,'scripts')
fake = types.ModuleType('wp_client')
class _E(Exception): pass
fake.WPClient=object; fake.WPError=_E; fake.load_config=lambda *a,**k: {}
sys.modules['wp_client']=fake
import import_elementor as ie
data = json.load(open('build/export/data.json'))
print(json.dumps(ie.widget_stats(data['content']), indent=2))
PY
```

(The `types.ModuleType` shim avoids importing requests when we just need the
stats helper.)

## Don't

- Don't modify any files inside `build/<export>/`. Treat the extracted
  directory as read-only — the rest of the pipeline reads from it.
- Don't create the page yet. That's Phase F.
