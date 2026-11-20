"""Raw JSON / HTML -> clean dicts for every /mobile-de/* endpoint. Pure functions, no I/O.

Fixtures (mobile_de/fixtures/, captured 2026-11-20 as the site sent them):
  search_golf_en.json            GET /api/s, VW Golf, relevance, English
  search_golf_de.json            the same search in German (German number forms)
  search_no_result.json          a search without a match
  search_past_end.json           ps=2000: past the 2,000-result window
  search_motorbikes.json         vc=Motorbike
  search_ebikes.json             vc=EBike (the e-bike specs)
  search_construction.json       vc=ConstructionMachine (operating hours, no registration)
  dealer_cars_15277564.json      GET /api/s?sid=<seller id>: a dealer's stock, every class
  search_refused_page_size.json  the 400 the site answers psz=1000 with
  listing_452170310_en.json      GET /api/a/<id>, a dealer's car, English
  listing_452170310_de.json      the same listing in German
  listing_private.json           a private seller's car (the sparse record)
  listing_motorbike.json / listing_ebike.json / listing_construction.json
  listing_not_found.json         the 404 body of an unknown id
  makes_cars.json, makes_motorbikes.json, models_volkswagen.json
  locations_berlin.json, locations_none.json
  dealer_page_huk.html.gz        home.mobile.de/<SLUG>: a dealer with a website of his own
  dealer_page_schlueter.html.gz  home.mobile.de/home/index.html?customerId=<id>: no website field, WhatsApp
  dealer_page_not_found.html.gz  the 404 page
  reviews_huk_page_1.html.gz     www.mobile.de/bewertungen/<SLUG>
  reviews_huk_helpful.html.gz    ?order=helpful (helpful counts, a dealer reply)
  reviews_huk_past_end.html.gz   ?page=51 of 50: no reviews
  reviews_not_found.html.gz      the 404 page

The listing API writes its values for people: "62,750 km" in English,
"62.750 km" in German, "110 kW (150 hp)". The numbers are read out of
those texts in either form (local_number); labels stay in the language asked.
"""
import re

from scraper_utils import flight
from scraper_utils.dates import epoch_utc, iso_datetime
from scraper_utils.text import clean, html_to_text, multiline, plain
from scraper_utils.values import amount, as_dict, as_list, bool_or_none, g, number, to_int
from mobile_de import refs

CURRENCY = "EUR"

_NUMBER_RE = re.compile(r"\d[\d.,   ']*")
_MONTH_YEAR_RE = re.compile(r"^\s*(\d{1,2})/(\d{4})\s*$")
_DAY_FIRST_RE = re.compile(r"(\d{1,2})\.(\d{1,2})\.(\d{4})")
_ENGLISH_DATE_RE = re.compile(r"([A-Z][a-z]{2})[a-z]* (\d{1,2}), (\d{4})")
_MONTHS = {name: number_ for number_, name in enumerate(
    ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"), 1)}
_ADDRESS_RE = re.compile(r"^\s*([A-Z]{1,3})-(\S+)\s+(.+?)\s*$")
_BOLD_RE = re.compile(r"\*\*(.+?)\*\*")


# ---- values --------------------------------------------------------------------------

def local_number(value):
    """The first number in a text the site wrote for people, in any of its
    languages: '62,750 km' / '62.750 km' -> 62750, '5,9 l/100km' / '5.9 l' ->
    5.9, '€1,543/Year' -> 1543. A number passes through. None for no number.
    One separator followed by exactly three digits is read as a thousands
    separator, any other single one as the decimal point."""
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return number(value)
    if not isinstance(value, str):
        return None
    match = _NUMBER_RE.search(value)
    if not match:
        return None
    text = re.sub(r"[   ']", "", match.group(0)).rstrip(".,")
    dots, commas = text.count("."), text.count(",")
    if dots and commas:
        decimal = "." if text.rfind(".") > text.rfind(",") else ","
        text = text.replace("," if decimal == "." else ".", "").replace(decimal, ".")
    elif dots + commas == 1:
        separator = "." if dots else ","
        whole, _, fraction = text.partition(separator)
        text = whole + fraction if len(fraction) == 3 and whole != "0" else whole + "." + fraction
    else:
        text = text.replace(".", "").replace(",", "")
    return number(text)


def local_int(value):
    found = local_number(value)
    return int(round(found)) if found is not None else None


def month(value):
    """'04/2024' -> '2024-04'; anything else (the site also writes 'New') -> None."""
    match = _MONTH_YEAR_RE.match(value) if isinstance(value, str) else None
    if not match or not 1 <= int(match.group(1)) <= 12:
        return None
    return f"{match.group(2)}-{int(match.group(1)):02d}"


def day_first(value):
    """'16.11.2026' (anywhere in a text) -> '2026-11-16'; no such date -> None."""
    match = _DAY_FIRST_RE.search(value) if isinstance(value, str) else None
    if not match:
        return None
    day, month_, year = int(match.group(1)), int(match.group(2)), int(match.group(3))
    return f"{year:04d}-{month_:02d}-{day:02d}" if 1 <= day <= 31 and 1 <= month_ <= 12 else None


def date_in(value):
    """The date inside "With mobile.de since Sep 1, 2016" / "Bei mobile.de
    seit 01.09.2016" -> '2016-09-01'. The other languages write it day first
    with dots too; a form that is neither -> None."""
    found = day_first(value)
    if found or not isinstance(value, str):
        return found
    match = _ENGLISH_DATE_RE.search(value)
    if match and match.group(1) in _MONTHS:
        return f"{int(match.group(3)):04d}-{_MONTHS[match.group(1)]:02d}-{int(match.group(2)):02d}"
    return None


def snake(value):
    """'envkv.co2Emissions' -> 'co2_emissions', 'VERY_GOOD_PRICE' -> 'very_good_price', 'EstateCar' -> 'estate_car'."""
    if not isinstance(value, str) or not value.strip():
        return None
    text = value.split("envkv.", 1)[-1]
    text = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", "_", text)
    return re.sub(r"[^a-z0-9]+", "_", text.lower()).strip("_") or None


def _images(items, rule=refs.IMAGE_RULE):
    return [link for link in (refs.image_link(as_dict(item).get("uri") if isinstance(item, dict) else item, rule)
                              for item in as_list(items)) if link]


def _money(value):
    """{amount, currency, localized} -> the amount as a number."""
    return amount(as_dict(value).get("amount"))


# ---- a listing's parts ---------------------------------------------------------------

# Search row `attr` code -> (output key, how its text is read).
_ATTR = {
    "ml": ("mileage_km", local_int), "fr": ("first_registration", month), "yc": ("construction_year", local_int),
    "cc": ("engine_cc", local_int), "ft": ("fuel_type", clean), "tr": ("transmission", clean),
    "ecol": ("color", clean), "door": ("doors", clean), "sc": ("seats", local_int),
    "emc": ("emission_class", clean), "pvo": ("previous_owners", local_int),
    "gi": ("inspection_valid_until", month), "nw": ("weight_kg", local_int), "eu": ("country_version", clean),
    "oh": ("operating_hours", local_int), "con": ("condition", clean),
}
# Listing `attributes` tag -> the same output keys.
_TAG = {
    "mileage": ("mileage_km", local_int), "firstRegistration": ("first_registration", month),
    "constructionYear": ("construction_year", local_int), "cubicCapacity": ("engine_cc", local_int),
    "fuel": ("fuel_type", clean), "transmission": ("transmission", clean), "color": ("color", clean),
    "doorCount": ("doors", clean), "numSeats": ("seats", local_int), "emissionClass": ("emission_class", clean),
    "numberOfPreviousOwners": ("previous_owners", local_int), "hu": ("inspection_valid_until", month),
    "netWeight": ("weight_kg", local_int), "countryVersion": ("country_version", clean),
    "operatingHours": ("operating_hours", local_int), "damageCondition": ("condition", clean),
}
# The specs every listing answer carries, in the order they are written.
_SPEC_KEYS = ("condition", "mileage_km", "first_registration", "construction_year", "power_kw", "power_hp",
              "engine_cc", "fuel_type", "transmission", "color", "doors", "seats", "emission_class",
              "previous_owners", "inspection_valid_until", "weight_kg", "country_version", "operating_hours")
# Search row `attr` codes that are written elsewhere (the location, the body type code).
_ATTR_PLACED = ("cn", "z", "loc", "c", "pw")
# The other codes a row can carry (the e-bike specs) -> a readable key.
_ATTR_NAMES = {
    "bcw": "battery_capacity", "bgt": "gear_type", "bm": "motor_manufacturer", "bp": "battery_position",
    "bsf": "suitable_for", "fh": "frame_height", "fm": "frame_material", "fs": "frame_shape",
    "mpo": "motor_position", "nog": "gears", "ws": "wheel_size", "csmpt": "consumption", "emiss": "co2_emissions",
    "co2class": "co2_class", "gi": "inspection", "fr": "first_registration", "hu": "inspection",
}
_SELLER_TYPES = {"DEALER": "dealer", "FSBO": "private", "COMM_FSBO": "private_commercial"}
_PHONE_TYPES = {"PHONE1": "phone", "PHONE2": "phone", "MOBILE": "mobile", "FAX": "fax"}


def _power(text):
    """'110 kW (150 hp)' -> (110, 150); '150 PS' -> (None, 150); junk -> (None, None)."""
    if not isinstance(text, str):
        return None, None
    kilowatt = re.search(r"(\d[\d.,]*)\s*kW", text.replace(" ", " "))
    inside = re.search(r"\(\s*(\d[\d.,]*)", text)
    kw = local_number(kilowatt.group(1)) if kilowatt else None
    hp = local_number(inside.group(1)) if inside else (local_number(text) if kw is None else None)
    return number(kw) if kw is not None else None, number(hp) if hp is not None else None


def _specs_from(pairs, table):
    """[(code or tag, text)] -> ({output key: value} for the known ones, the rest as [(code, text)])."""
    specs = dict.fromkeys(_SPEC_KEYS)
    rest = []
    for code, text in pairs:
        if code in ("pw", "power"):
            specs["power_kw"], specs["power_hp"] = _power(text)
        elif code in table:
            key, read = table[code]
            specs[key] = read(text) if isinstance(text, str) else None
            if specs[key] is None and isinstance(text, str) and text.strip():
                rest.append((code, text))        # a text the reader does not take ("New" for the inspection month)
        else:
            rest.append((code, text))
    return specs, rest


def parse_price(raw):
    """A listing's price block -> {amount, currency, net_amount, vat_rate,
    type, previous_amount, note}. Amounts are whole euros as the site holds them."""
    price = as_dict(raw.get("price"))
    gross = as_dict(price.get("grs"))
    if not gross and not price:
        return None
    vat_rate = number(price.get("vat"))
    if vat_rate is None:
        vat_rate = local_number(raw.get("vat"))
    return {
        "amount": _money(gross),
        "currency": clean(gross.get("currency")) or CURRENCY,
        "net_amount": _money(price.get("nt")),
        "vat_rate": vat_rate,
        "type": snake(price.get("type")),
        "previous_amount": _money(g(raw, "stPrice", "grs")),
        "note": clean(price.get("delivery")),
    }


def parse_price_rating(raw):
    """{rating, ratingLabel, thresholdLabels, vehiclePriceOffset, noRatingReason}
    -> the site's price rating; None when the listing has none."""
    rating = as_dict(raw.get("priceRating"))
    if not rating:
        return None
    return {
        "rating": snake(rating.get("rating")),
        "label": clean(rating.get("ratingLabel")),
        "thresholds": [value for value in (local_int(label) for label in as_list(rating.get("thresholdLabels")))
                       if value is not None] or None,
        "position": to_int(rating.get("vehiclePriceOffset")),
        "no_rating_reason": clean(rating.get("noRatingReason")),
    }


def _phones(items):
    out = []
    for item in as_list(items):
        item = as_dict(item)
        display = clean(item.get("number"))
        uri = item.get("uri") if isinstance(item.get("uri"), str) else ""
        dialable = uri[4:] if uri.startswith("tel:") else None
        if display or dialable:
            out.append({"type": _PHONE_TYPES.get(item.get("type")) or snake(item.get("type")),
                        "number": display or dialable, "e164": dialable})
    return out


def _messengers(items):
    out = []
    for item in as_list(items):
        item = as_dict(item)
        if item.get("number") or item.get("uri"):
            out.append({"type": (snake(item.get("type")) or "").replace("whats_app", "whatsapp") or None,
                        "number": clean(item.get("number")),
                        "link": (item.get("uri") or "").split("?")[0] or None})
    return out


def _rating(raw):
    rating = as_dict(raw)
    if not rating:
        return None
    link = rating.get("link") if isinstance(rating.get("link"), str) else None
    return {
        "score": number(rating.get("score")),
        "count": to_int(rating.get("count")),
        "total_count": to_int(rating.get("totalCount")),
        "recommendation_rate": number(rating.get("recommendationRate")),
        "ad_reality_rate": number(rating.get("adRealityRate")),
        "consulting_score": _score(rating.get("consultingScore")),
        "friendliness_score": _score(rating.get("friendlinessScore")),
        "response_time_score": _score(rating.get("responseTimeScore")),
        "reviews_link": link.split("?")[0] if link else None,
    }


def _score(value):
    found = number(value)
    return round(found, 2) if found is not None else None


def _address_line(text):
    """'DE-40599 Düsseldorf' -> ('DE', '40599', 'Düsseldorf'); another form -> (None, None, the text)."""
    match = _ADDRESS_RE.match(text) if isinstance(text, str) else None
    if match:
        return match.group(1), match.group(2), match.group(3)
    return None, None, clean(text)


def slug_of_reviews_link(link):
    """'https://www.mobile.de/bewertungen/HUKCOBURGAUTOWELTGMBH?lang=en' -> 'HUKCOBURGAUTOWELTGMBH'."""
    match = re.search(r"/bewertungen/([^/?#]+)", link) if isinstance(link, str) else None
    return match.group(1) if match else None


def parse_seller(raw):
    """A listing's `contact` block (+ its sellerId) -> the seller. A private
    seller has no name, no page and no rating on the site."""
    contact = as_dict(raw.get("contact"))
    seller_id = raw.get("sellerId")
    kind = _SELLER_TYPES.get(contact.get("enumType")) or snake(contact.get("enumType"))
    rating = _rating(contact.get("rating"))
    slug = slug_of_reviews_link(g(contact, "rating", "link"))
    country, postcode, city = _address_line(contact.get("address2"))
    person = as_dict(contact.get("person"))
    is_dealer = kind == "dealer"
    return {
        "id": to_int(seller_id),
        "type": kind,
        "name": clean(contact.get("name")),
        "slug": slug,
        "link": refs.dealer_link(slug, seller_id) if is_dealer else None,
        "address": clean(contact.get("address1")),
        "postcode": postcode,
        "city": city,
        "country": clean(contact.get("country")) or country,
        "latitude": number(g(contact, "latLong", "lat")),
        "longitude": number(g(contact, "latLong", "lon")),
        "phones": _phones(contact.get("phones")),
        "messengers": _messengers(contact.get("messengers")),
        "languages": [part.strip() for part in contact["languages"].split(",") if part.strip()]
        if isinstance(contact.get("languages"), str) else None,
        "member_since": date_in(contact.get("withMobileSince")),
        "opening_hours": [{"days": clean(item.get("day")), "hours": clean(item.get("time"))}
                          for item in as_list(contact.get("openingHours")) if isinstance(item, dict)] or None,
        "rating": rating,
        "logo": refs.image_link(contact.get("logo"), refs.LOGO_RULE),
        "hero_image": refs.image_link(g(contact, "heroImage", "uri")),
        "showroom_images": _images(contact.get("showroomGallery")) or None,
        "contact_person": {"name": clean(person.get("name")), "role": clean(person.get("role")),
                           "photo": refs.image_link(person.get("logo"), refs.LOGO_RULE)}
        if person.get("name") else None,
        "has_online_purchase": bool_or_none(contact.get("buyOnline")),
    }


def parse_finance_plans(raw):
    plans = []
    for plan in as_list(raw.get("financePlans")):
        plan = as_dict(plan)
        offer = as_dict(plan.get("offer"))
        plans.append({
            "type": snake(plan.get("type")),
            "link": plan.get("url") or None,
            "bank": clean(offer.get("bankName")),
            "broker": clean(offer.get("loanBroker")),
            "loan_type": snake(offer.get("loanType")),
            "down_payment": amount(offer.get("downPayment")),
            "term_months": to_int(offer.get("creditTerm")),
            "yearly_mileage_km": to_int(offer.get("yearlyMileage")),
            "credit_amount": amount(offer.get("creditAmount")),
            "interest_rate_nominal": number(offer.get("interestRateNominal")),
            "interest_rate_effective": number(offer.get("interestRateEffective")),
            "monthly_installment": amount(offer.get("monthlyInstallment")),
            "final_installment": amount(offer.get("finalInstallment")),
            "total_interest": amount(offer.get("totalInterest")),
            "total_amount": amount(offer.get("totalAmount")),
            "currency": CURRENCY,
            "example": clean(g(offer, "localized", "disclaimer")),
            "is_promotion": bool_or_none(plan.get("promotion")),
        })
    return plans


def parse_leasing(raw):
    """`leasingRate` {type, downPayment, termOfContract, annualMileage, netRate, grossRate, leasingFactor}."""
    rate = as_dict(raw.get("leasingRate"))
    if not rate:
        return None
    return {
        "type": snake(rate.get("type")),
        "monthly_net_rate": amount(rate.get("netRate")),
        "monthly_gross_rate": amount(rate.get("grossRate")),
        "down_payment": amount(rate.get("downPayment")),
        "term_months": to_int(rate.get("termOfContract")),
        "yearly_mileage_km": to_int(rate.get("annualMileage")),
        "leasing_factor": number(rate.get("leasingFactor")),
        "currency": CURRENCY,
    }


def parse_delivery(raw):
    """`deliveryOption` + `nationalDelivery` -> the seller's delivery offer, None when he has none."""
    national = as_dict(raw.get("nationalDelivery"))
    label = clean(raw.get("deliveryOption"))
    if not national and not label:
        return None
    return {
        "label": label,
        "radius_km": local_int(national.get("radius")),
        "fee": _money(national.get("fee")),
        "currency": clean(g(national, "fee", "currency")) or (CURRENCY if national.get("fee") else None),
        "period": clean(national.get("period")),
        "note": clean(national.get("additionalInformation")),
    }


def parse_seal(raw):
    seal = as_dict(raw.get("sealDetails"))
    if not seal and not raw.get("usedCarSealId"):
        return None
    return {
        "id": clean(raw.get("usedCarSealId")),
        "name": clean(seal.get("localizedName")),
        "elements": [text for text in (clean(item) for item in as_list(seal.get("elements"))) if text] or None,
        "info_link": refs.image_link(seal.get("infoUrl")) if seal.get("infoUrl") else None,
        "icon": refs.image_link(g(seal, "icons", "FULL_ICON", "url")) if g(seal, "icons", "FULL_ICON", "url") else None,
    }


def _other_specs(rest):
    """The row's remaining specs -> [{key, value}], the site's text as written."""
    out = []
    for code, text in rest:
        value = clean(text) if isinstance(text, str) else None
        if value:
            out.append({"key": _ATTR_NAMES.get(code) or snake(code) or code, "value": value})
    return out


def _listing(raw, specs, location, body_type):
    """The part of a listing that a search row and the full listing share."""
    listing_id = raw.get("id")
    vehicle_class = raw.get("vc") if isinstance(raw.get("vc"), str) else None
    kba = as_dict(raw.get("kba"))
    seller = parse_seller(raw)
    return {
        "id": to_int(listing_id),
        "link": (raw.get("url") or "").split("?")[0] or refs.car_link(listing_id),
        "title": clean(raw.get("title")),
        "make": clean(g(raw, "make", "localized")),
        "make_id": to_int(raw.get("makeId")) or to_int(g(raw, "make", "id")),
        "model": clean(g(raw, "model", "localized")),
        "model_id": to_int(raw.get("modelId")) or to_int(g(raw, "model", "id")),
        "variant": clean(raw.get("subTitle")),
        "vehicle_type": refs.TYPE_OF_CLASS.get(vehicle_class) or snake(vehicle_class),
        "body_type": body_type,
        # `isNew` marks an ad that went online lately (a 2015 car with 109,400 km carries it); the
        # vehicle's own condition is `isConditionNew`.
        "is_new": bool(raw.get("isConditionNew")),
        "price": parse_price(raw),
        "price_rating": parse_price_rating(raw),
        **specs,
        "kba": {"hsn": clean(kba.get("hsn")), "tsn": clean(kba.get("tsn"))} if kba else None,
        "has_damage": bool_or_none(raw.get("hasDamage")),
        "is_damage_case": bool_or_none(raw.get("isDamageCase")),
        "is_ready_to_drive": bool_or_none(raw.get("readyToDrive")),
        "is_export": bool_or_none(raw.get("isExport")),
        "is_highlighted": bool_or_none(raw.get("isEyeCatcher")),
        "has_video": bool_or_none(raw.get("isVideoEnabled")),
        "image_count": to_int(raw.get("numImages")) if raw.get("numImages") is not None else len(as_list(raw.get("images"))),
        "images": _images(raw.get("images")),
        "highlights": [text for text in (clean(item) for item in as_list(raw.get("highlights"))) if text] or None,
        "badges": [text for text in (clean(item) for item in as_list(raw.get("badges"))) if text] or None,
        "location": location,
        "seller": seller,
        "finance_plans": parse_finance_plans(raw),
        "leasing": parse_leasing(raw),
        "delivery": parse_delivery(raw),
        "seal": parse_seal(raw),
        "partner_name": clean(raw.get("partnerName")),
        "deal_types": [snake(item) for item in as_list(g(raw, "deal", "types")) if snake(item)] or None,
        "created_at": epoch_utc(raw.get("created")),
        "modified_at": epoch_utc(raw.get("modified")),
        "renewed_at": epoch_utc(raw.get("renewed")),
        "version": to_int(raw.get("version")),
    }


# ---- cars/search, dealers/cars -------------------------------------------------------

def parse_row(raw):
    """One row of a search answer -> a listing card."""
    raw = as_dict(raw)
    attr = as_dict(raw.get("attr"))
    specs, rest = _specs_from([(code, text) for code, text in attr.items() if code not in _ATTR_PLACED or code == "pw"],
                              _ATTR)
    contact = as_dict(raw.get("contact"))
    location = {
        "country": clean(attr.get("cn")) or clean(contact.get("country")),
        "postcode": clean(attr.get("z")),
        "city": clean(attr.get("loc")),
        "latitude": number(g(contact, "latLong", "lat")),
        "longitude": number(g(contact, "latLong", "lon")),
    }
    row = _listing(raw, specs, location, clean(raw.get("category")))
    row["body_type_code"] = snake(attr.get("c"))
    row["other_specs"] = _other_specs(rest)
    return _ordered(row, after="body_type", moved=("body_type_code",), then_after="operating_hours",
                    then=("other_specs",))


def _ordered(row, after, moved, then_after=None, then=()):
    """`row` with the `moved` keys right after `after` (and the `then` keys after `then_after`)."""
    out = {}
    for key, value in row.items():
        if key in moved or key in then:
            continue
        out[key] = value
        if key == after:
            out.update({name: row[name] for name in moved})
        if key == then_after:
            out.update({name: row[name] for name in then})
    return out


def parse_search(payload):
    """A search answer -> {"total_count", "results", "search_id"}."""
    payload = as_dict(payload)
    return {
        "total_count": to_int(payload.get("numResultsTotal")) or 0,
        "results": [parse_row(item) for item in as_list(payload.get("items")) if isinstance(item, dict)],
    }


# ---- cars/details --------------------------------------------------------------------

def _spec_value(value):
    if isinstance(value, list):
        return "; ".join(text for text in (clean(item) for item in value) if text) or None
    return clean(value)


def parse_attributes(raw):
    """The listing's `attributes` -> [{key, name, value, note}]: every
    line of its data sheet, in the site's order and in the language asked. A
    line with several values (the consumption per road type) has them joined
    with "; "."""
    out = []
    for item in as_list(raw.get("attributes")):
        item = as_dict(item)
        value = _spec_value(item.get("value"))
        if not item.get("tag") and not value:
            continue
        info = as_dict(item.get("infoText"))
        out.append({
            "key": snake(item.get("tag")),
            "name": clean(item.get("label")),
            "value": value,
            "note": plain(info.get("text")) if info.get("text") else None,
        })
    return out


def _co2_class(raw):
    """The CO2 class letter, which the site gives only as a picture name (…/images/co2class-D)."""
    for item in as_list(raw.get("attributes")):
        match = re.search(r"co2class-([A-G])\b", str(as_dict(item).get("image") or ""))
        if match:
            return match.group(1)
    return None


def parse_ai_summary(raw):
    summary = as_dict(raw.get("aiSummary"))
    if not summary:
        return None
    highlights = as_dict(summary.get("highlights"))
    text = clean(highlights.get("summary"))
    return {
        "summary": _BOLD_RE.sub(r"\1", text) if text else None,
        "tags": [tag for tag in (clean(item) for item in as_list(highlights.get("tags"))) if tag],
        "insights": [{"title": clean(item.get("title")), "text": clean(item.get("subtitle"))}
                     for item in as_list(g(summary, "insights", "items")) if isinstance(item, dict)],
    }


def parse_listing(raw):
    """GET /api/a/<id> -> the full listing."""
    raw = as_dict(raw)
    pairs = [(as_dict(item).get("tag"), as_dict(item).get("value")) for item in as_list(raw.get("attributes"))]
    specs, _rest = _specs_from([(tag, value) for tag, value in pairs if tag in _TAG or tag == "power"], _TAG)
    by_tag = {tag: value for tag, value in pairs if isinstance(tag, str)}
    contact = as_dict(raw.get("contact"))
    country, postcode, city = _address_line(contact.get("address2"))
    location = {
        "country": clean(contact.get("country")) or country,
        "postcode": postcode,
        "city": city,
        "latitude": number(g(contact, "latLong", "lat")),
        "longitude": number(g(contact, "latLong", "lon")),
    }
    listing = _listing(raw, specs, location, _spec_value(by_tag.get("category")))
    listing["body_type_code"] = snake(raw.get("category"))
    listing["vehicle_number"] = clean(raw.get("sku"))
    listing["co2_class"] = _co2_class(raw)
    listing["is_in_stock"] = bool_or_none(raw.get("isOnStock"))
    listing["specs"] = parse_attributes(raw)
    listing["features"] = [text for text in (clean(item) for item in as_list(raw.get("features"))) if text]
    listing["description"] = html_to_text(raw.get("htmlDescription"))
    listing["ai_summary"] = parse_ai_summary(raw)
    listing["notes"] = [text for text in (plain(item) for item in as_list(raw.get("footnotes"))) if text] or None
    listing["seller_website"] = None          # api.py fills it from the dealer's own "homepage" link
    order = ("id", "link", "title", "make", "make_id", "model", "model_id", "variant", "vehicle_type", "body_type",
             "body_type_code", "vehicle_number", "is_new", "price", "price_rating", *_SPEC_KEYS, "co2_class", "kba",
             "specs", "features", "description", "ai_summary", "notes", "has_damage", "is_damage_case",
             "is_ready_to_drive", "is_export", "is_in_stock", "is_highlighted", "has_video", "image_count", "images",
             "highlights", "badges", "location", "seller", "seller_website", "finance_plans", "leasing", "delivery",
             "seal", "partner_name", "deal_types", "created_at", "modified_at", "renewed_at", "version")
    return {key: listing.get(key) for key in order}


def seller_homepage(raw):
    """The seller id whose "homepage" link the listing carries, or None (a
    private seller, a dealer who gave the site no homepage)."""
    link = g(as_dict(raw), "contact", "homepageUrl")
    match = re.search(r"[?&]customerId=(\d+)", link) if isinstance(link, str) else None
    return match.group(1) if match else None


# ---- makes/list, models/list, locations/suggestions ----------------------------------

def parse_makes(makes):
    """[{i, n}] -> [{id, name}]."""
    return [{"id": to_int(item.get("i")), "name": clean(item.get("n"))}
            for item in as_list(makes) if isinstance(item, dict) and item.get("i") is not None and item.get("n")]


def parse_models(models):
    """[{i, n, g, p}] -> [{id, name, is_group, group_id}]: a model group
    ("Golf (all)") carries is_group, a model inside one names it in group_id."""
    return [{"id": to_int(item.get("i")), "name": clean(item.get("n")), "is_group": bool(item.get("g")),
             "group_id": to_int(item.get("p"))}
            for item in as_list(models) if isinstance(item, dict) and item.get("i") is not None and item.get("n")]


def parse_locations(items):
    """The place suggestions -> [{id, name, state, latitude, longitude}]."""
    return [{"id": to_int(item.get("id")), "name": clean(item.get("name")), "state": clean(item.get("state")),
             "latitude": number(item.get("latitude")), "longitude": number(item.get("longitude"))}
            for item in as_list(items) if isinstance(item, dict) and item.get("name")]


# ---- dealers/details -----------------------------------------------------------------

_DEALER_PHONE_TYPES = {"phone": "phone", "phone2": "phone", "cellPhone": "mobile", "fax": "fax",
                       "newCarPhone": "new_cars", "usedCarPhone": "used_cars", "servicePhone": "service"}


def _dealer_phone(item):
    """{internationalPrefix, prefix, number, normalized} -> ('+49 211 54232350', '+4921154232350')."""
    item = as_dict(item)
    if item.get("empty") or not (item.get("number") or item.get("normalized")):
        return None, None
    parts = [("+" + str(item["internationalPrefix"])) if item.get("internationalPrefix") else None,
             str(item.get("prefix") or "") or None, str(item.get("number") or "") or None]
    normalized = str(item.get("normalized") or "")
    return " ".join(part for part in parts if part) or None, ("+" + normalized) if normalized else None


def _own_site(link):
    """A link that is not one of the site's own -> the link, else None."""
    if not isinstance(link, str) or not link.strip().lower().startswith(("http://", "https://")):
        return None
    host = (re.sub(r"^https?://", "", link.strip(), flags=re.I).split("/")[0].split("@")[-1].split(":")[0]).lower()
    return None if host == "mobile.de" or host.endswith(".mobile.de") else link.strip()


def _user_text(value):
    """A dealer's own text: HTML entities decoded, the site's "\\\\" line breaks as line breaks, list stars as dashes."""
    if not isinstance(value, str):
        return None
    text = value.replace("\\\\", "\n")
    text = re.sub(r"^-{2,}", "", text)
    text = re.sub(r"(?m)^\s*\*\s*", "- ", text)
    return multiline(html_to_text(text.replace("\n", "<br>")) or "")


def parse_dealer_page(html, unshown=False):
    """The dealer page -> the dealer record, or None when the page carries
    no dealer (not a dealer page). `email` and `vat_id` are in the page's
    data but the page does not show them (it shows the imprint, which often
    names both): they are null unless `unshown` is true."""
    rows = flight.rows(html)
    found = flight.props_with(rows, "dealerData")
    data = next((as_dict(item.get("dealerData")) for item in found if as_dict(item.get("dealerData")).get("customerId")), None)
    if not data:
        return None
    homepage = next((as_dict(item.get("dealerHomepageData")) for item in found if item.get("dealerHomepageData")), {})
    location = as_dict(data.get("location"))
    slug = clean(data.get("uniqueIdentifier"))
    seller_id = to_int(data.get("customerId"))
    phones, whatsapp = [], None
    for key, item in as_dict(data.get("phoneNumbers")).items():
        display, dialable = _dealer_phone(item)
        if not display and not dialable:
            continue
        if key == "whatsApp":
            whatsapp = {"type": "whatsapp", "number": display, "link": f"https://wa.me/{dialable[1:]}" if dialable else None}
        else:
            phones.append({"type": _DEALER_PHONE_TYPES.get(key) or snake(key), "number": display, "e164": dialable})
    about = [text for text in (_user_text(homepage.get(key)) for key in ("userDefinedText1", "userDefinedText2")) if text]
    services = [{"title": clean(item.get("title")), "text": _user_text(item.get("description"))}
                for item in as_list(homepage.get("services"))
                if isinstance(item, dict) and item.get("active") is not False and (item.get("title") or item.get("description"))]
    years = re.search(r"\d+", str(data.get("withMobileSince") or ""))
    stock = next((to_int(item.get("numResultsTotal")) for item in flight.props_with(rows, "customerId", "numResultsTotal")), None)
    return {
        "id": seller_id,
        "slug": slug,
        "name": clean(data.get("name")),
        "link": refs.dealer_link(slug, seller_id),
        "reviews_link": refs.reviews_link(slug),
        "headline": clean(homepage.get("userDefinedInfoText")),
        "about": "\n\n".join(about) or None,
        "services_title": clean(homepage.get("serviceTitle")),
        "services_text": _user_text(homepage.get("serviceText")),
        "services": services,
        "languages": [str(code).lower() for code in as_list(data.get("contactLanguages")) if code] or None,
        "years_on_site": int(years.group(0)) if years else None,
        "listing_count": stock,
        "rating": {"score": number(g(data, "rating", "score")), "total_count": to_int(g(data, "rating", "totalCount"))}
        if as_dict(data.get("rating")) else None,
        "address": {
            "street": clean(location.get("street")),
            "extra": clean(location.get("additionalAddressInfo")),
            "postcode": clean(location.get("zipcode")),
            "city": clean(location.get("city")),
            "country": clean(location.get("country")),
            "latitude": number(g(location, "latLong", "lat")),
            "longitude": number(g(location, "latLong", "long")),
        },
        "vat_id": clean(g(data, "legalData", "vatId")) if unshown else None,
        "imprint": multiline(g(data, "legalData", "imprint")),
        "logo": refs.image_link(g(homepage, "images", "LOGO", "baseUrl") or data.get("logoBaseUrl"), refs.LOGO_RULE),
        "picture": refs.image_link(g(homepage, "images", "PICTURE", "baseUrl")),
        "video": homepage.get("dealerVideoUrl") if isinstance(homepage.get("dealerVideoUrl"), str) else None,
        "website": _own_site(data.get("externalHomepageUrl")) or _own_site(homepage.get("userDefinedLink")),
        "email": clean(data.get("email")) if unshown else None,
        "phones": phones,
        "messengers": [whatsapp] if whatsapp else [],
    }


# ---- dealers/reviews -----------------------------------------------------------------

_YES_NO = {"YES": True, "NO": False}
# Review question -> the output key its answer goes under.
_SCORE_QUESTIONS = {"DEALER_CONSULTING": "consulting_score", "DEALER_FRIENDLINESS": "friendliness_score",
                    "DEALER_RESPONSE_TIME": "response_time_score"}
_YES_NO_QUESTIONS = {"RECOMMEND_DEALER": "is_recommended", "AD_REALITY": "is_as_described",
                     "LANDING_PAGE": "is_landing_page_helpful"}
# The same questions in the rating summary, as the aspect they measure.
_ASPECTS = {"DEALER_CONSULTING": "consulting", "DEALER_FRIENDLINESS": "friendliness",
            "DEALER_RESPONSE_TIME": "response_time", "RECOMMEND_DEALER": "recommendation",
            "AD_REALITY": "ad_accuracy", "LANDING_PAGE": "landing_page"}
_TEXT_QUESTIONS = {"LIKE_ABOUT_DEALER": "liked", "NOT_LIKE_ABOUT_DEALER": "disliked"}
# Answers written elsewhere on the review (its header, reviewer_gender).
_PLACED_QUESTIONS = ("BUYER_NAME", "BOUGHT_CAR")
# What a review's data carries and the reviews page does not show (it shows the name, the date, the
# texts, the three scores, the two yes / no answers, the helpful count and the review number): the
# reviewer's gender, the salesperson he named, how he contacted the dealer, any further answer and
# the time of day he answered. Null unless the caller of the parser asks for them.
_UNSHOWN_ANSWERS = ("reviewer_gender", "salesperson_name", "contact_channels", "other_answers", "answered_at")


def _answer_value(answer):
    if answer.get("ratingScore") is not None:
        return number(answer.get("ratingScore"))
    if answer.get("yesOrNoAnswerType"):
        return _YES_NO.get(answer["yesOrNoAnswerType"], snake(answer["yesOrNoAnswerType"]))
    if answer.get("checkboxAnswers"):
        return [str(item) for item in as_list(answer["checkboxAnswers"])]
    if answer.get("selectAnswer"):
        return str(answer["selectAnswer"])
    return multiline(answer.get("fullTextAnswer"))


def _review(key, props, unshown=False):
    header = responses = footer = None
    for _tag, _key, inner in flight.elements(props.get("children")):
        if "averageScore" in inner and "creationTime" in inner:
            header = inner
        elif "ratingQuestionResponses" in inner:
            responses = as_dict(inner["ratingQuestionResponses"])
        elif "ratingId" in inner:
            footer = inner
    header, responses, footer = header or {}, responses or {}, footer or {}
    review = {
        "id": clean(footer.get("ratingId")) or (key if isinstance(key, str) else None),
        "number": to_int(footer.get("internalId")),
        "score": _score(header.get("averageScore")),
        "date": day_first(header.get("creationTime")),
        "reviewer_name": clean(header.get("buyerName")),
        "has_bought_vehicle": bool_or_none(header.get("userBoughtCar")),
        "liked": None, "disliked": None,
        "is_recommended": None, "is_as_described": None,
        "consulting_score": None, "friendliness_score": None, "response_time_score": None,
        "reviewer_gender": None, "salesperson_name": None, "contact_channels": None, "other_answers": [],
        "answered_at": None,
        "helpful_count": to_int(footer.get("helpfulCount")),
        "dealer_reply": multiline(footer.get("dealerReply")),
        "dealer_reply_date": day_first(footer.get("dealerReplyCreationDate")),
    }
    for question, answer in responses.items():
        answer = as_dict(answer)
        review["answered_at"] = review["answered_at"] or iso_datetime(answer.get("answerTime"))
        value = _answer_value(answer)
        if question in _SCORE_QUESTIONS:
            review[_SCORE_QUESTIONS[question]] = value if isinstance(value, (int, float)) else None
        elif question in _YES_NO_QUESTIONS and _YES_NO_QUESTIONS[question] in review:
            review[_YES_NO_QUESTIONS[question]] = value if isinstance(value, bool) else None
        elif question in _TEXT_QUESTIONS:
            review[_TEXT_QUESTIONS[question]] = value if isinstance(value, str) else None
        elif question == "SELLERS_NAME":
            review["salesperson_name"] = multiline(answer.get("fullTextAnswer"))
        elif question == "HOW_DID_YOU_COMMUNICATE_WITH_DEALER":
            review["contact_channels"] = value if isinstance(value, list) else None
        elif question not in _PLACED_QUESTIONS and value not in (None, "", []):
            review["other_answers"].append({"question": snake(question), "answer": value})
    review["other_answers"] = review["other_answers"] or None
    review["reviewer_gender"] = (as_dict(responses.get("BUYER_NAME")).get("selectAnswer") or "").lower() or None
    if not unshown:
        review.update(dict.fromkeys(_UNSHOWN_ANSWERS))
    return review


def _aspects(scores):
    """The page's `aggregatedScores` -> [{aspect, score, yes_rate, answer_count}]."""
    out = []
    for item in as_list(scores):
        item = as_dict(item)
        question = item.get("questionId")
        name = _ASPECTS.get(question) or snake(question)
        if not name:
            continue
        is_score = question in _SCORE_QUESTIONS or bool(number(item.get("ratingScore")))
        out.append({
            "aspect": name,
            "score": _score(item.get("ratingScore")) if is_score else None,
            "yes_rate": None if is_score else _score(item.get("yesPercentage")),
            "answer_count": to_int(item.get("numberOfAnswers")),
        })
    return out


def parse_reviews_page(html, unshown=False):
    """The reviews page -> {"dealer", "rating", "reviews", "page", "total_pages"},
    or None when the page carries no dealer. `unshown` also fills the
    answers the page holds without showing them (_UNSHOWN_ANSWERS)."""
    rows = flight.rows(html)
    company = next((as_dict(item.get("companyData")) for item in flight.props_with(rows, "companyData")), None)
    if not company:
        return None
    reviews = []
    for row in rows.values():
        if not isinstance(row, (list, dict)):
            continue
        for _tag, key, props in flight.elements(row):
            if "ratingEntry" in str(props.get("className") or ""):
                reviews.append(_review(key, flight.resolved(props, rows), unshown))
    scores = next(iter(flight.props_with(rows, "aggregatedScores")), {})
    pages = next(iter(flight.props_with(rows, "currentPage", "totalPages")), {})
    total = re.search(r'"reviewCount\\?":\s*\\?"?(\d+)', html or "")
    slug = re.search(r'<link rel="canonical" href="[^"]*/bewertungen/([^"/?#]+)', html or "")
    return {
        "dealer": {
            "slug": slug.group(1) if slug else None,
            "name": clean(company.get("companyName")),
            "street": clean(company.get("companyStreet")),
            "postcode": clean(company.get("companyZipCode")),
            "city": clean(company.get("companyCity")),
            "country": clean(company.get("companyCountry")),
            "member_since": day_first(company.get("companyActiveSince")),
        },
        "rating": {
            "score": _score(scores.get("overallScore")),
            "total_count": int(total.group(1)) if total else None,
            "aspects": _aspects(scores.get("aggregatedScores")),
        },
        "reviews": reviews,
        "page": to_int(pages.get("currentPage")),
        "total_pages": to_int(pages.get("totalPages")),
    }
