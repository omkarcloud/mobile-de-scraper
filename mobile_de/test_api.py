"""Offline tests for the transport's reading of the site's answers
(mobile_de/fetch.py) and the endpoint logic (mobile_de/api.py), with the
network and the cache replaced by fakes (the answers are the fixtures).

    python -m pytest mobile_de/test_api.py -q
"""
import gzip
import json
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import config  # noqa: E402
from mobile_de import api, fetch, refs, schemas  # noqa: E402
from scraper_errors import BadRequest, Blocked, NotFound, UpstreamError  # noqa: E402

FX = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures")


def raw(name):
    if name.endswith(".gz"):
        with gzip.open(os.path.join(FX, name), "rt", encoding="utf-8") as f:
            return f.read()
    with open(os.path.join(FX, name), encoding="utf-8") as f:
        return f.read()


def fixture(name):
    return json.loads(raw(name))


# ---- the transport -------------------------------------------------------------------

class Answer:
    def __init__(self, status=200, body="", headers=None):
        self.status_code = status
        self.text = body if isinstance(body, str) else json.dumps(body)
        self.content = self.text.encode()
        self.headers = headers or {}


class Wire:
    """A session whose answers are queued; it records what was sent."""

    def __init__(self, monkeypatch, *answers):
        self.answers = list(answers)
        self.sent = []
        self.dropped = 0
        monkeypatch.setattr(fetch, "_session", lambda: self)
        monkeypatch.setattr(fetch, "_drop_session", self._drop)
        monkeypatch.setattr(fetch.retry.time, "sleep", lambda seconds: None)
        monkeypatch.setattr(fetch._local, "hold_until", 0, raising=False)

    def _drop(self):
        self.dropped += 1

    def get(self, url, headers=None, timeout=None, allow_redirects=True):
        self.sent.append({"url": url, "headers": headers, "allow_redirects": allow_redirects})
        answer = self.answers.pop(0)
        if isinstance(answer, Exception):
            raise answer
        return answer


ACCESS_DENIED = "<html><head><title>Zugriff verweigert / Access denied</title></head><body>Access denied</body></html>"
INTERSTITIAL = '<html><body><div id="sec-if-cpt-container"></div><script src="/akam/13/1"></script></body></html>'


def test_error_classes_follow_the_shared_taxonomy():
    assert issubclass(fetch.MobileDeBlocked, Blocked) and issubclass(fetch.MobileDeBlocked, UpstreamError)
    assert issubclass(fetch.MobileDeUpstreamError, UpstreamError)
    assert issubclass(fetch.MobileDeBadRequest, BadRequest) and issubclass(fetch.MobileDeNotFound, NotFound)
    assert not issubclass(fetch.MobileDeNotFound, UpstreamError)


def test_every_call_goes_out_as_the_page_sends_it():
    for headers in (fetch.SEARCH_HEADERS, fetch.LISTING_HEADERS, fetch.REFERENCE_HEADERS):
        assert headers["x-mobile-client"]                    # without it the site answers 400
        assert "x-mobile-vi" not in headers                  # the visitor token is not needed and not sent
    for headers in (fetch.SEARCH_HEADERS, fetch.LISTING_HEADERS, fetch.REFERENCE_HEADERS, fetch.NEXT_API_HEADERS,
                    fetch.PAGE_HEADERS):
        lowered = {name.lower() for name in headers}
        # the session sets these itself: its browser's and its exit's
        assert not lowered & {"user-agent", "accept-language", "accept-encoding", "cookie"}
        assert not any(name.startswith("sec-ch-ua") for name in lowered)


def test_search_sends_the_offset_the_size_and_the_language(monkeypatch):
    wire = Wire(monkeypatch, Answer(body=fixture("search_golf_en.json")))
    payload = fetch.fetch_search([("vc", "Car"), ("ms", "25200;14;;"), ("ft", "PETROL"), ("ft", "DIESEL")], 40, 20, "de")
    assert payload["numResultsTotal"] == 54624
    sent = wire.sent[0]
    assert sent["url"] == ("https://www.mobile.de/api/s?vc=Car&ms=25200%3B14%3B%3B&ft=PETROL&ft=DIESEL"
                           "&ps=40&psz=20&_lang=de")
    assert sent["headers"] is fetch.SEARCH_HEADERS and sent["allow_redirects"] is False


def test_a_refused_filter_is_a_bad_request_and_is_not_retried(monkeypatch):
    wire = Wire(monkeypatch, Answer(400, fixture("search_refused_page_size.json")))
    with pytest.raises(fetch.MobileDeBadRequest) as e:
        fetch.fetch_search([("vc", "Car")], 0, 1000)
    assert str(e.value) == "the site refused page size exceeds limit" and len(wire.sent) == 1
    wire = Wire(monkeypatch, Answer(400, [{"field": "query.fuel", "code": "invalid-arg"}]))
    with pytest.raises(fetch.MobileDeBadRequest, match="the site refused fuel"):
        fetch.fetch_search([("ft", "BOGUS")])
    wire = Wire(monkeypatch, Answer(400, {"errors": [{"key": "400", "args": ["Missing or invalid client header"]}]}))
    with pytest.raises(fetch.MobileDeBadRequest, match="Missing or invalid client header"):
        fetch.fetch_search([])


def test_listing_and_its_not_found(monkeypatch):
    wire = Wire(monkeypatch, Answer(body=fixture("listing_452170310_en.json")))
    assert fetch.fetch_listing("452170310", "en")["id"] == 452170310
    assert wire.sent[0]["url"] == "https://www.mobile.de/api/a/452170310?_lang=en"
    assert wire.sent[0]["headers"] is fetch.LISTING_HEADERS
    wire = Wire(monkeypatch, Answer(404, fixture("listing_not_found.json")))
    with pytest.raises(fetch.MobileDeNotFound, match="listing 100000 not found"):
        fetch.fetch_listing("100000")
    assert len(wire.sent) == 1 and wire.dropped == 0


@pytest.mark.parametrize("answer", [
    Answer(403, ACCESS_DENIED), Answer(429, {"cpr_chlge": "true", "t": "239604003"}),
    Answer(200, {"cpr_chlge": "true", "t": "1"}), Answer(200, INTERSTITIAL),
])
def test_akamai_refusals_are_blocked_then_retried_on_a_fresh_session(monkeypatch, answer):
    monkeypatch.setattr(config, "MAX_RETRIES", 3, raising=False)
    # the hosted fallback exit, set here: the open-source kit's config.py has none (MOBILE_DE_FALLBACK_EXIT = None)
    monkeypatch.setattr(config, "MOBILE_DE_FALLBACK_EXIT", "gateway", raising=False)
    wire = Wire(monkeypatch, answer, Answer(body=fixture("listing_452170310_en.json")))
    assert fetch.fetch_listing("452170310")["id"] == 452170310
    assert len(wire.sent) == 2 and wire.dropped == 1
    assert fetch._local.hold_until > 0                      # the thread's next sessions ride the fallback exit


def test_a_block_that_stays_is_raised_as_blocked(monkeypatch):
    monkeypatch.setattr(config, "MAX_RETRIES", 2, raising=False)
    Wire(monkeypatch, Answer(403, ACCESS_DENIED), Answer(403, ACCESS_DENIED))
    with pytest.raises(fetch.MobileDeBlocked):
        fetch.fetch_listing("452170310")


def test_transport_failures_and_5xx_are_retryable(monkeypatch):
    monkeypatch.setattr(config, "MAX_RETRIES", 3, raising=False)
    wire = Wire(monkeypatch, RuntimeError("reset"), Answer(503, "upstream"), Answer(body=fixture("makes_cars.json")))
    assert len(fetch.fetch_makes("Car")) == 187
    assert wire.dropped == 2 and wire.sent[-1]["url"] == "https://www.mobile.de/api/r/makes/Car?_lang=en"
    assert wire.sent[-1]["headers"] is fetch.REFERENCE_HEADERS


def test_unexpected_bodies_are_upstream_errors(monkeypatch):
    monkeypatch.setattr(config, "MAX_RETRIES", 1, raising=False)
    Wire(monkeypatch, Answer(body={"numResultsTotal": 5}))
    with pytest.raises(fetch.MobileDeUpstreamError, match="no items"):
        fetch.fetch_search([])
    Wire(monkeypatch, Answer(body={"errors": []}))
    with pytest.raises(fetch.MobileDeUpstreamError, match="no id"):
        fetch.fetch_listing("452170310")


def test_models_and_places(monkeypatch):
    wire = Wire(monkeypatch, Answer(body=fixture("models_volkswagen.json")), Answer(body={"models": []}),
                Answer(body=fixture("locations_berlin.json")), Answer(body=[]))
    assert len(fetch.fetch_models(25200)) == 93 and fetch.fetch_models(99999999) == []
    assert fetch.fetch_locations("DE", "Münch en")[0]["name"] == "Berlin" and fetch.fetch_locations("DE", "zz") == []
    assert wire.sent[0]["url"] == "https://www.mobile.de/api/r/models/25200?_lang=en"
    assert wire.sent[2]["url"] == "https://www.mobile.de/consumer/next/api/geodata/suggest/DE/M%C3%BCnch%20en"
    assert wire.sent[2]["headers"] is fetch.NEXT_API_HEADERS


def test_dealer_page_by_id_and_by_slug(monkeypatch):
    html = raw("dealer_page_huk.html.gz")
    wire = Wire(monkeypatch, Answer(body=html), Answer(body=html))
    assert fetch.fetch_dealer_page("15277564") == html and fetch.fetch_dealer_page("HUKCOBURGAUTOWELTGMBH") == html
    assert wire.sent[0]["url"] == "https://home.mobile.de/home/index.html?customerId=15277564"
    assert wire.sent[1]["url"] == "https://home.mobile.de/HUKCOBURGAUTOWELTGMBH"
    assert wire.sent[0]["headers"] is fetch.PAGE_HEADERS and wire.sent[0]["allow_redirects"] is False


def test_dealer_page_follows_the_site_to_the_slugs_own_spelling_only(monkeypatch):
    html = raw("dealer_page_huk.html.gz")
    wire = Wire(monkeypatch, Answer(301, "", {"location": "https://home.mobile.de/HUKCOBURGAUTOWELTGMBH"}),
                Answer(body=html))
    assert fetch.fetch_dealer_page("hukcoburgautoweltgmbh") == html
    assert wire.sent[1]["url"] == "https://home.mobile.de/HUKCOBURGAUTOWELTGMBH"
    Wire(monkeypatch, Answer(302, "", {"location": "https://www.mobile.de/"}))
    with pytest.raises(fetch.MobileDeNotFound, match="dealer x1 not found"):
        fetch.fetch_dealer_page("x1")


def test_dealer_page_not_found_blocked_and_empty(monkeypatch):
    monkeypatch.setattr(config, "MAX_RETRIES", 1, raising=False)
    wire = Wire(monkeypatch, Answer(404, raw("dealer_page_not_found.html.gz")))
    with pytest.raises(fetch.MobileDeNotFound, match="dealer NOPE not found"):
        fetch.fetch_dealer_page("NOPE")
    assert len(wire.sent) == 1
    Wire(monkeypatch, Answer(200, INTERSTITIAL))
    with pytest.raises(fetch.MobileDeBlocked):
        fetch.fetch_dealer_page("15277564")
    Wire(monkeypatch, Answer(200, "<html>" + "x" * 30000 + "</html>"))
    with pytest.raises(fetch.MobileDeUpstreamError, match="carries no data"):
        fetch.fetch_dealer_page("15277564")


def test_reviews_page_link(monkeypatch):
    html = raw("reviews_huk_page_1.html.gz")
    wire = Wire(monkeypatch, Answer(body=html), Answer(body=html), Answer(404, raw("reviews_not_found.html.gz")))
    fetch.fetch_reviews_page("HUKCOBURGAUTOWELTGMBH")
    fetch.fetch_reviews_page("HUKCOBURGAUTOWELTGMBH", 3, "bad", "boughtVehicle")
    assert wire.sent[0]["url"] == "https://www.mobile.de/bewertungen/HUKCOBURGAUTOWELTGMBH"
    assert wire.sent[1]["url"] == "https://www.mobile.de/bewertungen/HUKCOBURGAUTOWELTGMBH?page=3&order=bad&filter=boughtVehicle"
    with pytest.raises(fetch.MobileDeNotFound):
        fetch.fetch_reviews_page("NOPE")


@pytest.mark.parametrize("answer, expected", [
    (Answer(301, "", {"location": "https://www.huk-autowelt.de/auto_kaufen"}), "https://www.huk-autowelt.de/auto_kaufen"),
    (Answer(301, "", {"location": "https://home.mobile.de/SCHLUETERAUTOMOBILE"}), None),   # no website of his own
    (Answer(301, "", {"location": "/home/index.html"}), None),
    (Answer(404, raw("dealer_page_not_found.html.gz")), None),                             # a private seller
])
def test_dealer_website_is_where_the_homepage_link_leads(monkeypatch, answer, expected):
    wire = Wire(monkeypatch, answer)
    assert fetch.fetch_dealer_website("15277564") == expected
    assert wire.sent[0]["url"] == "https://home.mobile.de/home/redirect.html?customerId=15277564"
    assert wire.sent[0]["allow_redirects"] is False


def test_the_thread_rides_the_fallback_exit_while_its_hold_runs(monkeypatch):
    monkeypatch.setattr(config, "MOBILE_DE_EXIT", "direct")
    monkeypatch.setattr(config, "MOBILE_DE_FALLBACK_EXIT", "gateway")
    monkeypatch.setattr(fetch._local, "hold_until", 0, raising=False)
    assert fetch._exit() == "direct"
    fetch._hold()
    assert fetch._exit() == "gateway"
    monkeypatch.setattr(config, "MOBILE_DE_FALLBACK_EXIT", None)
    assert fetch._exit() == "direct"
    monkeypatch.setattr(fetch._local, "hold_until", 0, raising=False)


# ---- the endpoints -------------------------------------------------------------------

class FakeCache:
    """cache.cached_call's contract in memory: keyed on (namespace, params)."""

    def __init__(self):
        self.rows = {}

    def __call__(self, namespace, data, fn, ttl, cache=True, refresh=False):
        key = (namespace, json.dumps(data, sort_keys=True))
        if key not in self.rows:
            self.rows[key] = fn(**data)
        return self.rows[key]


class Site:
    """The site's surfaces, answering from the fixtures and recording the calls."""

    def __init__(self, monkeypatch):
        self.calls = []
        self.search = fixture("search_golf_en.json")
        self.website = "https://www.huk-autowelt.de/auto_kaufen"
        for name in ("fetch_search", "fetch_listing", "fetch_makes", "fetch_models", "fetch_locations",
                     "fetch_dealer_page", "fetch_reviews_page", "fetch_dealer_website"):
            monkeypatch.setattr(api, name, getattr(self, name))
        self.cache = FakeCache()
        monkeypatch.setattr(api, "cached_call", self.cache)
        monkeypatch.setattr(config, "MOBILE_DE_UNSHOWN_DEALER_FIELDS", False)
        monkeypatch.setattr(config, "MOBILE_DE_UNSHOWN_REVIEW_ANSWERS", False)

    def named(self, name):
        return [call for call in self.calls if call[0] == name]

    def fetch_search(self, pairs, offset=0, size=20, language="en"):
        self.calls.append(("search", list(pairs), offset, size, language))
        return self.search

    def fetch_listing(self, listing_id, language="en"):
        self.calls.append(("listing", listing_id, language))
        if listing_id == "452170310":
            return fixture("listing_452170310_en.json")
        if listing_id == "52479701610208":
            return fixture("listing_private.json")
        raise fetch.MobileDeNotFound(f"listing {listing_id} not found")

    def fetch_makes(self, vehicle_class, language="en"):
        self.calls.append(("makes", vehicle_class))
        return fixture("makes_motorbikes.json" if vehicle_class == "Motorbike" else "makes_cars.json")["makes"]

    def fetch_models(self, make_id, language="en"):
        self.calls.append(("models", make_id))
        return fixture("models_volkswagen.json")["models"] if int(make_id) == 25200 else []

    def fetch_locations(self, country, text):
        self.calls.append(("locations", country, text))
        return fixture("locations_berlin.json") if text.lower().startswith("berl") else []

    def fetch_dealer_page(self, dealer):
        self.calls.append(("dealer_page", dealer))
        if dealer in ("15277564", "HUKCOBURGAUTOWELTGMBH"):
            return raw("dealer_page_huk.html.gz")
        raise fetch.MobileDeNotFound(f"dealer {dealer} not found")

    def fetch_reviews_page(self, slug, page=1, order=None, only=None):
        self.calls.append(("reviews", slug, page, order, only))
        if slug != "HUKCOBURGAUTOWELTGMBH":
            raise fetch.MobileDeNotFound(f"dealer {slug} not found")
        return raw("reviews_huk_past_end.html.gz" if page > 50 else "reviews_huk_page_1.html.gz")

    def fetch_dealer_website(self, seller_id):
        self.calls.append(("website", seller_id))
        if isinstance(self.website, Exception):
            raise self.website
        return self.website


def search(**params):
    return api.cars_search(**schemas.CarsSearchSchema().load(params))


def test_cars_search_builds_the_sites_query(monkeypatch):
    site = Site(monkeypatch)
    out = search(make="volkswagen", model="golf", fuel_type="petrol,diesel", min_price="10000", max_price="20000",
                 query="GTI", country="de", sort="price_low_to_high", page="3", page_size="50", language="de")
    (_, pairs, offset, size, language), = site.named("search")
    assert pairs == [("vc", "Car"), ("ms", "25200;14;;"), ("q", "GTI"), ("cn", "DE"), ("ft", "PETROL"), ("ft", "DIESEL"),
                     ("p", "10000:20000"), ("sb", "p"), ("od", "up")]
    assert (offset, size, language) == (100, 50, "de")
    assert list(out) == ["vehicle_type", "results", "is_truncated", "pagination"]
    assert out["vehicle_type"] == "cars" and len(out["results"]) == 20 and out["is_truncated"] is True
    assert out["pagination"] == {"page": 3, "items_per_page": 50, "total_pages": 40, "total_count": 54624}


def test_the_default_search_is_the_pages_own_order(monkeypatch):
    site = Site(monkeypatch)
    search()
    assert site.named("search")[0][1] == [("vc", "Car"), ("sb", "rel"), ("od", "up")]
    assert site.named("makes") == []                        # no make, no lookup


@pytest.mark.parametrize("model, expected", [
    ("Golf", "25200;14;;"), ("14", "25200;14;;"), ("Golf (all)", "25200;;29;"), ("golf plus", "25200;55;;"),
    (None, "25200;;;"),
])
def test_make_and_model_resolve_to_the_sites_ids(monkeypatch, model, expected):
    site = Site(monkeypatch)
    search(make="25200", **({"model": model} if model else {}))
    assert ("ms", expected) in site.named("search")[0][1]


def test_a_model_name_wins_over_another_models_id(monkeypatch):
    """Makes name models by number (Mercedes-Benz 200, BMW 320): the text is the model of that name,
    not the model whose id happens to be that number."""
    site = Site(monkeypatch)
    monkeypatch.setattr(site, "fetch_models", lambda make_id, language="en": [
        {"i": 3, "n": "200"}, {"i": 200, "n": "GLK 220"}, {"i": 7, "n": "190"}])
    monkeypatch.setattr(api, "fetch_models", site.fetch_models)
    search(make="Volkswagen", model="200")
    assert ("ms", "25200;3;;") in site.named("search")[-1][1]
    search(make="Volkswagen", model="7")                    # no model of that name: the id
    assert ("ms", "25200;7;;") in site.named("search")[-1][1]


def test_unknown_make_model_and_place_are_bad_requests(monkeypatch):
    Site(monkeypatch)
    with pytest.raises(fetch.MobileDeBadRequest, match="make: 'Nosuch' is not a make of cars"):
        search(make="Nosuch")
    with pytest.raises(fetch.MobileDeBadRequest, match="model: 'Nosuch' is not a model of Volkswagen"):
        search(make="Volkswagen", model="Nosuch")
    with pytest.raises(fetch.MobileDeBadRequest, match="location: 'zzzz' is not a place"):
        search(location="zzzz")


def test_a_location_is_the_sites_first_suggestion_or_the_coordinates_given(monkeypatch):
    site = Site(monkeypatch)
    search(location="Berlin", radius_km="50")
    assert site.named("search")[0][1][1:3] == [("ll", "52.516071,13.37698"), ("rd", 50)]
    assert site.named("locations") == [("locations", "DE", "berlin")]      # one cache row per place, in any letter case
    search(location="48.2082, 16.3738", country="AT")
    assert site.named("search")[1][1][1:4] == [("cn", "AT"), ("ll", "48.2082,16.3738"), ("rd", 100)]
    assert len(site.named("locations")) == 1


def test_the_last_page_stays_inside_the_sites_window(monkeypatch):
    site = Site(monkeypatch)
    out = search(page="100")                                # results 1981-2000
    assert site.named("search")[-1][2:4] == (1980, 20) and out["pagination"]["total_pages"] == 100
    search(page="7", page_size="300")                       # results 1801-2000: only 200 fit
    assert site.named("search")[-1][2:4] == (1800, 200)
    site.search = fixture("search_past_end.json")
    out = search(page="101")                                # past the window: the total alone
    assert site.named("search")[-1][2:4] == (0, 0) and out["results"] == []
    assert out["pagination"] == {"page": 101, "items_per_page": 20, "total_pages": 100, "total_count": 56259}


def test_a_search_without_a_match(monkeypatch):
    site = Site(monkeypatch)
    site.search = fixture("search_no_result.json")
    out = search(query="zzzzqqq")
    assert out["results"] == [] and out["is_truncated"] is False
    assert out["pagination"] == {"page": 1, "items_per_page": 20, "total_pages": 0, "total_count": 0}


def test_searches_are_cached_on_their_params(monkeypatch):
    site = Site(monkeypatch)
    search(make="Volkswagen")
    search(make="Volkswagen")
    search(make="Volkswagen", page="2")
    assert len(site.named("search")) == 2 and len(site.named("makes")) == 1


def test_cars_by_link(monkeypatch):
    site = Site(monkeypatch)
    link = ("https://suchen.mobile.de/fahrzeuge/search.html?dam=false&isSearchRequest=true&ms=25200%3B14%3B%3B"
            "&ref=quickSearch&s=Car&vc=Car")
    out = api.cars_by_link(**schemas.CarsByLinkSchema().load({"link": link}))
    assert site.named("search")[0][1:] == ([("dam", "false"), ("ms", "25200;14;;"), ("vc", "Car"), ("sb", "rel"),
                                            ("od", "up")], 0, 20, "en")
    assert out["vehicle_type"] == "cars" and out["pagination"]["page"] == 1
    api.cars_by_link(**schemas.CarsByLinkSchema().load({"link": link + "&sb=p&od=down&pageNumber=3"}))
    assert site.named("search")[1][1][-2:] == [("sb", "p"), ("od", "down")] and site.named("search")[1][2] == 40
    api.cars_by_link(**schemas.CarsByLinkSchema().load({"link": link + "&pageNumber=3", "page": "2"}))
    assert site.named("search")[2][2] == 20                 # the page param wins over the link's


def test_car_details_adds_the_dealers_own_website(monkeypatch):
    site = Site(monkeypatch)
    listing = api.car_details(car="452170310", language="en")
    assert listing["id"] == 452170310 and listing["seller_website"] == "https://www.huk-autowelt.de/auto_kaufen"
    assert list(listing).index("seller_website") == list(listing).index("seller") + 1
    assert site.named("website") == [("website", "15277564")]
    api.car_details(car="452170310", language="en")
    assert len(site.named("listing")) == 1 and len(site.named("website")) == 1


def test_car_details_of_a_private_seller_asks_for_no_website(monkeypatch):
    site = Site(monkeypatch)
    listing = api.car_details(car="52479701610208", language="en")
    assert listing["seller"]["type"] == "private" and listing["seller_website"] is None and site.named("website") == []


def test_a_website_lookup_that_fails_never_costs_the_listing(monkeypatch):
    site = Site(monkeypatch)
    site.website = fetch.MobileDeBlocked("HTTP 403")
    assert api.car_details(car="452170310", language="en")["seller_website"] is None
    site.website = None
    assert api.car_details(car="452170310", language="de")["seller_website"] is None


def test_car_details_not_found(monkeypatch):
    Site(monkeypatch)
    with pytest.raises(NotFound, match="listing 100000 not found"):
        api.car_details(car="100000", language="en")


def test_cars_filters_lists_the_types_own_filters(monkeypatch):
    Site(monkeypatch)
    out = api.cars_filters("Car")
    names = [item["name"] for item in out["filters"]]
    assert out["vehicle_type"] == "cars" and out["count"] == len(names) and "fuel_type" in names
    assert "frame_shape" not in names and out["captured_on"] == "2026-11-20"
    assert out["sorts"] == ["relevance", "price_low_to_high", "price_high_to_low", "mileage_low_to_high",
                            "mileage_high_to_low", "year_old_to_new", "year_new_to_old", "oldest", "most_recent"]
    fuel = api.cars_filters("Car", "fuel_type")["filters"][0]
    assert fuel["type"] == "multi" and fuel["params"] == ["fuel_type"] and fuel["steps"] is None
    assert {"value": "electric", "label": "Electric"} in fuel["options"]
    price = api.cars_filters("Car", "price")["filters"][0]
    assert price["params"] == ["min_price", "max_price"] and price["unit"] == "EUR" and price["steps"][:2] == [500, 1000]
    assert price["options"] is None
    damaged = api.cars_filters("Car", "damaged")["filters"][0]
    assert [option["value"] for option in damaged["options"]] == ["true", "false"]
    bodies = {option["value"] for option in api.cars_filters("Motorbike", "body_type")["filters"][0]["options"]}
    assert "motorcycle" in bodies and "estate_car" not in bodies
    with pytest.raises(fetch.MobileDeBadRequest, match="filter: 'frame_shape' is not one of"):
        api.cars_filters("Car", "frame_shape")
    for item in out["filters"]:                             # every listed param is one cars/search takes
        for param in item["params"]:
            assert param in schemas.CarsSearchSchema().fields


def test_makes_and_models(monkeypatch):
    site = Site(monkeypatch)
    makes = api.makes_list("Car")
    assert makes["vehicle_type"] == "cars" and makes["count"] == 187 and makes["results"][0] == {"id": 140, "name": "Abarth"}
    models = api.models_list("volkswagen", "Car")
    assert models["make"] == {"id": 25200, "name": "Volkswagen"} and models["count"] == 93
    assert api.models_list("25200", "Car")["count"] == 93 and len(site.named("models")) == 1
    with pytest.raises(NotFound, match="make 'Nosuch' not found among cars"):
        api.models_list("Nosuch", "Car")


def test_dealer_details_by_id_and_by_slug(monkeypatch):
    site = Site(monkeypatch)
    dealer = api.dealer_details("15277564")
    assert dealer["id"] == 15277564 and dealer["website"] == "https://www.huk-autowelt.de/auto_kaufen"
    assert dealer["email"] is None and dealer["vat_id"] is None       # the page does not show them
    assert api.dealer_details("HUKCOBURGAUTOWELTGMBH")["slug"] == "HUKCOBURGAUTOWELTGMBH"
    api.dealer_details("15277564")
    assert len(site.named("dealer_page")) == 2
    with pytest.raises(NotFound):
        api.dealer_details("NOPE")


def test_dealer_details_passes_the_unshown_fields_on_when_the_switch_is_on(monkeypatch):
    Site(monkeypatch)
    monkeypatch.setattr(config, "MOBILE_DE_UNSHOWN_DEALER_FIELDS", True)
    dealer = api.dealer_details("15277564")
    assert dealer["email"] == "leads-prestage@huk-autowelt.de" and dealer["vat_id"] == "DE307841379"


def dealer_cars(**params):
    return api.dealer_cars(**schemas.DealerCarsSchema().load(params))


def test_dealer_cars(monkeypatch):
    site = Site(monkeypatch)
    site.search = fixture("dealer_cars_15277564.json")
    out = dealer_cars(dealer="15277564")
    assert site.named("search")[0][1] == [("sid", "15277564"), ("sb", "rel"), ("od", "up")]
    assert out["dealer"] == {"id": 15277564, "link": "https://home.mobile.de/home/index.html?customerId=15277564"}
    assert out["vehicle_type"] is None and out["pagination"]["total_count"] == 232 and site.named("dealer_page") == []
    out = dealer_cars(dealer="HUKCOBURGAUTOWELTGMBH", vehicle_type="cars", make="Volkswagen")
    assert site.named("search")[1][1][:3] == [("vc", "Car"), ("sid", "15277564"), ("ms", "25200;;;")]
    assert out["dealer"]["link"] == "https://home.mobile.de/HUKCOBURGAUTOWELTGMBH" and len(site.named("dealer_page")) == 1


def test_dealer_cars_tells_an_empty_stock_from_an_unknown_dealer(monkeypatch):
    site = Site(monkeypatch)
    site.search = fixture("search_no_result.json")
    assert dealer_cars(dealer="15277564")["results"] == []                # a dealer the site knows
    with pytest.raises(NotFound, match="dealer 999999999 not found"):
        dealer_cars(dealer="999999999")
    with pytest.raises(NotFound):
        dealer_cars(dealer="NOPE")


def reviews(**params):
    return api.dealer_reviews(**schemas.DealerReviewsSchema().load(params))


def test_dealer_reviews(monkeypatch):
    site = Site(monkeypatch)
    out = reviews(dealer="HUKCOBURGAUTOWELTGMBH", sort="worst", filter="bought", page="2")
    assert site.named("reviews") == [("reviews", "HUKCOBURGAUTOWELTGMBH", 2, "bad", "boughtVehicle")]
    assert list(out) == ["dealer", "rating", "reviews", "pagination"]
    assert out["dealer"]["slug"] == "HUKCOBURGAUTOWELTGMBH" and out["dealer"]["link"] == "https://home.mobile.de/HUKCOBURGAUTOWELTGMBH"
    assert len(out["reviews"]) == 10
    # a filtered list: the page publishes the count of all reviews only
    assert out["pagination"] == {"page": 2, "items_per_page": 10, "total_pages": 50, "total_count": None}
    out = reviews(dealer="15277564")                        # a seller id: the slug comes from the dealer page
    assert site.named("reviews")[-1] == ("reviews", "HUKCOBURGAUTOWELTGMBH", 1, None, None)
    assert out["pagination"]["total_count"] == 495 and site.named("dealer_page") == [("dealer_page", "15277564")]


def test_dealer_reviews_past_the_end_and_unknown(monkeypatch):
    Site(monkeypatch)
    out = reviews(dealer="HUKCOBURGAUTOWELTGMBH", page="51")
    assert out["reviews"] == [] and out["pagination"]["total_pages"] == 50 and out["rating"]["score"] == 4.1
    with pytest.raises(NotFound):
        reviews(dealer="NOPE")
    with pytest.raises(NotFound):
        reviews(dealer="999999999")


def test_locations_suggestions(monkeypatch):
    site = Site(monkeypatch)
    out = api.locations_suggestions(**schemas.LocationsSchema().load({"query": "Berl"}))
    assert out["query"] == "Berl" and out["country"] == "DE" and out["count"] == 3 and out["results"][0]["name"] == "Berlin"
    assert site.named("locations") == [("locations", "DE", "berl")]
    assert api.locations_suggestions(query="zz", country="fr") == {"query": "zz", "country": "FR", "count": 0, "results": []}


def test_refs_tables_agree():
    assert set(refs.CLASS_SORTS) == set(refs.VEHICLE_TYPES.values())
    for row in refs.FILTERS:
        assert set(row["classes"]) <= set(refs.VEHICLE_TYPES.values()) and row["kind"] in ("choice", "multi", "range", "flag")
        if row["kind"] in ("choice", "multi"):
            for tokens in row["classes"].values():
                assert set(tokens) <= set(row["values"])
