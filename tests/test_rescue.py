"""Aggregation-level keyless rescue (offline).

Replaces the old per-engine SearXNG fallback tests (test_serp_fallback.py):
the fallback now lives in ONE place — ``aggregate_search`` runs a bounded
rescue pass (settings.rescue_engines, searx → bing) when the fresh run comes
back empty or nearly empty with demonstrably unhealthy engines. These tests
stub the engine registry so no I/O ever happens.
"""

from __future__ import annotations

import asyncio

import pytest

from search_mcp.aggregator import aggregate_search
from search_mcp.config import settings
from search_mcp.engines.base import SearchResult

# pytest.ini sets `asyncio_mode = auto` so async tests are auto-marked.


@pytest.fixture(autouse=True)
def _enable_rescue(monkeypatch):
    """conftest disables rescue suite-wide (offline safety); these tests run it
    against a fully stubbed registry, so re-enable it."""
    monkeypatch.setattr(settings, "rescue_enabled", True)


def _mk_results(engine: str, n: int, prefix: str = "") -> list[SearchResult]:
    return [
        SearchResult(
            title=f"{prefix}{engine} result {i}",
            url=f"https://{prefix or 'example'}.com/{engine}/{i}",
            snippet="s" * 100,
            engine=engine,
            rank=i + 1,
        )
        for i in range(n)
    ]


class _StubEngine:
    def __init__(self, name, results, *, gate=None, raise_exc=None, delay=0.0):
        self.name = name
        self._results = results
        self._gate = gate
        self._raise = raise_exc
        self._delay = delay
        self.calls = 0

    async def search(self, query, max_results, filters=None, diagnostics=None):
        self.calls += 1
        if self._delay:
            await asyncio.sleep(self._delay)
        if self._raise is not None:
            raise self._raise
        if diagnostics is not None:
            diagnostics.setdefault("raw_per_engine", {})[self.name] = len(self._results)
            diagnostics.setdefault("after_filter_per_engine", {})[self.name] = len(
                self._results
            )
            if self._gate and not self._results:
                diagnostics.setdefault("gated", {})[self.name] = self._gate
        return list(self._results)


class _StubCache:
    def __init__(self):
        self.put_calls: list = []

    async def get_search(self, key, max_age_seconds=None):
        return None

    async def put_search(self, key, query, engines, results):
        self.put_calls.append((key, query, engines, results))


def _wire(monkeypatch, engines: dict[str, _StubEngine]) -> _StubCache:
    def _get(name: str):
        try:
            return engines[name]
        except KeyError:
            raise ValueError(f"unknown engine: {name}") from None

    monkeypatch.setattr("search_mcp.aggregator.get_engine", _get)
    stub_cache = _StubCache()
    monkeypatch.setattr("search_mcp.aggregator.cache", stub_cache)
    return stub_cache


async def test_zero_results_triggers_rescue_and_caches(monkeypatch):
    searx = _StubEngine("searx", _mk_results("searx", 4, prefix="rescue"))
    stub_cache = _wire(
        monkeypatch,
        {
            "duckduckgo": _StubEngine("duckduckgo", [], gate="captcha"),
            "searx": searx,
        },
    )
    out = await aggregate_search("q", engines=["duckduckgo"])
    assert len(out["results"]) == 4
    assert all(r["engines"] == ["searx"] for r in out["results"])
    assert out["rescued_via"] == "searx"
    assert searx.calls == 1
    # Rescued results are cached under the original pool's key.
    assert len(stub_cache.put_calls) == 1
    assert len(stub_cache.put_calls[0][3]) == 4
    # Gate diagnostics survive alongside the rescue.
    assert out["gated_engines"]["duckduckgo"]["reason"] == "captcha"


async def test_sparse_unhealthy_run_merges_rescue_results(monkeypatch):
    searx = _StubEngine("searx", _mk_results("searx", 5, prefix="rescue"))
    _wire(
        monkeypatch,
        {
            "alpha": _StubEngine("alpha", _mk_results("alpha", 2)),
            "beta": _StubEngine("beta", [], gate="captcha"),  # unhealthy signal
            "searx": searx,
        },
    )
    out = await aggregate_search("q", engines=["alpha", "beta"], use_cache=False)
    assert out["rescued_via"] == "searx"
    urls = [r["url"] for r in out["results"]]
    # Both the partial default results and the rescue results survived the merge.
    assert any("/alpha/" in u for u in urls)
    assert any("/searx/" in u for u in urls)


async def test_sparse_but_healthy_run_does_not_rescue(monkeypatch):
    """The normal-path latency guarantee: 2 legit results, all engines healthy
    → the rescue engine must never be called."""
    searx = _StubEngine("searx", _mk_results("searx", 5))
    _wire(
        monkeypatch,
        {
            "alpha": _StubEngine("alpha", _mk_results("alpha", 2)),
            "searx": searx,
        },
    )
    out = await aggregate_search("nichequery", engines=["alpha"], use_cache=False)
    assert len(out["results"]) == 2
    assert "rescued_via" not in out
    assert searx.calls == 0


async def test_no_self_rescue_when_caller_asked_for_rescue_engine(monkeypatch):
    searx = _StubEngine("searx", [])
    bing = _StubEngine("bing", [])
    _wire(monkeypatch, {"searx": searx, "bing": bing})
    out = await aggregate_search("q", engines=["searx", "bing"], use_cache=False)
    assert out["results"] == []
    assert "rescued_via" not in out
    # Each ran once as a requested engine; the rescue pass never re-ran them.
    assert searx.calls == 1
    assert bing.calls == 1


async def test_rescue_engine_failure_is_swallowed(monkeypatch):
    searx = _StubEngine("searx", [], raise_exc=RuntimeError("instances dead"))
    bing = _StubEngine("bing", _mk_results("bing", 3, prefix="rescue"))
    _wire(
        monkeypatch,
        {
            "duckduckgo": _StubEngine("duckduckgo", []),
            "searx": searx,
            "bing": bing,
        },
    )
    out = await aggregate_search("q", engines=["duckduckgo"], use_cache=False)
    # searx blew up; the pass moved on to bing.
    assert out["rescued_via"] == "bing"
    assert len(out["results"]) == 3


async def test_all_rescue_engines_fail_returns_clean_empty(monkeypatch):
    _wire(
        monkeypatch,
        {
            "duckduckgo": _StubEngine("duckduckgo", []),
            "searx": _StubEngine("searx", [], raise_exc=RuntimeError("dead")),
            "bing": _StubEngine("bing", [], raise_exc=RuntimeError("dead")),
        },
    )
    out = await aggregate_search("q", engines=["duckduckgo"], use_cache=False)
    assert out["results"] == []
    assert "rescued_via" not in out


async def test_rescue_timeout_is_bounded(monkeypatch):
    monkeypatch.setattr(settings, "rescue_timeout", 0.05)
    searx = _StubEngine("searx", _mk_results("searx", 3), delay=0.5)
    _wire(
        monkeypatch,
        {
            "duckduckgo": _StubEngine("duckduckgo", []),
            "searx": searx,
        },
    )
    out = await aggregate_search("q", engines=["duckduckgo"], use_cache=False)
    assert out["results"] == []
    assert "rescued_via" not in out


async def test_rescue_disabled_by_setting(monkeypatch):
    monkeypatch.setattr(settings, "rescue_enabled", False)
    searx = _StubEngine("searx", _mk_results("searx", 3))
    _wire(
        monkeypatch,
        {
            "duckduckgo": _StubEngine("duckduckgo", []),
            "searx": searx,
        },
    )
    out = await aggregate_search("q", engines=["duckduckgo"], use_cache=False)
    assert out["results"] == []
    assert searx.calls == 0


# ---------------------------------------------------------------------------
# Engine-level behavior after the per-engine fallback removal
# ---------------------------------------------------------------------------

_GATE_HTML = "<html>/sorry/index unusual traffic</html>"


async def test_google_gated_returns_empty_no_engine_level_fallback(monkeypatch):
    """A gated Google now degrades to [] + a gate diagnostic; the rescue lives
    in the aggregator, not in the engine."""
    from unittest.mock import AsyncMock

    from search_mcp.engines.google import GoogleEngine

    engine = GoogleEngine()
    monkeypatch.setattr(engine, "_fetch", AsyncMock(return_value=_GATE_HTML))
    monkeypatch.setattr("search_mcp.engines.base.settings.fetch_strategy", "http")

    diag: dict = {}
    out = await engine.search("anything", 5, diagnostics=diag)
    assert out == []
    assert diag["gated"]["google"] == "captcha"
    assert "fallback" not in diag


async def test_bing_http_raise_propagates_like_any_engine(monkeypatch):
    """bing has no swallow-everything wrapper anymore: a raise surfaces to the
    aggregator's errors map (visible, and enough to trigger rescue) instead of
    masquerading as a 'silent zero' the empty-hint would mislabel 'no error'."""
    from unittest.mock import AsyncMock

    import pytest as _pytest
    from curl_cffi.requests.exceptions import RequestException

    from search_mcp.engines.bing import BingEngine

    engine = BingEngine()
    monkeypatch.setattr(
        engine, "_fetch", AsyncMock(side_effect=RequestException("non-200 shell"))
    )
    with _pytest.raises(RequestException):
        await engine.search("anything", 5, diagnostics={})


async def test_three_results_with_gate_still_rescues(monkeypatch):
    """Rescue and the sparse-warning hints share the <=3 threshold: a run must
    never be 'sparse enough to warn about' yet 'too healthy to rescue'."""
    searx = _StubEngine("searx", _mk_results("searx", 5, prefix="rescue"))
    _wire(
        monkeypatch,
        {
            "alpha": _StubEngine("alpha", _mk_results("alpha", 3)),
            "beta": _StubEngine("beta", [], gate="captcha"),
            "searx": searx,
        },
    )
    out = await aggregate_search("q", engines=["alpha", "beta"], use_cache=False)
    assert out["rescued_via"] == "searx"
    # The recovery is attributed to the gated engine in the payload.
    assert out["gated_engines"]["beta"]["fallback"] == "searx"
    assert "served via searx" in out["gated_hint"]


async def test_failed_rescue_probe_does_not_pollute_caller_diagnostics(monkeypatch):
    """A rescue engine that comes back empty (or gated) is an internal probe:
    it must not appear in empty_engines/gated_engines, which describe the
    engines the CALLER asked for."""
    searx = _StubEngine("searx", [], gate="no_live_instance")
    bing = _StubEngine("bing", _mk_results("bing", 2, prefix="rescue"))
    _wire(
        monkeypatch,
        {
            "duckduckgo": _StubEngine("duckduckgo", []),
            "searx": searx,
            "bing": bing,
        },
    )
    out = await aggregate_search("q", engines=["duckduckgo"], use_cache=False)
    assert out["rescued_via"] == "bing"
    assert "searx" not in out.get("empty_engines", [])
    assert "searx" not in (out.get("gated_engines") or {})
    # The probe's outcome is still visible under the rescue summary for debugging.
    assert out.get("gated_engines") is None or "searx" not in out["gated_engines"]
