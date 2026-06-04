"""Market data fetchers: stock price and option greeks via yfinance."""

import logging

log = logging.getLogger(__name__)


def fetch_last_price(symbol: str) -> float | None:
    """Return the last traded price for *symbol*, or None on failure."""
    import yfinance as yf
    try:
        price = yf.Ticker(symbol).fast_info.last_price
        if price is None:
            log.warning("fetch_last_price(%s): yfinance returned None", symbol)
        return price
    except Exception as exc:
        log.warning("fetch_last_price(%s) failed: %s", symbol, exc)
        return None


def fetch_option_theoretical_price(symbol: str, expiration_iso: str,
                                    strike: float, option_type: str) -> float | None:
    """Return the Black-Scholes theoretical price for the given contract, or None."""
    try:
        from option_lib.yahoo_data import fetch_option_theoretical_price as _fn
        return _fn(symbol, expiration_iso, strike, option_type)
    except ModuleNotFoundError:
        log.debug("option_lib not available — theoretical price skipped for %s", symbol)
        return None
    except Exception as exc:
        log.warning("fetch_option_theoretical_price(%s %s %s %s) failed: %s",
                    symbol, expiration_iso, strike, option_type, exc)
        return None


def fetch_option_theta(symbol: str, expiration_iso: str,
                       strike: float, option_type: str) -> float | None:
    """Return theta (daily $ decay per share) for the given contract, or None."""
    try:
        from option_lib.yahoo_data import fetch_option_theta as _fn
        return _fn(symbol, expiration_iso, strike, option_type)
    except ModuleNotFoundError:
        log.debug("option_lib not available — theta skipped for %s", symbol)
        return None
    except Exception as exc:
        log.warning("fetch_option_theta(%s %s %s %s) failed: %s",
                    symbol, expiration_iso, strike, option_type, exc)
        return None


def fetch_option_delta(symbol: str, expiration_iso: str,
                       strike: float, option_type: str) -> float | None:
    """Return probability of assignment (0–1) for the given contract, or None."""
    try:
        from option_lib.yahoo_data import fetch_option_delta as _fn
        return _fn(symbol, expiration_iso, strike, option_type)
    except ModuleNotFoundError:
        log.debug("option_lib not available — delta skipped for %s", symbol)
        return None
    except Exception as exc:
        log.warning("fetch_option_delta(%s %s %s %s) failed: %s",
                    symbol, expiration_iso, strike, option_type, exc)
        return None
