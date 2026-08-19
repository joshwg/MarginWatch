"""tests/test_bars.py — /api/bars (sparkline data) with the provider mocked.

1. Shape: {bars: {SYM: [[t,o,h,l,c], ...]}, days, interval}, one key per
   symbol in the book, values rounded to 2 dp, [] for a symbol with no data.
2. Caching: a second call within the TTL does not hit the provider again;
   invalidate(symbol) forces a refetch.
"""

import os
import sys
import shutil
import tempfile
from datetime import datetime
from pathlib import Path

import pytest

os.environ.setdefault("MARGIN_PWD", "test")
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))


@pytest.fixture()
def client(monkeypatch):
    tmp = tempfile.mkdtemp(prefix="mw_bars_")
    import config, db
    monkeypatch.setattr(config, "DATA_DIR", Path(tmp))
    monkeypatch.setattr(config, "CONFIG_FILE", Path(tmp) / "marginwatch.cfg")
    monkeypatch.setattr(db, "DB_PATH", os.path.join(tmp, "test.db"))
    db.init_db()
    import main_web
    monkeypatch.setattr(main_web, "_startup_prefetch", lambda: None)
    main_web.app.config["TESTING"] = True
    c = main_web.app.test_client()
    with c.session_transaction() as sess:
        sess["authenticated"] = True
        sess["last_activity"] = datetime.now().isoformat()
    yield c
    shutil.rmtree(tmp, ignore_errors=True)


def test_bars_shape_and_cache(client, monkeypatch):
    import main_web
    import repositories.positions_repository as pos_repo
    import services.market_data_service as mds

    calls = []
    def fake_bars(symbol, days=7, interval="1h"):
        calls.append(symbol)
        if symbol == "NONE":
            return []
        return [{"t": 1000, "o": 1.004, "h": 1.206, "l": 0.995, "c": 1.105, "v": 5},
                {"t": 4600000, "o": 1.105, "h": 1.3, "l": 1.0, "c": 1.2, "v": 7}]
    monkeypatch.setattr(mds, "fetch_price_bars", fake_bars)
    main_web._cache._bars.clear()

    base = {"option_type": "PUT", "strike": 1, "expiration": "2099-01-15", "quantity": 1,
            "long_shares": None, "long_cost": None, "strike2": None}
    pos_repo.insert_position({**base, "symbol": "AAPL"})
    pos_repo.insert_position({**base, "symbol": "AAPL", "strike": 2})   # same symbol twice
    pos_repo.insert_position({**base, "symbol": "NONE"})

    r = client.get("/api/bars")
    assert r.status_code == 200
    d = r.get_json()
    assert d["days"] == 7 and d["interval"] == "1h"
    assert set(d["bars"]) == {"AAPL", "NONE"}
    assert d["bars"]["NONE"] == []
    assert d["bars"]["AAPL"] == [[1000, 1.0, 1.21, 0.99, 1.1], [4600000, 1.1, 1.3, 1.0, 1.2]]
    assert sorted(calls) == ["AAPL", "NONE"]          # one fetch per symbol, not per row

    client.get("/api/bars")
    assert sorted(calls) == ["AAPL", "NONE"]          # served from cache
    main_web._cache.invalidate("AAPL")
    client.get("/api/bars")
    assert sorted(calls) == ["AAPL", "AAPL", "NONE"]  # only the invalidated one refetched
