# 🚀 Mobile de Scraper

Mobile de Scraper is a **free and open-source** scraper that gets you **unlimited** detailed Mobile de data for free.

## ✨ What Can I Get?

- 🚗 **Full details on 1.5M+ cars for sale** — price & price rating, every spec, feature & photo, finance plans, approved-used seal & the dealer's phones
- 🔍 **Search 1.8M+ cars, bikes, vans, motorhomes & trucks with 94 filters** — make, model, price, year, mileage, power, fuel, location & radius, 11 sort orders
- 🏢 **Every dealer's full profile & stock** — address, phones, website, opening hours, rating, legal notice & every vehicle they sell
- ⭐ **Every dealer review, with the score breakdown** — score, what buyers liked & disliked, recommended, as described, consulting, friendliness & response time

## 🚗 Example: A Full Mobile de Listing

```json
{
  "id": 451252928,
  "link": "https://suchen.mobile.de/auto-inserat/porsche-911-carrera-cabriolet-matrix-led-sitzbel%C3%BCftung-u-oldenburg/451252928.html",
  "title": "Porsche 911 Carrera Cabriolet Matrix LED…",
  "make": "Porsche",
  "model": "911",
  "body_type": "Convertible/Roadster, Demonstration Vehicle",
  "price": { "amount": 154890, "currency": "EUR", "net_amount": 130159.66, "vat_rate": 19 },
  "condition": "Used vehicle, Accident-free",
  "mileage_km": 16000,
  "first_registration": "2026-03",
  "power_hp": 394,
  "engine_cc": 2981,
  "fuel_type": "Petrol",
  "transmission": "Automatic",
  "color": "Black Metallic",
  "features": ["Adaptive Cruise Control", "Apple CarPlay", "Android Auto"],
  "image_count": 38,
  "seal": { "name": "Porsche Approved", "elements": ["12 to 36-month Porsche Approved warranty"] },
  "location": { "postcode": "26123", "city": "Oldenburg", "country": "DE" },
  "seller": {
    "name": "Porsche Zentrum Oldenburg Sportwagen-Center Schmidt + Koch GmbH",
    "phones": [{ "type": "phone", "number": "+49 (0)441 980540", "e164": "+49441980540" }],
    "rating": { "score": 5, "total_count": 325, "recommendation_rate": 100 },
    "member_since": "2006-05-19"
  },
  "finance_plans": [{ "bank": "Openbank Deutschland AG", "term_months": 60, "monthly_installment": 1642.12, "interest_rate_effective": 5.55 }]
}
```

*Trimmed for readability.*

## 🚀 Unlimited Free Mobile de Data — Get It in 60 Seconds

1️⃣ Clone and install:
```bash
git clone https://github.com/omkarcloud/mobile-de-scraper
cd mobile-de-scraper
python -m pip install -r requirements.txt
```

2️⃣ Start the API:
```bash
python run.py
```

mobile.de sits behind Akamai Bot Manager, but these requests get through on your own connection — no browser, no cookies, no proxy needed.

3️⃣ Get your first data:
```bash
curl "http://localhost:8000/cars/details?car=451252928"
```

```json
{
  "id": 451252928,
  "title": "Porsche 911 Carrera Cabriolet Matrix LED…",
  "make": "Porsche",
  "model": "911",
  "price": { "amount": 154890, "currency": "EUR" },
  "mileage_km": 16000,
  "first_registration": "2026-03",
  "power_kw": 290,
  "power_hp": 394,
  "fuel_type": "Petrol",
  "transmission": "Automatic",
  "color": "Black Metallic",
  "seats": 2,
  "image_count": 38,
  "location": { "postcode": "26123", "city": "Oldenburg", "country": "DE" },
  "seller": { "id": 456677, "type": "dealer", "name": "Porsche Zentrum Oldenburg Sportwagen-Center Schmidt + Koch GmbH" },
  "seal": { "id": "PORSCHE", "name": "Porsche Approved" },
  "created_at": "2026-03-24T15:39:17Z"
}
```

All 10 endpoints are now live at `http://localhost:8000`.

## 📚 Endpoints

10 endpoints cover everything you need.

| Endpoint | Path | Returns |
|---|---|---|
| Car Details | `/cars/details` | Everything about one listing, every photo and the dealer included (website contacts: hosted API only) |
| Search Cars | `/cars/search` | 1.8M+ vehicles, 94 filters, 11 sort orders, up to 500 per page |
| Search by Link | `/cars/by-link` | The results of any mobile.de search link, page by page |
| Dealer Details | `/dealers/details` | A dealer's full profile: phones, website, hours, rating and stock (website contacts: hosted API only) |
| Dealer Cars | `/dealers/cars` | Every vehicle a dealer sells, with all 94 search filters |
| Dealer Reviews | `/dealers/reviews` | Every review with its scores, plus the rating breakdown |
| List Makes / Models | `/makes/list`, `/models/list` | Every make and model with the id mobile.de knows it by |
| Search Filters | `/cars/filters` | Every filter and its allowed values, for each vehicle type |
| Location Suggestions | `/locations/suggestions` | Towns and postcodes with coordinates, to search around a place |


## 🔍 Exploring Parameters

The same API is published on RapidAPI, and its playground is the easiest place to try parameters and see raw responses. Once a request looks right, run it locally for **unlimited free** data.

1. [Subscribe to the free plan](https://rapidapi.com/OmkarCloud/api/best-mobile-de-scraper-free-1000-calls/pricing) — 1,000 calls/month, no credit card.
2. [Try the endpoints in the playground](https://rapidapi.com/OmkarCloud/api/best-mobile-de-scraper-free-1000-calls/playground) — every param is pre-filled, so you see real data in one click.
3. Copy the generated code and replace `https://best-mobile-de-scraper-free-1000-calls.p.rapidapi.com` with `http://localhost:8000`. It will now run against your local API.

```python
import requests

# generated by the playground, host swapped for the local API
response = requests.get(
    "http://localhost:8000/cars/details",
    params={"car": "451252928"},
)
print(response.json())
```

## 💬 Have Questions? We Have Answers.

You're a developer — we know how hard completing a project can be. So we offer full support: just message us and we'll reply ✅ with a solution within 1 working day.

[![Message Us on WhatsApp about Mobile de Scraper](https://raw.githubusercontent.com/omkarcloud/assets/master/images/whatsapp-us.png)](https://www.omkar.cloud/l/mobile-de-scraper-github-whatsapp)

[![Ask Us by Email about Mobile de Scraper](https://raw.githubusercontent.com/omkarcloud/assets/master/images/ask-on-email.png)](mailto:happy.to.help@omkar.cloud?subject=Help%20with%20Mobile%20de%20Scraper%20API&body=I%20need%20help%20using%20the%20Mobile%20de%20Scraper%20API.)

## ⚡ Popular Scrapers by Omkar Cloud

- [**Google Maps Scraper (3,100+ GitHub Stars)**](https://github.com/omkarcloud/google-maps-scraper) — type "dentists in New York", get every business as a ready-to-call lead list: phones, emails, websites & reviews. Up to 100K free leads/month.
- [**G2 Scraper**](https://www.omkar.cloud/tools/g2-scraper) — G2 product details, ratings & AI-found contacts
- [**Website Email Contact Scraper**](https://www.omkar.cloud/tools/website-email-contact-scraper) — emails, phones & socials from any website
- [**AliExpress Scraper**](https://www.omkar.cloud/tools/aliexpress-scraper-api) — live product details, SKU variants, stock & shipping
- [**Booking Scraper**](https://www.omkar.cloud/tools/booking-scraper) — Booking.com hotels: prices, ratings, rooms & amenities
- [**Etsy Scraper**](https://www.omkar.cloud/tools/etsy-scraper) — Etsy products: prices, discounts, shops & variations

## ⭐ Love It? [Star It ⭐!](https://github.com/omkarcloud/mobile-de-scraper)

Star the repo ⭐ and become my star hero!

It's just 1 click, but it means the world to me.

[![Star us on GitHub](https://raw.githubusercontent.com/omkarcloud/google-maps-scraper/master/screenshots/star-us.png)](https://github.com/omkarcloud/mobile-de-scraper)
