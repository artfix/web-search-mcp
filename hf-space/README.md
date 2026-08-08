---
title: Web Search MCP
emoji: 🔍
colorFrom: blue
colorTo: green
sdk: docker
app_port: 7860
pinned: false
license: mit
short_description: Multi-engine web search via MCP — 18 engines, no API key.
---

# Web Search MCP

Multi-engine web search, page fetching, and document reading exposed as a
[Model Context Protocol](https://modelcontextprotocol.io) endpoint. No API
key, no signup — point your MCP client at the URL and go.

This is a fork of [`sweetcornna/free-search-mcp`](https://github.com/sweetcornna/free-search-mcp)
packaged as a Hugging Face Space. The Space runs the server in **HTTP
transport** mode on port 7860, so any MCP client can reach it over the
network.

## Tools exposed (10)

| Tool | What it does |
|---|---|
| `search` | Multi-engine search, RRF-merged, deduped, with optional filters (freshness, category, include/exclude domains) |
| `research` | One-shot: search + fetch top N results, return a Markdown brief |
| `fetch` | Reader-mode Markdown for any URL, with optional Playwright render |
| `fetch_batch` | Concurrent multi-URL fetch (max 20) |
| `compare` | Concurrent fetch of 2–5 URLs, side-by-side excerpts |
| `read_doc` | Parse PDF/DOCX/XLSX/PPTX/EPUB/CSV/zip-tar |
| `extract_structured` | JSON-LD, OpenGraph, microdata |
| `cache_search` | FTS5 search across previously fetched pages |
| `engines` | List available engines |
| `download` | Save a file to the Space's cache (24h TTL) |

## Engines (18 keyless, 0 API key)

Default pool: `duckduckgo`, `mojeek`, `googlenews`, `bing`, `anysearch`.

Verticals enabled by category:
- **paper** → `arxiv`, `openalex`, `crossref`, `pubmed`
- **github** → `github` (repos + issues)
- **forum** → `stackexchange`, `hackernews`
- **news** → `googlenews`, `gdelt`
- **wiki** → `wikipedia`
- **books** → `openlibrary`
- **image** → `openverse`
- **dataset** → `zenodo`
- **video** → `bilibili`
- **chinese-web** → `sogou`, `so360`

Not enabled (require either Playwright/Chromium or an API key):
- `startpage`, `brave`, `google`, `serpsearch`, `baidu`, `zhihu` — need
  Chromium
- `searx` — public SearXNG instances don't answer in 2026
- `brave_api`, `serper`, `tavily`, `google_cse`, `github_code` — need API keys

## Use from your MCP client

### curl / any HTTP client

```bash
curl -X POST https://artfix-web-search-mcp.hf.space/mcp \
  -H 'Content-Type: application/json' \
  -H 'Accept: application/json, text/event-stream' \
  -d '{
    "jsonrpc": "2.0",
    "id": 1,
    "method": "tools/call",
    "params": {
      "name": "search",
      "arguments": {
        "query": "python programming language",
        "max_results": 5
      }
    }
  }'
```

### Claude Desktop / Codex / any MCP client

```json
{
  "mcpServers": {
    "websearch": {
      "url": "https://artfix-web-search-mcp.hf.space/mcp"
    }
  }
}
```

### Hermes (terminal assistant)

```
hermes mcp add websearch --url https://artfix-web-search-mcp.hf.space/mcp
hermes mcp test websearch
```

## Configuration

All settings are env vars prefixed `SEARCH_MCP_` (see the upstream
[`.env.example`](https://github.com/sweetcornna/free-search-mcp/blob/main/.env.example)).
The Space's `Dockerfile` sets sensible defaults; override via HF Space
**Settings → Variables and secrets**.

Common knobs:

| Variable | Default | What it does |
|---|---|---|
| `SEARCH_MCP_DEFAULT_ENGINES` | 21 keyless engines | Which engines `search` queries by default |
| `SEARCH_MCP_RATE_LIMIT_PER_MINUTE` | 30 | Per-IP cap on `search` calls |
| `SEARCH_MCP_FETCH_RATE_LIMIT_PER_MINUTE` | 20 | Per-IP cap on `fetch` calls |
| `SEARCH_MCP_CACHE_TTL_SECONDS` | 604800 (7d) | Page cache lifetime |
| `SEARCH_MCP_CACHE_MAX_MB` | 256 | Cache file size cap |
| `SEARCH_MCP_HTTP_ALLOWED_ORIGINS` | (empty) | Comma-separated browser origins allowed to call `/mcp` (DNS-rebinding guard) |

## Limits and notes

- **Free tier CPU Space**: 2 vCPU, 16 GB RAM, 50 GB ephemeral disk. The
  image is ~400 MB without Chromium.
- **No auth on the endpoint**. Anyone with the URL can call the
  10 tools. For a private deployment, terminate with a reverse proxy
  (Caddy with `basicauth`, Cloudflare Access) in front of the Space.
- **Caching is per-Space**. Pages stay cached for 7 days. The Space's
  `/data` volume survives restarts but is wiped on factory rebuilds.
- **gdelt is slow** (12-15s per query) and sometimes silently returns
  zero results. Remove it from `SEARCH_MCP_DEFAULT_ENGINES` if you
  don't need global news coverage.

## License

MIT (inherited from the upstream project). Engine-specific terms
apply — see the upstream README's note about not defeating PoW
CAPTCHAs.
