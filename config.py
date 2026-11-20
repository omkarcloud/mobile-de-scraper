"""Configuration for the Mobile de Scraper. Everything can be set with an environment
variable; the defaults work out of the box.

    PORT              port the API listens on (default 8000)
    MOBILE_DE_PROXY   proxy URL for every request, e.g. http://user:pass@host:port
                      (default: none, requests go out from your own connection).
                      mobile.de sits behind Akamai Bot Manager, which lets these
                      requests through from your own connection: no proxy needed,
                      leave this unset. The datacenter and residential proxies we
                      tried passed only about one request in two.

Everything else below is a plain constant with a working default.
"""
import os

PORT = int(os.environ.get("PORT", "8000"))

MOBILE_DE_PROXY = os.environ.get("MOBILE_DE_PROXY") or None


# ---- what the code reads off config: defaults for a direct run ----
MAX_RETRIES = 3
MOBILE_DE_BLOCK_HOLD_S = 600
MOBILE_DE_EXIT = MOBILE_DE_PROXY      # the exit every session leaves through: your proxy, or direct
MOBILE_DE_FALLBACK_EXIT = None
MOBILE_DE_UNSHOWN_DEALER_FIELDS = False
MOBILE_DE_UNSHOWN_REVIEW_ANSWERS = False
RETRY_BACKOFF = 2
