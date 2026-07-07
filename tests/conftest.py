"""Each pytest-asyncio test gets a fresh event loop, but the BrowserPool
caches a Playwright instance bound to the loop where it was first created.
Shutting it down between tests keeps the pool from carrying a dead loop into
the next test.
"""
import pytest


@pytest.fixture(autouse=True)
async def _reset_browser_pool():
    yield
    from search_mcp.browser import pool
    await pool.shutdown()


@pytest.fixture(autouse=True)
def _disable_rescue(monkeypatch):
    """The offline suite must never hit the network. The aggregation-level
    rescue (searx/bing) fires whenever a test stubs an engine into returning
    nothing, which would send REAL requests from an otherwise-offline test.
    test_rescue.py re-enables it against a fully stubbed engine registry."""
    from search_mcp.config import settings
    monkeypatch.setattr(settings, "rescue_enabled", False)
