"""Graceful degrade when the Chromium browser binary is missing (offline).

A missing browser must yield an actionable BrowserUnavailableError (with the
exact install command), be memoized so repeat calls fail fast without
re-starting the Playwright driver, and degrade engine searches to an honest
``browser_unavailable`` gate instead of a stack trace.
"""

from __future__ import annotations

from search_mcp.aggregator import aggregate_search
from search_mcp.browser import (
    BrowserPool,
    BrowserUnavailableError,
    _is_missing_browser_error,
)
from search_mcp.engines.base import Engine

# pytest.ini sets `asyncio_mode = auto` so async tests are auto-marked.


# Playwright's real launch error when the binary was never downloaded.
_PLAYWRIGHT_MISSING_MSG = (
    "BrowserType.launch_persistent_context: Executable doesn't exist at "
    "/home/u/.cache/ms-playwright/chromium-1140/chrome-linux/chrome\n"
    "╔══════════════════════════════════════════════════════╗\n"
    "║ Please run the following command to download new browsers: ║\n"
    "║     playwright install                                     ║\n"
    "╚══════════════════════════════════════════════════════╝"
)


def test_is_missing_browser_error_classification():
    assert _is_missing_browser_error(_PLAYWRIGHT_MISSING_MSG)
    assert _is_missing_browser_error("Executable doesn't exist at /x/chrome")
    assert not _is_missing_browser_error("Target crashed")
    assert not _is_missing_browser_error("net::ERR_TIMED_OUT")
    assert not _is_missing_browser_error("")


class _FakePlaywright:
    def __init__(self):
        self.stopped = False
        self.chromium = self

    async def launch_persistent_context(self, **kwargs):
        raise Exception(_PLAYWRIGHT_MISSING_MSG)

    async def stop(self):
        self.stopped = True


class _FakeAsyncPlaywright:
    starts = 0
    last: _FakePlaywright | None = None

    async def start(self):
        type(self).starts += 1
        type(self).last = _FakePlaywright()
        return type(self).last


async def test_missing_browser_raises_actionable_error_and_memoizes(
    monkeypatch, tmp_path
):
    from search_mcp import browser as browser_mod
    from search_mcp.config import settings

    monkeypatch.setattr(settings, "cache_dir", tmp_path)
    _FakeAsyncPlaywright.starts = 0
    monkeypatch.setattr(browser_mod, "async_playwright", lambda: _FakeAsyncPlaywright())

    p = BrowserPool()
    try:
        await p.fetch_html("https://example.com")
        raise AssertionError("expected BrowserUnavailableError")
    except BrowserUnavailableError as e:
        assert "playwright install chromium" in str(e)
        assert "HTTP-only" in str(e)
    # The failed driver was torn down, not leaked.
    assert _FakeAsyncPlaywright.last is not None
    assert _FakeAsyncPlaywright.last.stopped

    # Second call fails fast from the memo — no new driver start.
    try:
        await p.fetch_html("https://example.com")
        raise AssertionError("expected BrowserUnavailableError")
    except BrowserUnavailableError:
        pass
    assert _FakeAsyncPlaywright.starts == 1

    # shutdown() is the "restart" the hint asks for: memo cleared, next call
    # attempts a fresh launch.
    await p.shutdown()
    try:
        await p.fetch_html("https://example.com")
        raise AssertionError("expected BrowserUnavailableError")
    except BrowserUnavailableError:
        pass
    assert _FakeAsyncPlaywright.starts == 2


class _NoResultsEngine(Engine):
    name = "dummy"

    def build_url(self, query, max_results, filters=None):
        return "https://dummy.example/search"

    def parse(self, html):
        return []


async def test_engine_search_degrades_to_gate_when_browser_missing(monkeypatch):
    """The empty-parse browser fallback catches BrowserUnavailableError and
    records an honest gate instead of raising."""

    async def _raise(*a, **kw):
        raise BrowserUnavailableError("Playwright's Chromium browser is not installed…")

    monkeypatch.setattr("search_mcp.engines.base.pool.fetch_html", _raise)

    engine = _NoResultsEngine()

    async def _fetch(url):
        return "<html><body>plain empty page</body></html>"

    monkeypatch.setattr(engine, "_fetch", _fetch)

    diag: dict = {}
    out = await engine.search("anything", 5, diagnostics=diag)
    assert out == []
    assert diag["gated"]["dummy"] == "browser_unavailable"


async def test_aggregate_renders_single_install_hint(monkeypatch):
    """A browser_unavailable gate flows into gated_hint with the install
    command, via aggregate_search."""

    class _GatedStub:
        name = "startpage"

        async def search(self, query, max_results, filters=None, diagnostics=None):
            if diagnostics is not None:
                diagnostics.setdefault("raw_per_engine", {})[self.name] = 0
                diagnostics.setdefault("gated", {})[self.name] = "browser_unavailable"
            return []

    monkeypatch.setattr("search_mcp.aggregator.get_engine", lambda name: _GatedStub())
    out = await aggregate_search("q", engines=["startpage"], use_cache=False)
    assert out["results"] == []
    assert out["gated_engines"]["startpage"]["reason"] == "browser_unavailable"
    assert "playwright install chromium" in out["gated_hint"]
