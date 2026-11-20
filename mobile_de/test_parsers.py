"""Offline tests for the mobile.de parsers and value helpers, against the
fixtures (the site's own answers, captured 2026-11-20).

    python -m pytest mobile_de/test_parsers.py -q
"""
import gzip
import json
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from mobile_de import parsers, refs  # noqa: E402

FX = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures")


def fixture(name):
    with open(os.path.join(FX, name), encoding="utf-8") as f:
        return json.load(f)


def page(name):
    with gzip.open(os.path.join(FX, name), "rt", encoding="utf-8") as f:
        return f.read()


def _no_raw_leftovers(value, path=""):
    """No HTML tag, no empty string, no flight placeholder and no camelCase key anywhere in an answer."""
    if isinstance(value, dict):
        for key, item in value.items():
            assert key == key.lower() and " " not in key, f"{path}.{key}"
            _no_raw_leftovers(item, f"{path}.{key}")
    elif isinstance(value, list):
        for item in value:
            _no_raw_leftovers(item, path + "[]")
    elif isinstance(value, str):
        assert value.strip() and "$undefined" not in value and "<br" not in value and "&amp;" not in value, (path, value[:80])


# ---- values --------------------------------------------------------------------------

@pytest.mark.parametrize("text, expected", [
    ("62,750\xa0km", 62750), ("62.750\xa0km", 62750), ("1,984\xa0ccm", 1984), ("1.498 cm³", 1498),
    ("5.9 l/100km", 5.9), ("5,9 l/100km", 5.9), ("€1,543/Year", 1543), ("1.543\xa0€/Jahr", 1543),
    ("€1,000,000", 1000000), ("1.000.000 €", 1000000), ("16.773,11 €", 16773.11), ("€16,773.11", 16773.11),
    ("19.00% VAT", 19), ("19,00% MwSt.", 19), ("700 km", 700), ("0,5 l", 0.5), ("27.5 inches", 27.5),
    ("10 km", 10), (42, 42), (4.5, 4.5), ("New", None), ("-- g/km", None), ("", None), (None, None), (True, None),
])
def test_local_number_reads_both_number_forms(text, expected):
    assert parsers.local_number(text) == expected


def test_dates():
    assert parsers.month("04/2024") == "2024-04" and parsers.month("4/2024") == "2024-04"
    assert parsers.month("New") is None and parsers.month("13/2024") is None and parsers.month(None) is None
    assert parsers.day_first("16.11.2026") == "2026-11-16" and parsers.day_first("1.9.2016") == "2016-09-01"
    assert parsers.day_first("2026-11-16") is None and parsers.day_first(None) is None
    assert parsers.date_in("With mobile.de since Sep 1, 2016") == "2016-09-01"
    assert parsers.date_in("Bei mobile.de seit 01.09.2016") == "2016-09-01" and parsers.date_in("since long") is None


def test_snake_and_power():
    assert parsers.snake("envkv.co2Emissions") == "co2_emissions" and parsers.snake("VERY_GOOD_PRICE") == "very_good_price"
    assert parsers.snake("EstateCar") == "estate_car" and parsers.snake("") is None and parsers.snake(None) is None
    assert parsers._power("110\xa0kW\xa0(150\xa0hp)") == (110, 150) and parsers._power("110 kW (150 PS)") == (110, 150)
    assert parsers._power("150 PS") == (None, 150) and parsers._power(None) == (None, None)


def test_image_links_get_a_scheme_and_a_size():
    uri = "img.classistatic.de/api/v1/mo-prod/images/c9/c944"
    assert refs.image_link(uri) == "https://" + uri + "?rule=mo-1024.jpg"
    assert refs.image_link("https://" + uri + "?rule=mo-80.jpg") == "https://" + uri + "?rule=mo-80.jpg"
    assert refs.image_link("//static.classistatic.de/a.svg") == "https://static.classistatic.de/a.svg"
    assert refs.image_link("") is None and refs.image_link(None) is None


# ---- search --------------------------------------------------------------------------

def test_search_page():
    parsed = parsers.parse_search(fixture("search_golf_en.json"))
    assert parsed["total_count"] == 54624 and len(parsed["results"]) == 20
    row = parsed["results"][0]
    assert list(row)[:5] == ["id", "link", "title", "make", "make_id"]
    assert row["id"] == 54588315030528 and row["link"].endswith("/54588315030528.html")
    assert (row["make"], row["make_id"], row["model"], row["model_id"]) == ("Volkswagen", 25200, "Golf", 14)
    assert row["vehicle_type"] == "cars" and row["body_type_code"] == "other_car" and row["is_new"] is False
    assert row["price"] == {"amount": 27990, "currency": "EUR", "net_amount": None, "vat_rate": None, "type": "fixed",
                            "previous_amount": None, "note": None}
    assert row["price_rating"]["rating"] == "high_price" and row["price_rating"]["thresholds"][0] == 15800
    assert (row["mileage_km"], row["first_registration"], row["power_kw"], row["power_hp"], row["engine_cc"]) == \
        (76000, "2016-04", 195, 265, 1984)
    assert row["fuel_type"] == "Petrol" and row["seats"] == 5 and row["previous_owners"] == 3 and row["weight_kg"] == 1395
    assert row["inspection_valid_until"] is None and row["other_specs"] == [{"key": "inspection", "value": "New"}]
    assert row["kba"] == {"hsn": "0603", "tsn": "BYW"}
    assert row["image_count"] == 29 and len(row["images"]) == 29 and row["images"][0].endswith("?rule=mo-1024.jpg")
    assert row["location"] == {"country": "DE", "postcode": "31275", "city": "Lehrte", "latitude": 52.3859173,
                               "longitude": 9.9674198}
    assert row["created_at"] == "2026-11-05T12:00:51Z" and row["version"] == 17
    seller = row["seller"]
    assert (seller["id"], seller["type"], seller["name"], seller["slug"]) == \
        (17035765, "dealer", "Autowelt Schlüter", "SCHLUETERAUTOMOBILE")
    assert seller["link"] == "https://home.mobile.de/SCHLUETERAUTOMOBILE"
    assert seller["phones"][0] == {"type": "mobile", "number": "+49 (0)175 8816305", "e164": "+491758816305"}
    assert seller["messengers"] == [{"type": "whatsapp", "number": "+49 1523 6809451",
                                     "link": "https://wa.me/4915236809451"}]
    assert seller["rating"]["score"] == 4.9 and seller["rating"]["total_count"] == 96
    assert seller["rating"]["reviews_link"] == "https://www.mobile.de/bewertungen/SCHLUETERAUTOMOBILE"
    plan = row["finance_plans"][0]
    assert plan["bank"] == "Openbank Deutschland AG" and plan["monthly_installment"] == 261 and plan["term_months"] == 60
    assert plan["loan_type"] == "balloon" and plan["currency"] == "EUR" and plan["link"].startswith("https://www.mobile.de/")
    for item in parsed["results"]:
        _no_raw_leftovers(item)


def test_search_rows_read_the_same_in_german():
    english = parsers.parse_search(fixture("search_golf_en.json"))["results"]
    german = parsers.parse_search(fixture("search_golf_de.json"))["results"]
    numeric = ("id", "mileage_km", "first_registration", "power_kw", "power_hp", "engine_cc", "seats", "weight_kg",
               "previous_owners", "inspection_valid_until", "body_type_code")
    for en, de in zip(english, german):
        if en["id"] == de["id"]:
            assert {key: en[key] for key in numeric} == {key: de[key] for key in numeric}
    assert german[0]["fuel_type"] == "Benzin" and english[0]["fuel_type"] == "Petrol"


def test_private_sellers_and_rare_row_fields():
    rows = [row for name in ("search_golf_en.json", "search_golf_de.json", "dealer_cars_15277564.json",
                             "search_motorbikes.json", "search_construction.json", "search_ebikes.json")
            for row in parsers.parse_search(fixture(name))["results"]]
    private = [row for row in rows if row["seller"]["type"] == "private"]
    for row in private:
        assert row["seller"]["name"] is None and row["seller"]["link"] is None and row["seller"]["rating"] is None
    delivered = [row for row in rows if row["delivery"]]
    assert delivered and delivered[0]["delivery"]["fee"] == 349 and delivered[0]["delivery"]["radius_km"] == 700
    assert delivered[0]["delivery"]["currency"] == "EUR"
    reduced = [row for row in rows if row["price"]["previous_amount"]]
    assert reduced and reduced[0]["price"]["previous_amount"] > reduced[0]["price"]["amount"]
    sealed = [row for row in rows if row["seal"]]
    assert sealed and sealed[0]["seal"]["name"] and sealed[0]["seal"]["icon"].startswith("https://")
    assert any(row["highlights"] for row in rows) and any(row["badges"] for row in rows)
    assert any(row["price"]["net_amount"] and row["price"]["vat_rate"] for row in rows)


def test_other_vehicle_classes():
    bike = parsers.parse_search(fixture("search_motorbikes.json"))["results"][0]
    assert bike["vehicle_type"] == "motorbikes" and bike["mileage_km"] == 61375 and bike["construction_year"] == 2016
    machine = parsers.parse_search(fixture("search_construction.json"))["results"][0]
    assert machine["vehicle_type"] == "construction_machines" and machine["operating_hours"] == 1
    assert machine["first_registration"] is None and machine["construction_year"] == 2026
    ebike = parsers.parse_search(fixture("search_ebikes.json"))["results"][0]
    assert ebike["vehicle_type"] == "ebikes" and ebike["condition"] == "New vehicle"
    assert {"key": "frame_height", "value": "60 cm"} in ebike["other_specs"]
    stock = parsers.parse_search(fixture("dealer_cars_15277564.json"))
    assert stock["total_count"] == 232 and {row["seller"]["id"] for row in stock["results"]} == {15277564}


def test_is_new_is_the_vehicles_condition_not_the_age_of_the_ad():
    """The site's `isNew` marks an ad that went online lately; `isConditionNew` a new vehicle."""
    golfs = fixture("search_golf_en.json")["items"]
    fresh_ad = next(item for item in golfs if item.get("isNew"))
    row = parsers.parse_row(fresh_ad)
    assert (row["mileage_km"], row["first_registration"], row["is_new"]) == (109400, "2015-01", False)
    assert not any(row["is_new"] for name in ("search_golf_en.json", "search_motorbikes.json", "search_construction.json")
                   for row in parsers.parse_search(fixture(name))["results"])
    assert all(row["is_new"] and row["condition"] == "New vehicle"
               for row in parsers.parse_search(fixture("search_ebikes.json"))["results"])
    machine = parsers.parse_listing(fixture("listing_construction.json"))     # isNew true, a used machine
    assert machine["condition"] == "Used vehicle" and machine["is_new"] is False
    assert parsers.parse_listing(fixture("listing_ebike.json"))["is_new"] is True


def test_empty_searches():
    assert parsers.parse_search(fixture("search_no_result.json")) == {"total_count": 0, "results": []}
    past = parsers.parse_search(fixture("search_past_end.json"))
    assert past["total_count"] == 56259 and past["results"] == []
    assert parsers.parse_search(None) == {"total_count": 0, "results": []}
    assert parsers.parse_search({"items": [None, "x"]})["results"] == []


# ---- listing -------------------------------------------------------------------------

def test_listing():
    listing = parsers.parse_listing(fixture("listing_452170310_en.json"))
    assert list(listing)[:4] == ["id", "link", "title", "make"] and list(listing)[-1] == "version"
    assert listing["id"] == 452170310 and listing["title"].startswith("Volkswagen Golf VIII 1.5 eTSI")
    assert listing["body_type"] == "Estate car" and listing["body_type_code"] == "estate_car"
    assert listing["vehicle_number"] == "834388" and listing["co2_class"] == "D"
    assert listing["price"] == {"amount": 19960, "currency": "EUR", "net_amount": 16773.11, "vat_rate": 19,
                                "type": "fixed", "previous_amount": None, "note": "may include delivery costs"}
    assert listing["price_rating"]["rating"] == "very_good_price" and listing["price_rating"]["position"] == 94
    assert (listing["condition"], listing["mileage_km"], listing["first_registration"], listing["construction_year"]) == \
        ("Used vehicle", 62750, "2024-04", 2024)
    assert (listing["power_kw"], listing["power_hp"], listing["engine_cc"], listing["weight_kg"]) == (110, 150, 1498, 1427)
    assert listing["inspection_valid_until"] == "2028-03" and listing["previous_owners"] == 1
    assert len(listing["specs"]) == 38 and listing["specs"][0] == {
        "key": "damage_condition", "name": "Vehicle condition", "value": "Used vehicle", "note": None}
    consumption = next(spec for spec in listing["specs"] if spec["key"] == "consumption_details_fuel")
    assert consumption["value"].startswith("5.9 l/100km (combined); 7.7 l/100km (city)")
    assert next(spec for spec in listing["specs"] if spec["key"] == "co2_emissions")["note"].startswith("Only the CO₂")
    assert len(listing["features"]) == 64 and len(listing["images"]) == 32 and listing["image_count"] == 32
    assert listing["description"].startswith("Interne Fahrzeugnr.: 834388") and "<" not in listing["description"]
    assert "**" not in listing["ai_summary"]["summary"] and listing["ai_summary"]["tags"][0] == "Very good price"
    assert listing["ai_summary"]["insights"][0] == {"title": "Very good price rating",
                                                    "text": "Clear financial advantage compared to market"}
    assert "www.dat.de/co2/" in listing["notes"][0] and "<a" not in listing["notes"][0]
    assert listing["location"]["postcode"] == "40599" and listing["location"]["city"] == "Düsseldorf"
    seller = listing["seller"]
    assert seller["postcode"] == "40599" and seller["languages"] == ["Deutsch", "English"]
    assert seller["member_since"] == "2016-09-01" and seller["opening_hours"][0] == {"days": "Mon - Fri",
                                                                                   "hours": "09:00 h - 19:00 h"}
    assert seller["rating"]["consulting_score"] == 3.63 and seller["rating"]["recommendation_rate"] == 70
    assert seller["phones"][1] == {"type": "fax", "number": "+49 211 78868719", "e164": None}
    assert seller["contact_person"]["name"] == "HUK Autowelt" and len(seller["showroom_images"]) == 5
    assert listing["seller_website"] is None                    # api.py fills it
    assert listing["delivery"]["fee"] == 349 and listing["finance_plans"][0]["broker"] == "CHECK24 GmbH"
    assert listing["created_at"] == "2026-04-03T15:37:51Z" and listing["version"] == 276
    _no_raw_leftovers(listing)
    assert parsers.seller_homepage(fixture("listing_452170310_en.json")) == "15277564"


def test_listing_in_german_has_the_same_numbers():
    english = parsers.parse_listing(fixture("listing_452170310_en.json"))
    german = parsers.parse_listing(fixture("listing_452170310_de.json"))
    for key in ("mileage_km", "first_registration", "power_kw", "power_hp", "engine_cc", "weight_kg",
                "inspection_valid_until", "co2_class", "body_type_code", "construction_year"):
        assert english[key] == german[key], key
    assert {**english["price"], "note": None} == {**german["price"], "note": None}
    assert german["price"]["note"] == "ggf. zzgl. Lieferkosten"
    assert german["body_type"] == "Kombi" and german["seller"]["member_since"] == "2016-09-01"
    assert german["price"]["vat_rate"] == 19 and german["specs"][0]["name"] == "Fahrzeugzustand"


def test_private_listing_is_the_sparse_record():
    raw = fixture("listing_private.json")
    listing = parsers.parse_listing(raw)
    seller = listing["seller"]
    assert seller["type"] == "private" and seller["name"] is None and seller["link"] is None and seller["slug"] is None
    assert seller["rating"] is None and seller["opening_hours"] is None and seller["contact_person"] is None
    assert seller["phones"] == [{"type": "mobile", "number": "+49 (0)176 30792323", "e164": "+4917630792323"}]
    assert listing["price"]["net_amount"] is None and listing["price_rating"]["rating"] == "no_rating"
    assert listing["price_rating"]["no_rating_reason"].startswith("mobile.de only rates")
    assert listing["vehicle_number"] is None and listing["delivery"] is None and listing["notes"] is None
    assert listing["is_damage_case"] is False and parsers.seller_homepage(raw) is None
    _no_raw_leftovers(listing)


@pytest.mark.parametrize("name, vehicle_type, checks", [
    ("listing_motorbike.json", "motorbikes", {"mileage_km": 61375, "engine_cc": 1200, "power_kw": 92}),
    ("listing_ebike.json", "ebikes", {"condition": "New vehicle", "weight_kg": 15, "mileage_km": None}),
    ("listing_construction.json", "construction_machines", {"operating_hours": 1, "construction_year": 2026}),
])
def test_listings_of_other_classes(name, vehicle_type, checks):
    listing = parsers.parse_listing(fixture(name))
    assert listing["vehicle_type"] == vehicle_type and listing["id"] and listing["specs"]
    assert {key: listing[key] for key in checks} == checks
    _no_raw_leftovers(listing)


def test_listing_of_junk_is_all_null():
    listing = parsers.parse_listing({})
    assert listing["id"] is None and listing["specs"] == [] and listing["images"] == [] and listing["price"] is None
    assert parsers.parse_listing(None)["seller"]["phones"] == []


# ---- reference lists -----------------------------------------------------------------

def test_makes_models_locations():
    makes = parsers.parse_makes(fixture("makes_cars.json")["makes"])
    assert len(makes) == 187 and makes[0] == {"id": 140, "name": "Abarth"}
    assert {"id": 25200, "name": "Volkswagen"} in makes
    models = parsers.parse_models(fixture("models_volkswagen.json")["models"])
    assert len(models) == 93
    assert {"id": 29, "name": "Golf", "is_group": True, "group_id": None} in models
    assert {"id": 14, "name": "Golf", "is_group": False, "group_id": 29} in models
    places = parsers.parse_locations(fixture("locations_berlin.json"))
    assert places[0] == {"id": 3331, "name": "Berlin", "state": "Berlin", "latitude": 52.516071, "longitude": 13.37698}
    assert parsers.parse_locations(fixture("locations_none.json")) == [] and parsers.parse_makes(None) == []


# ---- dealer page ---------------------------------------------------------------------

def test_dealer_page():
    dealer = parsers.parse_dealer_page(page("dealer_page_huk.html.gz"))
    assert list(dealer)[:4] == ["id", "slug", "name", "link"]
    assert (dealer["id"], dealer["slug"], dealer["name"]) == (15277564, "HUKCOBURGAUTOWELTGMBH", "HUK-COBURG Autowelt GmbH")
    assert dealer["link"] == "https://home.mobile.de/HUKCOBURGAUTOWELTGMBH"
    assert dealer["reviews_link"] == "https://www.mobile.de/bewertungen/HUKCOBURGAUTOWELTGMBH"
    assert dealer["website"] == "https://www.huk-autowelt.de/auto_kaufen"
    assert dealer["address"] == {"street": "Reisholzer Bahnstraße 20", "extra": None, "postcode": "40599",
                                 "city": "Düsseldorf", "country": "DE", "latitude": 51.1804521,
                                 "longitude": 6.859133399999999}
    assert dealer["phones"] == [{"type": "phone", "number": "+49 211 54232350", "e164": "+4921154232350"},
                                {"type": "fax", "number": "+49 211 78868719", "e164": "+4921178868719"}]
    assert dealer["languages"] == ["de", "en"] and dealer["years_on_site"] == 10 and dealer["listing_count"] == 232
    assert dealer["rating"] == {"score": 4.1, "total_count": 495}
    assert dealer["imprint"].startswith("HUK-COBURG Autowelt GmbH\n\nSitz der Gesellschaft:")
    assert dealer["services"][1] == {"title": "Öffnungszeiten",
                                     "text": "Montag bis Freitag 09:00 bis 19:00\nSamstag 09:00 bis 16:00"}
    assert dealer["services"][0]["text"].startswith("- Geprüfte Qualität: Nur die besten")
    assert dealer["logo"].endswith("?rule=mo-360.jpg") and dealer["picture"].endswith("?rule=mo-1024.jpg")
    assert list(dealer)[-4:] == ["website", "email", "phones", "messengers"]
    _no_raw_leftovers(dealer)


def test_dealer_page_keeps_what_the_page_does_not_show_behind_the_switch():
    shown = parsers.parse_dealer_page(page("dealer_page_huk.html.gz"))
    assert shown["email"] is None and shown["vat_id"] is None
    everything = parsers.parse_dealer_page(page("dealer_page_huk.html.gz"), unshown=True)
    assert everything["email"] == "leads-prestage@huk-autowelt.de" and everything["vat_id"] == "DE307841379"


def test_dealer_page_without_a_website_field_and_with_whatsapp():
    dealer = parsers.parse_dealer_page(page("dealer_page_schlueter.html.gz"))
    assert dealer["id"] == 17035765 and dealer["slug"] == "SCHLUETERAUTOMOBILE"
    assert dealer["website"] == "https://www.autowelt-schlueter.de/"          # the "Internet" link of his page
    assert dealer["messengers"] == [{"type": "whatsapp", "number": "+49 1523 6809451",
                                     "link": "https://wa.me/4915236809451"}]
    assert dealer["services"] == [] and dealer["services_title"] is None and dealer["languages"] == ["de", "en", "pl"]
    assert "\\" not in dealer["about"] and dealer["about"].startswith("Wir bieten Dir")
    _no_raw_leftovers(dealer)


def test_a_page_without_a_dealer():
    assert parsers.parse_dealer_page(page("dealer_page_not_found.html.gz")) is None
    assert parsers.parse_dealer_page("") is None and parsers.parse_dealer_page(None) is None


# ---- reviews page --------------------------------------------------------------------

def test_reviews_page():
    parsed = parsers.parse_reviews_page(page("reviews_huk_page_1.html.gz"))
    assert parsed["page"] == 1 and parsed["total_pages"] == 50 and len(parsed["reviews"]) == 10
    assert parsed["dealer"] == {"slug": "HUKCOBURGAUTOWELTGMBH", "name": "HUK-COBURG Autowelt GmbH",
                                "street": "Reisholzer Bahnstraße 20", "postcode": "40599", "city": "Düsseldorf",
                                "country": "DE", "member_since": "2016-09-01"}
    rating = parsed["rating"]
    assert rating["score"] == 4.1 and rating["total_count"] == 495 and len(rating["aspects"]) == 6
    assert rating["aspects"][0] == {"aspect": "consulting", "score": 3.63, "yes_rate": None, "answer_count": 43}
    assert rating["aspects"][1] == {"aspect": "recommendation", "score": None, "yes_rate": 0.71, "answer_count": 34}
    review = parsed["reviews"][0]
    assert list(review)[:4] == ["id", "number", "score", "date"]
    assert review["id"] == "6afaa11bce7228cad3499ae3" and review["number"] == 4661297466
    assert (review["score"], review["date"], review["has_bought_vehicle"]) == (5, "2026-11-16", True)
    assert review["liked"] == "Reibungslos von Anfang bis Ende" and review["disliked"] is None
    assert review["is_recommended"] is True and review["is_as_described"] is True
    assert (review["consulting_score"], review["friendliness_score"], review["response_time_score"]) == (5, 5, 5)
    assert review["helpful_count"] == 0 and review["dealer_reply"] is None
    for item in parsed["reviews"]:
        _no_raw_leftovers(item)
        assert all(item[key] is None for key in parsers._UNSHOWN_ANSWERS)


def test_reviews_sorted_by_helpful_with_a_dealer_reply():
    parsed = parsers.parse_reviews_page(page("reviews_huk_helpful.html.gz"))
    first = parsed["reviews"][0]
    assert first["reviewer_name"] == "Jörg van Eß" and first["score"] == 2.67 and first["helpful_count"] == 19
    assert first["disliked"].startswith("Der Wagen war nicht so ausgestattet") and first["liked"] is None
    assert first["is_recommended"] is False and first["has_bought_vehicle"] is False
    replied = [review for review in parsed["reviews"] if review["dealer_reply"]]
    assert replied and replied[0]["dealer_reply"].startswith("Vielen Dank") and replied[0]["dealer_reply_date"] == "2016-11-18"


def test_reviews_answers_the_page_does_not_show_are_behind_the_switch():
    parsed = parsers.parse_reviews_page(page("reviews_huk_helpful.html.gz"), unshown=True)
    first = parsed["reviews"][0]
    assert first["reviewer_gender"] == "male" and first["contact_channels"] == ["mail"]
    assert first["answered_at"] == "2018-12-12T18:58:21Z"
    assert any(review["salesperson_name"] for review in parsed["reviews"])


def test_reviews_past_the_end_and_not_found():
    past = parsers.parse_reviews_page(page("reviews_huk_past_end.html.gz"))
    assert past["reviews"] == [] and past["page"] == 51 and past["total_pages"] == 50 and past["rating"]["score"] == 4.1
    assert parsers.parse_reviews_page(page("reviews_not_found.html.gz")) is None
    assert parsers.parse_reviews_page("") is None
