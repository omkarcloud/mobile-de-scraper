"""Reference resolvers, link builders and the filter tables for /mobile-de/*.

ONE param per input. Resolvers raise ValueError with a user-facing message
(RefField turns it into a 400).

Identifier shapes:
  * listing   452170310 / 52479701610208   6 to 16 digits, any vehicle type
              https://suchen.mobile.de/fahrzeuge/details.html?id=<id>
              https://suchen.mobile.de/auto-inserat/<slug>/<id>.html
              (motorrad-inserat, e-bike-inserat, lkw-inserat, ... for the others)
  * dealer    15277564                     the seller id (customerId), digits
              HUKCOBURGAUTOWELTGMBH        the dealer's slug on the site
              https://home.mobile.de/<SLUG>
              https://home.mobile.de/home/index.html?customerId=<id>
              https://www.mobile.de/bewertungen/<SLUG>     (its reviews page)
  * search    https://suchen.mobile.de/fahrzeuge/search.html?<filters>
              the query string is the search call's own, so it passes through

The filter table (filter_table.json) is the search page's own filter
config, captured in English for each of the 13 vehicle classes on
2026-11-20: per query parameter its kind (choice, multi, range, flag), its
tokens with their label, and which classes have it. The schemas build one
request param per filter from it and cars/filters lists it.
"""
import json
import os
import re
from urllib.parse import parse_qsl, urlparse

SITE = "https://www.mobile.de"
SEARCH_SITE = "https://suchen.mobile.de"
DEALER_SITE = "https://home.mobile.de"
IMAGE_RULE = "mo-1024.jpg"          # an image link without a rule answers 400; this one answered 200 (2026-11-20)
LOGO_RULE = "mo-360.jpg"

LISTING_ID_RE = re.compile(r"^[0-9]{6,16}$")
DEALER_ID_RE = re.compile(r"^[0-9]{3,12}$")
DEALER_SLUG_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{1,99}$")
_LISTING_PATH_RE = re.compile(r"/([0-9]{6,16})\.html$")

# Public vehicle type -> the site's vehicle class (the `vc` of a search, the `vc` of a listing).
VEHICLE_TYPES = {
    "cars": "Car",
    "motorbikes": "Motorbike",
    "motorhomes": "Motorhome",
    "vans": "VanUpTo7500",
    "trucks": "TruckOver7500",
    "semi_trailer_trucks": "SemiTrailerTruck",
    "buses": "Bus",
    "trailers": "Trailer",
    "semi_trailers": "SemiTrailer",
    "agricultural_vehicles": "AgriculturalVehicle",
    "construction_machines": "ConstructionMachine",
    "forklifts": "ForkliftTruck",
    "ebikes": "EBike",
}
DEFAULT_VEHICLE_TYPE = "cars"
TYPE_OF_CLASS = {vehicle_class: name for name, vehicle_class in VEHICLE_TYPES.items()}

# The languages the site answered in its own words (`_lang`, 2026-11-20). An unknown
# one silently falls back to the request's Accept-Language, so only these are taken.
LANGUAGES = ("en", "de", "fr", "es", "it", "nl", "pl", "ro", "ru", "cs", "tr")
DEFAULT_LANGUAGE = "en"

# Public sort -> (sb, od). "year" sorts by first registration, or by construction
# year for the machine classes, whichever the class's own sort menu has.
SORTS = {
    "relevance": ("rel", "up"),
    "price_low_to_high": ("p", "up"),
    "price_high_to_low": ("p", "down"),
    "mileage_low_to_high": ("ml", "up"),
    "mileage_high_to_low": ("ml", "down"),
    "year_old_to_new": ("fr", "up"),
    "year_new_to_old": ("fr", "down"),
    "operating_hours_low_to_high": ("oh", "up"),
    "operating_hours_high_to_low": ("oh", "down"),
    "oldest": ("doc", "up"),
    "most_recent": ("doc", "down"),
}
DEFAULT_SORT = "relevance"

# The radius menu of the search page, in km.
RADII = (10, 20, 30, 40, 50, 60, 70, 100, 150, 200, 250, 300, 500, 1000)
# The countries the page asks its place suggestions for (its own list).
LOCATION_COUNTRIES = ("DE", "FR", "IT", "PL", "RO")
DEFAULT_LOCATION_COUNTRY = "DE"

# Dealer review orders and filters: public -> the page's `order` / `filter` value (None = the page's default).
REVIEW_SORTS = {"newest": None, "best": "good", "worst": "bad", "helpful": "helpful"}
REVIEW_FILTERS = {"all": None, "bought": "boughtVehicle", "not_bought": "boughtNoVehicle"}

# Query parameters of a search link that say nothing about the search itself.
_LINK_NOISE = {"isSearchRequest", "ref", "refId", "s", "lang", "_lang", "pageNumber", "ps", "psz", "page",
               "searchId", "action", "top", "utm_source", "utm_medium", "utm_campaign"}

with open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "filter_table.json"), encoding="utf-8") as _f:
    _TABLE = json.load(_f)

FILTERS = _TABLE["params"]                      # one row per query parameter of the site
FILTERS_CAPTURED = _TABLE["captured"]
CLASS_SORTS = {vehicle_class: tuple(row["sorts"]) for vehicle_class, row in _TABLE["classes"].items()}
FILTER_OF = {row["name"]: row for row in FILTERS}


def public_values(row, vehicle_class=None):
    """The values a caller may send for a choice / multi filter, for one class or for any."""
    tokens = row["classes"].get(vehicle_class, []) if vehicle_class else list(row["values"])
    return [row["values"][token][0] for token in tokens if token in row["values"]]


def token_of(row):
    """{what a caller may type, lower case: the site's token}: the public spelling and the token itself."""
    out = {}
    for token, (public, _label) in row["values"].items():
        out[public.lower()] = token
        out[token.lower()] = token
    return out


def types_of(row):
    """The public vehicle types a filter applies to."""
    return [TYPE_OF_CLASS[vehicle_class] for vehicle_class in VEHICLE_TYPES.values() if vehicle_class in row["classes"]]


def sorts_of(vehicle_class=None):
    """The public sorts of a class (of any class when none is named)."""
    menus = [CLASS_SORTS[vehicle_class]] if vehicle_class else list(CLASS_SORTS.values())
    return [name for name, (by, _order) in SORTS.items()
            if any(by in menu or (by == "fr" and "yc" in menu) for menu in menus)]


def sort_tokens(sort, vehicle_class=None):
    """A public sort -> (sb, od) for the class: the year sorts read construction year where the class has
    no first registration."""
    by, order = SORTS[sort]
    menu = CLASS_SORTS.get(vehicle_class, ())
    if by == "fr" and "fr" not in menu and "yc" in menu:
        by = "yc"
    return by, order


# ---- links ---------------------------------------------------------------------------

def _as_url(value):
    """A pasted mobile.de link as an absolute URL, or None when the value is
    not a link. A link to another site is a ValueError."""
    low = value.lower()
    if not low.startswith(("http://", "https://", "//", "www.", "suchen.", "home.", "m.mobile.de", "mobile.de")):
        return None
    url = value
    if low.startswith("//"):
        url = "https:" + url
    elif not low.startswith(("http://", "https://")):
        url = "https://" + url
    host = (urlparse(url).hostname or "").lower()
    if not (host == "mobile.de" or host.endswith(".mobile.de")):
        raise ValueError("Must be a mobile.de link.")
    return url


def resolve_car(value):
    """Bare listing id or a mobile.de listing link -> the listing id."""
    value = (value or "").strip()
    if LISTING_ID_RE.match(value):
        return value
    url = _as_url(value)
    if url:
        parsed = urlparse(url)
        for name, candidate in parse_qsl(parsed.query):
            if name == "id" and LISTING_ID_RE.match(candidate):
                return candidate
        match = _LISTING_PATH_RE.search(parsed.path)
        if match:
            return match.group(1)
        raise ValueError("Must be a mobile.de listing link "
                         "(https://suchen.mobile.de/fahrzeuge/details.html?id=<id>).")
    raise ValueError("Must be a listing id (452170310) or a mobile.de listing link.")


def resolve_dealer(value):
    """Bare seller id, dealer slug or a mobile.de dealer link -> the seller id (digits) or the slug."""
    value = (value or "").strip()
    url = _as_url(value)
    if url:
        parsed = urlparse(url)
        for name, candidate in parse_qsl(parsed.query):
            if name in ("customerId", "sid") and DEALER_ID_RE.match(candidate):
                return candidate
        host = (parsed.hostname or "").lower()
        segments = [segment for segment in parsed.path.split("/") if segment]
        slug = None
        if host == "home.mobile.de" and len(segments) == 1 and not segments[0].endswith(".html"):
            slug = segments[0]
        elif len(segments) == 2 and segments[0].lower() == "bewertungen":
            slug = segments[1]
        if slug and (DEALER_ID_RE.match(slug) or DEALER_SLUG_RE.match(slug)):
            return slug
        raise ValueError("Must be a mobile.de dealer link (https://home.mobile.de/<DEALER>).")
    if DEALER_ID_RE.match(value) or DEALER_SLUG_RE.match(value):
        return value
    raise ValueError("Must be a seller id (15277564), a dealer slug (HUKCOBURGAUTOWELTGMBH) or a mobile.de dealer link.")


def is_dealer_id(dealer):
    return bool(DEALER_ID_RE.match(dealer or ""))


def resolve_search_link(value):
    """A mobile.de search link -> the link. Its query string is the search."""
    value = (value or "").strip()
    url = _as_url(value)
    if not url:
        raise ValueError("Must be a mobile.de search link (https://suchen.mobile.de/fahrzeuge/search.html?...).")
    parsed = urlparse(url)
    if "search.html" not in parsed.path or not parsed.query:
        raise ValueError("Must be a mobile.de search link with its filters in the address "
                         "(https://suchen.mobile.de/fahrzeuge/search.html?...).")
    return url


def link_query(link):
    """A search link -> ([(key, value)] of the search itself, the page number the link names or None)."""
    pairs, page = [], None
    for key, value in parse_qsl(urlparse(link).query, keep_blank_values=False):
        if key == "pageNumber" and value.isdigit():
            page = int(value)
        if key not in _LINK_NOISE:
            pairs.append((key, value))
    return pairs, page


def car_link(listing_id):
    return f"{SEARCH_SITE}/fahrzeuge/details.html?id={listing_id}" if listing_id else None


def dealer_link(slug=None, seller_id=None):
    """The dealer's page on the site: by its slug, else by the seller id."""
    if slug:
        return f"{DEALER_SITE}/{slug}"
    return f"{DEALER_SITE}/home/index.html?customerId={seller_id}" if seller_id else None


def reviews_link(slug):
    return f"{SITE}/bewertungen/{slug}" if slug else None


def image_link(uri, rule=IMAGE_RULE):
    """The site's image reference ("img.classistatic.de/api/v1/mo-prod/images/c9/c944…", no scheme, no size)
    -> a link that loads. A reference that already carries a rule or a scheme keeps it."""
    if not isinstance(uri, str) or not uri.strip():
        return None
    uri = uri.strip()
    if uri.startswith("//"):
        uri = "https:" + uri
    elif not uri.startswith(("http://", "https://")):
        uri = "https://" + uri
    if "classistatic.de/api/" in uri and "rule=" not in uri:
        uri += ("&" if "?" in uri else "?") + "rule=" + rule
    return uri


# ---- the search query ----------------------------------------------------------------

def number_text(value):
    """12000.0 -> '12000', 27.5 -> '27.5': how a range bound is written in the query."""
    return str(int(value)) if float(value).is_integer() else str(value)


def filter_pairs(data):
    """The validated filter params -> [(query parameter, value)] the way the search page writes them:
    a choice once, a multi once per value, a range as min:max, a flag as the site's own token."""
    pairs = []
    for row in FILTERS:
        name, param, kind = row["name"], row["param"], row["kind"]
        if kind == "range":
            low, high = data.get("min_" + name), data.get("max_" + name)
            if low is not None or high is not None:
                pairs.append((param, f"{number_text(low) if low is not None else ''}:"
                                     f"{number_text(high) if high is not None else ''}"))
            continue
        value = data.get(name)
        if value is None:
            continue
        if kind == "flag":
            token = row["true"] if value else row.get("false")
            if token is not None:
                pairs.append((param, token))
        elif kind == "multi":
            pairs.extend((param, token) for token in value)
        else:
            pairs.append((param, value))
    return pairs
