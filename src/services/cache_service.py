"""Market data cache: fetches and stores prices and greeks per session."""

from __future__ import annotations

import time

from models import Position
import services.market_data_service as mds
import services.position_service as ps

_PRICE_TTL = 600.0  # seconds before a cached stock price is considered stale

# Theta clipping: caps the raw per-share, per-day theta returned by cache.theta()
# to guard against blow-ups when pricing data is degenerate.
_THETA_ABS_MAX       = 1_000_000.0  # hard upper bound regardless of option type
_THETA_CALL_STRIKE_MULT = 2.0       # CALL cap = this × strike


class CacheService:
    """Holds per-symbol price and per-contract option data for the current session.

    Call fetch_all() after loading positions, then use the accessor methods
    (price, opt_price, theta) to read cached values without hitting the network.
    Stock prices expire after 10 min; call fetch_price() to get a fresh value.

    Parameters
    ----------
    r : float
        Risk-free interest rate as a decimal (e.g. 0.045 for 4.5%).  Used for
        all option pricing, theta, and delta calculations.
    """

    def __init__(self, r: float = 0.045, use_extended: bool = False):
        self._r = r
        self._use_extended = use_extended
        self._price: dict[str, float | None] = {}
        self._price_session: dict[str, str | None] = {}
        self._price_ts: dict[str, float] = {}
        self._opt_price: dict[tuple, float | None] = {}
        self._theta: dict[tuple, float | None] = {}
        self._delta: dict[tuple, float | None] = {}
        self._earnings: dict[str, str | None] = {}
        # symbol → short error description; persists until cache is reset
        self._failed: dict[str, str] = {}
        # Human-readable status of the in-progress fetch ("AAPL", "TSLA options", …)
        self.current_fetch: str = ""

    # ------------------------------------------------------------------
    # Public interface
    # ------------------------------------------------------------------

    def set_extended_hours(self, value: bool) -> None:
        """Switch extended-hours mode and clear all cached market data.

        Prices, option greeks, and the options chain all use S (the stock price)
        so everything must re-fetch with the new underlying price.
        """
        if self._use_extended == value:
            return
        self._use_extended = value
        self._price.clear()
        self._price_session.clear()
        self._price_ts.clear()
        self._opt_price.clear()
        self._theta.clear()
        self._delta.clear()

    def invalidate(self, symbol: str) -> None:
        """Drop all cached data for *symbol* so the next fetch is fresh."""
        self._price.pop(symbol, None)
        self._price_session.pop(symbol, None)
        self._price_ts.pop(symbol, None)
        self._opt_price = {k: v for k, v in self._opt_price.items() if k[0] != symbol}
        self._theta     = {k: v for k, v in self._theta.items()     if k[0] != symbol}
        self._delta     = {k: v for k, v in self._delta.items()     if k[0] != symbol}
        self._earnings.pop(symbol, None)

    def fetch_all(self, positions: list[Position]) -> None:
        """Fetch any missing prices and greeks for all positions."""
        self._fetch_prices(positions)
        self._fetch_greeks(positions)
        # Collect any new failures; setdefault keeps the first (price) error per symbol
        for sym, msg in mds.pop_fetch_failures().items():
            self._failed.setdefault(sym, msg)

    def fetch_errors(self) -> list[str]:
        """Return human-readable fetch errors, one entry per failed symbol."""
        return [f"{sym}: {msg}" for sym, msg in sorted(self._failed.items())]

    def fetch_price(self, symbol: str) -> float | None:
        """Return the stock price, re-fetching from the network if the cache is expired.

        A None result (fetch failed) is not cached so the next call retries immediately.
        """
        if time.monotonic() - self._price_ts.get(symbol, 0.0) > _PRICE_TTL:
            price, session = mds.fetch_last_price(symbol, use_extended=self._use_extended)
            if price is not None:
                self._price[symbol] = price
                self._price_session[symbol] = session
                self._price_ts[symbol] = time.monotonic()
        return self._price.get(symbol)

    def price(self, symbol: str) -> float | None:
        """Return the cached stock price without triggering a network fetch."""
        return self._price.get(symbol)

    def price_session(self, symbol: str) -> str | None:
        """Return 'pre', 'post', or None (regular session) for the cached price."""
        return self._price_session.get(symbol)

    def opt_price(self, key: tuple) -> float | None:
        return self._opt_price.get(key)

    def theta(self, key: tuple) -> float | None:
        val = self._theta.get(key)
        if val is None:
            return None
        symbol, _exp, strike, option_type = key
        # Clip degenerate theta values (blow-ups when pricing data is unavailable).
        # Theta (per share, per day) physically can't exceed the option's max value:
        #   PUT  → capped at the underlier price (a put can never be worth more than S)
        #   CALL → capped at twice the strike (generous but bounded upper limit)
        # A hard 1,000,000 limit also guards against any remaining edge cases.
        if option_type == 'PUT':
            stock_price = self._price.get(symbol)
            cap = min(_THETA_ABS_MAX, stock_price) if stock_price is not None else _THETA_ABS_MAX
        else:  # CALL
            cap = min(_THETA_ABS_MAX, _THETA_CALL_STRIKE_MULT * strike) if strike is not None else _THETA_ABS_MAX
        if val < -cap:
            val = -cap
        elif val > cap:
            val = cap
        return val

    def delta(self, key: tuple) -> float | None:
        return self._delta.get(key)

    def earnings_date(self, symbol: str) -> str | None:
        return self._earnings.get(symbol)

    def fetch_earnings_date(self, symbol: str) -> str | None:
        """Return the earnings date, fetching from the network if not yet cached."""
        if symbol not in self._earnings:
            self._earnings[symbol] = mds.fetch_earnings_date(symbol)
        return self._earnings.get(symbol)

    # ------------------------------------------------------------------
    # Private fetchers
    # ------------------------------------------------------------------

    def _fetch_prices(self, positions: list[Position]) -> None:
        for sym in {p.symbol for p in positions}:
            self.current_fetch = sym
            self.fetch_price(sym)
            if sym not in self._earnings:
                self._earnings[sym] = mds.fetch_earnings_date(sym)
        self.current_fetch = ""

    def _fetch_greeks(self, positions: list[Position]) -> None:
        """Fetch price, theta, and delta for every option contract in one pass.

        Replaces the former _fetch_opt_prices / _fetch_theta / _fetch_delta trio.
        Each contract is fetched with a single fetch_option_greeks() call which:
          - queries stock info once per symbol (cached)
          - tries a targeted single-contract OCC quote before falling back to the
            full option chain (avoiding downloading hundreds of strikes we don't need)
          - runs the binomial tree model once instead of three times
        """
        for pos in positions:
            if ps.is_stock(pos) and not pos.strike:
                continue
            if ps.is_straddle(pos):
                for s, ot in [(pos.strike, 'CALL'), (pos.strike2, 'PUT')]:
                    k = (pos.symbol, pos.expiration, s, ot)
                    if k not in self._theta:
                        self.current_fetch = f"{pos.symbol} options"
                        g = mds.fetch_option_greeks(pos.symbol, pos.expiration, s, ot, r=self._r, use_extended=self._use_extended)
                        self._opt_price[k] = g['price']
                        self._theta[k]     = g['theta']
                        self._delta[k]     = g['delta']
                continue
            ot  = ps.pricing_option_type(pos)
            key = (pos.symbol, pos.expiration, pos.strike, ot)
            if key not in self._theta:
                self.current_fetch = f"{pos.symbol} options"
                g = mds.fetch_option_greeks(pos.symbol, pos.expiration, pos.strike, ot, r=self._r, use_extended=self._use_extended)
                self._opt_price[key] = g['price']
                self._theta[key]     = g['theta']
                self._delta[key]     = g['delta']
            if ps.is_spread(pos):
                long_key = (pos.symbol, pos.expiration, pos.strike2, ot)
                if long_key not in self._theta:
                    self.current_fetch = f"{pos.symbol} options"
                    g = mds.fetch_option_greeks(pos.symbol, pos.expiration, pos.strike2, ot, r=self._r, use_extended=self._use_extended)
                    self._opt_price[long_key] = g['price']
                    self._theta[long_key]     = g['theta']
                    self._delta[long_key]     = g['delta']
        self.current_fetch = ""
