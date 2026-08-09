"""
Web Search MCP — Hugging Face Space / local entry point.

Serves:
  - a readable HTML landing page at `/` (Gradio Blocks, keeps HF's Gradio SDK
    happy);
  - the `free-search-mcp` MCP-over-HTTP server at `/gradio_api/mcp/`;
  - a local admin dashboard at `/admin` to edit settings and restart the
    service (protected by login);
  - a plain-English guide at `/admin/guide` (protected by login).

Auth flow:
  - First visit to /admin → redirect to /admin/setup (set username + password)
  - Subsequent visits → redirect to /admin/login
  - After login → session cookie (HMAC-signed, 7-day expiry)
  - Inside dashboard → change password section

To run locally:
  PORT=38472 uv run --with gradio --with mcp --with uvicorn --with fastapi python hf-space/app.py
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import secrets
import subprocess
import time
from contextlib import asynccontextmanager
from pathlib import Path

import gradio as gr
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, RedirectResponse

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
            if (value.startswith('"') and value.endswith('"')) or (
                value.startswith("'") and value.endswith("'")
            ):
                value = value[1:-1]
            if key and key not in os.environ:
                os.environ[key] = value

from search_mcp import server as search_server


os.environ.setdefault("SEARCH_MCP_RESCUE_ENABLED", "false")
os.environ.setdefault("SEARCH_MCP_RESCUE_ENGINES", "[]")
os.environ.setdefault("SEARCH_MCP_FETCH_STRATEGY", "http")


LANDING_MD = """\
# 🔍 Web Search MCP

A **multi-engine web search** server exposed as an MCP endpoint. No API
key, no signup — point your MCP client at this URL and go.

## Connect

**Endpoint:** `https://search.ai-vibe.org/gradio_api/mcp/`

| Client | How to connect |
|---|---|
| Hermes / generic MCP v2 | `https://search.ai-vibe.org/gradio_api/mcp/` |
| Claude Desktop / Codex | `{"mcpServers":{"websearch":{"url":"https://search.ai-vibe.org/gradio_api/mcp/"}}}` |
| Reachy Mini app | `reachy-mini-conversation-app tool-spaces add search.ai-vibe.org` |

**Local network** (if this server is running on your LAN): use
`http://<this-pc-ip>:38472/gradio_api/mcp/`.

**Admin:** [`/admin`](/admin) — edit settings, restart, read the guide. Login required.

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
curl -X POST https://search.ai-vibe.org/gradio_api/mcp/ \\
  -H 'Content-Type: application/json' \\
  -d '{"jsonrpc":"2.0","id":1,"method":"tools/list"}'
```

## Limits

- No auth on the MCP endpoint — anyone can search.
- 7-day page cache, 256 MB cache cap.
- `gdelt` is slow (12–15s) and sometimes returns zero results.

Forked from [sweetcornna/free-search-mcp](https://github.com/sweetcornna/free-search-mcp). MIT license.
"""


# Upstream MCP v2 server, already wired with a `/mcp` route.
mcp_app = search_server.mcp.streamable_http_app()


async def _mcp_mount(scope, receive, send) -> None:
    rewritten_scope = dict(scope)
    rewritten_scope["path"] = "/mcp"
    rewritten_scope["raw_path"] = b"/mcp"
    await mcp_app(rewritten_scope, receive, send)


@asynccontextmanager
async def lifespan(app):
    async with mcp_app.router.lifespan_context(app):
        yield


with gr.Blocks(title="Web Search MCP", analytics_enabled=False) as demo:
    gr.Markdown(LANDING_MD)


# ---------------------------------------------------------------------------
# Auth system
# ---------------------------------------------------------------------------

AUTH_FILE = HERE / ".auth.json"
SESSION_COOKIE = "wsmcp_session"
SESSION_MAX_AGE = 7 * 24 * 3600  # 7 days


def _hash_password(password: str, salt: str | None = None) -> tuple[str, str]:
    if salt is None:
        salt = secrets.token_hex(16)
    key = hashlib.pbkdf2_hmac("sha256", password.encode(), salt.encode(), 100_000)
    return key.hex(), salt


def _verify_password(password: str, stored_hash: str, salt: str) -> bool:
    key, _ = _hash_password(password, salt)
    return hmac.compare_digest(key, stored_hash)


def _load_auth() -> dict | None:
    if AUTH_FILE.exists():
        try:
            return json.loads(AUTH_FILE.read_text())
        except Exception:
            return None
    return None


def _save_auth(data: dict) -> None:
    AUTH_FILE.write_text(json.dumps(data, indent=2))


def _create_session_token(username: str, secret: str) -> str:
    ts = str(int(time.time()))
    msg = f"{username}:{ts}"
    sig = hmac.new(secret.encode(), msg.encode(), hashlib.sha256).hexdigest()
    return f"{msg}:{sig}"


def _verify_session_token(token: str, secret: str) -> bool:
    parts = token.split(":")
    if len(parts) != 3:
        return False
    username, ts_str, sig = parts
    try:
        ts = int(ts_str)
    except ValueError:
        return False
    if time.time() - ts > SESSION_MAX_AGE:
        return False
    expected = hmac.new(secret.encode(), f"{username}:{ts_str}".encode(), hashlib.sha256).hexdigest()
    return hmac.compare_digest(sig, expected)


def _get_session_user(request: Request) -> str | None:
    auth = _load_auth()
    if not auth:
        return None
    token = request.cookies.get(SESSION_COOKIE)
    if not token:
        return None
    if _verify_session_token(token, auth.get("secret", "")):
        return auth.get("username")
    return None


def _needs_setup() -> bool:
    return _load_auth() is None


# ---------------------------------------------------------------------------
# Admin dashboard data
# ---------------------------------------------------------------------------

KEYLESS_ENGINES = [
    "duckduckgo", "mojeek", "bing", "googlenews", "anysearch",
    "arxiv", "openalex", "crossref", "pubmed", "github",
    "stackexchange", "hackernews", "wikipedia", "openlibrary",
    "openverse", "zenodo", "bilibili", "sogou", "so360", "gdelt",
]

BROWSER_ENGINES = ["google", "startpage", "brave", "baidu", "zhihu"]
API_KEY_ENGINES = ["brave_api", "serper", "tavily", "google_cse", "github_code"]

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


# ---------------------------------------------------------------------------
# CSS (shared across all pages)
# ---------------------------------------------------------------------------

SHARED_CSS = """\
html, body {{ margin:0 !important; padding:0 !important; }}
body.mcp-admin, body.mcp-guide, body.mcp-auth {{
  display:flex !important;
  justify-content:center !important;
  min-height:100vh;
  font-family: "Inter", system-ui, -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif !important;
  background:#0b1120 !important;
  color:#e2e8f0 !important;
  line-height:1.65 !important;
  font-size:15px !important;
}}
.mcp-page {{
  width:100% !important;
  max-width:1100px !important;
  margin:0 auto !important;
  padding:2.5rem 1.75rem 4rem !important;
  box-sizing:border-box !important;
}}
.mcp-header {{
  display:flex; align-items:center; justify-content:space-between; gap:1rem;
  flex-wrap:wrap; margin-bottom:1.5rem; padding-bottom:1rem;
  border-bottom:1px solid #27364b;
}}
.mcp-header h1 {{
  margin:0; font-size:2rem; color:#38bdf8; letter-spacing:-0.02em; font-weight:700;
}}
.mcp-header p {{
  margin:.3rem 0 0; color:#94a3b8; font-size:1rem;
}}
.mcp-btn {{
  display:inline-flex; align-items:center; gap:.5rem;
  background:#2563eb; color:#fff; border:none; padding:.75rem 1.25rem;
  border-radius:.6rem; font-size:.95rem; font-weight:600; cursor:pointer;
  text-decoration:none; transition:background .15s;
}}
.mcp-btn:hover {{ background:#1d4ed8; }}
.mcp-btn-secondary {{ background:#334155; }}
.mcp-btn-secondary:hover {{ background:#475569; }}
.mcp-card {{
  background:#151e32; border:1px solid #27364b; border-radius:1rem;
  padding:1.5rem 1.75rem; margin-bottom:1.5rem;
  box-shadow:0 4px 24px rgba(0,0,0,.25);
}}
.mcp-card h2 {{
  margin:0 0 .9rem; font-size:1.35rem; color:#7dd3fc; font-weight:700;
}}
.mcp-card h3 {{
  margin:1.4rem 0 .6rem; font-size:1.1rem; color:#94a3b8; font-weight:600;
}}
.mcp-card p:first-child {{ margin-top:0; }}
.mcp-card p {{
  margin:.7rem 0; color:#cbd5e1;
}}
.mcp-card ul, .mcp-card ol {{
  margin:.6rem 0 0; padding-left:1.3rem; color:#cbd5e1;
}}
.mcp-card li {{ margin:.4rem 0; }}
.mcp-code {{
  background:#0f172a; color:#7dd3fc; padding:.2rem .5rem; border-radius:.35rem;
  font-family: ui-monospace, SFMono-Regular, Menlo, monospace; font-size:.88em;
  border:1px solid #334155;
}}
.mcp-pre {{
  background:#0b1120; color:#e2e8f0; padding:1rem 1.25rem; border-radius:.75rem;
  overflow-x:auto; border:1px solid #334155;
  font-family: ui-monospace, SFMono-Regular, Menlo, monospace; font-size:.9rem;
}}
.mcp-label {{
  display:block; font-weight:600; color:#94a3b8; font-size:.9rem; margin-bottom:.45rem;
}}
.mcp-input, .mcp-select {{
  width:100%; background:#0b1120; border:1px solid #334155; color:#e2e8f0;
  padding:.7rem 1rem; border-radius:.5rem; font-size:.95rem; outline:none;
  box-sizing:border-box;
}}
.mcp-input:focus, .mcp-select:focus {{ border-color:#38bdf8; box-shadow:0 0 0 3px rgba(56,189,248,.15); }}
.mcp-select {{
  appearance:none;
  background-image:url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='12' height='12' viewBox='0 0 12 12'%3E%3Cpath fill='%2394a3b8' d='M6 8L1 3h10z'/%3E%3C/svg%3E");
  background-repeat:no-repeat; background-position:right .9rem center; padding-right:2.2rem;
}}
.mcp-engine-grid {{
  display:grid; grid-template-columns: repeat(auto-fill, minmax(155px, 1fr)); gap:.6rem; margin-top:.6rem;
}}
.mcp-engine-chip {{
  display:flex; align-items:center; gap:.5rem;
  background:#0b1120; border:1px solid #334155; border-radius:.5rem;
  padding:.55rem .75rem; cursor:pointer; transition:background .12s, border-color .12s;
}}
.mcp-engine-chip:hover {{ background:#1e293b; border-color:#475569; }}
.mcp-engine-chip input {{ width:auto; accent-color:#38bdf8; margin:0; }}
.mcp-engine-chip span {{ color:#e2e8f0; font-size:.9rem; font-weight:500; }}
.mcp-help {{
  color:#64748b; font-size:.85rem; margin-top:.35rem;
}}
.mcp-status {{
  margin:1rem 0; padding:.85rem 1.1rem; border-radius:.5rem;
  background:#14532d; color:#86efac; border:1px solid #166534;
}}
.mcp-status.error {{ background:#450a0a; color:#fca5a5; border-color:#7f1d1d; }}
.mcp-note {{
  margin-top:1.5rem; padding:1rem 1.25rem; background:#0b1120;
  border:1px solid #27364b; border-radius:.75rem; color:#94a3b8; font-size:.9rem;
}}
.mcp-toc {{
  background:#151e32; border:1px solid #27364b; border-radius:1rem;
  padding:1.25rem 1.5rem; margin:1.25rem 0 1.75rem;
}}
.mcp-toc h3 {{
  margin:0 0 .75rem; color:#38bdf8; font-size:1.15rem;
}}
.mcp-toc ul {{
  list-style:none; padding:0; margin:0;
  display:grid; grid-template-columns: repeat(auto-fill, minmax(240px, 1fr)); gap:.4rem;
}}
.mcp-toc a {{
  color:#94a3b8; text-decoration:none; font-size:.95rem; display:block; padding:.25rem 0;
}}
.mcp-toc a:hover {{ color:#38bdf8; }}
.mcp-badge {{
  display:inline-block; background:#1e293b; border:1px solid #334155; color:#e2e8f0;
  padding:.3rem .65rem; border-radius:999px; font-size:.82rem; margin:.2rem;
}}
.mcp-badge.green {{ background:#14532d; border-color:#166534; color:#86efac; }}
.mcp-badge.orange {{ background:#451a03; border-color:#78350f; color:#fdba74; }}
.mcp-badge.red {{ background:#450a0a; border-color:#7f1d1d; color:#fca5a5; }}
.mcp-badge-row {{ margin:.6rem 0 1rem; }}
.mcp-table-wrap {{ overflow-x:auto; }}
.mcp-table {{
  width:100%; border-collapse:collapse; margin-top:.5rem; font-size:.95rem;
}}
.mcp-table th, .mcp-table td {{
  text-align:left; padding:.75rem 1rem; border-bottom:1px solid #27364b;
}}
.mcp-table th {{
  color:#94a3b8; font-weight:600; background:#0b1120;
}}
.mcp-table tr:last-child td {{ border-bottom:none; }}
.mcp-table tr:hover td {{ background:#0f172a; }}
.mcp-center {{ text-align:center; }}

/* Admin-specific */
.mcp-form-grid {{
  display:grid; grid-template-columns: repeat(auto-fit, minmax(260px, 1fr)); gap:1.25rem;
}}
.mcp-form-group {{ margin-bottom:.25rem; }}
.mcp-save-bar {{
  display:flex; justify-content:space-between; align-items:center; gap:1rem;
  flex-wrap:wrap; margin:1.5rem 0; padding:1rem 1.25rem;
  background:#0b1120; border:1px solid #27364b; border-radius:.75rem;
}}
.mcp-save-bar p {{ margin:0; color:#94a3b8; }}
.mcp-engine-fieldset {{
  border:1px solid #27364b; border-radius:.75rem; padding:1rem; margin-top:1rem; background:#0b1120;
}}
.mcp-engine-fieldset legend {{
  color:#94a3b8; font-weight:600; padding:0 .5rem;
}}

/* Guide-specific */
.mcp-hero {{
  background: linear-gradient(135deg, #1e293b 0%, #0b1120 100%);
  border:1px solid #334155; text-align:center;
}}
.mcp-hero h2 {{ font-size:1.5rem; margin-bottom:.5rem; }}
.mcp-section-title {{
  display:flex; align-items:center; gap:.6rem; font-size:1.4rem;
  color:#7dd3fc; margin:2.25rem 0 .75rem; font-weight:700;
}}
.mcp-section-title .mcp-icon {{ font-size:1.6rem; }}
.mcp-step {{
  display:flex; gap:.85rem; align-items:flex-start; margin:.85rem 0;
}}
.mcp-step-num {{
  flex-shrink:0; width:1.9rem; height:1.9rem; display:flex;
  align-items:center; justify-content:center; background:#2563eb;
  color:#fff; border-radius:50%; font-weight:700; font-size:.85rem;
}}
.mcp-tip, .mcp-warning, .mcp-danger {{
  background:#0b1120; padding:.9rem 1.1rem; border-radius:0 .5rem .5rem 0;
  margin:1rem 0; color:#cbd5e1;
}}
.mcp-tip {{ border-left:4px solid #38bdf8; }}
.mcp-warning {{ border-left:4px solid #f59e0b; }}
.mcp-danger {{ border-left:4px solid #ef4444; }}
.mcp-engine-list {{
  display:grid; grid-template-columns: repeat(auto-fill, minmax(240px, 1fr)); gap:.75rem; margin-top:.75rem;
}}
.mcp-engine-item {{
  background:#0b1120; border:1px solid #334155; border-radius:.5rem; padding:.75rem 1rem;
}}
.mcp-engine-item strong {{
  display:block; color:#e2e8f0; margin-bottom:.2rem;
}}
.mcp-engine-item span {{
  color:#94a3b8; font-size:.9rem;
}}

/* Auth pages */
.mcp-auth-page {{
  width:100% !important; max-width:420px !important;
  margin:0 auto !important; padding:2rem 1.5rem !important;
  box-sizing:border-box !important;
  display:flex; flex-direction:column; justify-content:center; min-height:100vh;
}}
.mcp-auth-card {{
  background:#151e32; border:1px solid #27364b; border-radius:1rem;
  padding:2rem; box-shadow:0 8px 32px rgba(0,0,0,.35);
}}
.mcp-auth-card h1 {{
  margin:0 0 .5rem; font-size:1.6rem; color:#38bdf8; text-align:center;
}}
.mcp-auth-card p {{
  margin:.5rem 0 1.5rem; color:#94a3b8; text-align:center; font-size:.95rem;
}}
.mcp-auth-form {{ display:flex; flex-direction:column; gap:1rem; }}
.mcp-auth-form .mcp-form-group {{ margin-bottom:0; }}
.mcp-auth-footer {{
  margin-top:1.5rem; text-align:center;
}}
.mcp-auth-footer a {{
  color:#64748b; font-size:.85rem; text-decoration:none;
}}
.mcp-auth-footer a:hover {{ color:#38bdf8; }}
"""


# ---------------------------------------------------------------------------
# Auth pages (login + first-time setup)
# ---------------------------------------------------------------------------

LOGIN_HTML = """\
<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta http-equiv="Cache-Control" content="no-cache, no-store, must-revalidate">
<title>Web Search MCP — Login</title>
<style>{css}</style>
</head>
<body class="mcp-auth">
<div class="mcp-auth-page">
  <div class="mcp-auth-card">
    <h1>🔧 Admin Login</h1>
    <p>Enter your credentials to access the dashboard.</p>
    {message}
    <form method="post" action="/admin/login" class="mcp-auth-form">
      <div class="mcp-form-group">
        <label class="mcp-label" for="username">Username</label>
        <input class="mcp-input" type="text" id="username" name="username" required autofocus>
      </div>
      <div class="mcp-form-group">
        <label class="mcp-label" for="password">Password</label>
        <input class="mcp-input" type="password" id="password" name="password" required>
      </div>
      <button type="submit" class="mcp-btn" style="width:100%; justify-content:center; margin-top:.5rem;">Login</button>
    </form>
    <div class="mcp-auth-footer">
      <a href="/">← Back to landing page</a>
    </div>
  </div>
</div>
</body>
</html>
"""


SETUP_HTML = """\
<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta http-equiv="Cache-Control" content="no-cache, no-store, must-revalidate">
<title>Web Search MCP — Setup</title>
<style>{css}</style>
</head>
<body class="mcp-auth">
<div class="mcp-auth-page">
  <div class="mcp-auth-card">
    <h1>🔐 First Time Setup</h1>
    <p>Create your admin username and password. You can change the password later from the dashboard.</p>
    {message}
    <form method="post" action="/admin/setup" class="mcp-auth-form">
      <div class="mcp-form-group">
        <label class="mcp-label" for="username">Admin username</label>
        <input class="mcp-input" type="text" id="username" name="username" required autofocus>
      </div>
      <div class="mcp-form-group">
        <label class="mcp-label" for="password">Password</label>
        <input class="mcp-input" type="password" id="password" name="password" required minlength="6">
        <p class="mcp-help">At least 6 characters.</p>
      </div>
      <div class="mcp-form-group">
        <label class="mcp-label" for="password2">Confirm password</label>
        <input class="mcp-input" type="password" id="password2" name="password2" required minlength="6">
      </div>
      <button type="submit" class="mcp-btn" style="width:100%; justify-content:center; margin-top:.5rem;">Create Admin Account</button>
    </form>
    <div class="mcp-auth-footer">
      <a href="/">← Back to landing page</a>
    </div>
  </div>
</div>
</body>
</html>
"""


async def login_page(request: Request) -> HTMLResponse:
    if _needs_setup():
        return RedirectResponse(url="/admin/setup", status_code=302)
    return HTMLResponse(content=LOGIN_HTML.format(css=SHARED_CSS, message=""))


async def login_verify(request: Request) -> HTMLResponse:
    if _needs_setup():
        return RedirectResponse(url="/admin/setup", status_code=302)
    form = await request.form()
    username = form.get("username") or ""
    password = form.get("password") or ""
    auth = _load_auth()
    if auth and username == auth.get("username") and _verify_password(password, auth["hash"], auth["salt"]):
        token = _create_session_token(username, auth["secret"])
        resp = RedirectResponse(url="/admin", status_code=302)
        resp.set_cookie(SESSION_COOKIE, token, max_age=SESSION_MAX_AGE, httponly=True, samesite="lax")
        return resp
    msg = '<div class="mcp-status error">Wrong username or password.</div>'
    return HTMLResponse(content=LOGIN_HTML.format(css=SHARED_CSS, message=msg))


async def setup_page(request: Request) -> HTMLResponse:
    if not _needs_setup():
        return RedirectResponse(url="/admin/login", status_code=302)
    return HTMLResponse(content=SETUP_HTML.format(css=SHARED_CSS, message=""))


async def setup_verify(request: Request) -> HTMLResponse:
    if not _needs_setup():
        return RedirectResponse(url="/admin/login", status_code=302)
    form = await request.form()
    username = (form.get("username") or "").strip()
    password = form.get("password") or ""
    password2 = form.get("password2") or ""
    if not username or len(username) < 2:
        msg = '<div class="mcp-status error">Username must be at least 2 characters.</div>'
        return HTMLResponse(content=SETUP_HTML.format(css=SHARED_CSS, message=msg))
    if len(password) < 6:
        msg = '<div class="mcp-status error">Password must be at least 6 characters.</div>'
        return HTMLResponse(content=SETUP_HTML.format(css=SHARED_CSS, message=msg))
    if password != password2:
        msg = '<div class="mcp-status error">Passwords do not match.</div>'
        return HTMLResponse(content=SETUP_HTML.format(css=SHARED_CSS, message=msg))
    hashed, salt = _hash_password(password)
    secret = secrets.token_hex(32)
    _save_auth({"username": username, "hash": hashed, "salt": salt, "secret": secret})
    token = _create_session_token(username, secret)
    resp = RedirectResponse(url="/admin", status_code=302)
    resp.set_cookie(SESSION_COOKIE, token, max_age=SESSION_MAX_AGE, httponly=True, samesite="lax")
    return resp


async def logout(request: Request) -> HTMLResponse:
    resp = RedirectResponse(url="/admin/login", status_code=302)
    resp.delete_cookie(SESSION_COOKIE)
    return resp


# ---------------------------------------------------------------------------
# Admin dashboard
# ---------------------------------------------------------------------------

ADMIN_HTML = """\
<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta http-equiv="Cache-Control" content="no-cache, no-store, must-revalidate">
<title>Web Search MCP — Admin</title>
<style>{css}</style>
</head>
<body class="mcp-admin">
<div class="mcp-page">
  <div class="mcp-header">
    <div>
      <h1>🔧 Web Search MCP Admin</h1>
      <p>Logged in as <strong>{username}</strong> · <a href="/admin/logout" style="color:#94a3b8;">Logout</a></p>
    </div>
    <a href="/admin/guide" class="mcp-btn mcp-btn-secondary">📖 Guide</a>
  </div>
  
  <form method="post" action="/admin/save">
    <div class="mcp-save-bar">
      <p>Review the changes, then restart the service.</p>
      <button type="submit" class="mcp-btn">💾 Save + Restart</button>
    </div>
    
    <div class="mcp-card">
      <h2>Server basics</h2>
      <div class="mcp-form-grid">
        <div class="mcp-form-group">
          <label class="mcp-label" for="PORT">Server port</label>
          <input class="mcp-input" type="number" id="PORT" name="PORT" value="{PORT}" min="1000" max="65535">
          <p class="mcp-help">TCP port this app listens on. Default 38472.</p>
        </div>
        <div class="mcp-form-group">
          <label class="mcp-label" for="SEARCH_MCP_LOG_LEVEL">Log level</label>
          <select class="mcp-select" id="SEARCH_MCP_LOG_LEVEL" name="SEARCH_MCP_LOG_LEVEL">
            {log_level_options}
          </select>
          <p class="mcp-help">Use debug only when troubleshooting.</p>
        </div>
      </div>
    </div>
    
    <div class="mcp-card">
      <h2>Search behaviour</h2>
      <div class="mcp-form-grid">
        <div class="mcp-form-group">
          <label class="mcp-label" for="SEARCH_MCP_FETCH_STRATEGY">Fetch strategy</label>
          <select class="mcp-select" id="SEARCH_MCP_FETCH_STRATEGY" name="SEARCH_MCP_FETCH_STRATEGY">
            {fetch_strategy_options}
          </select>
          <p class="mcp-help">http = fast CPU-only. browser = Chromium. auto = fallback.</p>
        </div>
        <div class="mcp-form-group">
          <label class="mcp-label" for="SEARCH_MCP_RESCUE_ENABLED">Rescue fallback</label>
          <select class="mcp-select" id="SEARCH_MCP_RESCUE_ENABLED" name="SEARCH_MCP_RESCUE_ENABLED">
            {rescue_enabled_options}
          </select>
          <p class="mcp-help">Try backup engines if a search fails. Keep off for now.</p>
        </div>
        <div class="mcp-form-group">
          <label class="mcp-label" for="SEARCH_MCP_MAX_RESULTS">Max results per engine</label>
          <input class="mcp-input" type="number" id="SEARCH_MCP_MAX_RESULTS" name="SEARCH_MCP_MAX_RESULTS" value="{SEARCH_MCP_MAX_RESULTS}" min="1" max="50">
          <p class="mcp-help">10 is a good default. More = slower.</p>
        </div>
        <div class="mcp-form-group">
          <label class="mcp-label" for="SEARCH_MCP_HTTP_TIMEOUT">HTTP timeout (seconds)</label>
          <input class="mcp-input" type="number" id="SEARCH_MCP_HTTP_TIMEOUT" name="SEARCH_MCP_HTTP_TIMEOUT" value="{SEARCH_MCP_HTTP_TIMEOUT}" min="1" max="120">
        </div>
        <div class="mcp-form-group">
          <label class="mcp-label" for="SEARCH_MCP_REQUEST_DELAY">Request delay (seconds)</label>
          <input class="mcp-input" type="number" id="SEARCH_MCP_REQUEST_DELAY" name="SEARCH_MCP_REQUEST_DELAY" value="{SEARCH_MCP_REQUEST_DELAY}" min="0" max="30">
          <p class="mcp-help">Increase if engines start blocking you.</p>
        </div>
      </div>
    </div>
    
    <div class="mcp-card">
      <h2>Default search engines</h2>
      <p class="mcp-help">Tick the engines to use when no specific engine is requested. Extra engines (browser/API-key) go in the text box below.</p>
      <div class="mcp-engine-grid">
        {default_engines_checkboxes}
      </div>
      <div style="margin-top:1.25rem;">
        <label class="mcp-label" for="SEARCH_MCP_DEFAULT_ENGINES_extra">Extra engines (comma-separated)</label>
        <input class="mcp-input" type="text" id="SEARCH_MCP_DEFAULT_ENGINES_extra" name="SEARCH_MCP_DEFAULT_ENGINES_extra" value="{default_engines_extra}" placeholder="e.g. brave_api, google_cse, google, zhihu">
        <p class="mcp-help">Type names of browser/API-key engines not shown above. These need extra setup.</p>
      </div>
    </div>
    
    <div class="mcp-card">
      <h2>Rescue engines</h2>
      <p class="mcp-help">Used only if Rescue fallback is enabled.</p>
      <div class="mcp-engine-grid">
        {rescue_engines_checkboxes}
      </div>
      <div style="margin-top:1.25rem;">
        <label class="mcp-label" for="SEARCH_MCP_RESCUE_ENGINES_extra">Extra rescue engines (comma-separated)</label>
        <input class="mcp-input" type="text" id="SEARCH_MCP_RESCUE_ENGINES_extra" name="SEARCH_MCP_RESCUE_ENGINES_extra" value="{rescue_engines_extra}" placeholder="e.g. bing, duckduckgo">
      </div>
    </div>
    
    <div class="mcp-card">
      <h2>Cache</h2>
      <div class="mcp-form-grid">
        <div class="mcp-form-group">
          <label class="mcp-label" for="SEARCH_MCP_CACHE_TTL_HOURS">Cache TTL (hours)</label>
          <input class="mcp-input" type="number" id="SEARCH_MCP_CACHE_TTL_HOURS" name="SEARCH_MCP_CACHE_TTL_HOURS" value="{SEARCH_MCP_CACHE_TTL_HOURS}" min="1" max="720">
        </div>
        <div class="mcp-form-group">
          <label class="mcp-label" for="SEARCH_MCP_CACHE_MAX_MB">Cache max size (MB)</label>
          <input class="mcp-input" type="number" id="SEARCH_MCP_CACHE_MAX_MB" name="SEARCH_MCP_CACHE_MAX_MB" value="{SEARCH_MCP_CACHE_MAX_MB}" min="10" max="2000">
        </div>
      </div>
    </div>
    
    <div class="mcp-save-bar">
      <p>Save all changes and restart the service.</p>
      <button type="submit" class="mcp-btn">💾 Save + Restart</button>
    </div>
    
    {status}
  </form>
  
  <div class="mcp-card">
    <h2>🔐 Change password</h2>
    <form method="post" action="/admin/change-password" class="mcp-form-grid" style="max-width:600px;">
      <div class="mcp-form-group">
        <label class="mcp-label" for="current_password">Current password</label>
        <input class="mcp-input" type="password" id="current_password" name="current_password" required>
      </div>
      <div class="mcp-form-group">
        <label class="mcp-label" for="new_password">New password</label>
        <input class="mcp-input" type="password" id="new_password" name="new_password" required minlength="6">
        <p class="mcp-help">At least 6 characters.</p>
      </div>
      <div class="mcp-form-group">
        <label class="mcp-label" for="new_password2">Confirm new password</label>
        <input class="mcp-input" type="password" id="new_password2" name="new_password2" required minlength="6">
      </div>
      <div class="mcp-form-group" style="display:flex; align-items:flex-end;">
        <button type="submit" class="mcp-btn">Change Password</button>
      </div>
    </form>
    {pw_status}
  </div>
  
  <div class="mcp-note">
    <strong>Files used:</strong> settings in <span class="mcp-code">{env_file}</span> · credentials in <span class="mcp-code">{auth_file}</span> · restart via <span class="mcp-code">systemctl --user restart web-search-mcp</span> · guide at <span class="mcp-code">/admin/guide</span>
  </div>
</div>
</body>
</html>
"""


def _admin_form(message: str = "", error: bool = False, pw_message: str = "", pw_error: bool = False, username: str = "admin") -> HTMLResponse:
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
            f'<label class="mcp-engine-chip">\n'
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
        status_html = f'<div class="mcp-status{" error" if error else ""}">{message}</div>\n'

    pw_status_html = ""
    if pw_message:
        pw_status_html = f'<div class="mcp-status{" error" if pw_error else ""}" style="margin-top:1rem;">{pw_message}</div>\n'

    content = ADMIN_HTML.format(
        css=SHARED_CSS,
        username=username,
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
        pw_status=pw_status_html,
        env_file=str(ENV_FILE),
        auth_file=str(AUTH_FILE),
    )
    return HTMLResponse(content=content)


async def admin_page(request: Request) -> HTMLResponse:
    if _needs_setup():
        return RedirectResponse(url="/admin/setup", status_code=302)
    user = _get_session_user(request)
    if not user:
        return RedirectResponse(url="/admin/login", status_code=302)
    return _admin_form(username=user)


async def admin_save(request: Request) -> HTMLResponse:
    if _needs_setup():
        return RedirectResponse(url="/admin/setup", status_code=302)
    user = _get_session_user(request)
    if not user:
        return RedirectResponse(url="/admin/login", status_code=302)

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
    return _admin_form(message=message, error=error, username=user)


async def admin_change_password(request: Request) -> HTMLResponse:
    if _needs_setup():
        return RedirectResponse(url="/admin/setup", status_code=302)
    user = _get_session_user(request)
    if not user:
        return RedirectResponse(url="/admin/login", status_code=302)

    form = await request.form()
    current_pw = form.get("current_password") or ""
    new_pw = form.get("new_password") or ""
    new_pw2 = form.get("new_password2") or ""

    auth = _load_auth()
    if not auth:
        return RedirectResponse(url="/admin/setup", status_code=302)

    if not _verify_password(current_pw, auth["hash"], auth["salt"]):
        return _admin_form(pw_message="Current password is wrong.", pw_error=True, username=user)
    if len(new_pw) < 6:
        return _admin_form(pw_message="New password must be at least 6 characters.", pw_error=True, username=user)
    if new_pw != new_pw2:
        return _admin_form(pw_message="New passwords do not match.", pw_error=True, username=user)

    hashed, salt = _hash_password(new_pw)
    auth["hash"] = hashed
    auth["salt"] = salt
    _save_auth(auth)
    return _admin_form(pw_message="Password changed successfully.", username=user)


# ---------------------------------------------------------------------------
# Guide page
# ---------------------------------------------------------------------------

GUIDE_HTML = """\
<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta http-equiv="Cache-Control" content="no-cache, no-store, must-revalidate">
<title>Web Search MCP — Guide</title>
<style>{css}</style>
</head>
<body class="mcp-guide">
<div class="mcp-page">
  <div class="mcp-header">
    <div>
      <h1>📖 Web Search MCP Guide</h1>
      <p>Everything you need to use and configure this search server.</p>
    </div>
    <a href="/admin" class="mcp-btn mcp-btn-secondary">🔧 Admin</a>
  </div>
  
  <div class="mcp-card mcp-hero">
    <h2>What is this thing?</h2>
    <p>This is a <strong>multi-engine search server</strong>. Other programs — Hermes, Reachy, Claude, Codex — can ask it &quot;find me X&quot;. It then queries several search engines, merges the results, removes duplicates, and returns a clean answer.</p>
    <p>It speaks <strong>MCP</strong> (Model Context Protocol), a kind of &quot;USB-C for AI tools&quot;. Any MCP client can plug in and search the web.</p>
  </div>
  
  <div class="mcp-toc">
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
  
  <div class="mcp-section-title" id="urls"><span class="mcp-icon">🔗</span> URLs you actually need</div>
  <div class="mcp-card">
    <table class="mcp-table">
      <tr><th>Page</th><th>URL</th></tr>
      <tr><td><strong>Landing page</strong></td><td><span class="mcp-code">https://search.ai-vibe.org/</span></td></tr>
      <tr><td><strong>Admin settings</strong></td><td><span class="mcp-code">https://search.ai-vibe.org/admin</span></td></tr>
      <tr><td><strong>MCP endpoint (robot/app)</strong></td><td><span class="mcp-code">https://search.ai-vibe.org/gradio_api/mcp/</span></td></tr>
    </table>
  </div>
  
  <div class="mcp-section-title" id="settings"><span class="mcp-icon">⚙️</span> Settings explained</div>
  <div class="mcp-card">
    <div class="mcp-table-wrap">
      <table class="mcp-table">
        <tr><th>Setting</th><th>What it does</th></tr>
        <tr><td><strong>Server port</strong></td><td>TCP port the server listens on. Default 38472. Change only if another app on this PC uses it.</td></tr>
        <tr><td><strong>Default search engines</strong></td><td>Which engines are used when you or the robot just say &quot;search&quot;. Tick the checkboxes in admin.</td></tr>
        <tr><td><strong>Fetch strategy</strong></td>
          <td>
            <span class="mcp-badge green">http</span> fast, no browser. <br>
            <span class="mcp-badge orange">browser</span> uses Playwright/Chromium, slow and heavy. <br>
            <span class="mcp-badge">auto</span> tries HTTP first, falls back to browser.
          </td>
        </tr>
        <tr><td><strong>Rescue fallback</strong></td><td>If <span class="mcp-code">true</span>, tries backup engines when a search fails. We keep it <span class="mcp-code">false</span> because the default rescue engine (searx) is dead and mislabels results.</td></tr>
        <tr><td><strong>Rescue engines</strong></td><td>Backup engines used only when rescue fallback is enabled.</td></tr>
        <tr><td><strong>Max results per engine</strong></td><td>How many results to ask each engine for. 10 is good; more = slower.</td></tr>
        <tr><td><strong>HTTP timeout</strong></td><td>Seconds to wait for each engine before giving up. 15 is fine.</td></tr>
        <tr><td><strong>Request delay</strong></td><td>Seconds to sleep between requests. Increase if engines start blocking you.</td></tr>
        <tr><td><strong>Cache TTL / Max size</strong></td><td>How long fetched pages stay on disk (hours) and the max disk space (MB). Old items are deleted automatically.</td></tr>
        <tr><td><strong>Log level</strong></td><td><span class="mcp-code">debug</span>, <span class="mcp-code">info</span>, <span class="mcp-code">warning</span>, <span class="mcp-code">error</span>. Use debug only when troubleshooting.</td></tr>
      </table>
    </div>
  </div>
  
  <div class="mcp-section-title" id="engines"><span class="mcp-icon">🚂</span> Engine list</div>
  <div class="mcp-card">
    <h3>Keyless engines — work out of the box</h3>
    <div class="mcp-badge-row">
      <span class="mcp-badge">duckduckgo</span> <span class="mcp-badge">mojeek</span> <span class="mcp-badge">bing</span> <span class="mcp-badge">googlenews</span> <span class="mcp-badge">anysearch</span>
      <span class="mcp-badge">arxiv</span> <span class="mcp-badge">openalex</span> <span class="mcp-badge">crossref</span> <span class="mcp-badge">pubmed</span> <span class="mcp-badge">github</span>
      <span class="mcp-badge">stackexchange</span> <span class="mcp-badge">hackernews</span> <span class="mcp-badge">wikipedia</span> <span class="mcp-badge">openlibrary</span>
      <span class="mcp-badge">openverse</span> <span class="mcp-badge">zenodo</span> <span class="mcp-badge">bilibili</span> <span class="mcp-badge">sogou</span> <span class="mcp-badge">so360</span> <span class="mcp-badge">gdelt</span>
    </div>
    <div class="mcp-engine-list">
      <div class="mcp-engine-item"><strong>General web</strong><span>duckduckgo, mojeek, bing, anysearch</span></div>
      <div class="mcp-engine-item"><strong>News</strong><span>googlenews, gdelt (slow)</span></div>
      <div class="mcp-engine-item"><strong>Academic</strong><span>arxiv, openalex, crossref, pubmed</span></div>
      <div class="mcp-engine-item"><strong>Code / tech</strong><span>github, stackexchange, hackernews</span></div>
      <div class="mcp-engine-item"><strong>Reference</strong><span>wikipedia, openlibrary, openverse, zenodo</span></div>
    </div>
    
    <h3 style="margin-top:1.5rem;">Browser engines — need Chromium/Playwright</h3>
    <div class="mcp-badge-row">
      <span class="mcp-badge orange">google</span> <span class="mcp-badge orange">startpage</span> <span class="mcp-badge orange">brave</span> <span class="mcp-badge orange">baidu</span> <span class="mcp-badge orange">zhihu</span>
    </div>
    
    <h3 style="margin-top:1.5rem;">API-key engines — need signup + key</h3>
    <div class="mcp-badge-row">
      <span class="mcp-badge red">brave_api</span> <span class="mcp-badge red">serper</span> <span class="mcp-badge red">tavily</span> <span class="mcp-badge red">google_cse</span> <span class="mcp-badge red">github_code</span>
    </div>
    <div class="mcp-warning">
      Browser and API-key engines are not enabled by default. They need extra setup. Type their names in the <strong>Extra engines</strong> box in admin if you want to use them.
    </div>
  </div>
  
  <div class="mcp-section-title" id="howto"><span class="mcp-icon">✏️</span> How to change engines</div>
  <div class="mcp-card">
    <div class="mcp-step"><div class="mcp-step-num">1</div><div>Go to <a href="/admin">/admin</a> and log in.</div></div>
    <div class="mcp-step"><div class="mcp-step-num">2</div><div>Under <strong>Default search engines</strong>, tick the engines you want.</div></div>
    <div class="mcp-step"><div class="mcp-step-num">3</div><div>Click <strong>Save + Restart</strong>.</div></div>
    <div class="mcp-step"><div class="mcp-step-num">4</div><div>Wait about 5 seconds, then test with the curl command below.</div></div>
    
    <div class="mcp-tip">
      To add an engine not shown in the checkboxes (like <span class="mcp-code">brave_api</span>), type it in the <strong>Extra engines</strong> box, comma-separated. Example: <span class="mcp-code">brave_api, google_cse</span>.
    </div>
  </div>
  
  <div class="mcp-section-title" id="browser"><span class="mcp-icon">🌐</span> How to enable browser or API-key engines</div>
  <div class="mcp-card">
    <ol>
      <li>Install Chromium / Playwright on this PC if you want browser engines.</li>
      <li>Set <strong>Fetch strategy</strong> to <span class="mcp-code">browser</span> or <span class="mcp-code">auto</span>.</li>
      <li>Type the engine names into the <strong>Extra engines</strong> box, e.g. <span class="mcp-code">google, brave_api</span>.</li>
      <li>Save + Restart.</li>
    </ol>
    <div class="mcp-danger">
      API keys are set separately as environment variables, not in this dashboard. See the upstream <span class="mcp-code">free-search-mcp</span> docs for the exact variable names.
    </div>
  </div>
  
  <div class="mcp-section-title" id="test"><span class="mcp-icon">🧪</span> How to test the server</div>
  <div class="mcp-card">
    <p>Run this in a terminal on this PC:</p>
    <pre class="mcp-pre">curl -X POST http://localhost:38472/gradio_api/mcp/ \\
  -H 'Content-Type: application/json' \\
  -d '{{"jsonrpc":"2.0","id":1,"method":"tools/list"}}'</pre>
    <p>You should get a big JSON list of tools. If you do, the server is alive.</p>
  </div>
  
  <div class="mcp-section-title" id="robot"><span class="mcp-icon">🤖</span> How the robot uses it</div>
  <div class="mcp-card">
    <p>The robot (or Hermes) needs the MCP URL. On your local network the URL is:</p>
    <pre class="mcp-pre">http://192.168.1.3:38472/gradio_api/mcp/</pre>
    <p>Or from the internet:</p>
    <pre class="mcp-pre">https://search.ai-vibe.org/gradio_api/mcp/</pre>
    <div class="mcp-tip">
      The MCP endpoint is public — no login needed. Only the admin dashboard requires login.
    </div>
  </div>
  
  <div class="mcp-section-title" id="trouble"><span class="mcp-icon">🛠️</span> Troubleshooting</div>
  <div class="mcp-card">
    <ul>
      <li><strong>Server not responding?</strong> Run <span class="mcp-code">systemctl --user status web-search-mcp</span>.</li>
      <li><strong>Port conflict?</strong> Change <strong>Server port</strong> in admin, save + restart.</li>
      <li><strong>Bad JSON error on restart?</strong> Make sure engine names in the Extra engines box have no spaces or quotes. Use plain commas.</li>
      <li><strong>Slow searches?</strong> Untick slow engines like <span class="mcp-code">gdelt</span> or reduce <strong>Max results per engine</strong>.</li>
      <li><strong>Forgot admin password?</strong> Delete <span class="mcp-code">.auth.json</span> in the hf-space directory and restart the service to set a new one.</li>
    </ul>
  </div>
  
  <div class="mcp-center" style="margin-top:2rem;">
    <a href="/admin" class="mcp-btn mcp-btn-secondary">← Back to admin settings</a>
  </div>
</div>
</body>
</html>
"""


async def admin_guide(request: Request) -> HTMLResponse:
    if _needs_setup():
        return RedirectResponse(url="/admin/setup", status_code=302)
    user = _get_session_user(request)
    if not user:
        return RedirectResponse(url="/admin/login", status_code=302)
    return HTMLResponse(content=GUIDE_HTML.format(css=SHARED_CSS))


# ---------------------------------------------------------------------------
# App wiring
# ---------------------------------------------------------------------------

parent = FastAPI(title="Web Search MCP", docs_url=None, redoc_url=None, lifespan=lifespan)
parent.mount("/mcp", _mcp_mount)
parent.mount("/gradio_api/mcp", _mcp_mount)

# Auth routes (public)
parent.get("/admin/login")(login_page)
parent.post("/admin/login")(login_verify)
parent.get("/admin/setup")(setup_page)
parent.post("/admin/setup")(setup_verify)
parent.get("/admin/logout")(logout)

# Protected routes
parent.get("/admin")(admin_page)
parent.post("/admin/save")(admin_save)
parent.post("/admin/change-password")(admin_change_password)
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