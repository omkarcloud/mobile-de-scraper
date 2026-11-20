"""Use the scraper straight from Python, no server needed.

    python main.py

Each call validates its params with the same schema the API uses (so the
defaults apply) and returns the same JSON the API does, except that a list
keeps its `pagination` block instead of next / previous links. The results
are written to output/*.json.
"""
import json
import os

from mobile_de import api, schemas
from schema_fields import load_query

os.makedirs("output", exist_ok=True)


def run(schema_cls, impl, **params):
    data, error = load_query(schema_cls, {k: str(v) for k, v in params.items()})
    if error:
        raise ValueError(error)
    return impl(**data)


def save(name, data):
    path = os.path.join("output", name)
    with open(path, "w") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    print(f"saved {path}")


if __name__ == "__main__":
    # a listing id or any mobile.de listing link: price and price rating, specs, features, every photo, finance, the dealer
    save("cars_details.json", run(schemas.CarSchema, api.car_details, car='451252928'))

    # 20 vehicles per page with the search's total; filter by make, model, price, year, mileage, fuel and 90+ more
    save("cars_search.json", run(schemas.CarsSearchSchema, api.cars_search))

    # the results of any mobile.de search link, its filters and its sort included
    save("cars_by_link.json", run(schemas.CarsByLinkSchema, api.cars_by_link, link='https://suchen.mobile.de/fahrzeuge/search.html?dam=false&isSearchRequest=true&ms=20100%3B3%3B%3B&ref=quickSearch&s=Car&vc=Car'))
