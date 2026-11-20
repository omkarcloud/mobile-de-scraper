"""Cache TTL per /mobile-de/* endpoint (cache.py, keyed on the validated
params). dealers/details, and the id <-> slug lookups of dealers/cars and
dealers/reviews, share ONE cached parse of the dealer page (api._dealer_record).
"""
from datetime import timedelta

# One row per (filters, sort, page, size, language): listings are added and
# sold all day, and "most_recent" is only useful fresh.
SEARCH_CACHE = timedelta(minutes=15)
# A listing's price changes a few times in its life; a sold one disappears,
# which an hour of caching hides no longer than the site's own pages.
DETAILS_CACHE = timedelta(hours=1)
# Makes and models: the lists grow by a model a month.
REFERENCE_CACHE = timedelta(hours=24)
# A dealer's page: contacts and texts change rarely, the stock count daily.
DEALER_CACHE = timedelta(hours=6)
# Where a dealer's "homepage" link leads: his own website changes about never.
WEBSITE_CACHE = timedelta(days=7)
# New reviews arrive a few times a week on a busy dealer.
REVIEWS_CACHE = timedelta(hours=6)
# Places and their coordinates do not move.
LOCATION_CACHE = timedelta(days=30)
