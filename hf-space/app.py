"""
Web Search MCP — Hugging Face Space entry point.

Builds a minimal Gradio Blocks UI (so the HF Gradio SDK is happy) and
mounts the `free-search-mcp` MCP-over-HTTP server on the same FastAPI
app at `/mcp/` and `/gradio_api/mcp/`.

Why Gradio + MCP v2? Gradio's built-in `mcp_server=True` requires `mcp`
v1, but `free-search-mcp` requires `mcp` v2. So we use Gradio only for
the landing page and run the upstream MCP server as a mounted ASGI app.

To run locally:
  PORT=38472 uv run --with gradio --with mcp --with uvicorn --with fastapi python hf-space/app.py
"""
from __future__ import annotations

import os
from contextlib import asynccontextmanager

import gradio as gr
from fastapi import FastAPI

from search_mcp import server as search_server


# Disable the searx rescue fallback before importing the server. It silently
# misattributes results to the requested engine and is dead infrastructure.
# Force HTTP-only fetching so we never try to use a browser on the free tier.
os.environ.setdefault("SEARCH_MCP_RESCUE_ENABLED", "false")
os.environ.setdefault("SEARCH_MCP_RESCUE_ENGINES", "[]")
os.environ.setdefault("SEARCH_MCP_FETCH_STRATEGY", "http")


LANDING_MD = """\
# 🔍 Web Search MCP

A **multi-engine web search** Space exposed as an MCP server. No API key,
no signup — point your MCP client at this URL and go.

## Connect

**Endpoint:** `https://artfix-web-search-mcp.hf.space/gradio_api/mcp/`

This single URL works for Hermes, Reachy Mini, Claude Desktop, Codex,
and any MCP client that speaks streamable-http.

| Client | How to connect |
|---|---|
| Hermes / generic MCP v2 | `https://artfix-web-search-mcp.hf.space/gradio_api/mcp/` |
| Claude Desktop / Codex | `{"mcpServers":{"websearch":{"url":"https://artfix-web-search-mcp.hf.space/gradio_api/mcp/"}}}` |
| Reachy Mini app | `reachy-mini-conversation-app tool-spaces add artfix/web-search-mcp` |

**Note:** this Space speaks **MCP protocol v2** (streamable-http,
revision 2026-07-28). Clients that only support MCP v1 (older Gradio
integrations) will not discover the tools.

## Tools (10)

- `search` — multi-engine web search, RRF-merged, deduped
- `research` — search + fetch top N, Markdown brief
- `fetch` — reader-mode Markdown for any URL
- `fetch_batch` — concurrent multi-URL fetch (max 20)
- `compare` — side-by-side excerpts for 2-5 URLs
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

Browser-rendered engines (`startpage`, `brave`, `google`, `baidu`,
`zhihu`) and API-key engines (`brave_api`, `serper`, `tavily`,
`google_cse`, `github_code`) are disabled on this free-tier CPU build.

## Quick test

```bash
curl -X POST https://artfix-web-search-mcp.hf.space/gradio_api/mcp/ \\
  -H 'Content-Type: application/json' \\
  -d '{"jsonrpc":"2.0","id":1,"method":"tools/list"}'
```

## Limits

- Free-tier CPU Space: 2 vCPU, 16 GB RAM.
- No auth on the endpoint.
- 7-day page cache, 256 MB cache cap.
- `gdelt` is slow (12–15s) and sometimes returns zero results.

Forked from [sweetcornna/free-search-mcp](https://github.com/sweetcornna/free-search-mcp). MIT license.
"""


# Upstream MCP v2 server, already wired with a `/mcp` route.
mcp_app = search_server.mcp.streamable_http_app()


async def _mcp_mount(scope, receive, send) -> None:
    """ASGI mount target: rewrite the stripped path back to `/mcp` and call
    the upstream MCP server."""
    rewritten_scope = dict(scope)
    rewritten_scope["path"] = "/mcp"
    rewritten_scope["raw_path"] = b"/mcp"
    await mcp_app(rewritten_scope, receive, send)


@asynccontextmanager
async def lifespan(app):
    """Run the upstream MCP server's lifespan (starts the session manager)."""
    async with mcp_app.router.lifespan_context(app):
        yield


with gr.Blocks(title="Web Search MCP", analytics_enabled=False) as demo:
    gr.Markdown(LANDING_MD)


# Build a parent FastAPI app, mount the MCP endpoint FIRST, then mount the
# Gradio UI at root. Mount order matters: the first prefix match wins.
parent = FastAPI(title="Web Search MCP", docs_url=None, redoc_url=None, lifespan=lifespan)
parent.mount("/mcp", _mcp_mount)
parent.mount("/gradio_api/mcp", _mcp_mount)

app = gr.mount_gradio_app(app=parent, blocks=demo, path="/")


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        app,
        host="0.0.0.0",
        port=int(os.environ.get("PORT", "38472")),
        log_level=os.environ.get("LOG_LEVEL", "info"),
    )
