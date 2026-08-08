# Hugging Face Space — MCP-over-HTTP endpoint for free-search-mcp.
#
# What this is:
#   A Docker HF Space that exposes the free-search-mcp server as an
#   MCP endpoint at /mcp. Any MCP client (Hermes, Claude Desktop, Codex,
#   Reachy Mini, custom apps) points at the Space URL and gets the
#   10 search tools (search, research, fetch, compare, ...).
#
# What's enabled:
#   The 18 KEYLESS engines verified to work in August 2026. No API
#   keys required. No playwright/chromium (would push the image over
#   1 GB and exceed the free-tier CPU space's 16 GB RAM during install
#   for marginal benefit).
#
# What's NOT enabled (and why):
#   - mojeek, google, serpsearch, startpage, brave, baidu, sogou, zhihu:
#     all keyless, but require playwright/chromium.
#   - gdelt: enabled but slow (12-15s) and sometimes silently returns
#     zero results. Kept on for the global-news use case; disable by
#     removing it from SEARCH_MCP_DEFAULT_ENGINES below if you don't
#     want the latency.
#   - searx: dead in 2026 (no public instance answers). Rescue chain
#     disabled so the aggregator doesn't waste a round-trip on it.
#   - brave_api, serper, tavily, google_cse, github_code: all need API
#     keys. Add them via HF Space secrets (Settings → Secrets) and
#     extend the env block below to enable.
#
# HF Spaces conventions:
#   - Port 7860 is the HF-recommended default; the Space frontend will
#     expect it. Override with PORT env if you need a different one.
#   - The Space needs to reach the internet to call search engines;
#     HF allows egress on free tier.
#   - The endpoint is UNAUTHENTICATED. If you want auth, terminate it
#     with a reverse proxy (Caddy, Cloudflare Access) in front.
#
# To run locally for testing:
#   docker build -t web-search-mcp-hf ./hf-space
#   docker run --rm -p 7860:7860 web-search-mcp-hf
#   # Then: curl -X POST http://localhost:7860/mcp -H 'Content-Type: application/json' \
#   #   -d '{"jsonrpc":"2.0","id":1,"method":"tools/list"}'

FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    UV_LINK_MODE=copy \
    UV_COMPILE_BYTECODE=1 \
    SEARCH_MCP_CACHE_DIR=/data \
    # Engine selection — the 18 keyless engines that work without
    # playwright or API keys. Edit this list to taste. See README.
    SEARCH_MCP_DEFAULT_ENGINES='["duckduckgo","mojeek","googlenews","bing","searx","anysearch","sogou","so360","arxiv","openalex","crossref","pubmed","github","stackexchange","hackernews","wikipedia","openlibrary","gdelt","openverse","zenodo","bilibili"]' \
    # Rescue chain: don't try searx as a fallback for gated engines —
    # public SearXNG instances are dead in 2026.
    SEARCH_MCP_RESCUE_ENABLED=false \
    SEARCH_MCP_RESCUE_ENGINES='[]' \
    # Conservative timeouts for free-tier CPU spaces (2 vCPU, 16 GB).
    SEARCH_MCP_REQUEST_TIMEOUT=10.0 \
    SEARCH_MCP_FETCH_TIMEOUT=20.0 \
    # Cache: 7 days default, 256 MB cap to stay under free-tier disk.
    SEARCH_MCP_CACHE_TTL_SECONDS=604800 \
    SEARCH_MCP_CACHE_MAX_MB=256 \
    # Rate limits: tight so one client can't DoS the space.
    SEARCH_MCP_RATE_LIMIT_PER_MINUTE=30 \
    SEARCH_MCP_FETCH_RATE_LIMIT_PER_MINUTE=20 \
    # Use the safe-by-default DNS-rebinding guard.
    SEARCH_MCP_HTTP_ALLOWED_ORIGINS=''

# uv (fast Python package manager).
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

WORKDIR /app

# Install deps first (better layer caching), then the source.
COPY pyproject.toml uv.lock README.md ./
COPY src ./src
RUN uv sync --frozen --no-dev

# Persistent cache across Space restarts.
VOLUME /data

# Expose the HF-recommended port. The HF Space runtime will set PORT
# automatically; we bind 0.0.0.0:7860 by default.
EXPOSE 7860

# Streamable HTTP transport. Revision 2026-07-28 is stateless, so any
# replica can answer any request — fine for horizontal scale.
CMD ["uv", "run", "search-mcp", "--transport", "streamable-http", "--host", "0.0.0.0", "--port", "7860"]
