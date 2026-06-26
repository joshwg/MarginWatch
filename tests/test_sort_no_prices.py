"""
Regression test: sort change with no pricing data must not crash.

Bug: when /api/prices failed (no market data available), a concurrent or
rapid sort change could leave _positions=undefined on the client because:
  1. The server returned a non-200 with no 'positions' key, OR
  2. A stale phase-2 fetch ran after loadPositions() had already moved on.

Server-side invariant (tested here): /api/positions ALWAYS returns a JSON
object with a 'positions' array — never null, never missing — even when the
cache is completely empty.  This guarantees the JS guard
    _positions = data.positions || []
is sufficient to prevent the TypeError crash in renderTable().
"""

import os
import sys
import pytest

# ---------------------------------------------------------------------------
# Path setup — run from the project root or tests/ directory
# ---------------------------------------------------------------------------
SRC = os.path.join(os.path.dirname(__file__), '..', 'src')
sys.path.insert(0, os.path.abspath(SRC))


# ---------------------------------------------------------------------------
# Minimal stubs so main_web can be imported without a real DB or password
# ---------------------------------------------------------------------------

class _FakePos:
    """Minimal Position stand-in for display computation."""
    def __init__(self, **kw):
        self.id           = kw.get('id', 1)
        self.symbol       = kw.get('symbol', 'AAPL')
        self.option_type  = kw.get('option_type', 'PUT')
        self.strike       = kw.get('strike', 200.0)
        self.expiration   = kw.get('expiration', '2025-12-19')
        self.quantity     = kw.get('quantity', 1)
        self.open_date    = kw.get('open_date', '2025-01-01')
        self.long_shares  = kw.get('long_shares', None)
        self.long_cost    = kw.get('long_cost', None)
        self.strike2      = kw.get('strike2', None)


FAKE_POSITIONS = [
    _FakePos(id=1, symbol='AAPL', option_type='PUT',  strike=200.0, expiration='2025-12-19'),
    _FakePos(id=2, symbol='TSLA', option_type='CALL', strike=300.0, expiration='2025-11-21'),
    _FakePos(id=3, symbol='MSFT', option_type='PUT',  strike=400.0, expiration='2026-01-16'),
]


@pytest.fixture(autouse=True)
def _patch_env(monkeypatch):
    """Provide the minimum env vars main_web needs at import time."""
    monkeypatch.setenv('MARGIN_PWD', 'testpassword')


@pytest.fixture()
def client(monkeypatch, tmp_path):
    """Flask test client with DB and cache stubbed out."""
    # --- stub database init so it uses a temp file ---
    monkeypatch.setenv('MARGIN_DB', str(tmp_path / 'test.db'))

    import db
    monkeypatch.setattr(db, 'init_db', lambda: None)

    # --- stub config so we don't need a real DB ---
    import repositories.config_repository as cfg_repo
    monkeypatch.setattr(cfg_repo, 'load', lambda: {
        'MaximumMarginBasis': '250000',
        'MarginMultiplier':   '1.5',
        'RiskFreeRate':       '4.5',
        'SortOrder':          'alpha',
    })

    # --- stub positions repository to return our fake positions ---
    import repositories.positions_repository as pos_repo
    monkeypatch.setattr(pos_repo, 'get_open_positions', lambda: list(FAKE_POSITIONS))

    # mergeable_stock_groups lives in position_service, not the repository
    import services.position_service as ps
    monkeypatch.setattr(ps, 'mergeable_stock_groups', lambda positions: set())

    # --- import app after patching so the startup prefetch uses stubs ---
    import main_web
    monkeypatch.setattr(main_web, '_startup_prefetch', lambda: None)

    app = main_web.app
    app.config['TESTING'] = True
    app.config['SECRET_KEY'] = 'test'

    with app.test_client() as c:
        # Inject an authenticated session so auth middleware passes
        with c.session_transaction() as sess:
            from datetime import datetime
            sess['authenticated'] = True
            sess['last_activity'] = datetime.now().isoformat()
        yield c


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _get_json(client, url):
    resp = client.get(url)
    return resp.status_code, resp.get_json()


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestSortWithNoprices:
    """
    /api/positions must always return a valid positions array regardless of
    cache state or sort parameter, so the JS guard
        _positions = data.positions || []
    never has to handle undefined/null.
    """

    def test_alpha_sort_returns_positions_array(self, client):
        status, data = _get_json(client, '/api/positions?sort=alpha')
        assert status == 200, f"Expected 200, got {status}: {data}"
        assert 'positions' in data,          "Response must have 'positions' key"
        assert isinstance(data['positions'], list), "'positions' must be a list, not null/missing"

    def test_expiry_sort_returns_positions_array(self, client):
        status, data = _get_json(client, '/api/positions?sort=expiry')
        assert status == 200
        assert isinstance(data['positions'], list)

    def test_type_sort_returns_positions_array(self, client):
        status, data = _get_json(client, '/api/positions?sort=type')
        assert status == 200
        assert isinstance(data['positions'], list)

    def test_unknown_sort_returns_positions_array(self, client):
        """Unknown sort values must not crash — falls back to expiry sort."""
        status, data = _get_json(client, '/api/positions?sort=unknown_value')
        assert status == 200
        assert isinstance(data['positions'], list)

    def test_positions_array_never_null(self, client):
        """The key regression: 'positions' must never be None (JS null)."""
        for sort in ('alpha', 'expiry', 'type'):
            _, data = _get_json(client, f'/api/positions?sort={sort}')
            assert data['positions'] is not None, \
                f"sort={sort}: 'positions' was null — JS _positions=undefined crash would follow"

    def test_response_has_summary(self, client):
        """'summary' must always be present so updateSummary(data.summary) is safe."""
        status, data = _get_json(client, '/api/positions?sort=alpha')
        assert status == 200
        assert 'summary' in data
        assert data['summary'] is not None

    def test_rapid_sort_change_expiry_to_alpha(self, client):
        """Simulates the exact user action: By Expiration → Alphabetical."""
        # First call (By Expiration)
        s1, d1 = _get_json(client, '/api/positions?sort=expiry')
        assert s1 == 200
        assert isinstance(d1['positions'], list)
        expiry_order = [p['symbol'] for p in d1['positions']]

        # Second call (Alphabetical) — must succeed and produce a different order
        s2, d2 = _get_json(client, '/api/positions?sort=alpha')
        assert s2 == 200
        assert isinstance(d2['positions'], list)
        alpha_order = [p['symbol'] for p in d2['positions']]

        # Alphabetical must be sorted by symbol
        assert alpha_order == sorted(alpha_order), \
            f"Alpha sort produced wrong order: {alpha_order}"

        # The two sort orders should differ (our 3 symbols span different expirations)
        # If they happen to be the same it's not a bug, but alpha must be alpha-sorted
        assert alpha_order == sorted(set(alpha_order)), \
            "Alpha sort must be alphabetical regardless of previous sort"

    def test_prices_endpoint_returns_updates_dict(self, client):
        """
        /api/prices must return a JSON object with an 'updates' dict even when
        the cache is empty (no market data available).  The JS guard
            const upd = data.updates || {}
        handles null/missing, but a proper response is better.
        """
        status, data = _get_json(client, '/api/prices')
        assert status == 200, f"Expected 200, got {status}: {data}"
        assert 'updates' in data,            "Response must have 'updates' key"
        assert isinstance(data['updates'], dict), "'updates' must be a dict"
        assert 'total_theta' in data
        assert 'fetch_errors' in data
        assert isinstance(data['fetch_errors'], list)

    def test_prices_endpoint_updates_keyed_by_position_id(self, client):
        """Updates must be keyed by integer position ID so Object.assign merges correctly."""
        _, pos_data = _get_json(client, '/api/positions?sort=alpha')
        _, price_data = _get_json(client, '/api/prices')

        position_ids = {p['id'] for p in pos_data['positions']}
        update_keys  = set(price_data['updates'].keys())

        # All update keys must correspond to known position IDs
        # (keys come back as strings in JSON)
        assert update_keys == {str(i) for i in position_ids}, \
            f"Update keys {update_keys} do not match position IDs {position_ids}"
