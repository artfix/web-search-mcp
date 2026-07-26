"""Golden-HTML parse() tests for the default-pool HTML scrapers (offline).

duckduckgo, mojeek, and bing are three of the four default engines, and HTML
markup drift is this project's most likely silent failure mode — an engine
whose selectors stop matching returns [] with no error. These fixtures freeze
the markup shape each parser expects, so a selector edit that breaks parsing
fails a test instead of silently emptying the default pool.
"""

from __future__ import annotations

from search_mcp.engines.bing import BingEngine
from search_mcp.engines.duckduckgo import DuckDuckGoEngine, _unwrap
from search_mcp.engines.mojeek import MojeekEngine

# ---------------------------------------------------------------------------
# duckduckgo (html.duckduckgo.com/html)
# ---------------------------------------------------------------------------

_DDG_HTML = """
<html><body>
  <div class="serp__results">
    <div class="result results_links results_links_deep web-result">
      <h2 class="result__title">
        <a class="result__a" href="//duckduckgo.com/l/?uddg=https%3A%2F%2Fexample.com%2Ffirst&amp;rut=abc">
          First result title
        </a>
      </h2>
      <a class="result__snippet" href="//duckduckgo.com/l/?uddg=https%3A%2F%2Fexample.com%2Ffirst">
        First snippet, published 2 days ago.
      </a>
    </div>
    <div class="result result--ad">
      <h2 class="result__title">
        <a class="result__a" href="https://duckduckgo.com/y.js?ad_provider=bingv7aa&amp;u3=spam">Buy stuff</a>
      </h2>
    </div>
    <div class="result web-result">
      <h2 class="result__title">
        <a class="result__a" href="//duckduckgo.com/l/?uddg=https%3A%2F%2Fen.wikipedia.org%2Fwiki%2FC%252B%252B">
          C++ - Wikipedia
        </a>
      </h2>
      <a class="result__snippet">Second snippet.</a>
    </div>
    <div class="result web-result">
      <h2 class="result__title">
        <a class="result__a" href="//duckduckgo.com/l/?uddg=https%3A%2F%2Fexample.com%2Ffirst">
          Duplicate of the first URL
        </a>
      </h2>
    </div>
  </div>
</body></html>
"""


def test_ddg_parse_extracts_organic_results():
    results = DuckDuckGoEngine().parse(_DDG_HTML)
    urls = [r.url for r in results]
    assert urls == [
        "https://example.com/first",
        "https://en.wikipedia.org/wiki/C%2B%2B",
    ]
    assert results[0].title == "First result title"
    assert "First snippet" in results[0].snippet
    assert results[0].engine == "duckduckgo"


def test_ddg_parse_skips_ads_and_dedups_urls():
    results = DuckDuckGoEngine().parse(_DDG_HTML)
    assert all("y.js" not in r.url and "ad_provider" not in r.url for r in results)
    # The fourth row repeats the first URL; the seen-set must drop it.
    assert len(results) == 2


def test_ddg_parse_sets_date_hint_from_snippet():
    results = DuckDuckGoEngine().parse(_DDG_HTML)
    assert results[0].published_age  # "2 days ago" in the snippet


def test_ddg_parse_empty_shell_yields_nothing():
    assert DuckDuckGoEngine().parse("<html><body>bot check</body></html>") == []


def test_ddg_unwrap_decodes_redirect_exactly_once():
    # parse_qs percent-decodes once; a second decode would turn the literal
    # %2B%2B of a real "C++" URL into ++ and 404.
    wrapped = "//duckduckgo.com/l/?uddg=https%3A%2F%2Fen.wikipedia.org%2Fwiki%2FC%252B%252B"
    assert _unwrap(wrapped) == "https://en.wikipedia.org/wiki/C%2B%2B"


def test_ddg_unwrap_passes_direct_urls_through():
    assert _unwrap("https://example.com/x") == "https://example.com/x"


# ---------------------------------------------------------------------------
# mojeek
# ---------------------------------------------------------------------------

_MOJEEK_HTML = """
<html><body>
  <ul class="results-standard">
    <li>
      <h2><a class="title" href="https://example.org/alpha">Alpha page</a></h2>
      <p class="s">Alpha snippet text. Jan 5, 2024.</p>
    </li>
    <li>
      <h2><a class="title" href="https://example.org/beta">Beta page</a></h2>
      <p class="s">Beta snippet text.</p>
    </li>
    <li>
      <!-- row without the title anchor must be skipped, not crash -->
      <p class="s">Orphan snippet.</p>
    </li>
  </ul>
</body></html>
"""


def test_mojeek_parse_extracts_results():
    results = MojeekEngine().parse(_MOJEEK_HTML)
    assert [(r.title, r.url) for r in results] == [
        ("Alpha page", "https://example.org/alpha"),
        ("Beta page", "https://example.org/beta"),
    ]
    assert results[0].snippet.startswith("Alpha snippet")
    assert results[0].engine == "mojeek"


def test_mojeek_parse_sets_date_hint():
    results = MojeekEngine().parse(_MOJEEK_HTML)
    assert results[0].published_age  # "Jan 5, 2024" in the snippet


def test_mojeek_parse_empty_shell_yields_nothing():
    assert MojeekEngine().parse("<html><body><ul></ul></body></html>") == []


# ---------------------------------------------------------------------------
# bing (www4 edge)
# ---------------------------------------------------------------------------

_BING_HTML = """
<html><body>
  <ol id="b_results">
    <li class="b_algo">
      <h2><a href="https://example.net/one">One - Example</a></h2>
      <div class="b_caption">
        <p>Primary caption snippet. 3 hours ago.</p>
      </div>
    </li>
    <li class="b_algo">
      <h2><a href="https://example.net/two">Two - Example</a></h2>
      <div class="b_lineclamp2">Clamped snippet without a caption block.</div>
    </li>
    <li class="b_ad">
      <h2><a href="https://ads.example.net/x">Sponsored thing</a></h2>
    </li>
    <li class="b_algo">
      <!-- no h2 a: must be skipped -->
      <div class="b_caption"><p>No link here.</p></div>
    </li>
  </ol>
</body></html>
"""


def test_bing_parse_extracts_organic_results():
    results = BingEngine().parse(_BING_HTML)
    assert [(r.title, r.url) for r in results] == [
        ("One - Example", "https://example.net/one"),
        ("Two - Example", "https://example.net/two"),
    ]
    assert results[0].engine == "bing"


def test_bing_parse_snippet_fallback_selectors():
    results = BingEngine().parse(_BING_HTML)
    assert results[0].snippet.startswith("Primary caption")
    # Second row has no .b_caption p; the .b_lineclamp2 fallback must kick in.
    assert results[1].snippet.startswith("Clamped snippet")


def test_bing_parse_ignores_ads_and_linkless_rows():
    results = BingEngine().parse(_BING_HTML)
    assert all("ads.example.net" not in r.url for r in results)
    assert len(results) == 2


def test_bing_parse_sets_date_hint():
    results = BingEngine().parse(_BING_HTML)
    assert results[0].published_age  # "3 hours ago" in the caption


def test_bing_parse_empty_shell_yields_nothing():
    assert BingEngine().parse("<html><body>something went wrong</body></html>") == []
