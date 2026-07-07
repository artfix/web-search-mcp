from pathlib import Path
from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

from .keystore import config_dir

DEFAULT_CACHE_DIR = Path.home() / ".cache" / "search-mcp"


class Settings(BaseSettings):
    # Two .env locations so uvx/PyPI installs (no project checkout) have a
    # stable config file. pydantic-settings gives LATER entries precedence,
    # matching keystore.load_all_env_files: real env > ./.env > config-dir .env.
    # (config_dir respects SEARCH_MCP_CONFIG_DIR; keystore is stdlib-only, so
    # no import cycle.)
    model_config = SettingsConfigDict(
        env_prefix="SEARCH_MCP_",
        env_file=(str(config_dir() / ".env"), ".env"),
        extra="ignore",
    )

    cache_dir: Path = DEFAULT_CACHE_DIR
    cache_ttl_seconds: int = 60 * 60 * 24 * 7
    # Size cap on the SQLite cache file (db + WAL). Enforced opportunistically
    # (connection init + every N writes) by dropping the oldest pages rows and
    # vacuuming; 0 disables the cap. Expired rows are purged on the same cadence.
    cache_max_mb: int = 512

    # All-HTTP, low-latency default pool. Picked for "consistently fast AND
    # consistently returns results in 2026":
    #   * duckduckgo  — curl_cffi chrome131 fingerprint dodges anomaly 202s
    #   * mojeek      — independent index; intermittently IP-blocked but cheap
    #                   to attempt and falls back fast when it is
    #   * googlenews  — RSS, ~1s, gives news-skewed coverage that complements
    #                   the other two on time-sensitive queries
    #   * bing        — www4 edge serves organic results over plain HTTP in
    #                   ~0.3s; safe as a default now that its old built-in
    #                   searx race moved to the aggregator rescue (a gated
    #                   bing costs one fast HTTP attempt, same as the others)
    # Searx public instances are unreliable (often ≥10s timeouts/empties) and
    # Startpage forces a browser render — both stay opt-in via `engines=`
    # (searx also serves as the first rescue engine, see rescue_engines).
    default_engines: list[str] = ["duckduckgo", "mojeek", "googlenews", "bing"]
    max_results_per_engine: int = 10

    # Public SearXNG instances rot constantly (DNS death, 429 walls, disabled
    # backends). An operator can pin a known-good instance (or several) via
    # SEARCH_MCP_SEARX_INSTANCES (comma/space separated, e.g.
    # "https://searx.be https://priv.au"); when set it takes precedence over the
    # built-in shortlist the searx engine races. searx is also the keyless
    # fallback for the google/bing scrapers, so a live instance here re-arms
    # those too.
    searx_instances: str = ""

    # Aggregation-level keyless rescue. When a FRESH search comes back empty —
    # or nearly empty while demonstrably unhealthy (engine errors, gates, or a
    # silent zero) — the aggregator makes one bounded recovery attempt via
    # these engines, in order, first hit wins. A healthy sparse result never
    # triggers it, so the normal path pays nothing.
    rescue_enabled: bool = True
    rescue_engines: list[str] = ["searx", "bing"]
    rescue_timeout: float = 10.0

    rate_limit_per_minute: int = Field(default=30, gt=0)
    fetch_rate_limit_per_minute: int = Field(default=20, gt=0)

    request_timeout: float = 15.0
    fetch_timeout: float = 25.0

    user_agent: str = (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/131.0.0.0 Safari/537.36"
    )
    accept_language: str = "en-US,en;q=0.9"

    fetch_strategy: Literal["auto", "http", "browser"] = "auto"
    browser_headless: bool = True
    browser_pool_size: int = 2
    max_content_chars: int = 50_000

    safesearch: Literal["strict", "moderate", "off"] = "moderate"
    region: str = "us-en"

    log_level: str = "INFO"

    # --- Safety / sandbox knobs -------------------------------------------
    # SSRF guard escape hatch: when False (default) URLs that resolve to
    # loopback/link-local/private/reserved addresses are rejected.
    allow_private_hosts: bool = False
    # read_doc local-file sandbox root. None (default) DISABLES local file
    # reads entirely — the user opts in by pointing this at a directory.
    document_root: Path | None = None
    # Response-bomb guard: cap on remote response body bytes.
    max_response_bytes: int = 25_000_000
    # Decompression-bomb guard: max PDF pages to parse.
    max_pdf_pages: int = 200
    # Cap on extracted document text (distinct from max_content_chars, which
    # is the fetch-truncation knob for web pages).
    max_document_chars: int = 2_000_000

    def cache_path(self) -> Path:
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        return self.cache_dir / "cache.sqlite"


settings = Settings()
