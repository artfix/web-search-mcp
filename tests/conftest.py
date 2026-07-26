"""Each pytest-asyncio test gets a fresh event loop, but the BrowserPool
caches a Playwright instance bound to the loop where it was first created.
Shutting it down between tests keeps the pool from carrying a dead loop into
the next test.
"""
import os
import socket

import pytest


@pytest.fixture(autouse=True)
async def _reset_browser_pool():
    yield
    from search_mcp.browser import pool
    await pool.shutdown()


@pytest.fixture(autouse=True)
async def _close_global_cache():
    """Cache maintenance is fire-and-forget; await/cancel it via close() so a
    pending task never outlives the test's event loop (aiosqlite's worker
    would then warn about call_soon_threadsafe on a closed loop)."""
    yield
    from search_mcp.cache import cache
    await cache.close()


@pytest.fixture(autouse=True)
def _hermetic_dns(monkeypatch):
    """Offline suite must never depend on the machine's real resolver: some
    environments (the CC sandbox, DNS-filtering VPNs) resolve public hosts to
    the reserved 198.18.x range, which the SSRF guard correctly blocks and
    would make DNS-touching tests flake. Every hostname resolves to a fixed
    public IP; tests that need specific resolutions monkeypatch over this.
    Live runs (SEARCH_MCP_TEST_NETWORK=1) keep the real resolver."""
    if os.environ.get("SEARCH_MCP_TEST_NETWORK"):
        yield
        return

    def _resolver(host, port, *args, **kwargs):
        return [
            (
                socket.AF_INET,
                socket.SOCK_STREAM,
                socket.IPPROTO_TCP,
                "",
                ("93.184.216.34", port or 0),
            )
        ]

    monkeypatch.setattr(socket, "getaddrinfo", _resolver)
    yield


@pytest.fixture(autouse=True)
def _clear_dns_ok_cache():
    """The SSRF guard memoizes successful (host, port) validations for a short
    TTL. Tests re-stub the resolver per test, so a memo carried across tests
    would leak the previous stub's verdict into the next test."""
    from search_mcp.url_safety import clear_dns_cache
    clear_dns_cache()
    yield
    clear_dns_cache()


@pytest.fixture(autouse=True)
def _disable_rescue(monkeypatch):
    """The offline suite must never hit the network. The aggregation-level
    rescue (searx/bing) fires whenever a test stubs an engine into returning
    nothing, which would send REAL requests from an otherwise-offline test.
    test_rescue.py re-enables it against a fully stubbed engine registry."""
    from search_mcp.config import settings
    monkeypatch.setattr(settings, "rescue_enabled", False)
