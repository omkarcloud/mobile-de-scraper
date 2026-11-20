"""The /mobile-de/* endpoint functions (route_glue's route_table calls them
with the schema's loaded kwargs). Every endpoint caches through
cache.cached_call keyed on the validated params.

mobile.de serves the first 2,000 results of a search and no more, whatever
the total: `total_pages` is capped there and `is_truncated` says when the
total is larger than what the pages reach (narrow the search, by price for
instance, to read the rest).

One cached parse of the dealer page serves dealers/details and tells
dealers/cars the seller id of a slug and dealers/reviews the slug of a
seller id.
"""
import re

import config
from cache import cached_call
from mobile_de import cache_config as ttl
from mobile_de import parsers, refs
from mobile_de.fetch import (MAX_PAGE_SIZE, REVIEW_PAGE_SIZE, SEARCH_WINDOW, MobileDeBadRequest, MobileDeNotFound,
                             MobileDeUpstreamError, fetch_dealer_page, fetch_dealer_website, fetch_listing,
                             fetch_locations, fetch_makes, fetch_models, fetch_reviews_page, fetch_search)

REFERENCE_LANGUAGE = "en"       # make and model names are the same in every language
DEFAULT_RADIUS_KM = 100
_COORDINATES_RE = re.compile(r"^\s*(-?\d{1,2}(?:\.\d+)?)\s*[,;]\s*(-?\d{1,3}(?:\.\d+)?)\s*$")
_ALL_SUFFIX_RE = re.compile(r"\s*\(all\)\s*$", re.I)


def _pages(total, page_size):
    return -(-min(total, SEARCH_WINDOW) // page_size) if total else 0


# ---- makes, models, places (cached lookups the searches share) -----------------------

def _makes_of(vehicle_class):
    return parsers.parse_makes(fetch_makes(vehicle_class, REFERENCE_LANGUAGE))


def _makes(vehicle_class):
    return cached_call("mobile_de.makes", {"vehicle_class": vehicle_class}, _makes_of, ttl.REFERENCE_CACHE)


def _models_of(make_id):
    return parsers.parse_models(fetch_models(make_id, REFERENCE_LANGUAGE))


def _models(make_id):
    return cached_call("mobile_de.models", {"make_id": int(make_id)}, _models_of, ttl.REFERENCE_CACHE)


def _find_make(make, vehicle_class):
    """A make name or id -> its {id, name} in the class's list (in any
    class's when none is named), or None."""
    wanted = str(make).strip().lower()
    for candidate in ([vehicle_class] if vehicle_class else list(refs.VEHICLE_TYPES.values())):
        for item in _makes(candidate):
            if str(item["id"]) == wanted or (item["name"] or "").lower() == wanted:
                return item
    return None


def _make_models(data, vehicle_class):
    """`make` / `model` -> the site's `ms` value "<make id>;<model id>;<model group id>;".
    A model name is looked up among the make's models first, then a model id,
    then among its model groups; "Golf (all)" names the group."""
    make, model = data.get("make"), data.get("model")
    if not make:
        return None
    found = _find_make(make, vehicle_class)
    if found is None:
        where = refs.TYPE_OF_CLASS.get(vehicle_class, "any vehicle type")
        raise MobileDeBadRequest(f"make: {make!r} is not a make of {where} (see makes/list)")
    model_id = group_id = ""
    if model:
        wants_group = bool(_ALL_SUFFIX_RE.search(model))
        wanted = _ALL_SUFFIX_RE.sub("", model).strip().lower()
        models = _models(found["id"])
        by_id = [item for item in models if str(item["id"]) == wanted and not item["is_group"]]
        by_name = [item for item in models if (item["name"] or "").lower() == wanted]
        plain = [item for item in by_name if not item["is_group"]]
        groups = [item for item in by_name if item["is_group"]]
        if wants_group and groups:
            group_id = groups[0]["id"]
        elif plain:                      # a name first: "200" is a model's name before it is another model's id
            model_id = plain[0]["id"]
        elif by_id:
            model_id = by_id[0]["id"]
        elif groups:
            group_id = groups[0]["id"]
        else:
            raise MobileDeBadRequest(f"model: {model!r} is not a model of {found['name']} (see models/list)")
    return f"{found['id']};{model_id};{group_id};"


def _places_of(country, query):
    return parsers.parse_locations(fetch_locations(country, query))


def _places(country, query):
    return cached_call("mobile_de.places", {"country": country, "query": query.strip().lower()}, _places_of,
                       ttl.LOCATION_CACHE)


def _coordinates(location, country):
    """`location` -> "latitude,longitude": coordinates as given, else the
    site's first place suggestion for the text."""
    match = _COORDINATES_RE.match(location)
    if match:
        return f"{match.group(1)},{match.group(2)}"
    country = country if country in refs.LOCATION_COUNTRIES else refs.DEFAULT_LOCATION_COUNTRY
    places = _places(country, location)
    if not places or places[0]["latitude"] is None or places[0]["longitude"] is None:
        raise MobileDeBadRequest(f"location: {location!r} is not a place mobile.de knows in {country} "
                                 "(see locations/suggestions, or give latitude,longitude)")
    return f"{places[0]['latitude']},{places[0]['longitude']}"


# ---- cars/search, cars/by-link, dealers/cars -----------------------------------------

def _search_pairs(data, seller_id=None):
    """The validated search params -> the search call's [(query parameter, value)]."""
    vehicle_class = data.get("vehicle_type")
    pairs = []
    if vehicle_class:
        pairs.append(("vc", vehicle_class))
    if seller_id:
        pairs.append(("sid", seller_id))
    make_models = _make_models(data, vehicle_class)
    if make_models:
        pairs.append(("ms", make_models))
    if data.get("query"):
        pairs.append(("q", data["query"]))
    if data.get("country"):
        pairs.append(("cn", data["country"]))
    if data.get("location"):
        pairs.append(("ll", _coordinates(data["location"], data.get("country"))))
        pairs.append(("rd", data.get("radius_km") or DEFAULT_RADIUS_KM))
    pairs.extend(refs.filter_pairs(data))
    by, order = refs.sort_tokens(data.get("sort") or refs.DEFAULT_SORT, vehicle_class)
    return pairs + [("sb", by), ("od", order)]


def _search_page(pairs, page, page_size, language, vehicle_class):
    """One page of a search, kept inside the site's 2,000-result window: the
    last page is asked for at the size that still fits, a page past it for
    the total alone."""
    page_size = min(page_size, MAX_PAGE_SIZE)
    offset = (page - 1) * page_size
    size = max(min(page_size, SEARCH_WINDOW - offset), 0)
    parsed = parsers.parse_search(fetch_search(pairs, offset if size else 0, size, language))
    total = parsed["total_count"]
    return {
        "vehicle_type": refs.TYPE_OF_CLASS.get(vehicle_class),
        "results": parsed["results"],
        "is_truncated": total > SEARCH_WINDOW,
        "pagination": {
            "page": page,
            "items_per_page": page_size,
            "total_pages": _pages(total, page_size),
            "total_count": total,
        },
    }


def _cars_search(**data):
    return _search_page(_search_pairs(data), data["page"], data["page_size"], data["language"], data.get("vehicle_type"))


def cars_search(**data):
    """Vehicles for sale that match the filters, in the site's own order."""
    return cached_call("mobile_de.cars_search", data, _cars_search, ttl.SEARCH_CACHE)


def _cars_by_link(link, language, page, page_size):
    pairs, link_page = refs.link_query(link)
    keys = {key for key, _value in pairs}
    if "sb" not in keys:                                 # a link without a sort is the page's default order
        by, order = refs.SORTS[refs.DEFAULT_SORT]
        pairs = [pair for pair in pairs if pair[0] != "od"] + [("sb", by), ("od", order)]
    vehicle_class = next((value for key, value in pairs if key == "vc"), None)
    return _search_page(pairs, page or link_page or 1, page_size, language, vehicle_class)


def cars_by_link(**data):
    """The results of a mobile.de search link: its filters and its sort, page by page."""
    return cached_call("mobile_de.cars_by_link", data, _cars_by_link, ttl.SEARCH_CACHE)


def _dealer_cars(**data):
    dealer = data.pop("dealer")
    seller_id = dealer if refs.is_dealer_id(dealer) else str(_dealer_record(dealer)["id"])
    result = _search_page(_search_pairs(data, seller_id), data["page"], data["page_size"], data["language"],
                          data.get("vehicle_type"))
    if not result["results"] and data["page"] == 1 and not result["pagination"]["total_count"]:
        _dealer_record(dealer)           # no stock, or no such dealer? An unknown one raises NotFound
    return {"dealer": {"id": int(seller_id), "link": refs.dealer_link(None if refs.is_dealer_id(dealer) else dealer,
                                                                    seller_id)}, **result}


def dealer_cars(**data):
    """A dealer's stock as search rows (the same filters as cars/search)."""
    return cached_call("mobile_de.dealer_cars", data, _dealer_cars, ttl.SEARCH_CACHE)


# ---- cars/details --------------------------------------------------------------------

def _website_of(dealer):
    return {"website": fetch_dealer_website(dealer)}


def _dealer_website(seller_id):
    """The dealer's own website, read from where the site's "homepage" link
    leads (one small call, cached per dealer); None when he has none, or
    when the lookup fails: the website never costs the caller the listing."""
    try:
        return cached_call("mobile_de.dealer_website", {"dealer": str(seller_id)}, _website_of,
                           ttl.WEBSITE_CACHE)["website"]
    except MobileDeUpstreamError:
        return None


def _car_details(car, language):
    raw = fetch_listing(car, language)
    listing = parsers.parse_listing(raw)
    seller_id = parsers.seller_homepage(raw)
    if seller_id:
        listing["seller_website"] = _dealer_website(seller_id)
    return listing


def car_details(**data):
    """Everything mobile.de shows for one listing, of any vehicle type."""
    return cached_call("mobile_de.car_details", data, _car_details, ttl.DETAILS_CACHE)


# ---- cars/filters, makes/list, models/list -------------------------------------------

def _steps(tokens):
    out = []
    for token in tokens:
        try:
            value = float(token)
        except ValueError:
            continue
        out.append(int(value) if value.is_integer() else value)
    return sorted(set(out))


def _filter_item(row, vehicle_class):
    name, kind = row["name"], row["kind"]
    item = {"name": name, "label": row["label"], "type": kind,
            "params": [f"min_{name}", f"max_{name}"] if kind == "range" else [name],
            "unit": row.get("unit"), "options": None, "steps": None}
    if kind in ("choice", "multi"):
        item["options"] = [{"value": row["values"][token][0], "label": row["values"][token][1]}
                           for token in row["classes"][vehicle_class] if token in row["values"]]
    elif kind == "range":
        item["steps"] = _steps(row["classes"][vehicle_class])
    elif kind == "flag":
        item["options"] = [{"value": "true", "label": "only these"}] + (
            [{"value": "false", "label": "without these"}] if row.get("false") else [])
    return item


def cars_filters(vehicle_type, filter=None):
    """Every filter cars/search takes for a vehicle type, with the values it
    accepts there (the site's own filter list), and the sorts."""
    items = [_filter_item(row, vehicle_type) for row in refs.FILTERS if vehicle_type in row["classes"]]
    if filter:
        names = [item["name"] for item in items]
        items = [item for item in items if item["name"] == filter.lower()]
        if not items:
            raise MobileDeBadRequest(f"filter: {filter!r} is not one of: {', '.join(names)}")
    return {
        "vehicle_type": refs.TYPE_OF_CLASS[vehicle_type],
        "captured_on": refs.FILTERS_CAPTURED,
        "sorts": refs.sorts_of(vehicle_type),
        "count": len(items),
        "filters": items,
    }


def makes_list(vehicle_type):
    """Every make of a vehicle type, with the id the site knows it by."""
    makes = _makes(vehicle_type)
    return {"vehicle_type": refs.TYPE_OF_CLASS[vehicle_type], "count": len(makes), "results": makes}


def models_list(make, vehicle_type):
    """Every model and model group of a make."""
    found = _find_make(make, vehicle_type)
    if found is None:
        raise MobileDeNotFound(f"make {make!r} not found among {refs.TYPE_OF_CLASS[vehicle_type]} (see makes/list)")
    models = _models(found["id"])
    return {"vehicle_type": refs.TYPE_OF_CLASS[vehicle_type], "make": found, "count": len(models), "results": models}


# ---- dealers -------------------------------------------------------------------------

def _dealer_page(dealer):
    record = parsers.parse_dealer_page(fetch_dealer_page(dealer), unshown=config.MOBILE_DE_UNSHOWN_DEALER_FIELDS)
    if record is None:
        raise MobileDeNotFound(f"dealer {dealer} not found")
    return record


def _dealer_record(dealer):
    """The dealer page, parsed and cached: by seller id or by slug."""
    return cached_call("mobile_de.dealer_page", {"dealer": dealer}, _dealer_page, ttl.DEALER_CACHE)


def dealer_details(dealer):
    """A dealer's profile: contacts, address, legal notice, rating, own website, the texts of his page."""
    return dict(_dealer_record(dealer))


def _dealer_reviews(dealer, sort, filter, page):
    slug = _dealer_record(dealer)["slug"] if refs.is_dealer_id(dealer) else dealer
    if not slug:
        raise MobileDeNotFound(f"dealer {dealer} has no reviews page")
    parsed = parsers.parse_reviews_page(fetch_reviews_page(slug, page, sort, filter),
                                        unshown=config.MOBILE_DE_UNSHOWN_REVIEW_ANSWERS)
    if parsed is None:
        raise MobileDeUpstreamError(f"the reviews page of {slug} carries no dealer")
    total_pages = parsed.pop("total_pages") or 0
    parsed.pop("page", None)
    parsed["dealer"]["slug"] = parsed["dealer"]["slug"] or slug
    parsed["dealer"] = {"slug": parsed["dealer"]["slug"], "link": refs.dealer_link(parsed["dealer"]["slug"]),
                        **{key: value for key, value in parsed["dealer"].items() if key != "slug"}}
    return {
        **parsed,
        "pagination": {
            "page": page,
            "items_per_page": REVIEW_PAGE_SIZE,
            "total_pages": total_pages,
            # The page publishes the count of all reviews, not of a filtered list.
            "total_count": parsed["rating"]["total_count"] if not filter else None,
        },
    }


def dealer_reviews(**data):
    """A dealer's customer reviews, 10 per page, with the rating summary."""
    return cached_call("mobile_de.dealer_reviews", data, _dealer_reviews, ttl.REVIEWS_CACHE)


# ---- locations/suggestions -----------------------------------------------------------

def locations_suggestions(query, country):
    """The places the search page suggests for a text: name, state, coordinates."""
    country = (country or refs.DEFAULT_LOCATION_COUNTRY).upper()
    places = _places(country, query)
    return {"query": query, "country": country, "count": len(places), "results": places}
