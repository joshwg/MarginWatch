"""Market data fetchers — routes through option_lib's provider abstraction.

get_provider() selects MassiveDataProvider when MASSIVE_API_KEY is set,
otherwise falls back to YahooDataProvider.  All fetches go through that
provider so switching the env var is all that is needed to switch sources.
"""

import logging
import os

log = logging.getLogger(__name__)

# Sanity bounds for a stock price.  Anything outside [_PRICE_MIN, _PRICE_MAX]
# is treated as a data error and discarded.
_PRICE_MIN =       0.01   # nothing trades below a penny
_PRICE_MAX  = 1_000_000   # nothing trades above $1M

# Module-level fetch-failure tracking.  Each failed symbol maps to a short
# human-readable reason.  Callers collect and clear via pop_fetch_failures().
_fetch_failures: dict[str, str] = {}


def pop_fetch_failures() -> dict[str, str]:
    """Return all fetch failures recorded since the last call, then clear them."""
    global _fetch_failures
    result, _fetch_failures = dict(_fetch_failures), {}
    return result


def _provider_name() -> str:
    return "Massive.com" if os.environ.get("MASSIVE_API_KEY") else "Yahoo Finance"


def _valid_price(price) -> bool:
    """Return True only if *price* is a finite number within plausible bounds."""
    try:
        return _PRICE_MIN <= float(price) <= _PRICE_MAX
    except (TypeError, ValueError):
        return False


def fetch_last_price(symbol: str, use_extended: bool = False) -> float | None:
    """Return the last traded price for *symbol*, or None on failure.

    Routes through get_provider() so Massive is used when MASSIVE_API_KEY is set.
    When use_extended=True, prefers post-market then pre-market price.
    Falls back to direct yfinance if option_lib is not installed.
    """
    provider_label = _provider_name()
    try:
        from option_lib.data_provider import get_provider
        info = get_provider().get_stock_info(symbol)
        if info.get('success'):
            if use_extended:
                price = (info.get('post_market_price') or info.get('pre_market_price')
                         or info.get('current_price'))
            else:
                price = info.get('current_price')
            if price and _valid_price(price):
                return float(price)
            if price is not None:
                log.warning("fetch_last_price(%s): implausible price %s from %s",
                            symbol, price, provider_label)
                _fetch_failures[symbol] = f"implausible price ({price}) from {provider_label}"
                return None
        # success=False or price=None: fall through to yfinance
    except ModuleNotFoundError:
        pass   # option_lib not installed — use yfinance directly
    except Exception as exc:
        log.warning("fetch_last_price(%s) provider fetch failed: %s", symbol, exc)

    # Direct yfinance fallback (no option_lib, or provider returned nothing)
    import yfinance as yf
    try:
        ticker = yf.Ticker(symbol)
        price = ticker.fast_info.last_price
        if price is None:
            log.debug("fetch_last_price(%s): fast_info.last_price None, trying previous_close", symbol)
            price = ticker.fast_info.previous_close
        if price is None:
            log.debug("fetch_last_price(%s): fast_info returned None, trying history", symbol)
            df = ticker.history(period="1d", auto_adjust=False)
            price = float(df["Close"].iloc[-1]) if not df.empty else None
        if price is None:
            log.warning("fetch_last_price(%s): all methods returned None", symbol)
            _fetch_failures[symbol] = f"no price data returned by {provider_label}"
            return None
        if not _valid_price(price):
            log.warning("fetch_last_price(%s): implausible price %s — treating as unavailable",
                        symbol, price)
            _fetch_failures[symbol] = f"implausible price ({price}) from {provider_label}"
            return None
        return float(price)
    except Exception as exc:
        log.warning("fetch_last_price(%s) failed: %s", symbol, exc)
        _fetch_failures[symbol] = f"price fetch failed from {provider_label} ({type(exc).__name__})"
        return None


def fetch_option_theoretical_price(symbol: str, expiration_iso: str,
                                    strike: float, option_type: str,
                                    r: float = 0.045) -> float | None:
    """Return the American-binomial theoretical price for the given contract, or None."""
    try:
        from option_lib.data_provider import get_provider
        return get_provider().fetch_option_theoretical_price(
            symbol, expiration_iso, strike, option_type, r)
    except ModuleNotFoundError:
        log.debug("option_lib not available — theoretical price skipped for %s", symbol)
        return None
    except Exception as exc:
        log.warning("fetch_option_theoretical_price(%s %s %s %s) failed: %s",
                    symbol, expiration_iso, strike, option_type, exc)
        _fetch_failures.setdefault(
            symbol, f"option data unavailable from {_provider_name()} ({type(exc).__name__})")
        return None


def fetch_option_theta(symbol: str, expiration_iso: str,
                       strike: float, option_type: str,
                       r: float = 0.045) -> float | None:
    """Return theta (daily $ decay per share) for the given contract, or None."""
    try:
        from option_lib.data_provider import get_provider
        return get_provider().fetch_option_theta(
            symbol, expiration_iso, strike, option_type, r)
    except ModuleNotFoundError:
        log.debug("option_lib not available — theta skipped for %s", symbol)
        return None
    except Exception as exc:
        log.warning("fetch_option_theta(%s %s %s %s) failed: %s",
                    symbol, expiration_iso, strike, option_type, exc)
        _fetch_failures.setdefault(
            symbol, f"option data unavailable from {_provider_name()} ({type(exc).__name__})")
        return None


def fetch_option_greeks(symbol: str, expiration_iso: str,
                        strike: float, option_type: str,
                        r: float = 0.045,
                        use_extended: bool = False) -> dict:
    """Return {'price', 'theta', 'delta'} for one contract in a single round-trip.

    Routes through get_provider() so Massive is used when MASSIVE_API_KEY is set.
    When use_extended=True, S is taken from the extended-hours price so that
    $/shr, theta, and delta all reflect the after/pre-market underlying price.
    """
    _none = {'price': None, 'theta': None, 'delta': None}
    try:
        from option_lib.data_provider import get_provider
        result = get_provider().fetch_option_greeks(
            symbol, expiration_iso, strike, option_type, r=r, use_extended=use_extended)
        if all(v is None for v in result.values()):
            _fetch_failures.setdefault(
                symbol, f"option data unavailable from {_provider_name()}")
        return result
    except ModuleNotFoundError:
        log.debug("option_lib not available — greeks skipped for %s", symbol)
        return _none
    except Exception as exc:
        log.warning("fetch_option_greeks(%s %s %s %s) failed: %s",
                    symbol, expiration_iso, strike, option_type, exc)
        _fetch_failures.setdefault(
            symbol, f"option data unavailable from {_provider_name()} ({type(exc).__name__})")
        return _none


def fetch_option_delta(symbol: str, expiration_iso: str,
                       strike: float, option_type: str,
                       r: float = 0.045) -> float | None:
    """Return probability of assignment (0–1) for the given contract, or None."""
    try:
        from option_lib.data_provider import get_provider
        return get_provider().fetch_option_delta(
            symbol, expiration_iso, strike, option_type, r)
    except ModuleNotFoundError:
        log.debug("option_lib not available — delta skipped for %s", symbol)
        return None
    except Exception as exc:
        log.warning("fetch_option_delta(%s %s %s %s) failed: %s",
                    symbol, expiration_iso, strike, option_type, exc)
        _fetch_failures.setdefault(
            symbol, f"option data unavailable from {_provider_name()} ({type(exc).__name__})")
        return None
