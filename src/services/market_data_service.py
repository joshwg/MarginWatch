"""Market data fetchers — routes through option_lib's provider abstraction.

get_provider() selects MassiveDataProvider when MASSIVE_API_KEY is set,
otherwise falls back to YahooDataProvider.  All fetches go through that
provider so switching the env var is all that is needed to switch sources.
"""

import logging
import os
import threading
from datetime import datetime, time as _time
from zoneinfo import ZoneInfo

log = logging.getLogger(__name__)

# Name this app to option_lib's disk caches (sectors, names), which are shared
# with the sibling apps through OPTION_LIB_CACHE_DIR.  Only this app's scratch
# file is named for it; the cached sectors themselves are common to all.
# option_lib stays an optional dependency here, as everywhere else in this file.
try:
    from option_lib.disk_cache import set_app_tag as _set_app_tag
    _set_app_tag("margin")
except ImportError:
    pass

# Sanity bounds for a stock price.  Anything outside [_PRICE_MIN, _PRICE_MAX]
# is treated as a data error and discarded.
_PRICE_MIN =       0.01   # nothing trades below a penny
_PRICE_MAX  = 1_000_000   # nothing trades above $1M

# Regular US equity session, Eastern time.
_MARKET_TZ    = ZoneInfo("America/New_York")
_MARKET_OPEN  = _time(9, 30)
_MARKET_CLOSE = _time(16, 0)
# Post-market pricing runs from the 16:00 close until 04:00 the next morning,
# when pre-market takes over.  Wider than the exchanges' own 20:00 cutoff on
# purpose: the last after-hours print stays the best available quote overnight.
_PRE_OPEN     = _time(4, 0)

# Which price basis each clock session uses: pre-market hours show the
# pre-market print, regular hours the regular one, and every other hour the
# post-market print.  'closed' — the weekend, and Monday before 04:00 — maps to
# 'post' because no trade happens in that window, so Friday's last after-hours
# print is still the freshest quote there is.  Falling back to the regular close
# would discard every after-hours move across the weekend, which is exactly when
# news tends to land.  Mirrors option_lib.math_util.SESSION_BASIS.
_SESSION_BASIS = {'pre': 'pre', 'regular': None, 'post': 'post', 'closed': 'post'}

# Price basis → the get_stock_info() field holding it.
_EXTENDED_PRICE_KEY = {'pre': 'pre_market_price', 'post': 'post_market_price'}

# Fetch-failure tracking: each failed symbol maps to a short human-readable
# reason, collected and cleared by the caller via pop_fetch_failures().
#
# Thread-local, not module-global.  The web server is threaded, and a client
# that navigates away (a delete or a sort mid-pull) leaves its /api/prices
# handler running to completion — with a shared dict that abandoned handler's
# pop would swallow the failures recorded by the request that replaced it, and
# the error banner would silently lose them.  Each request thread now drains
# only what it recorded itself.
_failures = threading.local()


def _failure_map() -> dict[str, str]:
    """This thread's failure dict, created on first use."""
    fmap = getattr(_failures, 'map', None)
    if fmap is None:
        fmap = _failures.map = {}
    return fmap


def record_fetch_failure(symbol: str, reason: str, *, overwrite: bool = True) -> None:
    """Note that *symbol* could not be fetched, for the caller to collect later."""
    if overwrite:
        _failure_map()[symbol] = reason
    else:
        _failure_map().setdefault(symbol, reason)


def pop_fetch_failures() -> dict[str, str]:
    """Return the failures this thread recorded since its last call, then clear them."""
    fmap = _failure_map()
    result = dict(fmap)
    fmap.clear()
    return result


def market_session(now: datetime | None = None) -> str:
    """Which price basis the clock calls for, Eastern time:

        04:00 – 09:30   'pre'
        09:30 – 16:00   'regular'
        16:00 – 04:00   'post'      (runs past midnight into the next morning)
        weekends        'closed'    (no extended-hours print exists)

    The 00:00–04:00 stretch belongs to the *previous* day's post session, so it
    counts as 'post' only when that previous day was a weekday — Saturday's
    small hours continue Friday's evening, Sunday's and Monday's do not.

    Market holidays are not tracked; a holiday reads as whatever window the
    clock falls in, which is harmless because no extended-hours quote exists
    then and fetch_last_price() falls back to the regular price.
    """
    now = now or datetime.now(_MARKET_TZ)
    hm  = now.time()
    if hm < _PRE_OPEN:                       # after midnight, before 04:00
        return 'post' if 1 <= now.weekday() <= 5 else 'closed'   # Tue–Sat
    if now.weekday() >= 5:                   # Saturday / Sunday daytime
        return 'closed'
    if hm < _MARKET_OPEN:
        return 'pre'
    if hm < _MARKET_CLOSE:
        return 'regular'
    return 'post'


def in_extended_hours(now: datetime | None = None) -> bool:
    """True when the US equity market is outside its regular 9:30–16:00 ET session.

    Weekends and the pre/post windows both count.  Market holidays are not
    tracked, so a holiday reads as extended hours — harmless, because no
    extended-hours quote exists then and fetch_last_price() falls back to the
    regular price with session=None.
    """
    return market_session(now) != 'regular'


def _provider_name() -> str:
    return "Massive.com" if os.environ.get("MASSIVE_API_KEY") else "Yahoo Finance"


# Logged once per process: a missing option_lib silently downgrades every price
# to bare yfinance, which has no extended-hours support at all.
_warned_missing_option_lib = False


def _warn_missing_option_lib() -> None:
    global _warned_missing_option_lib
    if not _warned_missing_option_lib:
        _warned_missing_option_lib = True
        log.warning("option_lib not importable — falling back to plain yfinance; "
                    "extended-hours prices and %s data are unavailable", _provider_name())


def _valid_price(price) -> bool:
    """Return True only if *price* is a finite number within plausible bounds."""
    try:
        return _PRICE_MIN <= float(price) <= _PRICE_MAX
    except (TypeError, ValueError):
        return False


def fetch_last_price(symbol: str, use_extended: bool = False) -> tuple[float | None, str | None]:
    """Return (price, session) for *symbol*, or (None, None) on failure.

    *session* is 'pre' or 'post' when the returned price came from extended-hours
    data, or None when it's a regular-session price.

    Routes through get_provider() so Massive is used when MASSIVE_API_KEY is set.
    When use_extended=True, uses whichever extended-hours price the provider
    reports; at most one of pre/post is populated for any given session.
    Falls back to direct yfinance if option_lib is not installed.
    """
    provider_label = _provider_name()
    try:
        from option_lib.data_provider import get_provider
        info = get_provider().get_stock_info(symbol)
        if info.get('success'):
            price, session = info.get('current_price'), None
            if use_extended:
                # Take only the price belonging to the basis the clock calls for.
                # Providers keep reporting a field after its session ends — Yahoo
                # still carries the morning's preMarketPrice all evening — so
                # trusting whichever field is populated served a ~12-hour-stale
                # quote after 16:00.  An empty field means no extended trading
                # has printed: fall back to the regular price.
                basis = _SESSION_BASIS.get(market_session())
                key = _EXTENDED_PRICE_KEY.get(basis)
                if key and info.get(key):
                    price, session = info.get(key), basis
            if price and _valid_price(price):
                return float(price), session
            if price is not None:
                log.warning("fetch_last_price(%s): implausible price %s from %s",
                            symbol, price, provider_label)
                record_fetch_failure(symbol, f"implausible price ({price}) from {provider_label}")
                return None, None
        # success=False or price=None: fall through to yfinance
    except ModuleNotFoundError:
        _warn_missing_option_lib()   # not installed — use yfinance directly
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
            record_fetch_failure(symbol, f"no price data returned by {provider_label}")
            return None, None
        if not _valid_price(price):
            log.warning("fetch_last_price(%s): implausible price %s — treating as unavailable",
                        symbol, price)
            record_fetch_failure(symbol, f"implausible price ({price}) from {provider_label}")
            return None, None
        return float(price), None
    except Exception as exc:
        log.warning("fetch_last_price(%s) failed: %s", symbol, exc)
        record_fetch_failure(symbol, f"price fetch failed from {provider_label} ({type(exc).__name__})")
        return None, None


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
        record_fetch_failure(
            symbol, f"option data unavailable from {_provider_name()} ({type(exc).__name__})", overwrite=False)
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
        record_fetch_failure(
            symbol, f"option data unavailable from {_provider_name()} ({type(exc).__name__})", overwrite=False)
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
            record_fetch_failure(
                symbol, f"option data unavailable from {_provider_name()}", overwrite=False)
        return result
    except ModuleNotFoundError:
        log.debug("option_lib not available — greeks skipped for %s", symbol)
        return _none
    except Exception as exc:
        log.warning("fetch_option_greeks(%s %s %s %s) failed: %s",
                    symbol, expiration_iso, strike, option_type, exc)
        record_fetch_failure(
            symbol, f"option data unavailable from {_provider_name()} ({type(exc).__name__})", overwrite=False)
        return _none


def fetch_sector(symbol: str) -> str | None:
    """Return the symbol's sector (e.g. 'Technology'), or None if it has none.

    option_lib caches this on disk for a month, so this is a network call at
    most once per symbol per month across every process on the machine — no
    local caching beyond CacheService's per-session dict is warranted.
    """
    try:
        from option_lib.data_provider import get_provider
        return get_provider().get_sector(symbol)
    except ModuleNotFoundError:
        _warn_missing_option_lib()
        return None
    except Exception as exc:
        log.warning("fetch_sector(%s) failed: %s", symbol, exc)
        return None


def fetch_company_name(symbol: str) -> str | None:
    """Return the company's display name (e.g. 'Apple Inc.'), or None.

    Cached on disk by option_lib on the same terms as the sector, so this costs
    a network call at most once per symbol per month across every process on
    the machine.
    """
    try:
        from option_lib.data_provider import get_provider
        return get_provider().get_company_name(symbol)
    except ModuleNotFoundError:
        _warn_missing_option_lib()
        return None
    except Exception as exc:
        log.warning("fetch_company_name(%s) failed: %s", symbol, exc)
        return None


def bars_cache_ttl(interval: str) -> int:
    """Seconds until a set of *interval* bars should be re-fetched: just past the
    next bar boundary (option_lib.math_util.bars_cache_ttl), or an hour if
    option_lib is missing."""
    try:
        from option_lib.math_util import bars_cache_ttl as _ttl
        return _ttl(interval)
    except Exception:
        return 3600


def fetch_price_bars(symbol: str, days: int = 7, interval: str = "1h") -> list[dict]:
    """Recent OHLC bars for the sparkline: [{'t','o','h','l','c','v'}, ...], oldest first.

    [] when the provider has nothing (or option_lib is missing); never raises.
    option_lib caches the answer for ~15 minutes, so this is cheap to repeat.
    """
    try:
        from option_lib.data_provider import get_provider
        return get_provider().get_price_bars(symbol, days=days, interval=interval) or []
    except ModuleNotFoundError:
        _warn_missing_option_lib()
        return []
    except Exception as exc:
        log.warning("fetch_price_bars(%s) failed: %s", symbol, exc)
        return []


def fetch_earnings_date(symbol: str) -> str | None:
    """Return the next upcoming earnings date as 'YYYY-MM-DD', or None."""
    try:
        from option_lib.data_provider import get_provider
        return get_provider().get_earnings_date(symbol)
    except ModuleNotFoundError:
        return None
    except Exception as exc:
        log.warning("fetch_earnings_date(%s) failed: %s", symbol, exc)
        return None


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
        record_fetch_failure(
            symbol, f"option data unavailable from {_provider_name()} ({type(exc).__name__})", overwrite=False)
        return None
