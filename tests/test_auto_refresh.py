"""tests/test_auto_refresh.py

The light refresh behind the web client's 15-minute auto-update:
CacheService.refresh_priced_state() drops everything derived from a stock
price (prices, option greeks) so the next pull re-prices, while display-only
caches that do not move with the price (bars, sector, earnings, company
names) survive.  No network.
"""

import os
import sys
import time

os.environ.setdefault("MARGIN_PWD", "test")
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from services.cache_service import CacheService


def test_refresh_priced_state_drops_prices_keeps_display_caches():
    c = CacheService()
    key = ("AAPL", "2099-01-15", 200.0, "PUT")
    c._price["AAPL"] = 150.0
    c._price_session["AAPL"] = None
    c._price_ts["AAPL"] = time.monotonic()
    c._price_extended["AAPL"] = False
    c._opt_price[key] = 5.0
    c._theta[key] = -0.05
    c._delta[key] = -0.3
    c._earnings["AAPL"] = "2099-01-01"
    c._sector["AAPL"] = "Technology"
    c._company_name["AAPL"] = "Apple Inc."
    c._bars["AAPL"] = (time.monotonic() + 3600, [{"t": 0, "c": 150.0}])
    c._failed["TSLA"] = "boom"

    c.refresh_priced_state()

    # Re-priced on the next pull…
    assert c.price("AAPL") is None
    assert c.opt_price(key) is None
    assert c._theta == {} and c._delta == {}
    assert c.price_age("AAPL") is None
    # …but display-only data and the error list stay.
    assert c.earnings_date("AAPL") == "2099-01-01"
    assert c.sector("AAPL") == "Technology"
    assert c._company_name["AAPL"] == "Apple Inc."
    assert "AAPL" in c._bars
    assert c.fetch_errors() == ["TSLA: boom"]


def test_refresh_prices_route_registered():
    import main_web
    rules = {r.rule: r.methods for r in main_web.app.url_map.iter_rules()}
    assert "/api/refresh-prices" in rules
    assert "POST" in rules["/api/refresh-prices"]
