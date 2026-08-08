---
title: Web Search MCP
emoji: 🔍
colorFrom: blue
colorTo: green
sdk: gradio
sdk_version: 6.0.0
app_port: 7860
pinned: false
license: mit
short_description: Multi-engine web search via MCP — 18 engines, zero API keys
---

# Web Search MCP

A public, keyless, multi-engine web-search [MCP](https://modelcontextprotocol.io/) server running on Hugging Face Spaces.

**Endpoint:** `https://artfix-web-search-mcp.hf.space/gradio_api/mcp/`

The Space is built with the **Gradio SDK** (Docker Spaces are blocked on this
free-tier account), but it does not use Gradio's built-in MCP integration.
Instead, it runs the upstream `free-search-mcp` server (MCP protocol v2,
streamable-http) mounted at `/gradio_api/mcp/`, plus a simple Gradio landing
page explaining how to connect.

## Connect

| Client | Configuration |
|---|---|
| **Hermes / generic MCP v2** | URL: `https://artfix-web-search-mcp.hf.space/gradio_api/mcp/` |
| **Claude Desktop / Codex** | `{"mcpServers":{"websearch":{"url":"https://artfix-web-search-mcp.hf.space/gradio_api/mcp/"}}}` |
| **Reachy Mini** | `reachy-mini-conversation-app tool-spaces add artfix/web-search-mcp` |

**Note:** this server speaks **MCP v2**. Clients that only support MCP v1
(older Gradio MCP integrations) will not discover the tools.

## Tools

- `search` — multi-engine web search, RRF-merged & deduped
- `research` — one-shot search + fetch, returns a Markdown brief
- `fetch` — reader-mode Markdown for any URL
- `fetch_batch` — concurrent multi-URL fetch (max 20)
- `compare` — side-by-side excerpts for 2–5 URLs
- `read_doc` — parse PDF/DOCX/XLSX/PPTX/EPUB/CSV/archives
- `extract_structured` — JSON-LD, OpenGraph, microdata
- `cache_search` — FTS5 search across cached pages
- `engines` — list available engines
- `download` — save a file to the Space cache (24h TTL)

## Engines (18 keyless)

Defaults: `duckduckgo`, `mojeek`, `googlenews`, `bing`, `anysearch`.

Verticals: `arxiv`, `openalex`, `crossref`, `pubmed`, `github`,
`stackexchange`, `hackernews`, `wikipedia`, `openlibrary`, `openverse`,
`zenodo`, `bilibili`, `sogou`, `so360`, `gdelt`.

Browser-rendered engines (`startpage`, `brave`, `google`, `baidu`, `zhihu`)
and API-key engines (`brave_api`, `serper`, `tavily`, `google_cse`,
`github_code`) are disabled on this free-tier CPU build.

## Quick test

```bash
curl -X POST https://artfix-web-search-mcp.hf.space/gradio_api/mcp/ \
  -H 'Content-Type: application/json' \
  -d '{"jsonrpc":"2.0","id":1,"method":"tools/list"}'
```

## Limits

- Free-tier CPU Space: 2 vCPU, 16 GB RAM.
- No authentication — anyone with the URL can call it.
- 7-day page cache, 256 MB cache cap.
- `gdelt` can be slow (12–15s) and sometimes returns zero results.

## License

MIT. Forked from [sweetcornna/free-search-mcp](https://github.com/sweetcornna/free-search-mcp).
