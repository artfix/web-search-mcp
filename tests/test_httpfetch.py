"""Direct tests for the shared GET loops in httpfetch (offline).

The end-to-end SSRF behavior is covered via fetcher/documents/structured in
test_fetch_safety.py; here we pin the shared loop's own contract: every
redirect hop is re-validated, and the raise_for_status flag switches between
documents' strict mode and structured's keep-the-shell mode.
"""

from __future__ import annotations


import pytest

from search_mcp.httpfetch import httpx_stream_capped
from search_mcp.url_safety import UnsafeURLError

# pytest.ini sets `asyncio_mode = auto` so async tests are auto-marked.



class _StreamCtx:
    def __init__(self, resp):
        self._resp = resp

    async def __aenter__(self):
        return self._resp

    async def __aexit__(self, *exc):
        return False


class _Resp:
    def __init__(self, status=200, headers=None, body=b"ok"):
        self.status_code = status
        self.headers = headers or {}
        self._body = body

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")

    async def aiter_bytes(self):
        yield self._body


class _FakeClient:
    def __init__(self, responses):
        self._responses = list(responses)
        self.requested: list[str] = []

    def stream(self, method, url):
        self.requested.append(url)
        return _StreamCtx(self._responses.pop(0))


async def test_httpx_loop_blocks_private_redirect_hop():
    client = _FakeClient(
        [
            _Resp(status=302, headers={"location": "http://127.0.0.1/secret"}),
            _Resp(status=200, body=b"leak"),
        ]
    )
    with pytest.raises(UnsafeURLError):
        await httpx_stream_capped(client, "https://example.com/start", raise_for_status=True)
    # Only the first URL was requested; the private hop was refused.
    assert client.requested == ["https://example.com/start"]


async def test_httpx_loop_follows_safe_redirect():
    client = _FakeClient(
        [
            _Resp(status=301, headers={"location": "https://example.org/final"}),
            _Resp(status=200, headers={"content-type": "text/html"}, body=b"done"),
        ]
    )
    status, ctype, body = await httpx_stream_capped(
        client, "https://example.com/start", raise_for_status=True
    )
    assert (status, body) == (200, b"done")
    assert "html" in ctype
    assert client.requested == [
        "https://example.com/start",
        "https://example.org/final",
    ]


async def test_httpx_loop_raise_for_status_flag():
    # Strict mode (documents): a 403 raises.
    with pytest.raises(RuntimeError):
        await httpx_stream_capped(
            _FakeClient([_Resp(status=403)]),
            "https://example.com/doc",
            raise_for_status=True,
        )
    # Shell-keeping mode (structured): the 403 body comes back for the
    # meta_fallback path.
    status, _, body = await httpx_stream_capped(
        _FakeClient([_Resp(status=403, body=b"<html>blocked</html>")]),
        "https://example.com/page",
        raise_for_status=False,
    )
    assert status == 403
    assert body == b"<html>blocked</html>"


async def test_httpx_loop_caps_redirect_chain():
    hops = [
        _Resp(status=302, headers={"location": f"https://example.com/{i}"})
        for i in range(10)
    ]
    client = _FakeClient(hops)
    with pytest.raises(RuntimeError, match="too many redirects"):
        await httpx_stream_capped(client, "https://example.com/0", raise_for_status=True)
