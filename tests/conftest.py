"""Shared pytest fixtures.

The agent's modules live under `scripts/`; this file makes them
importable from `tests/` without changing sys.path in every test.
"""
import json
import sys
import types
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
SCRIPTS = ROOT / "scripts"
SAMPLE_OUTPUT = ROOT / "scripts"  # populated below if local sample exists
PLUGIN_SAMPLE = Path("/tmp/figma-elementor-plugin/examples/sample-output.json")
PLUGIN_GLOBAL = Path("/tmp/figma-elementor-plugin/examples/sample-global.json")

sys.path.insert(0, str(SCRIPTS))


def _ensure_requests_stub() -> None:
    """Several modules transitively import `requests`. Tests don't need it,
    so install a tiny stub that satisfies imports without making real HTTP
    calls."""
    if "requests" in sys.modules:
        return
    fake = types.ModuleType("requests")

    class _Sess:
        def __init__(self):
            self.headers = {}

        def request(self, *a, **k):
            return None

        def get(self, *a, **k):
            return None

        def post(self, *a, **k):
            return None

    fake.Session = _Sess

    class _RE(Exception):
        pass

    fake.RequestException = _RE
    fake.exceptions = types.SimpleNamespace(RequestException=_RE)
    sys.modules["requests"] = fake


_ensure_requests_stub()


@pytest.fixture
def sample_content() -> list:
    """Parsed sample-output.json content array (5 widgets in 2 containers)."""
    if not PLUGIN_SAMPLE.exists():
        pytest.skip(f"plugin sample not available at {PLUGIN_SAMPLE}")
    return json.loads(PLUGIN_SAMPLE.read_text())["content"]


@pytest.fixture
def sample_global() -> dict:
    """Parsed sample-global.json (6 colors, 5 typography presets)."""
    if not PLUGIN_GLOBAL.exists():
        pytest.skip(f"plugin global not available at {PLUGIN_GLOBAL}")
    return json.loads(PLUGIN_GLOBAL.read_text())


@pytest.fixture
def kit_settings(sample_global) -> dict:
    """Run the agent's mapper against the sample to produce a kit dict."""
    import import_elementor as ie  # noqa: WPS433
    return ie.map_global_to_kit_settings(sample_global)


@pytest.fixture
def empty_enrichment():
    """Enrichment with no ai-layout / tokens / validation — the worst-case
    fallback every public function should still tolerate."""
    from enrich import Enrichment
    return Enrichment()


@pytest.fixture
def fake_enrichment_factory():
    """Build a synthetic Enrichment from a list of section dicts."""
    from enrich import Enrichment

    def _build(sections: list[dict]) -> Enrichment:
        e = Enrichment()
        e.section_by_index = sections
        e.ai_layout = {"sections": sections, "pageType": "page", "title": "X"}
        return e

    return _build
