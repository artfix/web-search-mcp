"""Silent-empty engine visibility (offline).

An engine that returns 0 raw results with no exception and no detected gate
(the mojeek-IP-block failure mode) must be surfaced via ``empty_engines`` /
``empty_hint`` when the merged result set is sparse — and stay invisible on a
healthy response so the happy-path output is unchanged. Also covers the
markdown rendering of the gate/silent/rescue hints, which previously never
reached markdown-mode callers at all.
"""

from __future__ import annotations

from search_mcp.aggregator import aggregate_search
from search_mcp.engines.base import SearchResult
from search_mcp.formatting import render_search

# pytest.ini sets `asyncio_mode = auto` so async tests are auto-marked.


def _mk_results(engine: str, n: int) -> list[SearchResult]:
    return [
        SearchResult(
            title=f"{engine} result {i}",
            url=f"https://example.com/{engine}/{i}",
            snippet="s" * 100,
            engine=engine,
            rank=i + 1,
        )
        for i in range(n)
    ]


class _StubEngine:
    """Minimal engine double that populates diagnostics the way base.search
    does: raw/after counts always, a gate entry when configured."""

    def __init__(self, name, results, *, gate=None, raise_exc=None):
        self.name = name
        self._results = results
        self._gate = gate
        self._raise = raise_exc

    async def search(self, query, max_results, filters=None, diagnostics=None):
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


def _stub_registry(monkeypatch, engines: dict[str, _StubEngine]) -> None:
    def _get(name: str):
        try:
            return engines[name]
        except KeyError:
            raise ValueError(f"unknown engine: {name}")

    monkeypatch.setattr("search_mcp.aggregator.get_engine", _get)


async def test_silent_empty_engine_surfaced_when_sparse(monkeypatch):
    _stub_registry(
        monkeypatch,
        {
            "alpha": _StubEngine("alpha", _mk_results("alpha", 3)),
            "beta": _StubEngine("beta", []),  # 0 raw, no error, no gate
        },
    )
    out = await aggregate_search("q", engines=["alpha", "beta"], use_cache=False)
    assert len(out["results"]) == 3
    assert out["empty_engines"] == ["beta"]
    assert "beta" in out["empty_hint"]
    assert "IP block" in out["empty_hint"]


async def test_silent_empty_hidden_when_healthy(monkeypatch):
    _stub_registry(
        monkeypatch,
        {
            "alpha": _StubEngine("alpha", _mk_results("alpha", 10)),
            "beta": _StubEngine("beta", []),
        },
    )
    out = await aggregate_search("q", engines=["alpha", "beta"], use_cache=False)
    assert len(out["results"]) == 10
    assert "empty_engines" not in out
    assert "empty_hint" not in out


async def test_gated_engine_not_double_reported_as_empty(monkeypatch):
    _stub_registry(
        monkeypatch,
        {
            "alpha": _StubEngine("alpha", _mk_results("alpha", 3)),
            "beta": _StubEngine("beta", [], gate="captcha"),
        },
    )
    out = await aggregate_search("q", engines=["alpha", "beta"], use_cache=False)
    assert out["gated_engines"]["beta"]["reason"] == "captcha"
    assert "empty_engines" not in out


async def test_errored_engine_not_reported_as_empty(monkeypatch):
    _stub_registry(
        monkeypatch,
        {
            "alpha": _StubEngine("alpha", _mk_results("alpha", 3)),
            "beta": _StubEngine("beta", [], raise_exc=RuntimeError("boom")),
        },
    )
    out = await aggregate_search("q", engines=["alpha", "beta"], use_cache=False)
    assert out["errors"] == {"beta": "boom"}
    assert "empty_engines" not in out


# ---------------------------------------------------------------------------
# Markdown rendering of the hint fields
# ---------------------------------------------------------------------------


def _payload(**extra):
    base = {
        "query": "q",
        "engines": ["alpha"],
        "cached": False,
        "results": [
            {
                "title": "T",
                "url": "https://example.com/x",
                "snippet": "s",
                "engines": ["alpha"],
                "score": 0.5,
            }
        ],
        "errors": None,
    }
    base.update(extra)
    return base


def test_render_search_shows_gated_hint():
    md = render_search(_payload(gated_hint="duckduckgo was captcha-gated (no results)."))
    assert "Gated engines" in md
    assert "captcha-gated" in md


def test_render_search_shows_empty_hint():
    md = render_search(_payload(empty_hint="mojeek returned 0 results …"))
    assert "Silent engines" in md
    assert "mojeek" in md


def test_render_search_shows_rescue_note():
    md = render_search(_payload(rescued_via="searx"))
    assert "rescue pass via searx" in md


def test_render_search_hints_shown_on_no_results_branch():
    md = render_search(
        _payload(
            results=[],
            gated_hint="duckduckgo was captcha-gated (no results).",
            empty_hint="mojeek returned 0 results …",
        )
    )
    assert "No results" in md
    assert "Gated engines" in md
    assert "Silent engines" in md


def test_render_search_happy_path_has_no_hint_blocks():
    md = render_search(_payload())
    assert "Gated engines" not in md
    assert "Silent engines" not in md
    assert "rescue pass" not in md
