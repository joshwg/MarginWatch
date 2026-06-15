"""tests/test_config_risk_free.py

Tests for saving and loading the RiskFreeRate configuration value via the
web API.  No network calls — uses a temporary on-disk SQLite database and
Flask's built-in test client.

Coverage
--------
1. GET /api/config returns RiskFreeRate after it is seeded in the DB.
2. POST /api/config saves a new RiskFreeRate and GET reflects the change.
3. POST /api/config updates _cache._r immediately.
4. POST /api/config rejects a rate below 0.
5. POST /api/config rejects a rate above 20.
6. POST /api/config rejects a request body that omits RiskFreeRate.
7. POST /api/config with rate 0.0 is accepted (boundary).
8. POST /api/config with rate 20.0 is accepted (boundary).
"""

import os
import sys
import json
import tempfile
import shutil
from datetime import datetime

os.environ.setdefault("MARGIN_PWD", "test")

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

_tmpdir = None


def _setup():
    """Create a temp DB directory and wire db.DB_PATH to it."""
    global _tmpdir
    _tmpdir = tempfile.mkdtemp(prefix="mw_test_")

    import db
    db.DB_DIR  = _tmpdir
    db.DB_PATH = os.path.join(_tmpdir, "test.db")
    db.init_db()

    # Seed a default RiskFreeRate so GET tests have something to find
    with db.get_connection() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO config (name, value) VALUES (?, ?)",
            ("RiskFreeRate", "4.5"),
        )
        conn.execute(
            "INSERT OR REPLACE INTO config (name, value) VALUES (?, ?)",
            ("MaximumMarginBasis", "250000"),
        )
        conn.execute(
            "INSERT OR REPLACE INTO config (name, value) VALUES (?, ?)",
            ("MarginMultiplier", "1.5"),
        )
        conn.commit()


def _teardown():
    if _tmpdir:
        shutil.rmtree(_tmpdir, ignore_errors=True)


def _make_client():
    """Return an authenticated Flask test client."""
    import main_web
    main_web.app.config["TESTING"] = True
    client = main_web.app.test_client()
    with client.session_transaction() as sess:
        sess["authenticated"] = True
        sess["last_activity"] = datetime.now().isoformat()
    return client


def _valid_payload(**overrides):
    """Return a minimal valid config POST body, with optional field overrides."""
    base = {
        "MaximumMarginBasis": 250000,
        "MarginMultiplier": 1.5,
        "RiskFreeRate": 4.5,
        "SortOrder": "alpha",
    }
    base.update(overrides)
    return base


# ---------------------------------------------------------------------------
# 1. GET /api/config returns RiskFreeRate
# ---------------------------------------------------------------------------

def test_get_returns_risk_free_rate():
    client = _make_client()
    resp = client.get("/api/config")
    assert resp.status_code == 200, f"expected 200, got {resp.status_code}"
    data = json.loads(resp.data)
    assert "RiskFreeRate" in data, f"RiskFreeRate missing from config response: {data}"
    assert data["RiskFreeRate"] == "4.5", f"expected '4.5', got {data['RiskFreeRate']!r}"
    print("PASS  GET /api/config includes RiskFreeRate=4.5")


# ---------------------------------------------------------------------------
# 2. POST saves a new rate and GET reflects it
# ---------------------------------------------------------------------------

def test_save_and_reload_risk_free_rate():
    client = _make_client()
    payload = _valid_payload(RiskFreeRate=3.75)
    resp = client.post("/api/config",
                       data=json.dumps(payload),
                       content_type="application/json")
    assert resp.status_code == 200, f"POST failed: {resp.status_code} {resp.data}"

    resp2 = client.get("/api/config")
    data = json.loads(resp2.data)
    assert data["RiskFreeRate"] == "3.75", f"expected '3.75', got {data['RiskFreeRate']!r}"
    print("PASS  POST RiskFreeRate=3.75 saved; GET returns 3.75")


# ---------------------------------------------------------------------------
# 3. POST updates _cache._r immediately
# ---------------------------------------------------------------------------

def test_save_updates_cache_r():
    import main_web
    client = _make_client()
    payload = _valid_payload(RiskFreeRate=5.0)
    resp = client.post("/api/config",
                       data=json.dumps(payload),
                       content_type="application/json")
    assert resp.status_code == 200, f"POST failed: {resp.status_code}"
    assert abs(main_web._cache._r - 0.05) < 1e-9, \
        f"expected _cache._r=0.05, got {main_web._cache._r}"
    print(f"PASS  _cache._r updated to {main_web._cache._r} after saving 5.0%")


# ---------------------------------------------------------------------------
# 4. Rate below 0 is rejected
# ---------------------------------------------------------------------------

def test_rate_below_zero_rejected():
    client = _make_client()
    resp = client.post("/api/config",
                       data=json.dumps(_valid_payload(RiskFreeRate=-0.1)),
                       content_type="application/json")
    assert resp.status_code == 400, f"expected 400 for negative rate, got {resp.status_code}"
    print("PASS  RiskFreeRate=-0.1 correctly rejected with 400")


# ---------------------------------------------------------------------------
# 5. Rate above 20 is rejected
# ---------------------------------------------------------------------------

def test_rate_above_20_rejected():
    client = _make_client()
    resp = client.post("/api/config",
                       data=json.dumps(_valid_payload(RiskFreeRate=20.1)),
                       content_type="application/json")
    assert resp.status_code == 400, f"expected 400 for rate>20, got {resp.status_code}"
    print("PASS  RiskFreeRate=20.1 correctly rejected with 400")


# ---------------------------------------------------------------------------
# 6. Missing RiskFreeRate key is rejected
# ---------------------------------------------------------------------------

def test_missing_risk_free_rejected():
    client = _make_client()
    payload = {"MaximumMarginBasis": 250000, "MarginMultiplier": 1.5, "SortOrder": "alpha"}
    resp = client.post("/api/config",
                       data=json.dumps(payload),
                       content_type="application/json")
    assert resp.status_code == 400, f"expected 400 for missing key, got {resp.status_code}"
    print("PASS  Missing RiskFreeRate key correctly rejected with 400")


# ---------------------------------------------------------------------------
# 7 & 8. Boundary values 0.0 and 20.0 are accepted
# ---------------------------------------------------------------------------

def test_rate_zero_accepted():
    client = _make_client()
    resp = client.post("/api/config",
                       data=json.dumps(_valid_payload(RiskFreeRate=0.0)),
                       content_type="application/json")
    assert resp.status_code == 200, f"expected 200 for 0.0%, got {resp.status_code}"
    print("PASS  RiskFreeRate=0.0 (lower boundary) accepted")


def test_rate_20_accepted():
    client = _make_client()
    resp = client.post("/api/config",
                       data=json.dumps(_valid_payload(RiskFreeRate=20.0)),
                       content_type="application/json")
    assert resp.status_code == 200, f"expected 200 for 20.0%, got {resp.status_code}"
    print("PASS  RiskFreeRate=20.0 (upper boundary) accepted")


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    tests = [
        test_get_returns_risk_free_rate,
        test_save_and_reload_risk_free_rate,
        test_save_updates_cache_r,
        test_rate_below_zero_rejected,
        test_rate_above_20_rejected,
        test_missing_risk_free_rejected,
        test_rate_zero_accepted,
        test_rate_20_accepted,
    ]

    _setup()
    try:
        print("=== RiskFreeRate config API tests ===\n")
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

        print(f"\n{'='*40}")
        print(f"{passed} passed  {failed} failed")
        sys.exit(0 if failed == 0 else 1)
    finally:
        _teardown()
