"""tests/test_market_sessions.py

Which price basis the app uses at each point on the clock (all times Eastern):

    09:30 – 16:00   regular session   → regular market price, session None
    16:00 – 04:00   after hours       → post-market price,    session 'post'
    04:00 – 09:30   pre-market        → pre-market price,     session 'pre'

Note the post window runs past midnight: 23:00 and 02:00 are still 'post',
and the flip to 'pre' happens at 04:00.

No network access — the option_lib provider is replaced by a stub whose
get_stock_info() returns whatever combination of regular/pre/post prices a
given test needs, and the clock is frozen by patching the datetime that
market_data_service reads.

Coverage
--------
 1. Regular-session times are not extended hours (09:30, 12:00, 15:59).
 2. After-hours times are extended hours (16:00 → 03:59, across midnight).
 3. Pre-market times are extended hours (04:00 → 09:29).
 4. Weekends are extended hours.
 5. During regular hours the regular price is used, session None, even when
    stale pre/post fields are present.
 6. After 16:00 the post-market price is used, session 'post'.
 7. Overnight (past midnight, before 04:00) still uses the post price.
 8. From 04:00 to 09:30 the pre-market price is used, session 'pre'.
 9. When the clock says post but only a pre price exists (and vice versa),
    the app falls back to the regular price rather than using the wrong
    session's price.
10. CacheService derives use_extended from the clock, not from a caller.
11. Crossing 16:00 clears the cached prices so the new basis is re-fetched.
12. Crossing 04:00 does too, even though both sides are extended hours.
13. option_lib picks the same session, and the same underlying S for greeks.
    Skipped when option_lib is not importable, since MarginWatch runs without it.
"""

import os
import sys
import types
import unittest
from datetime import datetime
from zoneinfo import ZoneInfo

os.environ.setdefault("MARGIN_PWD", "test")

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import services.market_data_service as mds          # noqa: E402
from services.cache_service import CacheService     # noqa: E402

# option_lib is optional at runtime (fetch_last_price falls back to plain
# yfinance without it), so the cross-repo checks below are skipped rather than
# failed when it is missing.  Bound at import time, before any test swaps
# sys.modules['option_lib'] for the stub.
try:
    from option_lib.math_util import (extended_underlying as ol_extended_underlying,
                                      market_session as ol_market_session)
except Exception as exc:                              # ImportError, or a broken install
    ol_extended_underlying = ol_market_session = None
    _OPTION_LIB_ERROR = exc

ET = ZoneInfo("America/New_York")

# A plain Wednesday and the weekend that follows it.
WED = (2025, 11, 12)
SAT = (2025, 11, 15)
SUN = (2025, 11, 16)

REGULAR_PRICE = 100.0
PRE_PRICE     = 101.0
POST_PRICE    = 99.0


def et(h, m, day=WED):
    """A datetime at h:m Eastern on *day*."""
    return datetime(day[0], day[1], day[2], h, m, tzinfo=ET)


def require_option_lib():
    """Skip the calling test when option_lib is not importable.

    unittest.SkipTest is what pytest recognises as a skip, and the runner at the
    bottom of this file counts it separately from a failure.
    """
    if ol_market_session is None:
        raise unittest.SkipTest(f"option_lib not importable ({_OPTION_LIB_ERROR})")


# ---------------------------------------------------------------------------
# Stubs: frozen clock + fake option_lib provider
# ---------------------------------------------------------------------------

class _FrozenDatetime(datetime):
    """datetime subclass whose now() returns a fixed instant."""
    _frozen = None

    @classmethod
    def now(cls, tz=None):
        return cls._frozen.astimezone(tz) if tz else cls._frozen


class freeze_clock:
    """Context manager pinning market_data_service's wall clock to *when*."""

    def __init__(self, when):
        self.when = when

    def __enter__(self):
        self._saved = mds.datetime
        _FrozenDatetime._frozen = self.when
        mds.datetime = _FrozenDatetime
        return self

    def __exit__(self, *exc):
        mds.datetime = self._saved
        return False


class _StubProvider:
    """Stands in for option_lib's provider; returns canned stock info."""

    def __init__(self, current=REGULAR_PRICE, pre=None, post=None, success=True):
        self.info = {
            'success':            success,
            'current_price':      current,
            'pre_market_price':   pre,
            'post_market_price':  post,
        }
        self.calls = []

    def get_stock_info(self, symbol):
        self.calls.append(symbol)
        return dict(self.info)


class stub_provider:
    """Context manager installing a fake option_lib.data_provider module.

    market_data_service imports get_provider lazily inside each function, so
    swapping the entry in sys.modules is enough — and it keeps the real
    option_lib (and any network it would reach for) out of these tests.
    """

    def __init__(self, provider):
        self.provider = provider

    def __enter__(self):
        self._saved = {name: sys.modules.get(name)
                       for name in ('option_lib', 'option_lib.data_provider')}
        dp = types.ModuleType('option_lib.data_provider')
        dp.get_provider = lambda: self.provider
        pkg = types.ModuleType('option_lib')
        pkg.__path__ = []                 # mark as a package
        pkg.data_provider = dp
        sys.modules['option_lib'] = pkg
        sys.modules['option_lib.data_provider'] = dp
        return self.provider

    def __exit__(self, *exc):
        for name, mod in self._saved.items():
            if mod is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = mod
        return False


def price_at(when, *, pre=None, post=None, current=REGULAR_PRICE):
    """Fetch AAPL's price as the app would at *when*: (price, session).

    Mirrors production wiring — CacheService derives use_extended from the
    clock and passes it to fetch_last_price.
    """
    with freeze_clock(when), stub_provider(_StubProvider(current, pre, post)):
        mds.pop_fetch_failures()          # start clean; failures are module state
        return mds.fetch_last_price('AAPL', use_extended=mds.in_extended_hours(when))


# ---------------------------------------------------------------------------
# 1–4: session windows
# ---------------------------------------------------------------------------

def test_regular_hours_not_extended():
    for h, m in [(9, 30), (9, 31), (12, 0), (15, 59)]:
        assert not mds.in_extended_hours(et(h, m)), f"{h:02d}:{m:02d} ET should be regular hours"


def test_after_hours_are_extended():
    for h, m in [(16, 0), (16, 1), (18, 30), (20, 0), (23, 59), (0, 30), (2, 0), (3, 59)]:
        assert mds.in_extended_hours(et(h, m)), f"{h:02d}:{m:02d} ET should be extended hours"


def test_pre_market_is_extended():
    for h, m in [(4, 0), (6, 15), (9, 29)]:
        assert mds.in_extended_hours(et(h, m)), f"{h:02d}:{m:02d} ET should be extended hours"


def test_weekend_is_extended():
    for day in (SAT, SUN):
        assert mds.in_extended_hours(et(12, 0, day)), "weekends are never a regular session"


# ---------------------------------------------------------------------------
# 5: regular session
# ---------------------------------------------------------------------------

def test_regular_hours_use_regular_price():
    for h, m in [(9, 30), (12, 0), (15, 59)]:
        # Stale pre/post fields are present and must be ignored during the session.
        price, session = price_at(et(h, m), pre=PRE_PRICE, post=POST_PRICE)
        assert price == REGULAR_PRICE, f"{h:02d}:{m:02d} ET used {price}, expected the regular price"
        assert session is None, f"{h:02d}:{m:02d} ET reported session {session!r}, expected None"


# ---------------------------------------------------------------------------
# 6–7: post-market, 16:00 → 04:00
# ---------------------------------------------------------------------------

def test_after_hours_use_post_price():
    for h, m in [(16, 0), (16, 1), (18, 30), (23, 59)]:
        price, session = price_at(et(h, m), post=POST_PRICE)
        assert price == POST_PRICE, f"{h:02d}:{m:02d} ET used {price}, expected the post price"
        assert session == 'post', f"{h:02d}:{m:02d} ET reported session {session!r}, expected 'post'"


def test_after_hours_prefer_post_over_stale_pre():
    """Both fields populated after 16:00 — the morning's pre price is stale."""
    for h, m in [(16, 30), (20, 0), (23, 0)]:
        price, session = price_at(et(h, m), pre=PRE_PRICE, post=POST_PRICE)
        assert price == POST_PRICE, f"{h:02d}:{m:02d} ET used {price}, expected the post price"
        assert session == 'post', f"{h:02d}:{m:02d} ET reported session {session!r}, expected 'post'"


def test_overnight_still_uses_post_price():
    """Post pricing runs until 04:00, so past midnight is still 'post'."""
    for h, m in [(0, 1), (2, 0), (3, 59)]:
        price, session = price_at(et(h, m), pre=PRE_PRICE, post=POST_PRICE)
        assert price == POST_PRICE, f"{h:02d}:{m:02d} ET used {price}, expected the post price"
        assert session == 'post', f"{h:02d}:{m:02d} ET reported session {session!r}, expected 'post'"


# ---------------------------------------------------------------------------
# 8: pre-market, 04:00 → 09:30
# ---------------------------------------------------------------------------

def test_pre_market_uses_pre_price():
    for h, m in [(4, 0), (6, 15), (9, 29)]:
        price, session = price_at(et(h, m), pre=PRE_PRICE, post=POST_PRICE)
        assert price == PRE_PRICE, f"{h:02d}:{m:02d} ET used {price}, expected the pre price"
        assert session == 'pre', f"{h:02d}:{m:02d} ET reported session {session!r}, expected 'pre'"


# ---------------------------------------------------------------------------
# 9: wrong-session data is not used
# ---------------------------------------------------------------------------

def test_only_wrong_session_price_available_falls_back_to_regular():
    # 17:00 with only a pre price: yesterday's morning quote, not tonight's.
    price, session = price_at(et(17, 0), pre=PRE_PRICE)
    assert price == REGULAR_PRICE, f"after hours used the pre price {price}"
    assert session is None, f"after hours reported session {session!r}, expected None"

    # 06:00 with only a post price: last night's quote, not this morning's.
    price, session = price_at(et(6, 0), post=POST_PRICE)
    assert price == REGULAR_PRICE, f"pre-market used the post price {price}"
    assert session is None, f"pre-market reported session {session!r}, expected None"


def test_no_extended_price_falls_back_to_regular():
    price, session = price_at(et(17, 0))
    assert price == REGULAR_PRICE
    assert session is None


# ---------------------------------------------------------------------------
# 10–11: CacheService wiring
# ---------------------------------------------------------------------------

def test_cache_derives_extended_from_clock():
    """use_extended comes from the clock, and the session label reaches the cache."""
    cases = [
        (et(12, 0),  False, REGULAR_PRICE, None),
        (et(17, 0),  True,  POST_PRICE,    'post'),
        (et(2, 0),   True,  POST_PRICE,    'post'),
        (et(6, 0),   True,  PRE_PRICE,     'pre'),
    ]
    for when, want_extended, want_price, want_session in cases:
        with freeze_clock(when), stub_provider(_StubProvider(REGULAR_PRICE, PRE_PRICE, POST_PRICE)):
            cache = CacheService()
            seen = {}
            real_fetch = mds.fetch_last_price

            def spy(symbol, use_extended=False):
                seen['use_extended'] = use_extended
                return real_fetch(symbol, use_extended=use_extended)

            mds.fetch_last_price = spy
            try:
                price = cache.fetch_price('AAPL')
            finally:
                mds.fetch_last_price = real_fetch

        label = when.strftime('%H:%M')
        assert seen['use_extended'] is want_extended, \
            f"{label} ET passed use_extended={seen['use_extended']}, expected {want_extended}"
        assert price == want_price, f"{label} ET cached {price}, expected {want_price}"
        assert cache.price_session('AAPL') == want_session, \
            f"{label} ET cached session {cache.price_session('AAPL')!r}, expected {want_session!r}"


def test_session_rollover_clears_cached_price():
    """15:59 → 16:01 must drop the regular price so the post price is fetched."""
    with stub_provider(_StubProvider(REGULAR_PRICE, PRE_PRICE, POST_PRICE)):
        with freeze_clock(et(15, 59)):
            cache = CacheService()
            assert cache.fetch_price('AAPL') == REGULAR_PRICE
            assert cache.price_session('AAPL') is None
        with freeze_clock(et(16, 1)):
            assert cache.fetch_price('AAPL') == POST_PRICE, \
                "price cached during the session survived the 16:00 rollover"
            assert cache.price_session('AAPL') == 'post'


def test_post_to_pre_turnover_clears_cached_price():
    """03:59 → 04:01 stays extended hours but changes basis from post to pre.

    A cache that tracks only extended-vs-regular would hold the overnight post
    price (and the greeks derived from it) right through the morning.
    """
    with stub_provider(_StubProvider(REGULAR_PRICE, PRE_PRICE, POST_PRICE)):
        with freeze_clock(et(3, 59)):
            cache = CacheService()
            assert cache.fetch_price('AAPL') == POST_PRICE
            assert cache.price_session('AAPL') == 'post'
        with freeze_clock(et(4, 1)):
            assert cache.fetch_price('AAPL') == PRE_PRICE, \
                "overnight post price survived the 04:00 turnover"
            assert cache.price_session('AAPL') == 'pre'


# ---------------------------------------------------------------------------
# 13: option_lib agreement
# ---------------------------------------------------------------------------

# Times the two repos must classify identically, including the overnight cases
# where the session belongs to the previous day.
_SESSION_TIMES = [
    (et(9, 30),      'regular'),
    (et(12, 0),      'regular'),
    (et(15, 59),     'regular'),
    (et(16, 0),      'post'),
    (et(20, 0),      'post'),
    (et(23, 59),     'post'),
    (et(0, 1),       'post'),
    (et(3, 59),      'post'),
    (et(4, 0),       'pre'),
    (et(9, 29),      'pre'),
    (et(2, 0, SAT),  'post'),      # continues Friday evening
    (et(12, 0, SAT), 'closed'),
    (et(2, 0, SUN),  'closed'),    # Saturday had no session to continue
]


def test_option_lib_agrees_on_session():
    """The window rules are implemented in both repos — they must not drift.

    MarginWatch keeps its own copy because market_data_service works without
    option_lib installed, so nothing but this test ties the two together.
    """
    require_option_lib()
    for when, expected in _SESSION_TIMES:
        label = when.strftime('%a %H:%M')
        assert mds.market_session(when) == expected, \
            f"{label} ET: MarginWatch says {mds.market_session(when)!r}, expected {expected!r}"
        assert ol_market_session(when) == expected, \
            f"{label} ET: option_lib says {ol_market_session(when)!r}, expected {expected!r}"


def test_option_lib_greeks_use_current_session_underlying():
    """S for theta/delta/$/shr follows the same clock as the displayed price."""
    require_option_lib()
    info = {'current_price':     REGULAR_PRICE,
            'pre_market_price':  PRE_PRICE,
            'post_market_price': POST_PRICE}
    for when, expected in [(et(12, 0), REGULAR_PRICE),
                           (et(17, 0), POST_PRICE),
                           (et(2, 0),  POST_PRICE),
                           (et(6, 0),  PRE_PRICE),
                           # Weekend: no trade has happened since Friday's last
                           # after-hours print, so that print is still S.
                           (et(12, 0, SAT), POST_PRICE)]:
        got = ol_extended_underlying(info, when)
        assert got == expected, f"{when.strftime('%a %H:%M')} ET priced greeks off {got}, expected {expected}"


# ---------------------------------------------------------------------------
# 14: the whole clock, against the stated rule
# ---------------------------------------------------------------------------

# The rule, in full: pre-market hours show the pre-market price, market hours
# show the market price, and *every other hour* shows the post-market price.
# The last clause is what makes the weekend a post-market window rather than a
# regular one — there is no trade after Friday's last after-hours print, so that
# print stays the freshest quote right through to Monday's pre-market.
#
# Both repos implement this independently (MarginWatch works without option_lib
# installed), so every row is asserted against both.
_PRICE_BASIS_CLOCK = [
    # Weekday pre-market
    (et(4, 0),        PRE_PRICE,     'pre'),
    (et(6, 30),       PRE_PRICE,     'pre'),
    (et(9, 29),       PRE_PRICE,     'pre'),
    # Weekday regular session
    (et(9, 30),       REGULAR_PRICE, None),
    (et(12, 0),       REGULAR_PRICE, None),
    (et(15, 59),      REGULAR_PRICE, None),
    # Weekday after-hours, running past midnight into the small hours
    (et(16, 0),       POST_PRICE,    'post'),
    (et(20, 0),       POST_PRICE,    'post'),
    (et(23, 59),      POST_PRICE,    'post'),
    (et(0, 1),        POST_PRICE,    'post'),
    (et(3, 59),       POST_PRICE,    'post'),
    # The weekend, start to finish
    (et(2, 0,  SAT),  POST_PRICE,    'post'),   # continues Friday evening
    (et(12, 0, SAT),  POST_PRICE,    'post'),
    (et(23, 0, SAT),  POST_PRICE,    'post'),
    (et(2, 0,  SUN),  POST_PRICE,    'post'),
    (et(12, 0, SUN),  POST_PRICE,    'post'),
    (et(23, 0, SUN),  POST_PRICE,    'post'),
]


def test_price_basis_across_the_clock():
    """Pre in pre-market, regular in market hours, post at every other time."""
    for when, expected_price, expected_session in _PRICE_BASIS_CLOCK:
        label = when.strftime('%a %H:%M')
        price, session = price_at(when, pre=PRE_PRICE, post=POST_PRICE)
        assert price == expected_price, \
            f"{label} ET showed {price}, expected {expected_price}"
        assert session == expected_session, \
            f"{label} ET reported session {session!r}, expected {expected_session!r}"


def test_option_lib_agrees_on_price_basis():
    """option_lib picks the same S as MarginWatch shows, at every hour."""
    require_option_lib()
    info = {'current_price':     REGULAR_PRICE,
            'pre_market_price':  PRE_PRICE,
            'post_market_price': POST_PRICE}
    for when, expected_price, _ in _PRICE_BASIS_CLOCK:
        got = ol_extended_underlying(info, when)
        assert got == expected_price, \
            f"{when.strftime('%a %H:%M')} ET: option_lib priced off {got}, " \
            f"expected {expected_price}"


def test_missing_price_for_the_hour_falls_back_to_regular():
    """The rule picks a field; an empty field still falls back to the regular price.

    A provider reports nothing for a session in which nothing traded — a stock
    with no pre-market interest, or a weekend after a quiet Friday close.  That
    must not leave the row blank.
    """
    for when in (et(6, 30), et(20, 0), et(12, 0, SAT), et(12, 0, SUN)):
        price, session = price_at(when)          # neither pre nor post supplied
        assert price == REGULAR_PRICE, \
            f"{when.strftime('%a %H:%M')} ET returned {price} with no extended print"
        assert session is None, \
            f"{when.strftime('%a %H:%M')} ET claimed session {session!r} with no print"


def test_option_lib_greeks_ignore_other_session_price():
    """A leftover quote from the other session must never become S."""
    require_option_lib()
    pre_only  = {'current_price': REGULAR_PRICE, 'pre_market_price':  PRE_PRICE}
    post_only = {'current_price': REGULAR_PRICE, 'post_market_price': POST_PRICE}
    assert ol_extended_underlying(pre_only, et(17, 0)) == REGULAR_PRICE, \
        "evening greeks used the morning's pre-market price"
    assert ol_extended_underlying(post_only, et(6, 0)) == REGULAR_PRICE, \
        "pre-market greeks used last night's post-market price"


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------

if __name__ == '__main__':
    tests = [
        test_regular_hours_not_extended,
        test_after_hours_are_extended,
        test_pre_market_is_extended,
        test_weekend_is_extended,
        test_regular_hours_use_regular_price,
        test_after_hours_use_post_price,
        test_after_hours_prefer_post_over_stale_pre,
        test_overnight_still_uses_post_price,
        test_pre_market_uses_pre_price,
        test_only_wrong_session_price_available_falls_back_to_regular,
        test_no_extended_price_falls_back_to_regular,
        test_cache_derives_extended_from_clock,
        test_session_rollover_clears_cached_price,
        test_post_to_pre_turnover_clears_cached_price,
        test_option_lib_agrees_on_session,
        test_option_lib_greeks_use_current_session_underlying,
        test_price_basis_across_the_clock,
        test_option_lib_agrees_on_price_basis,
        test_missing_price_for_the_hour_falls_back_to_regular,
        test_option_lib_greeks_ignore_other_session_price,
    ]

    print("=== market session pricing tests ===\n")
    passed = failed = skipped = 0
    for t in tests:
        try:
            t()
            passed += 1
        except unittest.SkipTest as e:
            print(f"SKIP  {t.__name__}: {e}")
            skipped += 1
        except AssertionError as e:
            print(f"FAIL  {t.__name__}: {e}")
            failed += 1
        except Exception as e:
            import traceback
            print(f"ERROR {t.__name__}: {e}")
            traceback.print_exc()
            failed += 1

    print(f"\n{'=' * 40}")
    print(f"{passed} passed  {failed} failed" + (f"  {skipped} skipped" if skipped else ""))
    sys.exit(0 if failed == 0 else 1)
