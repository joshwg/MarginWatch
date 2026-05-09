"""Market data cache: fetches and stores prices and greeks per session."""

from __future__ import annotations

from models import Position
import services.market_data_service as mds
import services.position_service as ps


class CacheService:
    """Holds per-symbol price and per-contract option data for the current session.

    Call fetch_all() after loading positions, then use the accessor methods
    (price, opt_price, theta) to read cached values without hitting the network.
    """

    def __init__(self):
        self._price: dict[str, float | None] = {}
        self._opt_price: dict[tuple, float | None] = {}
        self._theta: dict[tuple, float | None] = {}

    # ------------------------------------------------------------------
    # Public interface
    # ------------------------------------------------------------------

    def invalidate(self, symbol: str) -> None:
        """Drop all cached data for *symbol* so the next fetch is fresh."""
        self._price.pop(symbol, None)
        self._opt_price = {k: v for k, v in self._opt_price.items() if k[0] != symbol}
        self._theta     = {k: v for k, v in self._theta.items()     if k[0] != symbol}

    def fetch_all(self, positions: list[Position]) -> None:
        """Fetch any missing prices and greeks for all positions."""
        self._fetch_prices(positions)
        self._fetch_opt_prices(positions)
        self._fetch_theta(positions)

    def price(self, symbol: str) -> float | None:
        return self._price.get(symbol)

    def opt_price(self, key: tuple) -> float | None:
        return self._opt_price.get(key)

    def theta(self, key: tuple) -> float | None:
        return self._theta.get(key)

    # ------------------------------------------------------------------
    # Private fetchers
    # ------------------------------------------------------------------

    def _fetch_prices(self, positions: list[Position]) -> None:
        for sym in {p.symbol for p in positions}:
            if sym not in self._price:
                self._price[sym] = mds.fetch_last_price(sym)

    def _fetch_opt_prices(self, positions: list[Position]) -> None:
        for pos in positions:
            if not pos.strike:
                continue
            ot = ps.pricing_option_type(pos)
            key = (pos.symbol, pos.expiration, pos.strike, ot)
            if key not in self._opt_price:
                self._opt_price[key] = mds.fetch_option_last_price(
                    pos.symbol, pos.expiration, pos.strike, ot)

    def _fetch_theta(self, positions: list[Position]) -> None:
        for pos in positions:
            if ps.is_stock(pos) and not pos.strike:
                continue
            ot = ps.pricing_option_type(pos)
            key = (pos.symbol, pos.expiration, pos.strike, ot)
            if key not in self._theta:
                self._theta[key] = mds.fetch_option_theta(
                    pos.symbol, pos.expiration, pos.strike, ot)
