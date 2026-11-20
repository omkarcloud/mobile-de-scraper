"""Marshmallow request schemas for every /mobile-de/* route.

ONE param per input (never a sibling `url` / `id` pair):

    car      a listing id (452170310) OR any mobile.de listing link
    dealer   a seller id (15277564), a dealer slug (HUKCOBURGAUTOWELTGMBH) OR
             a mobile.de dealer / dealer reviews link
    link     a mobile.de search link (cars/by-link)
    make     a make name ("Volkswagen") or its id; makes/list returns every one
    model    a model name of that make ("Golf") or its id; models/list
             returns every one

The search filters are built from the site's own filter config
(refs.FILTERS, one row per query parameter of the search page): a choice
becomes one param, a multi-value filter a comma-separated param, a range a
`min_<name>` / `max_<name>` pair, a switch a true / false param. A filter
belongs to some vehicle types and its values can differ between them
(body_type): a filter or value the chosen vehicle type does not have is a
400 that names what it does have, because the site would silently ignore
the one and refuse the other.
"""
from marshmallow import ValidationError, fields, pre_load, validate, validates_schema

from schema_fields import (BaseSchema, ChoiceField, CountryCodeField, Flag, PageField, PageSizeField, QueryField,
                           RefField, StrippedString)
from scraper_utils.schema import max_length
from mobile_de import refs
from mobile_de.fetch import MAX_PAGE_SIZE, SEARCH_WINDOW

NAME_MAX = 80
MAX_REVIEW_PAGE = 1000
_SHOWN_VALUES = 14          # values a description lists before it points at cars/filters


class _Schema(BaseSchema):
    @pre_load
    def empty_is_absent(self, data, **kwargs):
        """An optional param sent empty (`make=`, `page=`: a form that sends
        every field) is the absent param, so its default applies and its
        validators never see None. A required param sent empty is still refused."""
        required = {field.data_key or name for name, field in self.fields.items() if field.required}
        return {key: value for key, value in data.items()
                if key in required or not (isinstance(value, str) and not value.strip())}


class CarRefField(RefField):
    resolver = staticmethod(refs.resolve_car)


class DealerRefField(RefField):
    resolver = staticmethod(refs.resolve_dealer)


class SearchLinkField(RefField):
    resolver = staticmethod(refs.resolve_search_link)


class TokenList(fields.Field):
    """'petrol, Diesel' -> ['PETROL', 'DIESEL']: a comma-separated list of a
    filter's values in any letter case, as the site's tokens (deduped, in
    the order given). An unknown value is refused with the values it may be."""

    def __init__(self, row, **kwargs):
        kwargs.setdefault("load_default", None)
        super().__init__(**kwargs)
        self._tokens = refs.token_of(row)
        self._publics = refs.public_values(row)

    def _deserialize(self, value, attr, data, **kwargs):
        tokens = []
        for raw in str(value).split(","):
            item = raw.strip()
            if not item:
                continue
            token = self._tokens.get(item.lower())
            if token is None:
                raise ValidationError(f"'{item}' is not one of: {', '.join(self._publics)}.")
            if token not in tokens:
                tokens.append(token)
        return tokens or None


class TokenChoice(StrippedString):
    """One of a filter's values in any letter case -> the site's token."""

    def __init__(self, row, **kwargs):
        kwargs.setdefault("load_default", None)
        super().__init__(**kwargs)
        self._tokens = refs.token_of(row)
        self._publics = refs.public_values(row)

    def _deserialize(self, value, attr, data, **kwargs):
        value = super()._deserialize(value, attr, data, **kwargs)
        if value is None:
            return None
        token = self._tokens.get(value.lower())
        if token is None:
            raise ValidationError(f"Must be one of: {', '.join(self._publics)}.")
        return token


def _text(description, maximum=NAME_MAX):
    return StrippedString(load_default=None, validate=max_length(maximum), metadata={"description": description})


def _language():
    return ChoiceField(list(refs.LANGUAGES), load_default=refs.DEFAULT_LANGUAGE, metadata={
        "description": "language of the labels and texts mobile.de writes (fuel, body type, features, the data "
                       f"sheet): {', '.join(refs.LANGUAGES)} (default en). A seller's own text stays as he wrote it"})


def _vehicle_type(default=refs.DEFAULT_VEHICLE_TYPE):
    return ChoiceField(dict(refs.VEHICLE_TYPES), load_default=refs.VEHICLE_TYPES.get(default), metadata={
        "description": f"what to look for: {', '.join(refs.VEHICLE_TYPES)}"
                       + (f" (default {default})" if default else " (default: every type)")})


def _only(row):
    """' (cars, vans only)' for a filter that some vehicle types do not have."""
    types = refs.types_of(row)
    return "" if len(types) == len(refs.VEHICLE_TYPES) else f" ({', '.join(types)} only)"


def _values_text(row):
    values = refs.public_values(row)
    if len(values) <= _SHOWN_VALUES:
        return ", ".join(values)
    return ", ".join(values[:_SHOWN_VALUES]) + f", ... ({len(values)} values, cars/filters lists them per vehicle type)"


def _filter_fields():
    """One request field per filter of the site (two for a range), each
    described from the site's own label."""
    out = {}
    for row in refs.FILTERS:
        name, label, unit = row["name"], row["label"], row.get("unit")
        only = _only(row)
        if row["kind"] == "range":
            in_unit = f" in {unit}" if unit else ""
            for bound, word in (("min", "lowest"), ("max", "highest")):
                out[f"{bound}_{name}"] = fields.Float(load_default=None, validate=validate.Range(min=0), metadata={
                    "description": f"{word} {label[0].lower() + label[1:]}{in_unit}{only}"})
        elif row["kind"] == "flag":
            meaning = "true keeps only these, false leaves them out" if row.get("false") else "true keeps only these"
            out[name] = Flag(metadata={"description": f"{label}: {meaning}{only}"})
        elif row["kind"] == "multi":
            out[name] = TokenList(row, metadata={
                "description": f"{label}{only}, one or more separated by commas: {_values_text(row)}"})
        else:
            out[name] = TokenChoice(row, metadata={"description": f"{label}{only}: {_values_text(row)}"})
    return out


def check_filters(data, vehicle_class):
    """Raise a ValidationError for a filter the vehicle type does not have, a
    value it does not have, a flag value the site has no token for, or a
    range given the wrong way round. With no vehicle type (a dealer's whole
    stock) only the range order is checked: any type's filters may be used."""
    for row in refs.FILTERS:
        name = row["name"]
        names = (f"min_{name}", f"max_{name}") if row["kind"] == "range" else (name,)
        given = [key for key in names if data.get(key) is not None]
        if not given:
            continue
        if vehicle_class and vehicle_class not in row["classes"]:
            raise ValidationError(f"Not a filter of {refs.TYPE_OF_CLASS[vehicle_class]}: it applies to "
                                  f"{', '.join(refs.types_of(row))}.", given[0])
        if row["kind"] == "range":
            low, high = data.get(names[0]), data.get(names[1])
            if low is not None and high is not None and low > high:
                raise ValidationError(f"{names[0]} must not be greater than {names[1]}.", names[0])
        elif row["kind"] == "flag":
            if data[name] is False and not row.get("false"):
                raise ValidationError("Only true is taken (the site has no filter for the opposite).", name)
        elif vehicle_class:
            allowed = row["classes"][vehicle_class]
            for token in data[name] if isinstance(data[name], list) else [data[name]]:
                if token not in allowed:
                    raise ValidationError(
                        f"'{row['values'][token][0]}' is not a value for {refs.TYPE_OF_CLASS[vehicle_class]}: "
                        f"{', '.join(refs.public_values(row, vehicle_class))}.", name)


def check_sort(data, vehicle_class):
    sort = data.get("sort")
    if sort and sort not in refs.sorts_of(vehicle_class):
        where = refs.TYPE_OF_CLASS[vehicle_class] if vehicle_class else "any vehicle type"
        raise ValidationError(f"Not a sort of {where}: {', '.join(refs.sorts_of(vehicle_class))}.", "sort")


class _SearchBase(_Schema):
    """The params cars/search and dealers/cars share, the site's filters included."""
    make = _text("make name, e.g. Volkswagen, or its id (see makes/list)")
    model = _text("model or model group of that make, e.g. Golf, or its id (see models/list); needs make")
    query = _text("words to look for in the listing, e.g. GTI or panoramic roof", maximum=100)
    country = CountryCodeField(metadata={
        "description": "only vehicles located in this country, ISO-2, e.g. DE, AT, NL (default: every country)"})
    location = _text("search around this place: a German town or postcode (Berlin, 80331), a postcode of the "
                     "chosen country for FR, IT, PL and RO, or coordinates as latitude,longitude "
                     "(52.52,13.40); locations/suggestions lists the places the site knows", maximum=100)
    radius_km = fields.Integer(strict=False, load_default=None, validate=validate.OneOf(refs.RADII), metadata={
        "description": f"distance from the location in km: {', '.join(str(r) for r in refs.RADII)} "
                       "(default 100); needs location"})
    sort = ChoiceField(list(refs.SORTS), load_default=refs.DEFAULT_SORT, metadata={
        "description": "result order: relevance (default), price_low_to_high, price_high_to_low, "
                       "mileage_low_to_high, mileage_high_to_low, year_old_to_new, year_new_to_old, "
                       "operating_hours_low_to_high, operating_hours_high_to_low (machines), oldest, most_recent "
                       "(by the date the listing went online)"})
    language = _language()
    page = PageField(max_page=SEARCH_WINDOW, metadata={
        "description": f"page number; mobile.de serves the first {SEARCH_WINDOW:,} results of a search, so the "
                       f"last page is {SEARCH_WINDOW:,} / page_size"})
    page_size = PageSizeField(default=20, max_size=MAX_PAGE_SIZE, metadata={
        "description": f"results per page, 1-{MAX_PAGE_SIZE} (default 20)"})

    @validates_schema
    def _consistent(self, data, **kwargs):
        if data.get("model") and not data.get("make"):
            raise ValidationError("model needs a make.", "model")
        if data.get("radius_km") and not data.get("location"):
            raise ValidationError("radius_km needs a location.", "radius_km")
        check_filters(data, data.get("vehicle_type"))
        check_sort(data, data.get("vehicle_type"))


_SearchFilters = type("_SearchFilters", (_SearchBase,), _filter_fields())


class CarsSearchSchema(_SearchFilters):
    vehicle_type = _vehicle_type()


class DealerCarsSchema(_SearchFilters):
    dealer = DealerRefField(metadata={
        "description": "seller id (e.g. 15277564), dealer slug (HUKCOBURGAUTOWELTGMBH), or a mobile.de dealer link"})
    vehicle_type = _vehicle_type(default=None)


class CarsByLinkSchema(_Schema):
    link = SearchLinkField(metadata={
        "description": "a mobile.de search link with its filters in the address "
                       "(https://suchen.mobile.de/fahrzeuge/search.html?...)"})
    language = _language()
    page = PageField(max_page=SEARCH_WINDOW, load_default=None, metadata={
        "description": "page number (default: the link's own page, else 1)"})
    page_size = PageSizeField(default=20, max_size=MAX_PAGE_SIZE, metadata={
        "description": f"results per page, 1-{MAX_PAGE_SIZE} (default 20)"})


class CarSchema(_Schema):
    car = CarRefField(metadata={
        "description": "listing id (e.g. 452170310), or a mobile.de listing link"})
    language = _language()


class CarsFiltersSchema(_Schema):
    vehicle_type = _vehicle_type()
    filter = _text("return only this filter, e.g. fuel_type (default: every filter)")


class MakesListSchema(_Schema):
    vehicle_type = _vehicle_type()


class ModelsListSchema(_Schema):
    make = QueryField(max_length=NAME_MAX, metadata={
        "description": "make name, e.g. Volkswagen, or its id (see makes/list)"})
    vehicle_type = _vehicle_type()


def _dealer():
    return DealerRefField(metadata={
        "description": "seller id (e.g. 15277564), dealer slug (HUKCOBURGAUTOWELTGMBH), or a mobile.de dealer link"})


class DealerSchema(_Schema):
    dealer = _dealer()


class DealerReviewsSchema(_Schema):
    dealer = _dealer()
    sort = ChoiceField(dict(refs.REVIEW_SORTS), load_default=None, metadata={
        "description": "newest (default), best, worst or helpful"})
    filter = ChoiceField(dict(refs.REVIEW_FILTERS), load_default=None, metadata={
        "description": "all (default), bought (reviewers who bought a vehicle there) or not_bought"})
    page = PageField(max_page=MAX_REVIEW_PAGE, metadata={"description": "page number, 10 reviews each"})


class LocationsSchema(_Schema):
    query = QueryField(max_length=100, metadata={
        "description": "the start of a place: a town or postcode in Germany (Berl, 80331), a postcode in the "
                       "other countries"})
    country = ChoiceField(list(refs.LOCATION_COUNTRIES), load_default=refs.DEFAULT_LOCATION_COUNTRY.lower(), metadata={
        "description": "country to look in: DE (default), FR, IT, PL or RO"})
