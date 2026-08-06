import base64
import binascii
import re
from urllib.parse import quote_plus

from ..config import settings
from .base import (
    Engine,
    SearchFilters,
    SearchResult,
    _region_to_bing_market,
    augment_query_with_operators,
    extract_date_hint,
    parse_html,
    safesearch_param,
    text_of,
)

# Organic Bing hrefs are frequently wrapped in a click-tracking redirect:
#   https://www.bing.com/ck/a?!&&p=<hash>&u=a1<base64url of the real URL>&ntb=1
# The `u` value carries a 2-character type prefix ("a1") in front of the
# base64url payload.
_BING_REDIRECT_U = re.compile(r"[?&]u=([A-Za-z0-9+/_=-]+)")


def resolve_bing_url(raw_url: str) -> str:
    """Unwrap a bing.com/ck/a click-tracking redirect to the publisher URL.

    Leaving the wrapper in place is not cosmetic. The blob is unique per SERP
    impression, so it defeats both the URL-keyed RRF merge and _dedup_by_title
    in the aggregator: the same page found by Bing and by DuckDuckGo is scored
    as two different results and both are emitted, spending the caller's
    max_results on duplicates. It also hands the model a link that says nothing
    about the publisher and cannot be judged for relevance without fetching it.

    Anything that is not a decodable wrapper is returned unchanged — a working
    redirect link beats dropping the result.
    """
    match = _BING_REDIRECT_U.search(raw_url)
    if not match:
        return raw_url
    encoded = match.group(1)
    if len(encoded) < 3:
        return raw_url
    payload = encoded[2:]
    # base64url -> base64, re-padded to a multiple of 4.
    padded = payload.replace("-", "+").replace("_", "/")
    padded += "=" * (-len(padded) % 4)
    try:
        decoded = base64.b64decode(padded).decode("utf-8")
    except (binascii.Error, UnicodeDecodeError, ValueError):
        return raw_url
    return decoded if decoded.startswith("http") else raw_url


# Bing's documented freshness filter values.
_BING_FRESHNESS = {
    "day": 'ex1:"ez1"',
    "week": 'ex1:"ez2"',
    "month": 'ex1:"ez3"',
    "year": 'ex1:"ez4"',
}


class BingEngine(Engine):
    name = "bing"
    # The www4 edge serves 10 real organic results over plain HTTP in ~0.3s
    # (verified), so we try HTTP FIRST and only pay for a Playwright render when
    # parse() comes back empty (a real gate) via the inherited
    # supports_browser_fallback. This is ~50x faster than the old always-browser
    # path on the common case. wait_selector still applies to the fallback render.
    needs_browser = False
    # Match the actual result item; #b_results is the empty container that
    # exists immediately and would short-circuit the wait.
    wait_selector = "li.b_algo"
    # Ask Bing as Edge — Microsoft's own browser is the client its SERP is
    # built and tested against. Verified equivalent to the Chrome default here
    # (10 organic results either way), so this is about presenting a coherent,
    # expected identity rather than about unblocking anything.
    impersonate = "edge"

    # No search() override: bing behaves like every other engine now. A raise
    # (e.g. a www4 non-200 under fetch_strategy="http") lands in the
    # aggregator's per-engine `errors` map — visible, and enough to trigger
    # the rescue pass — instead of being swallowed into a fake "silent zero"
    # the empty-engine hint would then mislabel as "no error". The old SearXNG
    # fallback moved to the aggregator's rescue pass.

    def build_url(
        self, query: str, max_results: int, filters: SearchFilters | None = None
    ) -> str:
        # www.bing.com aggressively challenges headless clients ("something went
        # wrong" page). The www4 edge serves the same index without that gate.
        # form=QBLH switches to a lighter JS-rendered layout missing .b_algo,
        # so we omit it.
        count = min(max(max_results, 10), 50)
        filetype = None
        if filters and filters.category == "pdf":
            filetype = "pdf"
        q = augment_query_with_operators(
            query,
            include_domains=filters.include_domains if filters else None,
            exclude_domains=filters.exclude_domains if filters else None,
            filetype=filetype,
        )
        url = f"https://www4.bing.com/search?q={quote_plus(q)}&count={count}"
        if filters and filters.freshness:
            url += f"&filters={quote_plus(_BING_FRESHNESS[filters.freshness])}"
        # SafeSearch: adlt=strict|moderate|off maps 1:1 to our setting.
        adlt = safesearch_param(self.name)
        if adlt is not None:
            url += f"&adlt={adlt}"
        # Region -> Bing market code, e.g. us-en -> en-US, uk-en -> en-GB.
        if settings.region:
            url += f"&mkt={quote_plus(_region_to_bing_market(settings.region))}"
        return url

    def parse(self, html: str) -> list[SearchResult]:
        tree = parse_html(html)
        results: list[SearchResult] = []
        for li in tree.css("li.b_algo"):
            link = li.css_first("h2 a")
            if not link:
                continue
            url = resolve_bing_url(link.attributes.get("href", ""))
            title = text_of(link)
            snippet_node = (
                li.css_first(".b_caption p")
                or li.css_first(".b_lineclamp4")
                or li.css_first(".b_lineclamp2")
                or li.css_first(".b_paractl")
            )
            snippet = text_of(snippet_node)
            if not url or not title:
                continue
            result = SearchResult(title=title, url=url, snippet=snippet, engine=self.name, rank=0)
            hint = extract_date_hint(snippet) or extract_date_hint(title)
            if hint:
                result.published_age = hint
            results.append(result)
        return results
