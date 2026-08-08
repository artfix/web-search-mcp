"""
Web Search MCP — Hugging Face Space / local entry point.

Serves:
  - a readable HTML landing page at `/` (Gradio Blocks, keeps HF's Gradio SDK
    happy);
  - the `free-search-mcp` MCP-over-HTTP server at `/gradio_api/mcp/`;
  - a local admin dashboard at `/admin` to edit settings and restart the
    service.

Why Gradio + MCP v2? Gradio's built-in `mcp_server=True` requires `mcp`
v1, but `free-search-mcp` requires `mcp` v2. So we use Gradio only for
the landing page and run the upstream MCP server as a mounted ASGI app.

To run locally:
  PORT=38472 uv run --with gradio --with mcp --with uvicorn --with fastapi python hf-space/app.py
"""
from __future__ import annotations

import os
import shlex
import subprocess
import sys
from contextlib import asynccontextmanager
from pathlib import Path

import gradio as gr
from fastapi import FastAPI, Form, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse

# Load local .env before importing the upstream server so settings can be
# overridden from disk.
HERE = Path(__file__).resolve().parent
ENV_FILE = HERE / ".env"
if ENV_FILE.exists():
    with ENV_FILE.open() as fh:
        for raw in fh:
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            key = key.strip()
            value = value.strip().strip('"').strip("'")
            if key and key not in os.environ:
                os.environ[key] = value

from search_mcp import server as search_server


# Default policy for the public/free build. Override via the admin page.
os.environ.setdefault("SEARCH_MCP_RESCUE_ENABLED", "false")
os.environ.setdefault("SEARCH_MCP_RESCUE_ENGINES", "[]")
os.environ.setdefault("SEARCH_MCP_FETCH_STRATEGY", "http")


LANDING_MD = """\
# 🔍 Web Search MCP

A **multi-engine web search** server exposed as an MCP endpoint. No API
key, no signup — point your MCP client at this URL and go.

## Connect

**Endpoint:** `https://artfix-web-search-mcp.hf.space/gradio_api/mcp/`

| Client | How to connect |
|---|---|
| Hermes / generic MCP v2 | `https://artfix-web-search-mcp.hf.space/gradio_api/mcp/` |
| Claude Desktop / Codex | `{"mcpServers":{"websearch":{"url":"https://artfix-web-search-mcp.hf.space/gradio_api/mcp/"}}}` |
| Reachy Mini app | `reachy-mini-conversation-app tool-spaces add artfix/web-search-mcp` |

**Local network** (if this server is running on your LAN): use
`http://<this-pc-ip>:38472/gradio_api/mcp/`.

**Note:** this server speaks **MCP protocol v2** (streamable-http,
revision 2026-07-28). Clients that only support MCP v1 (older Gradio
integrations) will not discover the tools.

## Admin

Local settings dashboard: [`/admin`](/admin)

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


# ---------------------------------------------------------------------------
# Admin dashboard — only useful when running locally; harmless on HF.
# ---------------------------------------------------------------------------

ADMIN_SETTINGS = [
    ("PORT", "Server port", "38472"),
    ("SEARCH_MCP_DEFAULT_ENGINES", "Default engines (JSON list)", '["duckduckgo","mojeek","googlenews","bing","anysearch"]'),
    ("SEARCH_MCP_FETCH_STRATEGY", "Fetch strategy (http|browser|auto)", "http"),
    ("SEARCH_MCP_RESCUE_ENABLED", "Enable rescue fallback (true|false)", "false"),
    ("SEARCH_MCP_RESCUE_ENGINES", "Rescue engines (JSON list)", "[]"),
    ("SEARCH_MCP_MAX_RESULTS", "Max results per engine", "10"),
    ("SEARCH_MCP_HTTP_TIMEOUT", "HTTP timeout seconds", "15"),
    ("SEARCH_MCP_REQUEST_DELAY", "Request delay seconds", "0"),
    ("SEARCH_MCP_CACHE_TTL_HOURS", "Cache TTL hours", "168"),
    ("SEARCH_MCP_CACHE_MAX_MB", "Cache max MB", "256"),
    ("SEARCH_MCP_LOG_LEVEL", "Log level", "info"),
]


def _read_env() -> dict[str, str]:
    values: dict[str, str] = {}
    if ENV_FILE.exists():
        with ENV_FILE.open() as fh:
            for raw in fh:
                line = raw.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, _, value = line.partition("=")
                key = key.strip()
                value = value.strip()
                # Strip only matching outer quotes; leave JSON arrays intact.
                if (value.startswith('"') and value.endswith('"')) or (
                    value.startswith("'") and value.endswith("'")
                ):
                    value = value[1:-1]
                if key:
                    values[key] = value
    return values


def _write_env(values: dict[str, str]) -> None:
    lines = ["# Web Search MCP local settings", ""]
    for key, label, default in ADMIN_SETTINGS:
        val = values.get(key, default)
        lines.append(f"# {label}")
        # Bare values; only quote strings that contain spaces or special chars.
        if " " in val or any(c in val for c in ';|&<>(){}[]`$\\*?!\n\r'):
            lines.append(f'{key}="{val}"')
        else:
            lines.append(f"{key}={val}")
        lines.append("")
    ENV_FILE.write_text("\n".join(lines) + "\n")


def _current_values() -> dict[str, str]:
    saved = _read_env()
    return {key: saved.get(key, os.environ.get(key, default)) for key, _, default in ADMIN_SETTINGS}


ADMIN_HTML_HEAD = """\
<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Web Search MCP — Admin</title>
<style>
:root {{ color-scheme: dark; }}
body {{ font-family: system-ui, -apple-system, BlinkMacSystemFont, sans-serif; background:#0f172a; color:#e2e8f0; margin:0; padding:2rem; }}
.container {{ max-width: 720px; margin: 0 auto; }}
h1 {{ color:#38bdf8; margin-top:0; }}
label {{ display:block; margin-top:1rem; font-weight:600; color:#94a3b8; font-size:.85rem; }}
input, textarea {{ width:100%; background:#1e293b; border:1px solid #334155; color:#e2e8f0; padding:.6rem .8rem; border-radius:.375rem; font-size:.95rem; box-sizing:border-box; }}
textarea {{ min-height:120px; font-family: ui-monospace, SFMono-Regular, Menlo, monospace; }}
button {{ margin-top:1.5rem; background:#2563eb; color:white; border:none; padding:.75rem 1.5rem; border-radius:.5rem; font-size:1rem; cursor:pointer; }}
button:hover {{ background:#1d4ed8; }}
.status {{ margin-top:1rem; padding:.75rem; border-radius:.375rem; background:#14532d; color:#86efac; }}
.status.error {{ background:#450a0a; color:#fca5a5; }}
.note {{ margin-top:2rem; font-size:.85rem; color:#64748b; border-top:1px solid #334155; padding-top:1rem; }}
</style>
</head>
<body>
<div class="container">
<h1>🔧 Web Search MCP Admin</h1>
<p>Edit settings and click <strong>Save + Restart</strong>. The service will reload with the new values.</p>
<form method="post" action="/admin/save">
"""

ADMIN_HTML_FOOT = """\
<button type="submit">Save + Restart</button>
</form>
<div class="note">
<p><strong>Paths:</strong></p>
<ul>
<li>Settings file: <code>{{env_file}}</code></li>
<li>Restart uses: <code>systemctl --user restart web-search-mcp</code></li>
</ul>
</div>
</div>
</body>
</html>
"""


def _admin_form(message: str = "", error: bool = False) -> HTMLResponse:
    values = _current_values()
    body = ADMIN_HTML_HEAD.format(env_file=str(ENV_FILE))
    for key, label, _ in ADMIN_SETTINGS:
        val = values.get(key, "")
        body += f'<label for="{key}">{label}</label>\n'
        if "\n" in val or len(val) > 60 or key == "SEARCH_MCP_RESCUE_ENGINES":
            body += f'<textarea id="{key}" name="{key}">{val}</textarea>\n'
        else:
            body += f'<input id="{key}" name="{key}" value="{val}">\n'
    if message:
        body += f'<div class="status{" error" if error else ""}">{message}</div>\n'
    body += ADMIN_HTML_FOOT.format(env_file=str(ENV_FILE))
    return HTMLResponse(content=body)


async def admin_page(request: Request) -> HTMLResponse:
    return _admin_form()


async def admin_save(request: Request) -> HTMLResponse:
    form = await request.form()
    new_values: dict[str, str] = {}
    for key, _, _ in ADMIN_SETTINGS:
        raw = form.get(key)
        if raw is not None:
            new_values[key] = str(raw)
    _write_env(new_values)

    error = False
    message = "Settings saved."
    try:
        # Fork a detached process that waits then restarts the service.
        subprocess.Popen(
            ["/usr/bin/env", "bash", "-c", "sleep 3 && systemctl --user restart web-search-mcp"],
            start_new_session=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        message += " Restarting service in 3 seconds..."
    except Exception as exc:
        error = True
        message += f" Restart failed: {exc}"
    return _admin_form(message=message, error=error)


# Build a parent FastAPI app, mount the MCP endpoint, the admin endpoints,
# then mount the Gradio UI at root.
parent = FastAPI(title="Web Search MCP", docs_url=None, redoc_url=None, lifespan=lifespan)
parent.mount("/mcp", _mcp_mount)
parent.mount("/gradio_api/mcp", _mcp_mount)
parent.get("/admin")(admin_page)
parent.post("/admin/save")(admin_save)

app = gr.mount_gradio_app(app=parent, blocks=demo, path="/")


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        app,
        host="0.0.0.0",
        port=int(os.environ.get("PORT", "38472")),
        log_level=os.environ.get("LOG_LEVEL", "info"),
    )
