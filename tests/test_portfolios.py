"""tests/test_portfolios.py

Portfolio rules and the web API around them.  Uses a temporary on-disk SQLite
database and Flask's test client; no network.

Coverage
--------
Repository
  1. init_db() creates a default "Main" portfolio when there are none.
  2. Names are unique ignoring case; the limit is MAX_PORTFOLIOS.
  3. The default cannot be deleted; deleting another portfolio moves its
     positions to the default.
  4. Positions inserted without a portfolio land in the default.
  5. Stock merges never cross portfolios.
API
  6. CRUD round trip through /api/portfolios.
  7. /api/positions summary: per-portfolio rows plus the "All" aggregate,
     and every row carries its portfolio tag.
  8. Neither adding nor editing a position changes the default portfolio.
"""

import json
import os
import shutil
import sys
import tempfile
from datetime import datetime
from pathlib import Path

import pytest

os.environ.setdefault("MARGIN_PWD", "test")
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))


@pytest.fixture()
def env(monkeypatch):
    """Fresh DB + cfg dir per test; main_web imported against it."""
    tmp = tempfile.mkdtemp(prefix="mw_pf_")
    import config, db
    monkeypatch.setattr(config, "DATA_DIR", Path(tmp))
    monkeypatch.setattr(config, "CONFIG_FILE", Path(tmp) / "marginwatch.cfg")
    monkeypatch.setattr(db, "DB_PATH", os.path.join(tmp, "test.db"))
    db.init_db()

    import main_web
    monkeypatch.setattr(main_web, "_startup_prefetch", lambda: None)
    main_web.app.config["TESTING"] = True
    client = main_web.app.test_client()
    with client.session_transaction() as sess:
        sess["authenticated"] = True
        sess["last_activity"] = datetime.now().isoformat()
    yield client
    shutil.rmtree(tmp, ignore_errors=True)


def _post(client, url, body):
    return client.post(url, data=json.dumps(body), content_type="application/json")


def _put(client, url, body):
    return client.put(url, data=json.dumps(body), content_type="application/json")


def _pos(**kw):
    d = {"symbol": "AAPL", "option_type": "PUT", "strike": 200, "expiration": "2099-01-15",
         "quantity": 1, "long_shares": None, "long_cost": None, "strike2": None}
    d.update(kw)
    return d


# ---------------------------------------------------------------------------
# Repository
# ---------------------------------------------------------------------------

def test_default_main_created(env):
    import repositories.portfolios_repository as pf
    pfs = pf.list_portfolios()
    assert [p.name for p in pfs] == ["Main"]
    assert pfs[0].is_default
    assert pf.get_default().id == pfs[0].id


def test_unique_name_case_insensitive_and_limit(env):
    import repositories.portfolios_repository as pf
    pf.create("IRA", 100000, 1.0)
    with pytest.raises(pf.PortfolioError):
        pf.create("ira", 100000, 1.0)
    with pytest.raises(pf.PortfolioError):
        pf.create("  Main ", 100000, 1.0)
    # rename onto an existing name is refused too
    ira = next(p for p in pf.list_portfolios() if p.name == "IRA")
    with pytest.raises(pf.PortfolioError):
        pf.update(ira.id, "MAIN", 1, 1.0)
    # renaming to a different-case spelling of itself is fine
    pf.update(ira.id, "Ira", 1, 1.0)
    assert pf.get_portfolio(ira.id).name == "Ira"

    for i in range(pf.MAX_PORTFOLIOS - 2):
        pf.create(f"P{i}", 1000, 1.0)
    assert len(pf.list_portfolios()) == pf.MAX_PORTFOLIOS
    with pytest.raises(pf.PortfolioError):
        pf.create("one too many", 1000, 1.0)


def test_delete_rules(env):
    import repositories.portfolios_repository as pf
    import repositories.positions_repository as pos_repo
    main = pf.get_default()
    ira_id = pf.create("IRA", 100000, 1.0)
    pos_repo.insert_position(_pos(portfolio_id=ira_id))
    pos_repo.insert_position(_pos(symbol="MSFT", portfolio_id=ira_id))
    with pytest.raises(pf.PortfolioError):
        pf.delete(main.id)
    moved = pf.delete(ira_id)
    assert moved == 2
    assert {p.portfolio_id for p in pos_repo.get_open_positions()} == {main.id}
    assert [p.name for p in pf.list_portfolios()] == ["Main"]


def test_insert_without_portfolio_uses_default(env):
    import repositories.portfolios_repository as pf
    import repositories.positions_repository as pos_repo
    ira_id = pf.create("IRA", 100000, 1.0)
    pf.set_default(ira_id)
    pos_repo.insert_position(_pos())
    assert pos_repo.get_open_positions()[0].portfolio_id == ira_id


def test_merge_stays_within_portfolio(env):
    import repositories.portfolios_repository as pf
    import repositories.positions_repository as pos_repo
    import services.position_service as ps
    main = pf.get_default()
    ira_id = pf.create("IRA", 100000, 1.0)
    stock = dict(symbol="MU", option_type="STOCK", strike=0, expiration="9999-12-31",
                 quantity=1, long_shares=100, long_cost=50.0, strike2=None)
    pos_repo.insert_position({**stock, "portfolio_id": main.id})
    pos_repo.insert_position({**stock, "portfolio_id": ira_id})
    positions = pos_repo.get_open_positions()
    assert ps.mergeable_stock_groups(positions) == set()
    pos_repo.merge_stock_positions("MU", "9999-12-31", 0.0, main.id)
    assert len(pos_repo.get_open_positions()) == 2
    # same portfolio → merges
    pos_repo.insert_position({**stock, "portfolio_id": main.id})
    assert len(ps.mergeable_stock_groups(pos_repo.get_open_positions())) == 1
    pos_repo.merge_stock_positions("MU", "9999-12-31", 0.0, main.id)
    rows = pos_repo.get_open_positions()
    assert len(rows) == 2
    assert sum(r.long_shares for r in rows if r.portfolio_id == main.id) == 200


# ---------------------------------------------------------------------------
# API
# ---------------------------------------------------------------------------

def test_api_crud(env):
    client = env
    r = _post(client, "/api/portfolios", {"name": "IRA", "max_margin": 100000, "multiplier": 2.0})
    assert r.status_code == 200, r.get_json()
    ira_id = r.get_json()["id"]

    r = _post(client, "/api/portfolios", {"name": "ira", "max_margin": 1, "multiplier": 1.0})
    assert r.status_code == 400
    r = _post(client, "/api/portfolios", {"name": "X", "max_margin": 1, "multiplier": 9.0})
    assert r.status_code == 400

    r = _put(client, f"/api/portfolios/{ira_id}",
             {"name": "Roth", "max_margin": 50000, "multiplier": 1.0, "is_default": True})
    assert r.status_code == 200
    data = client.get("/api/portfolios").get_json()
    assert data["max_portfolios"] == 10
    by_name = {p["name"]: p for p in data["portfolios"]}
    assert by_name["Roth"]["is_default"] and not by_name["Main"]["is_default"]
    assert by_name["Roth"]["abbrev"] == "Rot"

    # default cannot be deleted; the other can
    assert client.delete(f"/api/portfolios/{ira_id}").status_code == 400
    assert client.delete(f"/api/portfolios/{by_name['Main']['id']}").status_code == 200
    assert [p["name"] for p in client.get("/api/portfolios").get_json()["portfolios"]] == ["Roth"]


def test_summary_per_portfolio(env):
    client = env
    import repositories.portfolios_repository as pf
    main = pf.get_default()
    pf.update(main.id, "Main", 100000, 1.0)          # capacity 100k
    ira_id = pf.create("IRA", 200000, 1.5)           # capacity 300k
    # 1 contract of a 200 put: margin_k = 20% × 200 × 100 / 1000 = 4.0k? — use
    # whatever margin_k says, and check the arithmetic, not the constant.
    assert _post(client, "/api/positions", _pos(portfolio_id=main.id)).status_code == 200
    assert _post(client, "/api/positions", _pos(symbol="MSFT", strike=100,
                                                 portfolio_id=ira_id)).status_code == 200
    assert _post(client, "/api/positions", _pos(symbol="TSLA", strike=100,
                                                 portfolio_id=ira_id)).status_code == 200

    data = client.get("/api/positions?sort=alpha").get_json()
    rows = data["positions"]
    assert [r["portfolio_abbrev"] for r in rows] == ["Mai", "IRA", "IRA"]
    assert all("portfolio_id" in r and "portfolio" in r for r in rows)

    s = data["summary"]
    by = {p["name"]: p for p in s["portfolios"]}
    m_used = sum(r["margin"] for r in rows if r["portfolio"] == "Main")
    i_used = sum(r["margin"] for r in rows if r["portfolio"] == "IRA")
    assert by["Main"]["position_count"] == 1 and by["IRA"]["position_count"] == 2
    assert by["Main"]["total_margin"] == pytest.approx(m_used, abs=0.05)
    assert by["Main"]["avail_margin"] == pytest.approx(100.0 - m_used, abs=0.05)
    assert by["IRA"]["avail_margin"] == pytest.approx(300.0 - i_used, abs=0.05)
    assert s["total_margin"] == pytest.approx(m_used + i_used, abs=0.05)
    assert s["avail_margin"] == pytest.approx(400.0 - m_used - i_used, abs=0.05)
    assert "total_theta" in s

    # the snapshot carries the same block
    snap = client.get("/api/snapshot?cached=1", headers={"X-Api-Key": "test"}).get_json()
    assert snap["summary"]["portfolios"][0]["name"] == "Main"
    assert snap["positions"][0]["portfolio"] in ("Main", "IRA")


def test_position_add_edit_leave_default_alone(env):
    client = env
    import repositories.portfolios_repository as pf
    main = pf.get_default()
    ira_id = pf.create("IRA", 200000, 1.5)
    assert _post(client, "/api/positions", _pos(portfolio_id=ira_id)).status_code == 200
    assert pf.get_default().id == main.id                      # unchanged
    pid = client.get("/api/positions").get_json()["positions"][0]["id"]
    assert client.get(f"/api/positions/{pid}").get_json()["portfolio_id"] == ira_id
    assert _put(client, f"/api/positions/{pid}", _pos(portfolio_id=main.id)).status_code == 200
    assert pf.get_default().id == main.id                      # still unchanged
    assert client.get(f"/api/positions/{pid}").get_json()["portfolio_id"] == main.id
    # unknown portfolio is refused
    assert _post(client, "/api/positions", _pos(portfolio_id=999)).status_code == 400
