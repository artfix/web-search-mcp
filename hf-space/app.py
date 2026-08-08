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


def _render_engines_checkbox(name: str, selected: list[str], options: list[str]) -> str:
    html = f'<fieldset><legend>{name}</legend><div class="engine-grid">\n'
    for engine in options:
        checked = "checked" if engine in selected else ""
        html += (
            f'<label class="engine-chip">\n'
            f'  <input type="checkbox" name="{name}" value="{engine}" {checked}>\n'
            f'  <span>{engine}</span>\n'
            f'</label>\n'
        )
    html += "</div></fieldset>\n"
    return html


ADMIN_CSS = """\
:root {{ color-scheme: dark; }}
body {{ font-family: system-ui, -apple-system, BlinkMacSystemFont, sans-serif; background:#0f172a; color:#e2e8f0; margin:0; padding:2rem; }}
.container {{ max-width: 820px; margin: 0 auto; }}
h1 {{ color:#38bdf8; margin-top:0; }}
h2 {{ color:#7dd3fc; margin-top:2rem; border-bottom:1px solid #334155; padding-bottom:.3rem; }}
h3 {{ color:#94a3b8; margin-top:1.5rem; }}
.topbar {{ display:flex; gap:1rem; align-items:center; margin-bottom:1rem; }}
label {{ display:block; margin-top:1.1rem; font-weight:600; color:#94a3b8; font-size:.85rem; }}
input[type="text"], input[type="number"], select {{ width:100%; background:#1e293b; border:1px solid #334155; color:#e2e8f0; padding:.6rem .8rem; border-radius:.375rem; font-size:.95rem; box-sizing:border-box; }}
select {{ appearance:none; background-image: url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='12' height='12' viewBox='0 0 12 12'%3E%3Cpath fill='%2394a3b8' d='M6 8L1 3h10z'/%3E%3C/svg%3E"); background-repeat:no-repeat; background-position:right .8rem center; padding-right:2rem; }}
button {{ margin-top:1.5rem; background:#2563eb; color:white; border:none; padding:.75rem 1.5rem; border-radius:.5rem; font-size:1rem; cursor:pointer; }}
button:hover {{ background:#1d4ed8; }}
button.secondary {{ background:#475569; }}
button.secondary:hover {{ background:#334155; }}
.status {{ margin-top:1rem; padding:.75rem; border-radius:.375rem; background:#14532d; color:#86efac; }}
.status.error {{ background:#450a0a; color:#fca5a5; }}
.note {{ margin-top:2rem; font-size:.85rem; color:#64748b; border-top:1px solid #334155; padding-top:1rem; }}
code {{ background:#1e293b; padding:.15rem .4rem; border-radius:.25rem; font-family: ui-monospace, SFMono-Regular, Menlo, monospace; font-size:.9rem; }}
pre {{ background:#1e293b; padding:1rem; border-radius:.5rem; overflow-x:auto; }}
ul {{ line-height:1.8; }}
table {{ border-collapse: collapse; width:100%; margin-top:1rem; }}
th, td {{ border:1px solid #334155; padding:.5rem .75rem; text-align:left; }}
th {{ background:#1e293b; }}
a {{ color:#38bdf8; text-decoration:none; }}
.engine-grid {{ display:grid; grid-template-columns: repeat(auto-fill, minmax(140px, 1fr)); gap:.5rem; margin-top:.5rem; }}
fieldset {{ border:1px solid #334155; border-radius:.5rem; padding:1rem; margin-top:1rem; }}
legend {{ color:#94a3b8; font-weight:600; padding:0 .5rem; }}
.engine-chip {{ display:flex; align-items:center; gap:.4rem; background:#1e293b; border:1px solid #334155; border-radius:.375rem; padding:.4rem .6rem; cursor:pointer; margin:0; }}
.engine-chip:hover {{ background:#27364b; }}
.engine-chip input {{ width:auto; accent-color:#38bdf8; }}
.engine-chip span {{ color:#e2e8f0; font-weight:500; }}
.extra-engines {{ margin-top:.75rem; }}
.extra-engines input {{ width:100%; }}
"""

ADMIN_HTML_HEAD = """\
<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Web Search MCP — Admin</title>
<style>
{css}
</style>
</head>
<body>
<div class="container">
<div class="topbar">
<h1>🔧 Web Search MCP Admin</h1>
<a href="/admin/guide"><button type="button" class="secondary">📖 Guide</button></a>
</div>
<p>Change settings below and click <strong>Save + Restart</strong>. The service will reload with the new values.</p>
<form method="post" action="/admin/save">
"""

ADMIN_HTML_FOOT = """\
<button type="submit">💾 Save + Restart</button>
</form>
<div class="note">
<p><strong>Paths:</strong></p>
<ul>
<li>Settings file: <code>{env_file}</code></li>
<li>Restart uses: <code>systemctl --user restart web-search-mcp</code></li>
<li>Full guide: <code>/admin/guide</code></li>
</ul>
</div>
</div>
</body>
</html>
"""


def _admin_form(message: str = "", error: bool = False) -> HTMLResponse:
    values = _current_values()
    body = ADMIN_HTML_HEAD.format(css=ADMIN_CSS)

    for key, label, default, kind in ADMIN_SETTINGS:
        val = values.get(key, default)
        body += f'<label for="{key}">{label}</label>\n'

        if kind == "engines":
            selected = _parse_engines(val)
            body += _render_engines_checkbox(key, selected, KEYLESS_ENGINES)
            body += f'''
<div class="extra-engines">
  <label for="{key}_extra">Extra engines (browser/API-key, comma-separated)</label>
  <input type="text" id="{key}_extra" name="{key}_extra" value="{','.join(e for e in selected if e not in KEYLESS_ENGINES)}" placeholder="e.g. brave_api, google_cse">
</div>
'''
        elif kind.startswith("choice:"):
            options = kind.split(":", 1)[1].split(",")
            body += f'<select id="{key}" name="{key}">\n'
            for opt in options:
                selected = "selected" if opt == val else ""
                body += f'  <option value="{opt}" {selected}>{opt}</option>\n'
            body += '</select>\n'
        elif kind == "number":
            body += f'<input type="number" id="{key}" name="{key}" value="{val}">\n'
        else:
            body += f'<input type="text" id="{key}" name="{key}" value="{val}">\n'

    if message:
        body += f'<div class="status{" error" if error else ""}">{message}</div>\n'
    body += ADMIN_HTML_FOOT.format(env_file=str(ENV_FILE))
    return HTMLResponse(content=body)


async def admin_page(request: Request) -> HTMLResponse:
    return _admin_form()


async def admin_save(request: Request) -> HTMLResponse:
    form = await request.form()
    new_values: dict[str, str] = {}

    for key, label, default, kind in ADMIN_SETTINGS:
        if kind == "engines":
            selected = form.getlist(key) if hasattr(form, "getlist") else []
            if not selected:
                # FastAPI's FormData doesn't have getlist; fall back to multi keys.
                selected = []
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
</style>
</head>
<body>
<div class="container">
<h1>📖 Web Search MCP — Guide</h1>
<p><a href="/admin">← Back to admin</a></p>

<h2>What is this thing?</h2>
<p>This is a <strong>search engine server</strong> that other programs (Hermes, Reachy, Claude, Codex, etc.) can talk to using a protocol called <strong>MCP</strong>. You ask it &quot;find me X&quot; and it queries multiple search engines, merges the results, removes duplicates, and returns a clean answer.</p>

<h2>URLs you actually need</h2>
<ul>
<li><strong>Landing page:</strong> <code>http://&lt;this-pc-ip&gt;:38472/</code></li>
<li><strong>Admin settings:</strong> <code>http://&lt;this-pc-ip&gt;:38472/admin</code></li>
<li><strong>MCP endpoint (the one your robot/app uses):</strong> <code>http://&lt;this-pc-ip&gt;:38472/gradio_api/mcp/</code></li>
</ul>

<h2>The settings explained</h2>
<table>
<tr><th>Setting</th><th>What it does</th></tr>
<tr><td><code>PORT</code></td><td>TCP port the server listens on. Default 38472. Change it here if it collides with another app on this PC.</td></tr>
<tr><td><code>Default search engines</code></td><td>Which engines are used when you (or the robot) just say &quot;search&quot; without picking one. Tick the checkboxes you want.</td></tr>
<tr><td><code>Fetch strategy</code></td><td><code>http</code> = fast, no browser. <code>browser</code> = uses Playwright/Chromium (slow, heavy, needs install). <code>auto</code> = tries HTTP first, falls back to browser. On a free CPU box keep <code>http</code>.</td></tr>
<tr><td><code>Enable rescue fallback</code><td>If <code>true</code>, when a search fails it tries other engines as backup. We keep it <code>false</code> because the default rescue engine (searx) is dead and mislabels results.</td></tr>
<tr><td><code>Rescue engines</code></td><td>Engines used only when rescue is enabled. Tick the ones you want as backup.</td></tr>
<tr><td><code>Max results per engine</code></td><td>How many results to ask each engine for. 10 is a good default. More = slower.</td></tr>
<tr><td><code>HTTP timeout</code></td><td>Seconds to wait for each engine before giving up. 15 is fine.</td></tr>
<tr><td><code>Request delay</code></td><td>Seconds to sleep between requests. Increase if engines start blocking you.</td></tr>
<tr><td><code>Cache TTL</code></td><td>How long fetched pages stay in the local cache. 168 = 7 days.</td></tr>
<tr><td><code>Cache max size</code></td><td>Max disk space for the cache. Old items are deleted when it gets full.</td></tr>
<tr><td><code>Log level</code></td><td><code>debug</code>, <code>info</code>, <code>warning</code>, or <code>error</code>. Use <code>debug</code> only when troubleshooting.</td></tr>
</table>

<h2>Available engines</h2>
<p>These engines work with <strong>no API key</strong> and no browser:</p>
<ul>
<li><code>duckduckgo</code> — general web, reliable</li>
<li><code>mojeek</code> — general web, fast</li>
<li><code>bing</code> — Microsoft search, usually works</li>
<li><code>googlenews</code> — news headlines</li>
<li><code>anysearch</code> — meta search aggregator</li>
<li><code>arxiv</code> — scientific papers</li>
<li><code>openalex</code> — academic graph</li>
<li><code>crossref</code> — DOI / scholarly works</li>
<li><code>pubmed</code> — biomedical papers</li>
<li><code>github</code> — code repositories</li>
<li><code>stackexchange</code> — Stack Overflow family</li>
<li><code>hackernews</code> — Hacker News stories</li>
<li><code>wikipedia</code> — encyclopedia</li>
<li><code>openlibrary</code> — books</li>
<li><code>openverse</code> — open media</li>
<li><code>zenodo</code> — research datasets</li>
<li><code>bilibili</code> — video search</li>
<li><code>sogou</code> — Chinese web search (HTTP)</li>
<li><code>so360</code> — 360 search</li>
<li><code>gdelt</code> — global news archive (slow, sometimes empty)</li>
</ul>

<h2>Engines that need a browser or API key</h2>
<p>These are <strong>disabled on this free build</strong>:</p>
<ul>
<li><strong>Browser engines:</strong> google, startpage, brave, baidu, zhihu — require Playwright/Chromium.</li>
<li><strong>API-key engines:</strong> brave_api, serper, tavily, google_cse, github_code — require signing up and adding keys in env vars.</li>
</ul>

<h2>How to change which engines are used</h2>
<p>Go to <strong>/admin</strong>, tick the engines you want under &quot;Default search engines&quot;, and click <strong>Save + Restart</strong>. Wait about 5 seconds, then test.</p>

<h2>How to add an engine that is not in the default list</h2>
<p>You don&#39;t &quot;add&quot; new engines to the code from this dashboard. The engine must already be supported by <code>free-search-mcp</code>. To use one that is not in the defaults, type its name in the &quot;Extra engines&quot; field under the engine picker. For example:</p>
<pre>brave_api, google_cse</pre>
<p>Then save + restart. Note: those engines need API keys set separately.</p>

<h2>How to enable browser engines (advanced, heavy)</h2>
<ol>
<li>Install Chromium/Playwright on this PC.</li>
<li>Set <code>Fetch strategy</code> to <code>browser</code>.</li>
<li>Type the browser engine names into the &quot;Extra engines&quot; field (e.g. <code>google, brave</code>).</li>
<li>Save + Restart.</li>
</ol>

<h2>How to test if the server works</h2>
<pre>curl -X POST http://localhost:38472/gradio_api/mcp/ \\
  -H 'Content-Type: application/json' \\
  -d '{{"jsonrpc":"2.0","id":1,"method":"tools/list"}}'</pre>
<p>You should get a big JSON list of tools. If you do, the server is alive.</p>

<h2>How to make the robot use it</h2>
<p>The robot (or Hermes) needs the MCP URL. On your local network the URL is:</p>
<pre>http://192.168.1.3:38472/gradio_api/mcp/</pre>
<p>For a public URL you would need to host this on a public server or tunnel. The Hugging Face Space attempt failed because HF forces an old MCP v1 dependency that conflicts with this app.</p>

<h2>Troubleshooting</h2>
<ul>
<li><strong>Server not responding?</strong> Run <code>systemctl --user status web-search-mcp</code>.</li>
<li><strong>Port conflict?</strong> Change <code>PORT</code> in admin, save + restart.</li>
<li><strong>Bad JSON error on restart?</strong> Make sure no engine names have spaces or quotes. Use plain comma-separated names in the Extra engines box.</li>
<li><strong>Slow searches?</strong> Untick slow engines like <code>gdelt</code> or reduce <code>Max results per engine</code>.</li>
</ul>

<div class="note">
<p><a href="/admin">← Back to admin settings</a></p>
</div>
</div>
</body>
</html>
"""


async def admin_guide(request: Request) -> HTMLResponse:
    return HTMLResponse(content=GUIDE_HTML.format(css=ADMIN_CSS))


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
