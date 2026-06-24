"""Market data fetchers: stock price and option greeks via yfinance."""

import logging
import os

log = logging.getLogger(__name__)

# Print once at import time so the operator knows which data source is active.
_provider = "Massive.com" if os.environ.get("MASSIVE_API_KEY") else "Yahoo Finance"
print(f"Pricing data: {_provider}"
      + ("" if _provider == "Massive.com" else "  (set MASSIVE_API_KEY to use Massive.com)"))

# Sanity bounds for a stock price.  Anything outside [_PRICE_MIN, _PRICE_MAX]
# is treated as a yfinance data error and discarded.
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


def _valid_price(price) -> bool:
    """Return True only if *price* is a finite number within plausible bounds."""
    try:
        return _PRICE_MIN <= float(price) <= _PRICE_MAX
    except (TypeError, ValueError):
        return False


def fetch_last_price(symbol: str, use_extended: bool = False) -> float | None:
    """Return the last traded price for *symbol*, or None on failure.

    When use_extended=True, prefers post-market then pre-market price if the
    extended-hours session is active; falls back to the regular-session price.
    In regular mode, tries fast_info.last_price first then history().
    Prices outside [_PRICE_MIN, _PRICE_MAX] are treated as data errors.
    """
    if use_extended:
        try:
            from option_lib.yahoo_data import get_stock_info
            info = get_stock_info(symbol)
            if info.get('success'):
                ext = info.get('post_market_price') or info.get('pre_market_price')
                reg = info.get('current_price')
                price = ext or reg
                if price and _valid_price(price):
                    return float(price)
        except ModuleNotFoundError:
            pass  # option_lib not installed — fall through to yfinance path
        except Exception as exc:
            log.warning("fetch_last_price(%s) extended-hours fetch failed: %s", symbol, exc)

    import yfinance as yf
    try:
        ticker = yf.Ticker(symbol)
        price = ticker.fast_info.last_price
        if price is None:
            log.debug("fetch_last_price(%s): fast_info returned None, trying history", symbol)
            df = ticker.history(period="1d", auto_adjust=False)
            price = float(df["Close"].iloc[-1]) if not df.empty else None
        if price is None:
            log.warning("fetch_last_price(%s): all methods returned None", symbol)
            _fetch_failures[symbol] = f"no price data returned by {_provider}"
            return None
        if not _valid_price(price):
            log.warning("fetch_last_price(%s): implausible price %s — treating as unavailable",
                        symbol, price)
            _fetch_failures[symbol] = f"implausible price ({price}) from {_provider}"
            return None
        return float(price)
    except Exception as exc:
        log.warning("fetch_last_price(%s) failed: %s", symbol, exc)
        _fetch_failures[symbol] = f"price fetch failed from {_provider} ({type(exc).__name__})"
        return None


def fetch_option_theoretical_price(symbol: str, expiration_iso: str,
                                    strike: float, option_type: str,
                                    r: float = 0.045) -> float | None:
    """Return the American-binomial theoretical price for the given contract, or None."""
    try:
        from option_lib.yahoo_data import fetch_option_theoretical_price as _fn
        return _fn(symbol, expiration_iso, strike, option_type, r=r)
    except ModuleNotFoundError:
        log.debug("option_lib not available — theoretical price skipped for %s", symbol)
        return None
    except Exception as exc:
        log.warning("fetch_option_theoretical_price(%s %s %s %s) failed: %s",
                    symbol, expiration_iso, strike, option_type, exc)
        _fetch_failures.setdefault(
            symbol, f"option data unavailable from {_provider} ({type(exc).__name__})")
        return None


def fetch_option_theta(symbol: str, expiration_iso: str,
                       strike: float, option_type: str,
                       r: float = 0.045) -> float | None:
    """Return theta (daily $ decay per share) for the given contract, or None."""
    try:
        from option_lib.yahoo_data import fetch_option_theta as _fn
        return _fn(symbol, expiration_iso, strike, option_type, r=r)
    except ModuleNotFoundError:
        log.debug("option_lib not available — theta skipped for %s", symbol)
        return None
    except Exception as exc:
        log.warning("fetch_option_theta(%s %s %s %s) failed: %s",
                    symbol, expiration_iso, strike, option_type, exc)
        _fetch_failures.setdefault(
            symbol, f"option data unavailable from {_provider} ({type(exc).__name__})")
        return None


def fetch_option_greeks(symbol: str, expiration_iso: str,
                        strike: float, option_type: str,
                        r: float = 0.045) -> dict:
    """Return {'price', 'theta', 'delta'} for one contract in a single round-trip.

    Replaces three separate fetch_option_* calls: fetches stock info and IV once,
    runs the binomial tree model once, and returns all three values together.
    """
    _none = {'price': None, 'theta': None, 'delta': None}
    try:
        from option_lib.yahoo_data import fetch_option_greeks as _fn
        result = _fn(symbol, expiration_iso, strike, option_type, r=r)
        if all(v is None for v in result.values()):
            _fetch_failures.setdefault(
                symbol, f"option data unavailable from {_provider}")
        return result
    except ModuleNotFoundError:
        log.debug("option_lib not available — greeks skipped for %s", symbol)
        return _none
    except Exception as exc:
        log.warning("fetch_option_greeks(%s %s %s %s) failed: %s",
                    symbol, expiration_iso, strike, option_type, exc)
        _fetch_failures.setdefault(
            symbol, f"option data unavailable from {_provider} ({type(exc).__name__})")
        return _none


def fetch_option_delta(symbol: str, expiration_iso: str,
                       strike: float, option_type: str,
                       r: float = 0.045) -> float | None:
    """Return probability of assignment (0–1) for the given contract, or None."""
    try:
        from option_lib.yahoo_data import fetch_option_delta as _fn
        return _fn(symbol, expiration_iso, strike, option_type, r=r)
    except ModuleNotFoundError:
        log.debug("option_lib not available — delta skipped for %s", symbol)
        return None
    except Exception as exc:
        log.warning("fetch_option_delta(%s %s %s %s) failed: %s",
                    symbol, expiration_iso, strike, option_type, exc)
        _fetch_failures.setdefault(
            symbol, f"option data unavailable from {_provider} ({type(exc).__name__})")
        return None
