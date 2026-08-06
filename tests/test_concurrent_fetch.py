"""tests/test_concurrent_fetch.py

Two overlapping price pulls must not duplicate work or lose errors.

The web server is threaded, and aborting a request on the client does not stop
its handler: deleting a position (or editing one) mid-pull leaves the old
/api/prices running while the reload starts a second one.  Before the locks
below, both passes saw the same symbols as uncached and fetched every one of
them twice, and pop_fetch_failures() — a shared module dict — let the abandoned
pass swallow the errors belonging to the live one.

No network access: market_data_service's fetchers are replaced with stubs that
count calls and sleep long enough for the two threads to genuinely overlap.

Coverage
--------
1. Two concurrent fetch_all() passes fetch each symbol exactly once.
2. Two concurrent fetch_price() calls for one symbol fetch it once.
3. A single-symbol fetch is not blocked behind a full pull.
4. Fetch failures reach the errors list even when a second pass runs alongside.
5. Failures recorded in one thread are not popped by another.
"""

import os
import sys
import threading
import time

os.environ.setdefault("MARGIN_PWD", "test")

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import services.market_data_service as mds          # noqa: E402
from services.cache_service import CacheService     # noqa: E402


class _FakePos:
    def __init__(self, symbol, pid=1):
        self.id = pid
        self.symbol = symbol
        self.option_type = 'STOCK'
        self.strike = None
        self.strike2 = None
        self.expiration = '2099-01-01'
        self.quantity = 1


class _Stub:
    """Replaces the module's network calls; counts them and can be made slow."""

    def __init__(self, delay=0.05, fail=()):
        self.delay = delay
        self.fail = set(fail)
        self.price_calls: dict[str, int] = {}
        self.lock = threading.Lock()

    def install(self):
        self._saved = (mds.fetch_last_price, mds.fetch_option_greeks,
                       mds.fetch_earnings_date)
        mds.fetch_last_price    = self.fetch_last_price
        mds.fetch_option_greeks = lambda *a, **k: {'price': None, 'theta': None, 'delta': None}
        mds.fetch_earnings_date = lambda *a, **k: None
        return self

    def restore(self):
        (mds.fetch_last_price, mds.fetch_option_greeks,
         mds.fetch_earnings_date) = self._saved

    def __enter__(self):
        return self.install()

    def __exit__(self, *exc):
        self.restore()
        return False

    def fetch_last_price(self, symbol, use_extended=False):
        with self.lock:
            self.price_calls[symbol] = self.price_calls.get(symbol, 0) + 1
        time.sleep(self.delay)                    # hold the lock window open
        if symbol in self.fail:
            mds.record_fetch_failure(symbol, "stub failure")
            return None, None
        return 100.0, None


def _run_both(target_a, target_b):
    """Run two callables in threads, started as close together as possible."""
    start = threading.Barrier(2)
    errors = []

    def wrap(fn):
        def run():
            try:
                start.wait()
                fn()
            except Exception as e:               # surface thread errors in the test
                errors.append(e)
        return run

    threads = [threading.Thread(target=wrap(t)) for t in (target_a, target_b)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(10)
    assert not errors, f"thread raised: {errors[0]!r}"
    assert not any(t.is_alive() for t in threads), "deadlock: threads did not finish"


# ---------------------------------------------------------------------------
# 1–2: no duplicate fetches
# ---------------------------------------------------------------------------

def test_overlapping_fetch_all_fetches_each_symbol_once():
    positions = [_FakePos('AAPL', 1), _FakePos('TSLA', 2), _FakePos('NVDA', 3)]
    with _Stub() as stub:
        cache = CacheService()
        _run_both(lambda: cache.fetch_all(positions),
                  lambda: cache.fetch_all(positions))
    assert stub.price_calls == {'AAPL': 1, 'TSLA': 1, 'NVDA': 1}, \
        f"symbols fetched more than once: {stub.price_calls}"


def test_overlapping_fetch_price_fetches_once():
    with _Stub() as stub:
        cache = CacheService()
        _run_both(lambda: cache.fetch_price('AAPL'),
                  lambda: cache.fetch_price('AAPL'))
    assert stub.price_calls == {'AAPL': 1}, f"AAPL fetched {stub.price_calls} times"


# ---------------------------------------------------------------------------
# 3: a quote does not queue behind a whole pull
# ---------------------------------------------------------------------------

def test_single_symbol_fetch_not_blocked_by_full_pull():
    """The add-form quote must answer while a price pull is still running."""
    positions = [_FakePos(s, i) for i, s in enumerate(['AAA', 'BBB', 'CCC', 'DDD'])]
    with _Stub(delay=0.1):
        cache = CacheService()
        done = threading.Event()

        pull = threading.Thread(target=lambda: (cache.fetch_all(positions), done.set()))
        pull.start()
        time.sleep(0.05)                          # let the pull get going

        started = time.monotonic()
        cache.fetch_price('ZZZ')                  # different symbol
        elapsed = time.monotonic() - started

        pull.join(10)

    assert elapsed < 0.35, \
        f"quote waited {elapsed:.2f}s — it is queued behind the full pull"


# ---------------------------------------------------------------------------
# 4–5: failures are not stolen across threads
# ---------------------------------------------------------------------------

def test_failures_survive_a_concurrent_pass():
    positions = [_FakePos('GOOD', 1), _FakePos('BAD', 2)]
    with _Stub(fail=['BAD']):
        cache = CacheService()
        _run_both(lambda: cache.fetch_all(positions),
                  lambda: cache.fetch_all(positions))
        errors = cache.fetch_errors()
    assert any('BAD' in e for e in errors), f"BAD's failure was lost: {errors}"


def test_failures_are_per_thread():
    """One thread's pop must not drain another's pending failures."""
    mds.pop_fetch_failures()                      # clean slate for this thread
    mds.record_fetch_failure('MINE', 'this thread')

    other = {}

    def in_other_thread():
        other['before_pop'] = mds.pop_fetch_failures()   # must not see MINE
        mds.record_fetch_failure('THEIRS', 'other thread')
        other['after'] = mds.pop_fetch_failures()

    t = threading.Thread(target=in_other_thread)
    t.start()
    t.join(5)

    assert other['before_pop'] == {}, \
        f"another thread popped this thread's failures: {other['before_pop']}"
    assert list(other['after']) == ['THEIRS']
    mine = mds.pop_fetch_failures()
    assert list(mine) == ['MINE'], f"this thread's failure went missing: {mine}"


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------

if __name__ == '__main__':
    tests = [
        test_overlapping_fetch_all_fetches_each_symbol_once,
        test_overlapping_fetch_price_fetches_once,
        test_single_symbol_fetch_not_blocked_by_full_pull,
        test_failures_survive_a_concurrent_pass,
        test_failures_are_per_thread,
    ]

    print("=== concurrent fetch tests ===\n")
    passed = failed = 0
    for t in tests:
        try:
            t()
            print(f"PASS  {t.__name__}")
            passed += 1
        except AssertionError as e:
            print(f"FAIL  {t.__name__}: {e}")
            failed += 1
        except Exception as e:
            import traceback
            print(f"ERROR {t.__name__}: {e}")
            traceback.print_exc()
            failed += 1

    print(f"\n{'=' * 40}")
    print(f"{passed} passed  {failed} failed")
    sys.exit(0 if failed == 0 else 1)
