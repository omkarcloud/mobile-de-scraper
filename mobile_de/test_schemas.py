"""Offline tests for the /mobile-de/* request schemas, the id / link
resolvers behind them and the search query they produce.

    python -m pytest mobile_de/test_schemas.py -q
"""
import os
import sys

import pytest
from marshmallow import ValidationError

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from mobile_de import refs, schemas  # noqa: E402
from schema_fields import load_query  # noqa: E402

ALL_SCHEMAS = [schemas.CarsSearchSchema, schemas.CarsByLinkSchema, schemas.CarSchema, schemas.CarsFiltersSchema,
               schemas.MakesListSchema, schemas.ModelsListSchema, schemas.DealerSchema, schemas.DealerCarsSchema,
               schemas.DealerReviewsSchema, schemas.LocationsSchema]
LINK = ("https://suchen.mobile.de/fahrzeuge/search.html?dam=false&isSearchRequest=true&ms=25200%3B14%3B%3B"
        "&ref=quickSearch&s=Car&vc=Car")


def _errors(schema, params):
    with pytest.raises(ValidationError) as e:
        schema().load(params)
    return e.value.normalized_messages()


# ---- one param per input -------------------------------------------------------------

@pytest.mark.parametrize("value", [
    "452170310", " 452170310 ",
    "https://suchen.mobile.de/fahrzeuge/details.html?id=452170310",
    "https://suchen.mobile.de/fahrzeuge/details.html?id=452170310&lang=en&ref=srp",
    "https://suchen.mobile.de/auto-inserat/volkswagen-golf-viii-1-5-etsi-d%C3%BCsseldorf/452170310.html",
    "suchen.mobile.de/auto-inserat/volkswagen-golf/452170310.html?refId=abc",
    "https://m.mobile.de/fahrzeuge/details.html?id=452170310",
])
def test_car_takes_an_id_or_a_listing_link(value):
    assert schemas.CarSchema().load({"car": value})["car"] == "452170310"


def test_car_keeps_a_long_id_and_refuses_other_things():
    link = "https://suchen.mobile.de/motorrad-inserat/bmw-r-1200-gs/42005231841216.html"
    assert schemas.CarSchema().load({"car": link})["car"] == "42005231841216"
    assert "listing id" in _errors(schemas.CarSchema, {"car": "abc"})["car"][0]
    assert _errors(schemas.CarSchema, {"car": "https://example.com/details.html?id=452170310"})["car"] == \
        ["Must be a mobile.de link."]
    assert "listing link" in _errors(schemas.CarSchema, {"car": "https://www.mobile.de/"})["car"][0]
    assert "car" in _errors(schemas.CarSchema, {}) and "car" in _errors(schemas.CarSchema, {"car": ""})


@pytest.mark.parametrize("value, expected", [
    ("15277564", "15277564"),
    ("HUKCOBURGAUTOWELTGMBH", "HUKCOBURGAUTOWELTGMBH"),
    ("AS-AUTOMOBILEGMBH", "AS-AUTOMOBILEGMBH"),
    ("https://home.mobile.de/HUKCOBURGAUTOWELTGMBH", "HUKCOBURGAUTOWELTGMBH"),
    ("https://home.mobile.de/HUKCOBURGAUTOWELTGMBH?vc=Car#ses", "HUKCOBURGAUTOWELTGMBH"),
    ("https://home.mobile.de/home/index.html?customerId=15277564", "15277564"),
    ("https://home.mobile.de/home/redirect.html?customerId=15277564", "15277564"),
    ("https://www.mobile.de/bewertungen/HUKCOBURGAUTOWELTGMBH?lang=en", "HUKCOBURGAUTOWELTGMBH"),
    ("https://suchen.mobile.de/fahrzeuge/search.html?sid=15277564&vc=Car", "15277564"),
])
def test_dealer_takes_an_id_a_slug_or_a_link(value, expected):
    for schema in (schemas.DealerSchema, schemas.DealerCarsSchema, schemas.DealerReviewsSchema):
        assert schema().load({"dealer": value})["dealer"] == expected


def test_dealer_refuses_other_things():
    assert _errors(schemas.DealerSchema, {"dealer": "https://example.com/HUK"})["dealer"] == ["Must be a mobile.de link."]
    assert "dealer link" in _errors(schemas.DealerSchema, {"dealer": "https://www.mobile.de/"})["dealer"][0]
    assert "seller id" in _errors(schemas.DealerSchema, {"dealer": "a b"})["dealer"][0]
    assert "dealer" in _errors(schemas.DealerSchema, {})


def test_by_link_takes_a_search_link():
    data = schemas.CarsByLinkSchema().load({"link": LINK})
    assert data == {"link": LINK, "language": "en", "page": None, "page_size": 20}
    assert refs.link_query(LINK + "&pageNumber=3") == (
        [("dam", "false"), ("ms", "25200;14;;"), ("vc", "Car")], 3)
    assert refs.link_query(LINK + "&sb=p&od=up&ft=DIESEL&ft=PETROL")[0][-4:] == [
        ("sb", "p"), ("od", "up"), ("ft", "DIESEL"), ("ft", "PETROL")]
    for bad in ("https://example.com/search.html?vc=Car", "https://suchen.mobile.de/fahrzeuge/details.html?id=1234567",
                "https://suchen.mobile.de/fahrzeuge/search.html", "golf"):
        assert "link" in _errors(schemas.CarsByLinkSchema, {"link": bad})


# ---- the search filters --------------------------------------------------------------

def test_search_defaults():
    data = schemas.CarsSearchSchema().load({})
    assert data["vehicle_type"] == "Car" and data["sort"] == "relevance" and data["language"] == "en"
    assert data["page"] == 1 and data["page_size"] == 20
    assert all(value is None for key, value in data.items()
               if key not in ("vehicle_type", "sort", "language", "page", "page_size"))
    assert schemas.DealerCarsSchema().load({"dealer": "15277564"})["vehicle_type"] is None


def test_every_filter_of_the_site_is_a_param():
    declared = schemas.CarsSearchSchema().fields
    for row in refs.FILTERS:
        names = [f"min_{row['name']}", f"max_{row['name']}"] if row["kind"] == "range" else [row["name"]]
        for name in names:
            assert name in declared and declared[name].metadata.get("description"), name
    assert len(refs.FILTERS) == 94


def test_filters_become_the_sites_query():
    data = schemas.CarsSearchSchema().load({
        "fuel_type": "petrol, Diesel,PETROL", "transmission": "automatic", "body_type": "estate_car",
        "min_price": "10000", "max_price": "20000", "max_mileage": "80000", "min_year": "2019",
        "seller_type": "private", "damaged": "false", "vat_reclaimable": "true", "features": "navigation_system,sunroof",
        "emissions_sticker": "green"})
    pairs = refs.filter_pairs(data)
    assert sorted(pairs) == sorted([
        ("ft", "PETROL"), ("ft", "DIESEL"), ("tr", "AUTOMATIC_GEAR"), ("c", "EstateCar"), ("p", "10000:20000"),
        ("ml", ":80000"), ("fr", "2019:"), ("st", "FSBO"), ("dam", "false"), ("vat", "1"),
        ("fe", "NAVIGATION_SYSTEM"), ("fe", "SUNROOF"), ("ems", "EMISSIONSSTICKER_GREEN")])
    assert refs.filter_pairs(schemas.CarsSearchSchema().load({})) == []
    assert refs.number_text(27.5) == "27.5" and refs.number_text(10000.0) == "10000"


def test_a_value_may_be_the_sites_own_token():
    data = schemas.CarsSearchSchema().load({"fuel_type": "ELECTRICITY", "seller_type": "fsbo"})
    assert data["fuel_type"] == ["ELECTRICITY"] and data["seller_type"] == "FSBO"


@pytest.mark.parametrize("params, field, words", [
    ({"fuel_type": "coal"}, "fuel_type", "'coal' is not one of: petrol, diesel, electric"),
    ({"seller_type": "shop"}, "seller_type", "Must be one of: dealer, private, private_commercial."),
    ({"frame_shape": "crossbar"}, "frame_shape", "Not a filter of cars: it applies to ebikes."),
    ({"body_type": "motorcycle"}, "body_type", "'motorcycle' is not a value for cars: cabrio, off_road"),
    ({"vehicle_type": "trailers", "fuel_type": "diesel"}, "fuel_type", "Not a filter of trailers"),
    ({"min_price": "5", "max_price": "1"}, "min_price", "min_price must not be greater than max_price."),
    ({"min_price": "-1"}, "min_price", "Must be greater than or equal to 0."),
    ({"carfax": "false"}, "carfax", "Only true is taken"),
    ({"damaged": "maybe"}, "damaged", "Must be true or false."),
    ({"sort": "cheapest"}, "sort", "Must be one of: relevance"),
    ({"sort": "operating_hours_low_to_high"}, "sort", "Not a sort of cars: relevance"),
    ({"vehicle_type": "trailers", "sort": "mileage_low_to_high"}, "sort", "Not a sort of trailers"),
    ({"model": "Golf"}, "model", "model needs a make."),
    ({"radius_km": "50"}, "radius_km", "radius_km needs a location."),
    ({"location": "Berlin", "radius_km": "55"}, "radius_km", "Must be one of"),
    ({"vehicle_type": "boats"}, "vehicle_type", "Must be one of: cars, motorbikes"),
    ({"language": "xx"}, "language", "Must be one of: en, de"),
    ({"country": "Germany"}, "country", ""),
    ({"page": "0"}, "page", ""),
    ({"page_size": "501"}, "page_size", ""),
    ({"colour": "red"}, "colour", "Unknown field."),
])
def test_search_refusals_name_the_field(params, field, words):
    messages = _errors(schemas.CarsSearchSchema, params)
    assert list(messages) == [field] and words in messages[field][0]


def test_a_dealers_stock_takes_any_types_filters():
    data = schemas.DealerCarsSchema().load({"dealer": "15277564", "frame_shape": "crossbar", "body_type": "motorcycle",
                                            "sort": "operating_hours_low_to_high"})
    assert data["frame_shape"] == ["CROSSBAR"] and data["body_type"] == ["Motorcycle"]
    messages = _errors(schemas.DealerCarsSchema, {"dealer": "15277564", "vehicle_type": "cars", "frame_shape": "crossbar"})
    assert list(messages) == ["frame_shape"]


def test_sorts_follow_the_class():
    assert refs.sort_tokens("year_new_to_old", "Car") == ("fr", "down")
    assert refs.sort_tokens("year_new_to_old", "ConstructionMachine") == ("yc", "down")
    assert refs.sort_tokens("relevance") == ("rel", "up") and refs.sort_tokens("most_recent", "EBike") == ("doc", "down")
    assert "operating_hours_low_to_high" in refs.sorts_of("ForkliftTruck")
    assert "mileage_low_to_high" not in refs.sorts_of("Trailer") and "year_old_to_new" in refs.sorts_of("AgriculturalVehicle")


def test_other_schemas():
    assert schemas.CarsFiltersSchema().load({"vehicle_type": "ebikes", "filter": "frame_shape"}) == {
        "vehicle_type": "EBike", "filter": "frame_shape"}
    assert schemas.MakesListSchema().load({}) == {"vehicle_type": "Car"}
    assert schemas.ModelsListSchema().load({"make": " Volkswagen "}) == {"make": "Volkswagen", "vehicle_type": "Car"}
    assert "make" in _errors(schemas.ModelsListSchema, {})
    assert schemas.DealerReviewsSchema().load({"dealer": "15277564", "sort": "Worst", "filter": "bought", "page": "3"}) == {
        "dealer": "15277564", "sort": "bad", "filter": "boughtVehicle", "page": 3}
    assert schemas.DealerReviewsSchema().load({"dealer": "15277564"}) == {
        "dealer": "15277564", "sort": None, "filter": None, "page": 1}
    assert schemas.DealerReviewsSchema().load({"dealer": "15277564", "sort": "newest", "filter": "all"})["sort"] is None
    assert "sort" in _errors(schemas.DealerReviewsSchema, {"dealer": "15277564", "sort": "oldest"})
    assert schemas.LocationsSchema().load({"query": "Berl"}) == {"query": "Berl", "country": "de"}
    assert schemas.LocationsSchema().load({"query": "75001", "country": "fr"})["country"] == "fr"
    assert "country" in _errors(schemas.LocationsSchema, {"query": "x", "country": "US"})


# ---- every schema --------------------------------------------------------------------

@pytest.mark.parametrize("schema", ALL_SCHEMAS)
def test_every_field_is_described_and_unknown_params_are_refused(schema):
    for name, field in schema().fields.items():
        assert field.metadata.get("description"), f"{schema.__name__}.{name} has no description"
    data, error = load_query(schema, {"nosuchparam": "1"})
    assert data is None and "nosuchparam" in error["errors"]


@pytest.mark.parametrize("schema", ALL_SCHEMAS)
def test_an_optional_param_sent_empty_is_the_absent_param(schema):
    """A form that sends every field (`page=&sort=`) gets the defaults, not a 400 or a 500."""
    instance = schema()
    required = {name: value for name, value in {"car": "452170310", "dealer": "15277564", "link": LINK,
                                                "make": "Volkswagen", "query": "Berlin"}.items()
                if name in instance.fields and instance.fields[name].required}
    everything = {name: "" for name in instance.fields}
    everything.update(required)
    assert schema().load(everything) == schema().load(required)
    for name in required:
        assert name in _errors(schema, {**everything, name: " "})
