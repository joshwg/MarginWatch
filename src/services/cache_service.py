"""Market data cache: fetches and stores prices and greeks per session."""

from __future__ import annotations

import time

from models import Position
import services.market_data_service as mds
import services.position_service as ps

_PRICE_TTL = 600.0  # seconds before a cached stock price is considered stale


class CacheService:
    """Holds per-symbol price and per-contract option data for the current session.

    Call fetch_all() after loading positions, then use the accessor methods
    (price, opt_price, theta) to read cached values without hitting the network.
    Stock prices expire after 10 min; call fetch_price() to get a fresh value.
    """

    def __init__(self):
        self._price: dict[str, float | None] = {}
        self._price_ts: dict[str, float] = {}
        self._opt_price: dict[tuple, float | None] = {}
        self._theta: dict[tuple, float | None] = {}
        self._delta: dict[tuple, float | None] = {}

    # ------------------------------------------------------------------
    # Public interface
    # ------------------------------------------------------------------

    def invalidate(self, symbol: str) -> None:
        """Drop all cached data for *symbol* so the next fetch is fresh."""
        self._price.pop(symbol, None)
        self._price_ts.pop(symbol, None)
        self._opt_price = {k: v for k, v in self._opt_price.items() if k[0] != symbol}
        self._theta     = {k: v for k, v in self._theta.items()     if k[0] != symbol}
        self._delta     = {k: v for k, v in self._delta.items()     if k[0] != symbol}

    def fetch_all(self, positions: list[Position]) -> None:
        """Fetch any missing prices and greeks for all positions."""
        self._fetch_prices(positions)
        self._fetch_opt_prices(positions)
        self._fetch_theta(positions)
        self._fetch_delta(positions)

    def fetch_price(self, symbol: str) -> float | None:
        """Return the stock price, re-fetching from the network if the cache is expired.

        A None result (fetch failed) is not cached so the next call retries immediately.
        """
        if time.monotonic() - self._price_ts.get(symbol, 0.0) > _PRICE_TTL:
            price = mds.fetch_last_price(symbol)
            if price is not None:
                self._price[symbol] = price
                self._price_ts[symbol] = time.monotonic()
        return self._price.get(symbol)

    def price(self, symbol: str) -> float | None:
        """Return the cached stock price without triggering a network fetch."""
        return self._price.get(symbol)

    def opt_price(self, key: tuple) -> float | None:
        return self._opt_price.get(key)

    def theta(self, key: tuple) -> float | None:
        return self._theta.get(key)

    def delta(self, key: tuple) -> float | None:
        return self._delta.get(key)

    # ------------------------------------------------------------------
    # Private fetchers
    # ------------------------------------------------------------------

    def _fetch_prices(self, positions: list[Position]) -> None:
        for sym in {p.symbol for p in positions}:
            self.fetch_price(sym)

    def _fetch_opt_prices(self, positions: list[Position]) -> None:
        for pos in positions:
            if not pos.strike:
                continue
            ot = ps.pricing_option_type(pos)
            key = (pos.symbol, pos.expiration, pos.strike, ot)
            if key not in self._opt_price:
                self._opt_price[key] = mds.fetch_option_theoretical_price(
                    pos.symbol, pos.expiration, pos.strike, ot)
            if ps.is_spread(pos):
                long_key = (pos.symbol, pos.expiration, pos.long_strike, ot)
                if long_key not in self._opt_price:
                    self._opt_price[long_key] = mds.fetch_option_theoretical_price(
                        pos.symbol, pos.expiration, pos.long_strike, ot)

    def _fetch_theta(self, positions: list[Position]) -> None:
        for pos in positions:
            if ps.is_stock(pos) and not pos.strike:
                continue
            ot = ps.pricing_option_type(pos)
            key = (pos.symbol, pos.expiration, pos.strike, ot)
            if key not in self._theta:
                self._theta[key] = mds.fetch_option_theta(
                    pos.symbol, pos.expiration, pos.strike, ot)
            if ps.is_spread(pos):
                long_key = (pos.symbol, pos.expiration, pos.long_strike, ot)
                if long_key not in self._theta:
                    self._theta[long_key] = mds.fetch_option_theta(
                        pos.symbol, pos.expiration, pos.long_strike, ot)

    def _fetch_delta(self, positions: list[Position]) -> None:
        for pos in positions:
            if ps.is_stock(pos) and not pos.strike:
                continue
            ot = ps.pricing_option_type(pos)
            key = (pos.symbol, pos.expiration, pos.strike, ot)
            if key not in self._delta:
                self._delta[key] = mds.fetch_option_delta(
                    pos.symbol, pos.expiration, pos.strike, ot)
