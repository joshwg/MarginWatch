"""Market data fetchers: stock price and option greeks via yfinance."""

import yfinance as yf
from option_lib.yahoo_data import fetch_option_theta as _lib_fetch_option_theta


def fetch_last_price(symbol: str) -> float | None:
    """Return the last traded price for *symbol*, or None on failure."""
    try:
        return yf.Ticker(symbol).fast_info.last_price
    except Exception:
        return None


def fetch_option_last_price(symbol: str, expiration_iso: str,
                             strike: float, option_type: str) -> float | None:
    """Return the last option price for the given contract, or None."""
    try:
        chain = yf.Ticker(symbol).option_chain(expiration_iso)
        df = chain.calls if option_type in ("CALL", "STOCK") else chain.puts
        match = df[df["strike"] == float(strike)]
        if not match.empty:
            return float(match.iloc[0]["lastPrice"])
    except Exception:
        pass
    return None


def fetch_option_theta(symbol: str, expiration_iso: str,
                       strike: float, option_type: str) -> float | None:
    """Return theta (daily $ decay per share) for the given contract, or None."""
    return _lib_fetch_option_theta(symbol, expiration_iso, strike, option_type)
