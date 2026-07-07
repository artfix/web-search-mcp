"""Bounded retry for transient failures on the keyless HTTP path (offline).

``Engine._http_get`` retries EXACTLY once for connection errors and retryable
statuses (429/5xx, honoring Retry-After up to a small cap) and never retries
timeouts — the policy that keeps a failing engine's cost bounded inside the
aggregator's parallel gather.
"""

from __future__ import annotations

import asyncio

import pytest
from curl_cffi.requests.exceptions import HTTPError, RequestException, Timeout

from search_mcp.engines import base as base_mod
from search_mcp.engines.base import (
    _RETRY_AFTER_CAP,
    Engine,
    _is_retryable_status,
    _retry_after_seconds,
)

# pytest.ini sets `asyncio_mode = auto` so async tests are auto-marked.


class _DummyEngine(Engine):
    name = "dummy"

    def build_url(self, query, max_results, filters=None):
        return "https://dummy.example/search"

    def parse(self, html):
        return []


class _FakeResp:
    def __init__(self, status=200, text="ok", headers=None):
        self.status_code = status
        self.text = text
        self.headers = headers or {}

    def raise_for_status(self):
        if self.status_code >= 400:
            raise HTTPError(f"HTTP {self.status_code}")


class _FakeSession:
    """Async-context-manager stand-in for curl_cffi's AsyncSession. ``script``
    items are consumed per get(): an Exception instance raises, a _FakeResp
    returns."""

    script: list = []
    calls: int = 0

    def __init__(self, **kwargs):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def get(self, url):
        item = type(self).script[type(self).calls]
        type(self).calls += 1
        if isinstance(item, Exception):
            raise item
        return item


@pytest.fixture
def fake_session(monkeypatch):
    _FakeSession.script = []
    _FakeSession.calls = 0
    monkeypatch.setattr(base_mod, "AsyncSession", _FakeSession)
    return _FakeSession


@pytest.fixture
def sleeps(monkeypatch):
    recorded: list[float] = []
    real_sleep = asyncio.sleep

    async def _sleep(t):
        recorded.append(t)
        await real_sleep(0)

    monkeypatch.setattr(asyncio, "sleep", _sleep)
    return recorded


URL = "https://dummy.example/search"


async def test_connection_error_retried_once_then_success(fake_session, sleeps):
    fake_session.script = [RequestException("connection reset"), _FakeResp(text="page")]
    out = await _DummyEngine()._http_get(URL)
    assert out == "page"
    assert fake_session.calls == 2
    assert len(sleeps) == 1 and 0.4 <= sleeps[0] <= 0.8


async def test_connection_error_exhausts_after_two_attempts(fake_session, sleeps):
    fake_session.script = [RequestException("reset"), RequestException("reset")]
    with pytest.raises(RequestException):
        await _DummyEngine()._http_get(URL)
    assert fake_session.calls == 2


async def test_429_honors_retry_after(fake_session, sleeps):
    fake_session.script = [
        _FakeResp(status=429, headers={"Retry-After": "1"}),
        _FakeResp(text="page"),
    ]
    out = await _DummyEngine()._http_get(URL)
    assert out == "page"
    assert fake_session.calls == 2
    assert sleeps == [1.0]


async def test_429_retry_after_beyond_cap_fails_fast(fake_session, sleeps):
    """Retry-After far beyond our cap means a capped-sleep retry is a
    guaranteed second rejection — fail immediately, don't burn ~3s + a
    round-trip on it."""
    fake_session.script = [
        _FakeResp(status=429, headers={"Retry-After": "120"}),
        _FakeResp(text="page"),
    ]
    with pytest.raises(HTTPError):
        await _DummyEngine()._http_get(URL)
    assert fake_session.calls == 1
    assert sleeps == []


async def test_429_retry_after_within_cap_is_honored(fake_session, sleeps):
    fake_session.script = [
        _FakeResp(status=429, headers={"Retry-After": str(_RETRY_AFTER_CAP)}),
        _FakeResp(text="page"),
    ]
    out = await _DummyEngine()._http_get(URL)
    assert out == "page"
    assert sleeps == [_RETRY_AFTER_CAP]


async def test_503_retried_with_default_delay(fake_session, sleeps):
    fake_session.script = [_FakeResp(status=503), _FakeResp(text="page")]
    out = await _DummyEngine()._http_get(URL)
    assert out == "page"
    assert sleeps == [0.6]


async def test_403_not_retried(fake_session, sleeps):
    fake_session.script = [_FakeResp(status=403)]
    with pytest.raises(HTTPError):
        await _DummyEngine()._http_get(URL)
    assert fake_session.calls == 1
    assert sleeps == []


async def test_timeout_never_retried(fake_session, sleeps):
    fake_session.script = [Timeout("deadline exceeded")]
    with pytest.raises(Timeout):
        await _DummyEngine()._http_get(URL)
    assert fake_session.calls == 1
    assert sleeps == []


async def test_retryable_status_on_last_attempt_raises(fake_session, sleeps):
    fake_session.script = [_FakeResp(status=429), _FakeResp(status=429)]
    with pytest.raises(HTTPError):
        await _DummyEngine()._http_get(URL)
    assert fake_session.calls == 2
    assert len(sleeps) == 1  # only the first 429 sleeps; the second raises


# ---------------------------------------------------------------------------
# Pure helpers
# ---------------------------------------------------------------------------


def test_retry_after_parsing():
    assert _retry_after_seconds({"Retry-After": "2"}) == 2.0
    assert _retry_after_seconds({"Retry-After": " 2.5 "}) == 2.5
    assert _retry_after_seconds({"Retry-After": "garbage"}) is None
    assert _retry_after_seconds({"Retry-After": "-5"}) is None
    assert _retry_after_seconds({}) is None
    assert _retry_after_seconds(None) is None


def test_is_retryable_status():
    for s in (429, 500, 502, 503, 504):
        assert _is_retryable_status(s)
    for s in (200, 301, 400, 403, 404, 501):
        assert not _is_retryable_status(s)
