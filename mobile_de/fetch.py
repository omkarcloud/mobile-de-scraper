"""mobile.de transport: plain curl_cffi with chrome TLS on the JSON API the
site's own pages call, plus two server-rendered pages. No browser, no
login, no cookie and no token. One session per request thread on the exit
config.MOBILE_DE_EXIT names (egress.exit_session): in production the
server's own IP (pod probe 2026-11-20, table in the config notes). A thread
whose call was refused rides config.MOBILE_DE_FALLBACK_EXIT (the gateway
exit) for config.MOBILE_DE_BLOCK_HOLD_S. A retryable failure drops the
session, so the retry rides a fresh connection (and, on the gateway exit, a
fresh address).

Upstreams (every one answered 200 from the server's own IP on 2026-11-20):

  the listing API   https://www.mobile.de/api   (the pages' own calls)
    /s?<filters>&ps=<offset>&psz=<size>   a search page: numResultsTotal and
                    the rows. `ps` is an offset, `psz` at most 500, and the
                    site serves the first 2,000 results of a search: a call
                    with ps + psz > 2000 answers no rows at all. Without
                    `sb` the rows come cheapest first; the page's own
                    default is sb=rel&od=up. A filter value the site does
                    not know answers 400 [{field, code}].
    /a/<id>         one listing in full. Unknown id: 404 {errors:[{key:
                    "entity.not-found"}]}.
    /r/makes/<class>, /r/models/<makeId>   the reference lists.
    Every call needs the page's `x-mobile-client` header (without it: 400
    "Missing or invalid client header") and takes `_lang`; without `_lang`
    the answer follows Accept-Language, and an unknown one falls back
    silently, so it is always sent.

  place suggestions   https://www.mobile.de/consumer/next/api/geodata/suggest/<CC>/<text>

  the dealer page     https://home.mobile.de/<SLUG>  or
                      /home/index.html?customerId=<id>   (flight data in the HTML)
      /home/redirect.html?customerId=<id>   a 301 to the dealer's own
                      website, or to his page here when he has none; 404
                      for a private seller.
  the reviews page    https://www.mobile.de/bewertungen/<SLUG>?page=&order=&filter=
                      10 reviews a page (flight data in the HTML). A page
                      past the end is a 200 without reviews; an unknown
                      dealer is a 404.

The search and listing PAGES (suchen.mobile.de) are not used: Akamai
answers curl a 2.7 KB interstitial with HTTP 200 there on every exit.

Each call goes out with the headers the site's own page sends for it (one
dict per kind of call below; the visitor token `x-mobile-vi` is left out:
the answers are the same without it).

Failure taxonomy (scraper_errors, mapped to HTTP by route_glue):
  MobileDeUpstreamError  transport failure / 5xx / unexpected body      retryable
  MobileDeBlocked        403 / 429 / an Akamai page where data was due  retryable
  MobileDeBadRequest     the site refused a filter value (400)          never retried
  MobileDeNotFound       unknown listing / dealer / make                never retried
"""
import threading
import time
from urllib.parse import quote, urlencode, urljoin, urlparse

import config
import egress
import fastjson
from scraper_errors import BadRequest, Blocked, NotFound, UpstreamError
from scraper_utils import retry, sessions
from mobile_de.refs import DEALER_SITE, SITE

IMPERSONATE = "chrome"
# Answers take 0.2-1 s; a gateway exit request gets 29 s in all, so a hung
# request is cut early and retried.
API_TIMEOUT = 20
PAGE_TIMEOUT = 25

API = SITE + "/api"
MAX_PAGE_SIZE = 500         # psz=500 answered 500 rows, psz=501 "page size exceeds limit" (2026-11-20)
SEARCH_WINDOW = 2000        # ps + psz <= 2000 answers rows, one more answers none (2026-11-20)
REVIEW_PAGE_SIZE = 10       # the reviews page shows 10; it takes no size

# The dealer page's own search call (GET /api/s?vc=Car&sid=<id>&psz=0).
SEARCH_HEADERS = {
    "accept": "*/*",
    "content-type": "application/json",
    "origin": DEALER_SITE,
    "priority": "u=1, i",
    "referer": DEALER_SITE + "/",
    "sec-fetch-dest": "empty",
    "sec-fetch-mode": "cors",
    "sec-fetch-site": "same-site",
    "x-mobile-api-version": "12",
    "x-mobile-client": "de.mobile.dealerhomepage",
}
# The listing page's own calls to the same API (GET /api/similar/...): the nearest call a page makes to /api/a/<id>.
LISTING_HEADERS = {
    "accept": "*/*",
    "content-type": "application/json",
    "origin": "https://suchen.mobile.de",
    "priority": "u=1, i",
    "referer": "https://suchen.mobile.de/",
    "sec-fetch-dest": "empty",
    "sec-fetch-mode": "cors",
    "sec-fetch-site": "same-site",
    "x-mobile-api-version": "12",
    "x-mobile-client": "de.mobile.consumer-webapp/vip.desktop",
    "x-mobile-device-type": "desktop",
}
# The listing page's make / model menus (GET /api/r/makes/Car?_lang=de).
REFERENCE_HEADERS = {
    "accept": "application/json, text/plain, */*",
    "origin": "https://suchen.mobile.de",
    "priority": "u=1, i",
    "referer": "https://suchen.mobile.de/",
    "sec-fetch-dest": "empty",
    "sec-fetch-mode": "cors",
    "sec-fetch-site": "same-site",
    "x-mobile-client": "mde-ui-ps-teaser",
}
# The home page's own calls to its /consumer/next/api.
NEXT_API_HEADERS = {
    "accept": "*/*",
    "priority": "u=1, i",
    "referer": SITE + "/",
    "sec-fetch-dest": "empty",
    "sec-fetch-mode": "cors",
    "sec-fetch-site": "same-origin",
}
# A page opened by its address (the dealer page, the reviews page, the dealer's redirect).
PAGE_HEADERS = {
    "accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/jxl,image/avif,image/webp,image/apng,"
              "*/*;q=0.8,application/signed-exchange;v=b3;q=0.7",
    "priority": "u=0, i",
    "sec-fetch-dest": "document",
    "sec-fetch-mode": "navigate",
    "sec-fetch-site": "none",
    "sec-fetch-user": "?1",
    "upgrade-insecure-requests": "1",
}


class MobileDeUpstreamError(UpstreamError):
    """Transport failure, 5xx, or an answer that is not the expected data — retryable."""


class MobileDeBlocked(MobileDeUpstreamError, Blocked):
    """403 / 429, or an Akamai page where data was due — retryable."""


class MobileDeBadRequest(BadRequest):
    """The site refused a filter value (HTTP 400) — never retried."""


class MobileDeNotFound(NotFound):
    """No such listing / dealer / make — never retried."""


# ---- sessions ------------------------------------------------------------------------

# One curl session per request thread (a curl handle must not be shared
# across threads). Nothing else lives here: the site needs no token.
_local = threading.local()


def _exit():
    """The exit this thread's next session rides: the fallback while its hold runs."""
    fallback = getattr(config, "MOBILE_DE_FALLBACK_EXIT", None)
    if fallback and getattr(_local, "hold_until", 0) > time.monotonic():
        return fallback
    return config.MOBILE_DE_EXIT


def _hold():
    """This thread's call was refused: its next sessions ride the fallback exit."""
    if getattr(config, "MOBILE_DE_FALLBACK_EXIT", None):
        _local.hold_until = time.monotonic() + config.MOBILE_DE_BLOCK_HOLD_S


def _session():
    sess = getattr(_local, "session", None)
    exit = _exit()
    if sess is not None and getattr(_local, "session_exit", None) != exit:
        _drop_session()                  # a hold began or ended: change exits
        sess = None
    if sess is None:
        sess = _local.session = egress.exit_session(exit, impersonate=IMPERSONATE)
        _local.session_exit = exit
    if getattr(sess, "via_exit_gateway", False):
        # Every gateway exit request leaves from a new address: an Akamai
        # cookie earned by the last one would arrive from the wrong address.
        # No call needs a cookie, so each goes out without the ones the site set.
        sess.cookies.clear()
    return sess


def _drop_session():
    """Close the thread's session so the retry rides a fresh connection / exit."""
    sessions.drop(_local)


def _on_failure(error):
    if isinstance(error, MobileDeBlocked):
        _hold()
    _drop_session()
    print(f"mobile-de: attempt failed: {error}")


def _retrying(fn):
    """fn() under the shared retry policy: MobileDeUpstreamError only (Blocked
    included), each retry on a fresh session; BadRequest and NotFound surface at once."""
    return retry.run(fn, config, MobileDeUpstreamError, _on_failure)


# ---- one request ---------------------------------------------------------------------

_REDIRECTS = (301, 302, 303, 307, 308)
_AKAMAI_MARKERS = ("access denied", "zugriff verweigert", "cpr_chlge", "akamai", "bm-verify", "sec-if-cpt-container")


def looks_blocked(status, text):
    """True for Akamai's refusals: a 403 / 429, or its own page (the "Access
    denied" page, the 2.7 KB interstitial it answers with HTTP 200, the
    {"cpr_chlge": "true"} it answers an API call with)."""
    if status in (403, 429):
        return True
    head = (text or "")[:6000].lower()
    return len(text or "") < 20000 and any(marker in head for marker in _AKAMAI_MARKERS)


def _send(url, label, headers, timeout=API_TIMEOUT):
    """One GET -> the response, redirects not followed. A transport failure is retryable."""
    try:
        return _session().get(url, headers=headers, timeout=timeout, allow_redirects=False)
    except Exception as e:
        raise MobileDeUpstreamError(f"{label}: request failed: {type(e).__name__}: {e}")


def _refusal(payload):
    """The site's 400 body [{"field": "query.fuel", "code": "invalid-arg"}] or
    {"errors": [{"key": "400", "args": [...]}]} -> what it refused, in words."""
    rows = payload if isinstance(payload, list) else (payload or {}).get("errors") if isinstance(payload, dict) else None
    first = rows[0] if isinstance(rows, list) and rows and isinstance(rows[0], dict) else {}
    field = str(first.get("field") or "")
    if field:
        return "the site refused " + (field[len("query."):] if field.startswith("query.") else field)
    args = first.get("args")
    return str(args[0]) if isinstance(args, list) and args else "the site refused the request"


def _api(url, label, headers, not_found=None):
    """GET a JSON call -> the decoded body. 404 -> MobileDeNotFound(`not_found`
    or the label), 400 -> MobileDeBadRequest with what the site refused."""
    def once():
        resp = _send(url, label, headers)
        status, text = resp.status_code, resp.text
        try:
            payload = fastjson.loads(resp.content)
        except ValueError:
            payload = None
        if payload is None or (isinstance(payload, dict) and "cpr_chlge" in payload):
            if looks_blocked(status, text):
                raise MobileDeBlocked(f"HTTP {status} for {label}")
            raise MobileDeUpstreamError(f"{label}: HTTP {status}, not JSON: {text[:120]!r}")
        if status == 404:
            raise MobileDeNotFound(not_found or f"{label} not found")
        if status == 400:
            raise MobileDeBadRequest(_refusal(payload))
        if status in (401, 403, 429):
            raise MobileDeBlocked(f"HTTP {status} for {label}")
        if status != 200:
            raise MobileDeUpstreamError(f"HTTP {status} for {label}")
        return payload
    return _retrying(once)


def _page(url, label, not_found):
    """GET a server-rendered page -> its HTML. 404 -> MobileDeNotFound; a page
    without its flight data is a block (Akamai's interstitial) or an upstream error."""
    def once():
        resp = _send(url, label, PAGE_HEADERS, timeout=PAGE_TIMEOUT)
        if resp.status_code in _REDIRECTS:
            # A slug in another letter case is sent on to the page's own address; anything else
            # the site redirects (to its home page, to another site) is not the page asked for.
            target = urljoin(url, (resp.headers.get("location") or "").strip())
            if urlparse(target).hostname != urlparse(url).hostname or target.split("?")[0].lower() != url.split("?")[0].lower():
                raise MobileDeNotFound(not_found)
            resp = _send(target, label, PAGE_HEADERS, timeout=PAGE_TIMEOUT)
        status, text = resp.status_code, resp.text
        if status == 404 and not looks_blocked(status, text):
            raise MobileDeNotFound(not_found)
        if status in _REDIRECTS:
            raise MobileDeNotFound(not_found)
        if looks_blocked(status, text):
            raise MobileDeBlocked(f"HTTP {status} for {label}")
        if status != 200:
            raise MobileDeUpstreamError(f"HTTP {status} for {label}")
        if "__next_f" not in text:
            raise MobileDeUpstreamError(f"{label}: the page carries no data")
        return text
    return _retrying(once)


# ---- the listing API -----------------------------------------------------------------

def fetch_search(pairs, offset=0, size=20, language="en"):
    """One search call -> the raw answer {numResultsTotal, items, searchId}.
    `pairs` is [(query parameter, value)] as the search page writes them
    (vc, ms, the filters, sb, od). The caller keeps offset + size inside
    SEARCH_WINDOW: past it the site answers no rows."""
    query = list(pairs) + [("ps", offset), ("psz", size), ("_lang", language)]
    payload = _api(f"{API}/s?{urlencode(query)}", "search", SEARCH_HEADERS)
    if not isinstance(payload, dict) or "items" not in payload:
        raise MobileDeUpstreamError(f"search answered no items: {str(payload)[:120]!r}")
    return payload


def fetch_listing(listing_id, language="en"):
    """One listing in full. Unknown (or removed) id -> MobileDeNotFound."""
    payload = _api(f"{API}/a/{listing_id}?{urlencode({'_lang': language})}", f"listing {listing_id}",
                   LISTING_HEADERS, not_found=f"listing {listing_id} not found")
    if not isinstance(payload, dict) or "id" not in payload:
        raise MobileDeUpstreamError(f"listing {listing_id} answered no id: {str(payload)[:120]!r}")
    return payload


def fetch_makes(vehicle_class, language="en"):
    """Every make of a vehicle class -> [{i: id, n: name}]."""
    payload = _api(f"{API}/r/makes/{vehicle_class}?{urlencode({'_lang': language})}", f"makes of {vehicle_class}",
                   REFERENCE_HEADERS)
    makes = payload.get("makes") if isinstance(payload, dict) else None
    if not isinstance(makes, list):
        raise MobileDeUpstreamError(f"makes of {vehicle_class} answered no list: {str(payload)[:120]!r}")
    return makes


def fetch_models(make_id, language="en"):
    """Every model of a make -> [{i: id, n: name, g: true for a model group,
    p: the group a model belongs to}]. A make the site does not know answers
    an empty list (a 404 is read the same way)."""
    try:
        payload = _api(f"{API}/r/models/{make_id}?{urlencode({'_lang': language})}", f"models of make {make_id}",
                       REFERENCE_HEADERS)
    except MobileDeNotFound:
        return []
    models = payload.get("models") if isinstance(payload, dict) else None
    return models if isinstance(models, list) else []


def fetch_locations(country, text):
    """The search page's place suggestions for a text in a country ->
    [{id, name, state, latitude, longitude}] ([] when the site knows none)."""
    url = f"{SITE}/consumer/next/api/geodata/suggest/{quote(country, safe='')}/{quote(text, safe='')}"
    payload = _api(url, f"places for {text!r}", NEXT_API_HEADERS)
    return payload if isinstance(payload, list) else []


# ---- the dealer page, the reviews page -----------------------------------------------

def dealer_page_url(dealer):
    """A seller id -> the page by customerId; a slug -> the page by its name."""
    if dealer.isdigit():
        return f"{DEALER_SITE}/home/index.html?{urlencode({'customerId': dealer})}"
    return f"{DEALER_SITE}/{quote(dealer, safe='')}"


def fetch_dealer_page(dealer):
    """The dealer's page on the site (HTML). Unknown dealer, or a private
    seller (he has no page) -> MobileDeNotFound."""
    return _page(dealer_page_url(dealer), f"dealer {dealer}", f"dealer {dealer} not found")


def fetch_reviews_page(slug, page=1, order=None, only=None):
    """One page of a dealer's reviews (HTML). `order` and `only` are the
    page's own `order` / `filter` values. Unknown dealer -> MobileDeNotFound."""
    query = {}
    if page and page > 1:
        query["page"] = page
    if order:
        query["order"] = order
    if only:
        query["filter"] = only
    url = f"{SITE}/bewertungen/{quote(slug, safe='')}" + (f"?{urlencode(query)}" if query else "")
    return _page(url, f"reviews of {slug}", f"dealer {slug} not found")


def fetch_dealer_website(seller_id):
    """Where the dealer's "homepage" link leads: his own website, or None
    when the site sends the visitor to his page on mobile.de (no website of
    his own) or knows no such dealer (a private seller)."""
    url = f"{DEALER_SITE}/home/redirect.html?{urlencode({'customerId': seller_id})}"
    label = f"website of dealer {seller_id}"

    def once():
        resp = _send(url, label, PAGE_HEADERS)
        status = resp.status_code
        if status in _REDIRECTS:
            target = (resp.headers.get("location") or "").strip()
            host = (urlparse(target).hostname or "").lower()
            if not target.startswith(("http://", "https://")) or host == "mobile.de" or host.endswith(".mobile.de"):
                return None
            return target
        if status == 404 and not looks_blocked(status, resp.text):
            return None
        if looks_blocked(status, resp.text):
            raise MobileDeBlocked(f"HTTP {status} for {label}")
        raise MobileDeUpstreamError(f"HTTP {status} for {label}")
    return _retrying(once)
