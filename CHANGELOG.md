# Changelog

All notable changes to this project are documented here. The format is loosely
based on [Keep a Changelog](https://keepachangelog.com/), and the project follows
semantic versioning.

## [0.4.1] - 2026-07-08

First release actually on PyPI.

### Changed

- **Distribution renamed to `free-search-mcp`** — the name `search-mcp` turned
  out to be taken on PyPI. The import package (`search_mcp`), all three
  console scripts, and every env var (`SEARCH_MCP_*`) are unchanged; a new
  `free-search-mcp` console alias makes the bare `uvx free-search-mcp` start
  the server without `--from`.
- Release workflow publishes with a repo-secret API token (`PYPI_API_TOKEN`)
  instead of Trusted Publishing — pushing a `v*` tag is the entire release
  process.

## [0.4.0] - 2026-07-08

Keyless-search reliability + one-command deploy. Focus: no result should be
silently lost, and `uvx free-search-mcp` should give any agent working search with
zero setup.

### Added

**Keyless search reliability:**
- **Aggregation-level rescue.** When a fresh search comes back empty — or
  nearly empty with demonstrably unhealthy engines (errors, CAPTCHA gates, or
  a silent zero) — the aggregator runs one bounded recovery pass via
  `SEARCH_MCP_RESCUE_ENGINES` (default `searx` → `bing`, capped at
  `SEARCH_MCP_RESCUE_TIMEOUT`, default 10s). Rescue results merge via RRF
  with honest attribution and surface as `rescued_via`. A healthy sparse
  result never triggers it, so the normal path pays nothing. This uniformly
  protects a gated DuckDuckGo (previously unprotected) and replaces the
  per-engine searx fallbacks inside `google`/`bing`.
- **Bounded retry on the keyless HTTP path.** One retry for connection errors
  (0.4–0.8s jittered) and 429/5xx (honoring `Retry-After` up to 3s); timeouts
  are never retried. Happy path unchanged.
- **Silent-block visibility.** An engine returning 0 results with no error
  and no detected gate (the Mojeek-IP-block failure mode) now surfaces as
  `empty_engines` + an actionable hint when results are sparse.
- **`bing` joins the default pool** (`duckduckgo`, `mojeek`, `googlenews`,
  `bing`) — its www4 edge answers over plain HTTP in ~0.3s, and with the
  per-engine searx race gone a gated bing costs one fast attempt.
- **Cache eviction.** Expired rows are now actually deleted, and the cache
  file is capped at `SEARCH_MCP_CACHE_MAX_MB` (default 512, 0 disables) by
  dropping the oldest pages + one VACUUM — opportunistic (init + every 200
  writes), no background tasks.

**Deploy:**
- **PyPI packaging + `uvx free-search-mcp`.** Full metadata (urls, classifiers,
  PEP 639 license), verified end-to-end: `claude mcp add search -- uvx
  search-mcp` gives an agent working keyless search with zero config.
- **GitHub Actions.** `ci.yml` (ruff + offline pytest on Python
  3.11/3.12/3.13) and `release.yml` (tag-triggered build + PyPI publish via
  Trusted Publishing/OIDC — no token stored in the repo).
- **Config-dir `.env`.** Settings also load from `~/.config/search-mcp/.env`
  so uvx installs launched from any directory stay configurable. Precedence:
  real env > `./.env` > config-dir `.env`.
- **Admin UI opens the browser automatically** (`SEARCH_MCP_ADMIN_NO_BROWSER=1`
  to suppress).

### Fixed

- **Gate diagnostics finally render.** `gated_engines`/`gated_hint` were
  attached to the payload but never rendered in markdown mode (the default),
  so callers never saw WHY an engine returned nothing. Gate/silent/rescue
  hints now render in both the results and no-results branches.
- **Missing Chromium degrades gracefully.** A never-downloaded browser now
  raises one actionable error carrying the exact install command
  (`uvx --from free-search-mcp playwright install chromium`), memoized instead of
  re-starting the Playwright driver per attempt; engines record an honest
  `browser_unavailable` gate instead of stack traces. HTTP-only search and
  fetching keep working without Chromium.
- **SSRF DNS lookups no longer block the event loop.** The guard resolves via
  `loop.getaddrinfo` on all async paths (initial URL + every redirect hop).
- **`install.sh` installs Chromium's OS deps on Linux** (`--with-deps`, with
  a plain-install fallback) — browser-rendered engines no longer crash on a
  clean Linux host.

### Changed

- The triplicated redirect-following GET loop (fetcher/documents/structured)
  is consolidated into `httpfetch.py` — one SSRF-checked, size-capped loop
  with curl_cffi and httpx flavors.
- Dependencies: dropped unused `tenacity`; declared direct `w3lib`.
- README hero GIF is now committed and referenced by absolute URL (renders on
  PyPI and fresh clones).

### Known debt

- `documents._source_chars_consumed` mirrors `formatting.smart_truncate`
  boundary logic (fragile coupling; behavior-neutral refactor pending).
- Page metadata rides in the cache `title` column behind a sentinel rather
  than a schema change (deliberate, back-compat).

## [0.3.0] - 2026-06-16

No-API-key usability audit + fixes. Focus: the default keyless path
(duckduckgo + mojeek + googlenews) and the opt-in keyless engines.

### Fixed

**Result quality (default keyless path):**
- **GoogleNews URLs are now readable.** `news.google.com/.../articles/CBM…` links
  resolved to an empty JS shell over both HTTP and a headless browser, so
  `fetch`/`research` returned zero content for every news result. They are now
  decoded to the real publisher URL via Google's `batchexecute` RPC (memoised,
  best-effort). News `research` went from empty shells to full publisher text.
- **DuckDuckGo no longer double-counts every result.** The `div.result,
  div.web-result` selector matched each organic row twice (rows carry both
  classes), doubling DDG's weight in the RRF merge and skewing ranking. Now
  selects `div.result` once with a URL-dedup guard.
- **Title-dedup keeps version/year/quantity variants.** "Python 3.13 released" vs
  "3.12", "best … 2026" vs "2025" scored ≥92 and the second was silently
  dropped; a digit-token guard now keeps them as distinct results.
- **Lead snippet** attributes GoogleNews items to the real outlet (from the
  "(Reuters)" suffix) instead of "news.google.com".

**Fetch / document path:**
- **PDF/DOCX URLs are parsed, not garbled.** `fetch`/`research` on a binary
  document URL returned `U+FFFD` garbage; they now route to the document parser.
- **Charset is honored.** Non-UTF-8 pages (GBK/Big5/Shift-JIS — common for
  baidu/zhihu hits) were decoded as UTF-8 (mojibake); now decoded per the
  Content-Type/`<meta>` charset.
- **read_doc degrades to HTML** when a `.pdf`/`.docx` URL actually serves an
  HTML login wall / soft-404 instead of crashing.
- Short non-HTML responses no longer trigger a needless browser render that
  mislabels them `text/html`.

**Engines:**
- **Bing is HTTP-first** (~0.3s) instead of always browser-rendered (~15s), with
  the browser kept only as a gate fallback.
- **SearXNG instance list refreshed** (the old five were all dead), now
  operator-overridable via `SEARCH_MCP_SEARX_INSTANCES`. This also re-arms the
  google/bing keyless fallbacks that depend on it.
- **Baidu** returns the real destination (`mu` attribute) instead of the opaque
  `baidu.com/link?url=` redirector; **AnySearch** snippets are capped instead of
  dumping the full page body; **Bilibili** upgrades `http://` watch URLs to https.

**Honesty / diagnostics:**
- A gated DuckDuckGo anomaly/CAPTCHA page (HTTP 202) is now detected, so the #1
  default engine reports an honest hint instead of a silent empty.
- SearXNG records a `no_live_instance` gate reason when every instance is dead.
- Tool docstrings corrected: `engines()` no longer lists a stale 7-engine subset;
  `category="github"` documents all forges; `freshness` is described as
  best-effort; `category="news"` documents its whitelist; a keyless-recovery hint
  was added. Token estimator widened to cover CJK punctuation/Extension-A.

**Lower-priority robustness:**
- **Bad/expired API keys raise an actionable error** (HTTP 401/403/422/429)
  instead of a silent empty, for brave_api/serper/tavily/google_cse. Transient
  5xx still degrades to empty.
- **Freshness no longer over-drops.** An absolute date scraped from snippet text
  ("…founded in 2009…") is treated as display-only; only relative "N ago" phrases
  and structured RSS/API dates are trusted to drop a result under a freshness
  filter.
- `read_doc`/`extract_structured` raise a clean `UnsafeURLError` for an invalid
  port instead of leaking a bare `ValueError`.
- `httpx[socks]` is now a dependency, so the documented `socks5://` proxy works
  for `extract_structured`/`read_doc`; `extract_structured` also honors page
  charset. SSRF-guard docstring corrected to describe the real per-hop check.
- `use_cache` docstring now notes the cache key includes all active filters.

## [0.2.0] - 2026-06-01

A large feature release: 9 new search engines (keyless + keyed), a local admin
backend for configuration, and a full set of fixes for provider-gated engines
(proxy, SearXNG fallback, gate diagnostics, Zhihu login).

### Added

**Keyless engines** (no API key, opt-in via `engines=[...]`):
- `google` — Google web SERP scraper (HTTP + browser fallback).
- `serpsearch` — alias of `google` (keyless Google SERP).
- `anysearch` — AnySearch unified-search REST API (anonymous tier; optional key
  lifts limits).
- `bilibili` — Bilibili (哔哩哔哩) video search via the public JSON API.
- `zhihu` — Zhihu (知乎) search, browser-rendered (best-effort; see login below).

**Keyed engines** (dormant until a key is configured):
- `brave_api` (Brave Search API), `serper` (Serper/Google), `tavily` (Tavily AI
  search), `google_cse` (Google Custom Search — needs API key + cx).

**Admin backend & configuration:**
- `search-mcp-admin` — a localhost-only web UI to enter API keys and a proxy,
  with a per-provider "how to get a key" guide, masked inputs, live Save (no
  restart), Test, and Clear. Secrets are stored at `~/.config/search-mcp/config.json`
  (`0600`) and never echoed back to the page.
- `keystore` module: hot-reloaded JSON config with `SEARCH_MCP_*` env override.
- `.env` keys are loaded at server/admin startup.

**Gated-engine fixes (proxy · fallback · diagnostics · login):**
- Optional **proxy** support (`SEARCH_MCP_PROXY` / admin "Network / Proxy" card)
  applied to HTTP engines, the browser pool, and remote fetch/document/structured
  calls. Scope with `SEARCH_MCP_PROXY_ENGINES`. (`http`/`https`/`socks5`.)
- **SearXNG auto-fallback**: `google`/`serpsearch`/`bing` transparently recover
  via the working `searx` meta-search when CAPTCHA-gated; results attributed to
  `searx`.
- **Gate diagnostics**: responses include `gated_engines` + `gated_hint`
  (`captcha`/`consent`/`login`).
- `search-mcp-login` — one-time interactive Zhihu login; cookies persist so
  later headless searches work.
- Transient browser navigation errors are retried once.

**Deploy & docs:**
- One-click `scripts/install.sh`, `Dockerfile` + `docker-compose.yml`,
  project-scoped `.mcp.json`, annotated `.env.example`.
- New docs: `docs/USAGE.md`, `docs/API_KEYS.md`, `docs/PROXY_AND_GATES.md`.

### Fixed
- `anysearch` response mapping (results are nested under `data.results`).

### Notes
- Keyless defaults (`duckduckgo`, `mojeek`, `googlenews`) are unchanged; all new
  engines are opt-in to preserve the fast default-pool latency.
- We deliberately do not attempt to defeat provider CAPTCHAs (ToS); the proxy and
  fallback are the supported ways around datacenter-IP gating.

## [0.1.0]

- Initial release: multi-engine keyless search, smart fetch (httpx → Playwright),
  document reading, FTS5 cache, filters, and LLM-tuned Markdown output.

[0.2.0]: https://github.com/sweetcornna/free-search-mcp/releases/tag/v0.2.0
