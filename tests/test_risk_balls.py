"""
tests/test_risk_balls.py

Tests for the risk-ball (delta / probability-of-assignment) pipeline.
Uses entirely faked data — no network access or option_lib required.

Coverage
--------
1. CacheService  — stores, retrieves, and invalidates delta values.
2. _compute_display() — delta key present and value flows through correctly.
3. _compute_display() — positions with no strike yield delta=None.
4. Risk-colour mapping — Python mirror of constants.js RISK_BANDS thresholds.
5. Visual spectrum — printed summary for manual inspection.
"""

import os, sys

# Must be set before main_web is imported (it calls _require_password() at
# module level).  Using setdefault so a real password isn't overwritten if
# the caller already has MARGIN_PWD set.
os.environ.setdefault("MARGIN_PWD", "test")

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from datetime import date, timedelta
from models import Position
from services.cache_service import CacheService

# ---------------------------------------------------------------------------
# Python mirror of constants.js RISK_BANDS  (kept in sync manually)
# ---------------------------------------------------------------------------

RISK_BANDS = [
    (0.85, "#dc2626", "🔴 Red    — Deep ITM   (≥ 85%)"),
    (0.65, "#ea580c", "🟠 Orange — Mod ITM    (≥ 65%)"),
    (0.45, "#ca8a04", "🟡 Yellow — ATM        (≥ 45%)"),
    (0.25, "#7c3aed", "🟣 Purple — Slight OTM (≥ 25%)"),
    (0.10, "#2563eb", "🔵 Blue   — OTM        (≥ 10%)"),
    (0.00, "#16a34a", "🟢 Green  — Deep OTM   ( < 10%)"),
]


def risk_color(delta):
    """Python mirror of riskColor() in constants.js."""
    if delta is None:
        return None
    for threshold, color, _ in RISK_BANDS:
        if delta >= threshold:
            return color
    return RISK_BANDS[-1][1]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_EXPIRY = (date.today() + timedelta(days=30)).isoformat()


def _put(symbol="SPY", strike=500.0, expiry=None):
    return Position(
        id=1, symbol=symbol, option_type="PUT",
        strike=strike, expiration=expiry or _EXPIRY,
        quantity=2, open_date=date.today().isoformat(),
        long_shares=None, long_cost=None, long_strike=None,
    )


def _stock_no_cover():
    """Plain long stock — no covered call written."""
    return Position(
        id=2, symbol="AAPL", option_type="STOCK",
        strike=0.0, expiration="9999-12-31",
        quantity=0, open_date=date.today().isoformat(),
        long_shares=100, long_cost=180.0, long_strike=None,
    )


class _FakeCache:
    """CacheService stand-in with controllable per-strike delta values."""

    def __init__(self, delta_by_strike: dict):
        self._delta_by_strike = delta_by_strike

    # key = (symbol, expiration, strike, otype)
    def price(self, symbol):      return 500.0
    def opt_price(self, key):     return 3.50
    def theta(self, key):         return -0.08
    def delta(self, key):         return self._delta_by_strike.get(key[2])


# ---------------------------------------------------------------------------
# 1. CacheService unit tests
# ---------------------------------------------------------------------------

def test_cache_stores_delta():
    cache = CacheService()
    key = ("SPY", _EXPIRY, 500.0, "PUT")
    cache._delta[key] = 0.42
    assert cache.delta(key) == 0.42
    print("PASS  CacheService stores and returns delta")


def test_cache_missing_returns_none():
    cache = CacheService()
    assert cache.delta(("SPY", _EXPIRY, 999.0, "PUT")) is None
    print("PASS  CacheService returns None for unknown key")


def test_cache_invalidate_clears_delta():
    cache = CacheService()
    key = ("SPY", _EXPIRY, 500.0, "PUT")
    cache._delta[key] = 0.30
    cache.invalidate("SPY")
    assert cache.delta(key) is None
    print("PASS  CacheService.invalidate() clears delta for symbol")


# ---------------------------------------------------------------------------
# 2. _compute_display integration (uses fake cache, real logic)
# ---------------------------------------------------------------------------

def _get_compute_display():
    """Import main_web lazily so env var is set first."""
    import main_web
    return main_web._compute_display


def test_display_includes_delta():
    compute = _get_compute_display()
    pos   = _put(strike=500.0)
    cache = _FakeCache({500.0: 0.35})
    out   = compute(pos, cache)
    assert "delta" in out, "_compute_display() output missing 'delta' key"
    assert out["delta"] == 0.35, f"expected 0.35, got {out['delta']}"
    print(f"PASS  _compute_display includes delta={out['delta']}")


def test_display_delta_rounded():
    """Delta is rounded to 3 decimal places in the API output."""
    compute = _get_compute_display()
    pos   = _put(strike=500.0)
    cache = _FakeCache({500.0: 0.123456789})
    out   = compute(pos, cache)
    assert out["delta"] == 0.123, f"expected 0.123 (rounded), got {out['delta']}"
    print(f"PASS  _compute_display rounds delta to 3dp  ({out['delta']})")


def test_display_no_strike_gives_none():
    """Plain STOCK with no covered call must have delta=None."""
    compute = _get_compute_display()
    pos   = _stock_no_cover()
    cache = _FakeCache({})
    out   = compute(pos, cache)
    assert out["delta"] is None, f"expected None for no-cover stock, got {out['delta']}"
    print("PASS  plain STOCK (no cover) has delta=None")


def test_display_cache_miss_gives_none():
    """If the cache has no entry for this key, delta must be None."""
    compute = _get_compute_display()
    pos   = _put(strike=500.0)
    cache = _FakeCache({})            # no deltas at all
    out   = compute(pos, cache)
    assert out["delta"] is None, f"expected None on cache miss, got {out['delta']}"
    print("PASS  cache miss yields delta=None in display")


# ---------------------------------------------------------------------------
# 3. Risk-colour band mapping
# ---------------------------------------------------------------------------

def test_risk_color_bands():
    cases = [
        # (delta, expected_hex, description)
        (0.95, "#dc2626", "Deep ITM   → Red"),
        (0.85, "#dc2626", "Deep ITM boundary → Red"),
        (0.84, "#ea580c", "just below 85% → Orange"),
        (0.70, "#ea580c", "Mod ITM    → Orange"),
        (0.65, "#ea580c", "Mod ITM boundary → Orange"),
        (0.64, "#ca8a04", "just below 65% → Yellow"),
        (0.50, "#ca8a04", "ATM        → Yellow"),
        (0.45, "#ca8a04", "ATM boundary → Yellow"),
        (0.44, "#7c3aed", "just below 45% → Purple"),
        (0.30, "#7c3aed", "Slight OTM → Purple"),
        (0.25, "#7c3aed", "Slight OTM boundary → Purple"),
        (0.24, "#2563eb", "just below 25% → Blue"),
        (0.15, "#2563eb", "OTM        → Blue"),
        (0.10, "#2563eb", "OTM boundary → Blue"),
        (0.09, "#16a34a", "just below 10% → Green"),
        (0.05, "#16a34a", "Deep OTM   → Green"),
        (0.00, "#16a34a", "Zero delta → Green"),
    ]
    failures = []
    for delta, expected, label in cases:
        got = risk_color(delta)
        ok  = got == expected
        print(f"  {'PASS' if ok else 'FAIL'}  δ={delta:.2f}  {label}  → {got}")
        if not ok:
            failures.append(f"δ={delta}: expected {expected}, got {got}")
    assert not failures, "\n".join(failures)
    print("PASS  all risk-colour band boundaries correct")


def test_risk_color_none():
    assert risk_color(None) is None
    print("PASS  risk_color(None) returns None")


# ---------------------------------------------------------------------------
# 4. Visual spectrum (for manual inspection)
# ---------------------------------------------------------------------------

def print_spectrum():
    print("\n── Risk spectrum preview ──────────────────────────────────────────")
    samples = [
        (0.95, "Deep ITM   — almost certain assignment"),
        (0.70, "Mod ITM    — high risk"),
        (0.50, "ATM        — coin-flip"),
        (0.30, "Slight OTM — getting close"),
        (0.15, "OTM        — comfortable theta burn"),
        (0.05, "Deep OTM   — minimal risk"),
        (None, "No delta   — plain stock / fetch failed"),
    ]
    for delta, label in samples:
        color = risk_color(delta)
        d_str = f"{delta:.0%}" if delta is not None else "—"
        print(f"  δ={d_str:>4}  {color or 'none':>9}  {label}")
    print()


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    tests = [
        test_cache_stores_delta,
        test_cache_missing_returns_none,
        test_cache_invalidate_clears_delta,
        test_display_includes_delta,
        test_display_delta_rounded,
        test_display_no_strike_gives_none,
        test_display_cache_miss_gives_none,
        test_risk_color_bands,
        test_risk_color_none,
    ]

    print("=== Risk-ball pipeline tests ===\n")
    passed = failed = 0
    for t in tests:
        try:
            t()
            passed += 1
        except AssertionError as e:
            print(f"FAIL  {t.__name__}: {e}")
            failed += 1
        except Exception as e:
            import traceback
            print(f"ERROR {t.__name__}: {e}")
            traceback.print_exc()
            failed += 1

    print_spectrum()
    print(f"{'='*40}")
    print(f"{passed} passed  {failed} failed")
    sys.exit(0 if failed == 0 else 1)
