"""Default-on ephemeral downloads and their filesystem safety boundary."""

from __future__ import annotations

import time
from pathlib import Path

import pytest

from search_mcp import downloads
from search_mcp.config import settings


@pytest.fixture
def enabled(tmp_path, monkeypatch) -> Path:
    root = tmp_path / "dl"
    root.mkdir()
    monkeypatch.setattr(settings, "download_enabled", True)
    monkeypatch.setattr(settings, "download_dir", root)
    return root


# ---------------------------------------------------------------------------
# Default policy and operator overrides
# ---------------------------------------------------------------------------


def test_enabled_by_default(monkeypatch, tmp_path):
    cache_dir = tmp_path / "cache"
    monkeypatch.setattr(settings, "download_enabled", True)
    monkeypatch.setattr(settings, "download_dir", None)
    monkeypatch.setattr(settings, "cache_dir", cache_dir)

    assert downloads.require_download_dir() == cache_dir / "downloads"


def test_default_directory_tracks_active_cache_root(monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "download_enabled", True)
    monkeypatch.setattr(settings, "download_dir", None)
    monkeypatch.setattr(settings, "cache_dir", tmp_path / "first")
    assert downloads.download_dir() == tmp_path / "first" / "downloads"

    monkeypatch.setattr(settings, "cache_dir", tmp_path / "second")
    assert downloads.download_dir() == tmp_path / "second" / "downloads"


def test_resolving_default_directory_does_not_create_it(monkeypatch, tmp_path):
    cache_dir = tmp_path / "cache"
    monkeypatch.setattr(settings, "download_enabled", True)
    monkeypatch.setattr(settings, "download_dir", None)
    monkeypatch.setattr(settings, "cache_dir", cache_dir)

    root = downloads.download_dir()
    assert root == cache_dir / "downloads"
    assert not root.exists()

    path = downloads.save("https://x.example/a.bin", b"data")
    assert path.parent == root.resolve()


def test_configured_directory_overrides_default(monkeypatch, tmp_path):
    custom = tmp_path / "configured"
    monkeypatch.setattr(settings, "download_enabled", True)
    monkeypatch.setattr(settings, "download_dir", custom)

    assert downloads.require_download_dir() == custom


def test_explicit_disable_overrides_custom_directory(monkeypatch, tmp_path):
    custom = tmp_path / "configured"
    monkeypatch.setattr(settings, "download_enabled", False)
    monkeypatch.setattr(settings, "download_dir", custom)

    assert downloads.download_dir() is None
    with pytest.raises(PermissionError, match="DOWNLOAD_ENABLED=false"):
        downloads.require_download_dir()
    with pytest.raises(PermissionError, match="DOWNLOAD_ENABLED=false"):
        downloads.save("https://x.example/a.bin", b"data")
    assert not custom.exists()


# ---------------------------------------------------------------------------
# Filename safety — the remote controls this string
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "url",
    [
        "https://x.example/../../etc/passwd",
        "https://x.example/%2e%2e%2f%2e%2e%2fetc%2fpasswd",
        "https://x.example/a/b/../../../../root/.ssh/id_rsa",
        "https://x.example/..%5c..%5cwindows%5csystem32",
    ],
)
def test_traversal_attempts_collapse_to_one_component(url):
    name = downloads.safe_filename(url)
    assert "/" not in name
    assert "\\" not in name
    assert ".." not in name


def test_saved_path_stays_inside_the_download_dir(enabled: Path):
    path = downloads.save("https://x.example/../../escape.txt", b"x")
    assert path.parent == enabled.resolve()


def test_filename_is_content_addressed_so_collisions_cannot_overwrite(enabled: Path):
    first = downloads.save("https://a.example/report.pdf", b"one")
    second = downloads.save("https://b.example/report.pdf", b"two")
    assert first != second
    assert first.read_bytes() == b"one"
    assert second.read_bytes() == b"two"


def test_identical_bytes_reuse_one_file(enabled: Path):
    a = downloads.save("https://a.example/x.bin", b"same")
    b = downloads.save("https://a.example/x.bin", b"same")
    assert a == b


def test_long_names_are_capped(enabled: Path):
    path = downloads.save(f"https://x.example/{'n' * 500}.bin", b"x")
    assert len(path.name) < 120


def test_extension_is_inferred_from_media_type_when_missing():
    name = downloads.safe_filename("https://x.example/download", "image/png", b"x")
    assert name.endswith(".png")


def test_nameless_url_still_produces_a_file(enabled: Path):
    path = downloads.save("https://x.example/", b"x")
    assert path.is_file()


# ---------------------------------------------------------------------------
# Size cap
# ---------------------------------------------------------------------------


def test_oversized_download_is_refused(enabled: Path, monkeypatch):
    monkeypatch.setattr(settings, "download_max_mb", 1)
    with pytest.raises(ValueError, match="over the 1 MB"):
        downloads.save("https://x.example/big.bin", b"x" * (2 * 1024 * 1024))


def test_nothing_is_written_when_the_size_cap_trips(enabled: Path, monkeypatch):
    monkeypatch.setattr(settings, "download_max_mb", 1)
    with pytest.raises(ValueError):
        downloads.save("https://x.example/big.bin", b"x" * (2 * 1024 * 1024))
    assert list(enabled.iterdir()) == []


# ---------------------------------------------------------------------------
# Expiry
# ---------------------------------------------------------------------------


def test_expired_files_are_purged(enabled: Path, monkeypatch):
    monkeypatch.setattr(settings, "download_ttl_hours", 24)
    path = downloads.save("https://x.example/old.bin", b"x")
    old = time.time() - 25 * 3600
    import os

    os.utime(path, (old, old))

    assert downloads.purge_expired() == 1
    assert not path.exists()


def test_fresh_files_survive_the_purge(enabled: Path, monkeypatch):
    monkeypatch.setattr(settings, "download_ttl_hours", 24)
    path = downloads.save("https://x.example/new.bin", b"x")
    assert downloads.purge_expired() == 0
    assert path.exists()


def test_ttl_zero_disables_expiry(enabled: Path, monkeypatch):
    monkeypatch.setattr(settings, "download_ttl_hours", 0)
    path = downloads.save("https://x.example/keep.bin", b"x")
    import os

    old = time.time() - 999 * 3600
    os.utime(path, (old, old))
    assert downloads.purge_expired() == 0
    assert path.exists()


def test_purge_is_a_noop_when_downloads_are_disabled(monkeypatch):
    monkeypatch.setattr(settings, "download_enabled", False)
    assert downloads.purge_expired() == 0


def test_purge_is_a_noop_when_directory_is_missing(monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "download_enabled", True)
    monkeypatch.setattr(settings, "download_dir", tmp_path / "missing")

    assert downloads.purge_expired() == 0


def test_purge_ignores_subdirectories(enabled: Path, monkeypatch):
    monkeypatch.setattr(settings, "download_ttl_hours", 1)
    sub = enabled / "nested"
    sub.mkdir()
    import os

    old = time.time() - 999 * 3600
    os.utime(sub, (old, old))
    assert downloads.purge_expired() == 0
    assert sub.exists()


# ---------------------------------------------------------------------------
# MCP tool policy
# ---------------------------------------------------------------------------


async def test_default_tool_call_saves_without_prompting(monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "download_enabled", True)
    monkeypatch.setattr(settings, "download_dir", tmp_path / "dl")

    from search_mcp import fetcher
    from search_mcp.server import download

    async def fake_fetch_bytes(url):
        return fetcher.FetchResult(
            url=url,
            title="a.bin",
            content="",
            method="asset",
            truncated=False,
            media_type="application/octet-stream",
            bytes_size=4,
            sha256="abc",
            data=b"data",
        )

    monkeypatch.setattr("search_mcp.server.fetch_bytes", fake_fetch_bytes)

    out = await download("https://x.example/a.bin", format="json")

    def _on_disk(path: str) -> bytes:
        return Path(path).read_bytes()

    assert _on_disk(out["saved_path"]) == b"data"
    assert out["expires_in_hours"] == settings.download_ttl_hours


async def test_tool_ttl_zero_reports_cleanup_is_disabled(monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "download_enabled", True)
    monkeypatch.setattr(settings, "download_dir", tmp_path / "dl")
    monkeypatch.setattr(settings, "download_ttl_hours", 0)

    from search_mcp import fetcher
    from search_mcp.server import download

    async def fake_fetch_bytes(url):
        return fetcher.FetchResult(
            url=url,
            title="a.bin",
            content="",
            method="asset",
            truncated=False,
            media_type="application/octet-stream",
            bytes_size=4,
            sha256="abc",
            data=b"data",
        )

    monkeypatch.setattr("search_mcp.server.fetch_bytes", fake_fetch_bytes)

    out = await download("https://x.example/a.bin")

    assert "TTL cleanup is disabled" in out
    assert "0h" not in out


async def test_explicit_disable_rejects_before_network(monkeypatch):
    monkeypatch.setattr(settings, "download_enabled", False)
    from search_mcp.server import download

    called = False

    async def unexpected_fetch(url):
        nonlocal called
        called = True
        raise AssertionError(f"fetched {url} while downloads were disabled")

    monkeypatch.setattr("search_mcp.server.fetch_bytes", unexpected_fetch)

    with pytest.raises(PermissionError, match="DOWNLOAD_ENABLED=false"):
        await download("https://x.example/a.bin", format="json")
    assert called is False
