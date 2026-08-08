"""
Web Search MCP — Hugging Face Space / local entry point.

Serves:
  - a readable HTML landing page at `/` (Gradio Blocks, keeps HF's Gradio SDK
    happy);
  - the `free-search-mcp` MCP-over-HTTP server at `/gradio_api/mcp/`;
  - a local admin dashboard at `/admin` to edit settings and restart the
    service;
  - a plain-English guide at `/admin/guide`.

Why Gradio + MCP v2? Gradio's built-in `mcp_server=True` requires `mcp`
v1, but `free-search-mcp` requires `mcp` v2. So we use Gradio only for
the landing page and run the upstream MCP server as a mounted ASGI app.

To run locally:
  PORT=38472 uv run --with gradio --with mcp --with uvicorn --with fastapi python hf-space/app.py
"""
from __future__ import annotations

import json
import os
import subprocess
from contextlib import asynccontextmanager
from pathlib import Path

import gradio as gr
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse

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
            value = value.strip()
            # Strip only matching outer quotes; leave JSON arrays intact.
            if (value.startswith('"') and value.endswith('"')) or (
                value.startswith("'") and value.endswith("'")
            ):
                value = value[1:-1]
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

**Admin:** [`/admin`](/admin) — edit settings, restart, read the guide.

**Note:** this server speaks **MCP protocol v2** (streamable-http,
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


# ---------------------------------------------------------------------------
# Admin dashboard — only useful when running locally; harmless on HF.
# ---------------------------------------------------------------------------

# Engines available without API keys or browsers.
KEYLESS_ENGINES = [
    "duckduckgo",
    "mojeek",
    "bing",
    "googlenews",
    "anysearch",
    "arxiv",
    "openalex",
    "crossref",
    "pubmed",
    "github",
    "stackexchange",
    "hackernews",
    "wikipedia",
    "openlibrary",
    "openverse",
    "zenodo",
    "bilibili",
    "sogou",
    "so360",
    "gdelt",
]

# Engines that need Playwright/Chromium.
BROWSER_ENGINES = [
    "google",
    "startpage",
    "brave",
    "baidu",
    "zhihu",
]

# Engines that need an API key (set via extra env vars, not shown here).
API_KEY_ENGINES = [
    "brave_api",
    "serper",
    "tavily",
    "google_cse",
    "github_code",
]


ADMIN_SETTINGS = [
    ("PORT", "Server port", "38472", "number"),
    ("SEARCH_MCP_DEFAULT_ENGINES", "Default search engines", '["duckduckgo","mojeek","googlenews","bing","anysearch"]', "engines"),
    ("SEARCH_MCP_FETCH_STRATEGY", "Fetch strategy", "http", "choice:http,browser,auto"),
    ("SEARCH_MCP_RESCUE_ENABLED", "Enable rescue fallback", "false", "choice:true,false"),
    ("SEARCH_MCP_RESCUE_ENGINES", "Rescue engines", "[]", "engines"),
    ("SEARCH_MCP_MAX_RESULTS", "Max results per engine", "10", "number"),
    ("SEARCH_MCP_HTTP_TIMEOUT", "HTTP timeout (seconds)", "15", "number"),
    ("SEARCH_MCP_REQUEST_DELAY", "Request delay (seconds)", "0", "number"),
    ("SEARCH_MCP_CACHE_TTL_HOURS", "Cache TTL (hours)", "168", "number"),
    ("SEARCH_MCP_CACHE_MAX_MB", "Cache max size (MB)", "256", "number"),
    ("SEARCH_MCP_LOG_LEVEL", "Log level", "info", "choice:debug,info,warning,error"),
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
    for key, label, default, kind in ADMIN_SETTINGS:
        val = values.get(key, default)
        lines.append(f"# {label}")
        if " " in val or any(c in val for c in ';|&<>(){}[]`$\\*?!\n\r'):
            lines.append(f'{key}="{val}"')
        else:
            lines.append(f"{key}={val}")
        lines.append("")
    ENV_FILE.write_text("\n".join(lines) + "\n")


def _current_values() -> dict[str, str]:
    saved = _read_env()
    return {key: saved.get(key, os.environ.get(key, default)) for key, _, default, _ in ADMIN_SETTINGS}


def _parse_engines(raw: str) -> list[str]:
    raw = raw.strip()
    if not raw:
        return []
    if raw.startswith("["):
        try:
            return json.loads(raw)
        except Exception:
            return []
    return [e.strip() for e in raw.split(",") if e.strip()]


SHARED_CSS = """\
:root {{ color-scheme: dark; }}
* {{ box-sizing: border-box; }}
body {{ margin:0; padding:0; font-family: "Inter", system-ui, -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif; background:#0b1120; color:#e2e8f0; line-height:1.6; font-size:15px; }}
.page {{ max-width: 920px; margin:0 auto; padding:2rem 1.5rem 3rem; }}
.header {{ display:flex; align-items:center; justify-content:space-between; gap:1rem; flex-wrap:wrap; margin-bottom:1rem; }}
.header h1 {{ margin:0; font-size:1.9rem; color:#38bdf8; letter-spacing:-0.02em; }}
.header p {{ margin:.25rem 0 0; color:#94a3b8; font-size:.95rem; }}
.btn {{ display:inline-flex; align-items:center; gap:.5rem; background:#2563eb; color:#fff; border:none; padding:.7rem 1.2rem; border-radius:.6rem; font-size:.95rem; font-weight:600; cursor:pointer; text-decoration:none; transition:background .15s; }}
.btn:hover {{ background:#1d4ed8; }}
.btn-secondary {{ background:#334155; }}
.btn-secondary:hover {{ background:#475569; }}
.card {{ background:#151e32; border:1px solid #27364b; border-radius:1rem; padding:1.5rem; margin-bottom:1.25rem; box-shadow:0 4px 20px rgba(0,0,0,.25); }}
.card h2 {{ margin:0 0 .75rem; font-size:1.25rem; color:#7dd3fc; }}
.card h3 {{ margin:1.25rem 0 .5rem; font-size:1.05rem; color:#94a3b8; }}
.card p:first-child {{ margin-top:0; }}
.card p {{ margin:.6rem 0; color:#cbd5e1; }}
.card ul, .card ol {{ margin:.5rem 0 0; padding-left:1.2rem; color:#cbd5e1; }}
.card li {{ margin:.35rem 0; }}
code {{ background:#1e293b; color:#7dd3fc; padding:.2rem .45rem; border-radius:.35rem; font-family: ui-monospace, SFMono-Regular, Menlo, monospace; font-size:.88em; border:1px solid #334155; }}
pre {{ background:#0f172a; color:#e2e8f0; padding:1rem; border-radius:.75rem; overflow-x:auto; border:1px solid #334155; font-family: ui-monospace, SFMono-Regular, Menlo, monospace; font-size:.9rem; }}
label {{ display:block; font-weight:600; color:#94a3b8; font-size:.9rem; margin-bottom:.4rem; }}
input[type="text"], input[type="number"], select {{ width:100%; background:#0f172a; border:1px solid #334155; color:#e2e8f0; padding:.65rem .9rem; border-radius:.5rem; font-size:.95rem; outline:none; }}
input[type="text"]:focus, input[type="number"]:focus, select:focus {{ border-color:#38bdf8; box-shadow:0 0 0 3px rgba(56,189,248,.15); }}
select {{ appearance:none; background-image:url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='12' height='12' viewBox='0 0 12 12'%3E%3Cpath fill='%2394a3b8' d='M6 8L1 3h10z'/%3E%3C/svg%3E"); background-repeat:no-repeat; background-position:right .9rem center; padding-right:2.2rem; }}
.engine-grid {{ display:grid; grid-template-columns: repeat(auto-fill, minmax(150px, 1fr)); gap:.6rem; margin-top:.6rem; }}
.engine-chip {{ display:flex; align-items:center; gap:.5rem; background:#0f172a; border:1px solid #334155; border-radius:.5rem; padding:.5rem .7rem; cursor:pointer; transition:background .12s, border-color .12s; }}
.engine-chip:hover {{ background:#1e293b; border-color:#475569; }}
.engine-chip input {{ width:auto; accent-color:#38bdf8; margin:0; }}
.engine-chip span {{ color:#e2e8f0; font-size:.9rem; font-weight:500; }}
.help {{ color:#64748b; font-size:.85rem; margin-top:.35rem; }}
.status {{ margin-top:1rem; padding:.8rem 1rem; border-radius:.5rem; background:#14532d; color:#86efac; border:1px solid #166534; }}
.status.error {{ background:#450a0a; color:#fca5a5; border-color:#7f1d1d; }}
.toc {{ background:#151e32; border:1px solid #27364b; border-radius:1rem; padding:1.25rem 1.5rem; margin:1rem 0 1.5rem; }}
.toc h3 {{ margin:0 0 .75rem; color:#38bdf8; font-size:1.1rem; }}
.toc ul {{ list-style:none; padding:0; margin:0; display:grid; grid-template-columns: repeat(auto-fill, minmax(220px, 1fr)); gap:.35rem; }}
.toc a {{ color:#94a3b8; text-decoration:none; font-size:.95rem; }}
.toc a:hover {{ color:#38bdf8; }}
.badge {{ display:inline-block; background:#1e293b; border:1px solid #334155; color:#e2e8f0; padding:.3rem .65rem; border-radius:999px; font-size:.82rem; margin:.2rem; }}
.badge.green {{ background:#14532d; border-color:#166534; color:#86efac; }}
.badge.orange {{ background:#451a03; border-color:#78350f; color:#fdba74; }}
.badge.red {{ background:#450a0a; border-color:#7f1d1d; color:#fca5a5; }}
.badge-row {{ margin:.5rem 0 1rem; }}
.table-wrap {{ overflow-x:auto; }}
table {{ width:100%; border-collapse:collapse; margin-top:.5rem; font-size:.95rem; }}
th, td {{ text-align:left; padding:.7rem .9rem; border-bottom:1px solid #27364b; }}
th {{ color:#94a3b8; font-weight:600; background:#0f172a; }}
tr:last-child td {{ border-bottom:none; }}
tr:hover td {{ background:#0f172a; }}
"""

ADMIN_HTML = """\
<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Web Search MCP — Admin</title>
<style>
{css}
.form-grid {{ display:grid; grid-template-columns: repeat(auto-fit, minmax(220px, 1fr)); gap:1.25rem; }}
.form-group {{ margin-bottom:.25rem; }}
.form-group.full {{ grid-column: 1 / -1; }}
.save-bar {{ display:flex; justify-content:space-between; align-items:center; gap:1rem; flex-wrap:wrap; margin:1.5rem 0; padding:1rem 1.25rem; background:#0f172a; border:1px solid #27364b; border-radius:.75rem; }}
.save-bar p {{ margin:0; color:#94a3b8; }}
.extra-hint {{ color:#94a3b8; font-size:.85rem; margin-top:.25rem; }}
.note {{ margin-top:1.5rem; padding:1rem 1.25rem; background:#0f172a; border:1px solid #27364b; border-radius:.75rem; color:#94a3b8; font-size:.9rem; }}
.note code {{ font-size:.85em; }}
</style>
</head>
<body>
<div class="page">
  <div class="header">
    <div>
      <h1>🔧 Web Search MCP Admin</h1>
      <p>Change settings and click Save + Restart. The service will reload with the new values.</p>
    </div>
    <a href="/admin/guide" class="btn btn-secondary">📖 Guide</a>
  </div>
  <form method="post" action="/admin/save">
    <div class="save-bar">
      <p>Review the changes, then restart the service.</p>
      <button type="submit" class="btn">💾 Save + Restart</button>
    </div>
    
    <div class="card">
      <h2>Server basics</h2>
      <div class="form-grid">
        <div class="form-group">
          <label for="PORT">Server port</label>
          <input type="number" id="PORT" name="PORT" value="{PORT}" min="1000" max="65535">
          <p class="help">TCP port this app listens on. Default 38472.</p>
        </div>
        <div class="form-group">
          <label for="SEARCH_MCP_LOG_LEVEL">Log level</label>
          <select id="SEARCH_MCP_LOG_LEVEL" name="SEARCH_MCP_LOG_LEVEL">
            {log_level_options}
          </select>
          <p class="help">Use debug only when troubleshooting.</p>
        </div>
      </div>
    </div>
    
    <div class="card">
      <h2>Search behaviour</h2>
      <div class="form-grid">
        <div class="form-group">
          <label for="SEARCH_MCP_FETCH_STRATEGY">Fetch strategy</label>
          <select id="SEARCH_MCP_FETCH_STRATEGY" name="SEARCH_MCP_FETCH_STRATEGY">
            {fetch_strategy_options}
          </select>
          <p class="help">http = fast CPU-only. browser = Chromium. auto = fallback.</p>
        </div>
        <div class="form-group">
          <label for="SEARCH_MCP_RESCUE_ENABLED">Rescue fallback</label>
          <select id="SEARCH_MCP_RESCUE_ENABLED" name="SEARCH_MCP_RESCUE_ENABLED">
            {rescue_enabled_options}
          </select>
          <p class="help">Try backup engines if a search fails. Keep off for now.</p>
        </div>
        <div class="form-group">
          <label for="SEARCH_MCP_MAX_RESULTS">Max results per engine</label>
          <input type="number" id="SEARCH_MCP_MAX_RESULTS" name="SEARCH_MCP_MAX_RESULTS" value="{SEARCH_MCP_MAX_RESULTS}" min="1" max="50">
          <p class="help">10 is a good default. More = slower.</p>
        </div>
        <div class="form-group">
          <label for="SEARCH_MCP_HTTP_TIMEOUT">HTTP timeout (seconds)</label>
          <input type="number" id="SEARCH_MCP_HTTP_TIMEOUT" name="SEARCH_MCP_HTTP_TIMEOUT" value="{SEARCH_MCP_HTTP_TIMEOUT}" min="1" max="120">
        </div>
        <div class="form-group">
          <label for="SEARCH_MCP_REQUEST_DELAY">Request delay (seconds)</label>
          <input type="number" id="SEARCH_MCP_REQUEST_DELAY" name="SEARCH_MCP_REQUEST_DELAY" value="{SEARCH_MCP_REQUEST_DELAY}" min="0" max="30">
          <p class="help">Increase if engines start blocking you.</p>
        </div>
      </div>
    </div>
    
    <div class="card">
      <h2>Default search engines</h2>
      <p class="help">Tick the engines to use when no specific engine is requested. Extra engines (browser/API-key) go in the text box below.</p>
      <div class="engine-grid">
        {default_engines_checkboxes}
      </div>
      <div style="margin-top:1.25rem;">
        <label for="SEARCH_MCP_DEFAULT_ENGINES_extra">Extra engines (comma-separated)</label>
        <input type="text" id="SEARCH_MCP_DEFAULT_ENGINES_extra" name="SEARCH_MCP_DEFAULT_ENGINES_extra" value="{default_engines_extra}" placeholder="e.g. brave_api, google_cse, google, zhihu">
        <p class="extra-hint">Type names of browser/API-key engines not shown above. These need extra setup.</p>
      </div>
    </div>
    
    <div class="card">
      <h2>Rescue engines</h2>
      <p class="help">Used only if <strong>Rescue fallback</strong> is enabled.</p>
      <div class="engine-grid">
        {rescue_engines_checkboxes}
      </div>
      <div style="margin-top:1.25rem;">
        <label for="SEARCH_MCP_RESCUE_ENGINES_extra">Extra rescue engines (comma-separated)</label>
        <input type="text" id="SEARCH_MCP_RESCUE_ENGINES_extra" name="SEARCH_MCP_RESCUE_ENGINES_extra" value="{rescue_engines_extra}" placeholder="e.g. bing, duckduckgo">
      </div>
    </div>
    
    <div class="card">
      <h2>Cache</h2>
      <div class="form-grid">
        <div class="form-group">
          <label for="SEARCH_MCP_CACHE_TTL_HOURS">Cache TTL (hours)</label>
          <input type="number" id="SEARCH_MCP_CACHE_TTL_HOURS" name="SEARCH_MCP_CACHE_TTL_HOURS" value="{SEARCH_MCP_CACHE_TTL_HOURS}" min="1" max="720">
        </div>
        <div class="form-group">
          <label for="SEARCH_MCP_CACHE_MAX_MB">Cache max size (MB)</label>
          <input type="number" id="SEARCH_MCP_CACHE_MAX_MB" name="SEARCH_MCP_CACHE_MAX_MB" value="{SEARCH_MCP_CACHE_MAX_MB}" min="10" max="2000">
        </div>
      </div>
    </div>
    
    <div class="save-bar">
      <p>Save all changes and restart the service.</p>
      <button type="submit" class="btn">💾 Save + Restart</button>
    </div>
    
    {status}
  </form>
  
  <div class="note">
    <strong>Files used:</strong> settings in <code>{env_file}</code> · restart via <code>systemctl --user restart web-search-mcp</code> · guide at <code>/admin/guide</code>
  </div>
</div>
</body>
</html>
"""


def _admin_form(message: str = "", error: bool = False) -> HTMLResponse:
    values = _current_values()

    def opts(key: str, choices: str) -> str:
        current = values.get(key, "")
        return "".join(
            f'<option value="{opt}"{" selected" if opt == current else ""}>{opt}</option>\n'
            for opt in choices.split(",")
        )

    def chips(key: str, options: list[str]) -> str:
        selected = _parse_engines(values.get(key, ""))
        return "".join(
            f'<label class="engine-chip">\n'
            f'  <input type="checkbox" name="{key}" value="{engine}"{" checked" if engine in selected else ""}>\n'
            f'  <span>{engine}</span>\n'
            f'</label>\n'
            for engine in options
        )

    def extras(key: str) -> str:
        selected = _parse_engines(values.get(key, ""))
        return ",".join(e for e in selected if e not in KEYLESS_ENGINES)

    status_html = ""
    if message:
        status_html = f'<div class="status{" error" if error else ""}">{message}</div>\n'

    content = ADMIN_HTML.format(
        css=SHARED_CSS,
        PORT=values.get("PORT", "38472"),
        SEARCH_MCP_MAX_RESULTS=values.get("SEARCH_MCP_MAX_RESULTS", "10"),
        SEARCH_MCP_HTTP_TIMEOUT=values.get("SEARCH_MCP_HTTP_TIMEOUT", "15"),
        SEARCH_MCP_REQUEST_DELAY=values.get("SEARCH_MCP_REQUEST_DELAY", "0"),
        SEARCH_MCP_CACHE_TTL_HOURS=values.get("SEARCH_MCP_CACHE_TTL_HOURS", "168"),
        SEARCH_MCP_CACHE_MAX_MB=values.get("SEARCH_MCP_CACHE_MAX_MB", "256"),
        log_level_options=opts("SEARCH_MCP_LOG_LEVEL", "debug,info,warning,error"),
        fetch_strategy_options=opts("SEARCH_MCP_FETCH_STRATEGY", "http,browser,auto"),
        rescue_enabled_options=opts("SEARCH_MCP_RESCUE_ENABLED", "true,false"),
        default_engines_checkboxes=chips("SEARCH_MCP_DEFAULT_ENGINES", KEYLESS_ENGINES),
        default_engines_extra=extras("SEARCH_MCP_DEFAULT_ENGINES"),
        rescue_engines_checkboxes=chips("SEARCH_MCP_RESCUE_ENGINES", KEYLESS_ENGINES),
        rescue_engines_extra=extras("SEARCH_MCP_RESCUE_ENGINES"),
        status=status_html,
        env_file=str(ENV_FILE),
    )
    return HTMLResponse(content=content)


async def admin_page(request: Request) -> HTMLResponse:
    return _admin_form()


async def admin_save(request: Request) -> HTMLResponse:
    form = await request.form()
    new_values: dict[str, str] = {}

    for key, label, default, kind in ADMIN_SETTINGS:
        if kind == "engines":
            selected = []
            if hasattr(form, "getlist"):
                selected = form.getlist(key)
            else:
                for k, v in form.multi_items():
                    if k == key:
                        selected.append(v)
            extra_raw = form.get(f"{key}_extra") or ""
            extras = [e.strip() for e in extra_raw.split(",") if e.strip()]
            engines = selected + [e for e in extras if e not in selected]
            new_values[key] = json.dumps(engines)
        else:
            raw = form.get(key)
            if raw is not None:
                new_values[key] = str(raw)

    _write_env(new_values)

    error = False
    message = "Settings saved."
    try:
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


GUIDE_HTML = """\
<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Web Search MCP — Guide</title>
<style>
{css}
.hero {{ background: linear-gradient(135deg, #1e293b 0%, #0f172a 100%); border:1px solid #334155; }}
.section-title {{ display:flex; align-items:center; gap:.6rem; font-size:1.35rem; color:#7dd3fc; margin:2rem 0 .75rem; }}
.section-title .icon {{ font-size:1.5rem; }}
.step {{ display:flex; gap:.75rem; align-items:flex-start; margin:.75rem 0; }}
.step-num {{ flex-shrink:0; width:1.8rem; height:1.8rem; display:flex; align-items:center; justify-content:center; background:#2563eb; color:#fff; border-radius:50%; font-weight:700; font-size:.85rem; }}
.tip {{ background:#0f172a; border-left:4px solid #38bdf8; padding:.8rem 1rem; border-radius:0 .5rem .5rem 0; margin:1rem 0; color:#cbd5e1; }}
.warning {{ background:#0f172a; border-left:4px solid #f59e0b; padding:.8rem 1rem; border-radius:0 .5rem .5rem 0; margin:1rem 0; color:#cbd5e1; }}
.danger {{ background:#0f172a; border-left:4px solid #ef4444; padding:.8rem 1rem; border-radius:0 .5rem .5rem 0; margin:1rem 0; color:#cbd5e1; }}
.engine-list {{ display:grid; grid-template-columns: repeat(auto-fill, minmax(200px, 1fr)); gap:.6rem; }}
.engine-item {{ display:flex; align-items:center; gap:.6rem; background:#0f172a; border:1px solid #334155; border-radius:.5rem; padding:.55rem .75rem; }}
.engine-item strong {{ color:#e2e8f0; min-width:7rem; }}
.engine-item span {{ color:#94a3b8; font-size:.9rem; }}
</style>
</head>
<body>
<div class="page">
  <div class="header">
    <div>
      <h1>📖 Web Search MCP Guide</h1>
      <p>Everything you need to use and configure this search server.</p>
    </div>
    <a href="/admin" class="btn btn-secondary">🔧 Admin</a>
  </div>
  
  <div class="card hero">
    <h2>What is this thing?</h2>
    <p>This is a <strong>multi-engine search server</strong>. Other programs — Hermes, Reachy, Claude, Codex — can ask it &quot;find me X&quot;. It then queries several search engines, merges the results, removes duplicates, and returns a clean answer.</p>
    <p>It speaks <strong>MCP</strong> (Model Context Protocol), a kind of &quot;USB-C for AI tools&quot;. Any MCP client can plug in and search the web.</p>
  </div>
  
  <div class="toc">
    <h3>On this page</h3>
    <ul>
      <li><a href="#urls">URLs you need</a></li>
      <li><a href="#settings">Settings explained</a></li>
      <li><a href="#engines">Engine list</a></li>
      <li><a href="#howto">How to change engines</a></li>
      <li><a href="#browser">Browser / API-key engines</a></li>
      <li><a href="#test">How to test</a></li>
      <li><a href="#robot">How the robot uses it</a></li>
      <li><a href="#trouble">Troubleshooting</a></li>
    </ul>
  </div>
  
  <div class="section-title" id="urls"><span class="icon">🔗</span> URLs you actually need</div>
  <div class="card">
    <table>
      <tr><th>Page</th><th>URL</th></tr>
      <tr><td>Landing page</td><td><code>http://&lt;this-pc-ip&gt;:38472/</code></td></tr>
      <tr><td>Admin settings</td><td><code>http://&lt;this-pc-ip&gt;:38472/admin</code></td></tr>
      <tr><td>MCP endpoint (robot/app)</td><td><code>http://&lt;this-pc-ip&gt;:38472/gradio_api/mcp/</code></td></tr>
    </table>
  </div>
  
  <div class="section-title" id="settings"><span class="icon">⚙️</span> Settings explained</div>
  <div class="card">
    <div class="table-wrap">
      <table>
        <tr><th>Setting</th><th>What it does</th></tr>
        <tr><td><strong>Server port</strong></td><td>TCP port the server listens on. Default 38472. Change only if another app on this PC uses it.</td></tr>
        <tr><td><strong>Default search engines</strong><td>Which engines are used when you or the robot just say &quot;search&quot;. Tick the checkboxes in admin.</td></tr>
        <tr><td><strong>Fetch strategy</strong></td>
          <td>
            <span class="badge green">http</span> fast, no browser. <br>
            <span class="badge orange">browser</span> uses Playwright/Chromium, slow and heavy. <br>
            <span class="badge">auto</span> tries HTTP first, falls back to browser.
          </td>
        </tr>
        <tr><td><strong>Rescue fallback</strong></td><td>If <code>true</code>, tries backup engines when a search fails. We keep it <code>false</code> because the default rescue engine (searx) is dead and mislabels results.</td></tr>
        <tr><td><strong>Rescue engines</strong></td><td>Backup engines used only when rescue fallback is enabled.</td></tr>
        <tr><td><strong>Max results per engine</strong></td><td>How many results to ask each engine for. 10 is good; more = slower.</td></tr>
        <tr><td><strong>HTTP timeout</strong></td><td>Seconds to wait for each engine before giving up. 15 is fine.</td></tr>
        <tr><td><strong>Request delay</strong></td><td>Seconds to sleep between requests. Increase if engines start blocking you.</td></tr>
        <tr><td><strong>Cache TTL / Max size</strong></td><td>How long fetched pages stay on disk (hours) and the max disk space (MB). Old items are deleted automatically.</td></tr>
        <tr><td><strong>Log level</strong></td><td><code>debug</code>, <code>info</code>, <code>warning</code>, <code>error</code>. Use debug only when troubleshooting.</td></tr>
      </table>
    </div>
  </div>
  
  <div class="section-title" id="engines"><span class="icon">🚂</span> Engine list</div>
  <div class="card">
    <h3>Keyless engines — work out of the box</h3>
    <div class="badge-row">
      <span class="badge">duckduckgo</span> <span class="badge">mojeek</span> <span class="badge">bing</span> <span class="badge">googlenews</span> <span class="badge">anysearch</span>
      <span class="badge">arxiv</span> <span class="badge">openalex</span> <span class="badge">crossref</span> <span class="badge">pubmed</span> <span class="badge">github</span>
      <span class="badge">stackexchange</span> <span class="badge">hackernews</span> <span class="badge">wikipedia</span> <span class="badge">openlibrary</span>
      <span class="badge">openverse</span> <span class="badge">zenodo</span> <span class="badge">bilibili</span> <span class="badge">sogou</span> <span class="badge">so360</span> <span class="badge">gdelt</span>
    </div>
    <div class="engine-list">
      <div class="engine-item"><strong>General web</strong><span>duckduckgo, mojeek, bing, anysearch</span></div>
      <div class="engine-item"><strong>News</strong><span>googlenews, gdelt (slow)</span></div>
      <div class="engine-item"><strong>Academic</strong><span>arxiv, openalex, crossref, pubmed</span></div>
      <div class="engine-item"><strong>Code / tech</strong><span>github, stackexchange, hackernews</span></div>
      <div class="engine-item"><strong>Reference</strong><span>wikipedia, openlibrary, openverse, zenodo</span></div>
    </div>
    
    <h3 style="margin-top:1.5rem;">Browser engines — need Chromium/Playwright</h3>
    <div class="badge-row">
      <span class="badge orange">google</span> <span class="badge orange">startpage</span> <span class="badge orange">brave</span> <span class="badge orange">baidu</span> <span class="badge orange">zhihu</span>
    </div>
    
    <h3 style="margin-top:1.5rem;">API-key engines — need signup + key</h3>
    <div class="badge-row">
      <span class="badge red">brave_api</span> <span class="badge red">serper</span> <span class="badge red">tavily</span> <span class="badge red">google_cse</span> <span class="badge red">github_code</span>
    </div>
    <div class="warning">
      Browser and API-key engines are not enabled by default. They need extra setup. Type their names in the <strong>Extra engines</strong> box in admin if you want to use them.
    </div>
  </div>
  
  <div class="section-title" id="howto"><span class="icon">✏️</span> How to change engines</div>
  <div class="card">
    <div class="step"><div class="step-num">1</div><div>Go to <a href="/admin">/admin</a>.</div></div>
    <div class="step"><div class="step-num">2</div><div>Under <strong>Default search engines</strong>, tick the engines you want.</div></div>
    <div class="step"><div class="step-num">3</div><div>Click <strong>Save + Restart</strong>.</div></div>
    <div class="step"><div class="step-num">4</div><div>Wait about 5 seconds, then test with the curl command below.</div></div>
    
    <div class="tip">
      To add an engine not shown in the checkboxes (like <code>brave_api</code>), type it in the <strong>Extra engines</strong> box, comma-separated. Example: <code>brave_api, google_cse</code>.
    </div>
  </div>
  
  <div class="section-title" id="browser"><span class="icon">🌐</span> How to enable browser or API-key engines</div>
  <div class="card">
    <ol>
      <li>Install Chromium / Playwright on this PC if you want browser engines.</li>
      <li>Set <strong>Fetch strategy</strong> to <code>browser</code> or <code>auto</code>.</li>
      <li>Type the engine names into the <strong>Extra engines</strong> box, e.g. <code>google, brave_api</code>.</li>
      <li>Save + Restart.</li>
    </ol>
    <div class="danger">
      API keys are set separately as environment variables, not in this dashboard. See the upstream <code>free-search-mcp</code> docs for the exact variable names.
    </div>
  </div>
  
  <div class="section-title" id="test"><span class="icon">🧪</span> How to test the server</div>
  <div class="card">
    <p>Run this in a terminal on this PC:</p>
    <pre>curl -X POST http://localhost:38472/gradio_api/mcp/ \\
  -H 'Content-Type: application/json' \\
  -d '{{"jsonrpc":"2.0","id":1,"method":"tools/list"}}'</pre>
    <p>You should get a big JSON list of tools. If you do, the server is alive.</p>
  </div>
  
  <div class="section-title" id="robot"><span class="icon">🤖</span> How the robot uses it</div>
  <div class="card">
    <p>The robot (or Hermes) needs the MCP URL. On your local network the URL is:</p>
    <pre>http://192.168.1.3:38472/gradio_api/mcp/</pre>
    <div class="warning">
      For a public URL you would need to host this on a public server or tunnel. The Hugging Face Space attempt failed because HF forces an old MCP v1 dependency that conflicts with this app.
    </div>
  </div>
  
  <div class="section-title" id="trouble"><span class="icon">🛠️</span> Troubleshooting</div>
  <div class="card">
    <ul>
      <li><strong>Server not responding?</strong> Run <code>systemctl --user status web-search-mcp</code>.</li>
      <li><strong>Port conflict?</strong> Change <strong>Server port</strong> in admin, save + restart.</li>
      <li><strong>Bad JSON error on restart?</strong> Make sure engine names in the Extra engines box have no spaces or quotes. Use plain commas.</li>
      <li><strong>Slow searches?</strong> Untick slow engines like <code>gdelt</code> or reduce <strong>Max results per engine</strong>.</li>
    </ul>
  </div>
  
  <div style="text-align:center; margin-top:2rem;">
    <a href="/admin" class="btn btn-secondary">← Back to admin settings</a>
  </div>
</div>
</body>
</html>
"""


async def admin_guide(request: Request) -> HTMLResponse:
    return HTMLResponse(content=GUIDE_HTML.format(css=SHARED_CSS))


# Build a parent FastAPI app, mount the MCP endpoint, the admin endpoints,
# then mount the Gradio UI at root.
parent = FastAPI(title="Web Search MCP", docs_url=None, redoc_url=None, lifespan=lifespan)
parent.mount("/mcp", _mcp_mount)
parent.mount("/gradio_api/mcp", _mcp_mount)
parent.get("/admin")(admin_page)
parent.post("/admin/save")(admin_save)
parent.get("/admin/guide")(admin_guide)

app = gr.mount_gradio_app(app=parent, blocks=demo, path="/")


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        app,
        host="0.0.0.0",
        port=int(os.environ.get("PORT", "38472")),
        log_level=os.environ.get("LOG_LEVEL", "info"),
    )
