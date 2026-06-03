# MarginWatch

A small desktop app for tracking naked option, covered-call, and vertical spread positions and their margin requirements.

## Requirements

- Python 3.10+
- WSL2 (Windows) — all commands below assume WSL

## Setup

```bash
python3 -m venv venv
venv/bin/pip install -r src/requirements.txt
```

The database is created automatically on first run.

## Running

### Desktop app (Tkinter)

```bash
export PYTHONPATH=src:..
venv/bin/python src/main.py
```

> Requires an X server (WSLg on Windows 11, or VcXsrv/X410 on Windows 10).

### Web service (Flask)

```bash
export PYTHONPATH=src:..
export MARGIN_PWD=yourpassword
venv/bin/python src/main_web.py
```

Then open `http://localhost:5000` in a browser. `MARGIN_PWD` is required — the server refuses to start without it.

### Tests

```bash
export PYTHONPATH=src:../option_lib
venv/bin/python tests/test_theta.py
```

Tests live in `tests/` and are excluded from `pack.sh` deployment bundles.

## Project Structure

```
src/
  main.py            desktop entry point (Tkinter)
  main_web.py        web entry point (Flask)
  db.py              SQLite schema, migrations, connection helper
  models.py          Position dataclass
  constants.py       shared constants
  ui_styles.py       shared colour/style helpers (used by both UIs)
  utils.py           misc helpers
  repositories/      database access layer
  services/          business logic (positions, market data, cache, export)
  ui/                Tkinter widgets (desktop only)
  ui_web/            Flask templates and static assets
tests/
  test_theta.py      smoke-test for option theta fetching
data/
  marginwatch.db     SQLite database (created on first run)
```

## Position Types

| `option_type` | Meaning |
|---|---|
| `CALL` | Naked call |
| `PUT` | Naked put |
| `STOCK` | Long stock — `strike=0` means no covered call written yet; `strike>0` means a covered call is written at that strike/expiration |
| `CALL_SPREAD` | Vertical call spread — `strike` is the short leg, `long_strike` is the long (protective) leg |
| `PUT_SPREAD` | Vertical put spread — `strike` is the short leg, `long_strike` is the long (protective) leg |

Over-covered positions (more calls than shares ÷ 100) are represented as a `STOCK` row for the covered portion plus a `CALL` row for the naked excess.

### Spread types

| Spread | Short leg | Long leg | Margin |
|---|---|---|---|
| Bear call (credit) | Lower strike call | Higher strike call | `(long − short) × contracts × 100` |
| Bull put (credit) | Higher strike put | Lower strike put | `(short − long) × contracts × 100` |
| Bull call (debit) | Higher strike call | Lower strike call | 0 |
| Bear put (debit) | Lower strike put | Higher strike put | 0 |

Credit spreads are detected automatically: for calls, `short_strike < long_strike`; for puts, `short_strike > long_strike`.

In the UI, credit spreads display the short leg on top; debit spreads display the long leg on top. Exports write one row per leg.

## Position Abbreviation Format

```
TICKER YY-MM-DD STRIKE[c/p]               e.g.  XYZ 26-06-20 50c
TICKER YY-MM-DD SHORT/LONG[c/p]           e.g.  XYZ 26-06-20 50/55p
TICKER (no cover)                          e.g.  XYZ (no cover)
```

## Expiry Color Coding

| Color | Days to expiry |
|---|---|
| Pale green | ≤ 6 days |
| Pale yellow | ≤ 13 days |
| Pale red | ≤ 20 days |
| Pale blue | ≤ 27 days |
| Gray | > 27 days |

A small yellow rectangle in the left margin of a row indicates the option is in-the-money.

## Margin Calculation

```
Naked CALL/PUT:   strike × contracts ÷ 10  ($k)
Credit spread:    |long_strike − short_strike| × contracts ÷ 10  ($k)
Debit spread:     0  (max loss is the debit paid, not tracked)
STOCK:            long_shares × long_cost ÷ 1000  ($k)
```

## Configuration

| Parameter | Default | Description |
|---|---|---|
| `MaximumMarginBasis` | $250,000 | Total margin ceiling ($1k increments) |
| `MarginMultiplier` | 1.5 | Multiplier applied to individual position margins (0.5 – 4.0) |

Configuration is stored in the `config` table and editable from the bottom panel of the UI.

## Environment Variables

| Variable | Required | Description |
|---|---|---|
| `MARGIN_PWD` | Yes | Password for the web UI login page. The server refuses to start without it. |
| `MARGIN_DEBUG` | No | Set to `1` to enable debug endpoints (see below). Omit or set to any other value to disable. |

### Debug endpoints (require `MARGIN_DEBUG=1`)

| Endpoint | Description |
|---|---|
| `GET /api/price/<SYMBOL>` | Fetches the live stock price via yfinance. Returns `{"symbol","price"}` or `{"error":"..."}` on failure. |
| `GET /api/optprice/<SYMBOL>/<EXPIRATION>/<STRIKE>/<TYPE>` | Fetches the Black-Scholes theoretical price and theta via option_lib. Example: `/api/optprice/AAPL/2025-06-20/200/PUT`. |

These endpoints bypass login so they can be tested with a plain browser or `curl`. Enable them temporarily for diagnostics, then remove `MARGIN_DEBUG` from the environment.

## Maintenance

Expired `CALL`, `PUT`, `CALL_SPREAD`, and `PUT_SPREAD` positions are automatically deleted on the Monday after expiration.
