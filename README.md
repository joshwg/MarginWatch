# MarginWatch

A small desktop app for tracking naked option and covered-call positions and their margin requirements.

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
export PYTHONPATH=src
venv/bin/python src/main.py
```

> Requires an X server (WSLg on Windows 11, or VcXsrv/X410 on Windows 10).

### Web service (Flask)

```bash
export PYTHONPATH=src
export MARGIN_PWD=yourpassword
venv/bin/python src/main_web.py
```

Then open `http://localhost:5000` in a browser. `MARGIN_PWD` is required — the server refuses to start without it.

## Project Structure

```
src/
  main.py            desktop entry point (Tkinter)
  main_web.py        web entry point (Flask)
  db.py              SQLite schema, migrations, connection helper
  models.py          Position dataclass
  constants.py       shared constants
  utils.py           misc helpers
  repositories/      database access layer
  services/          business logic (positions, market data, cache, export)
  ui/                Tkinter widgets
  ui_web/            Flask templates and static assets
data/
  marginwatch.db     SQLite database (created on first run)
```

## Position Types

| `option_type` | Meaning |
|---|---|
| `CALL` | Naked call |
| `PUT` | Naked put |
| `STOCK` | Long stock — `strike=0` means no covered call written yet; `strike>0` means a covered call is written at that strike/expiration |

Over-covered positions (more calls than shares ÷ 100) are represented as a `STOCK` row for the covered portion plus a `CALL` row for the naked excess.

## Position Abbreviation Format

```
TICKER YY-MM-DD STRIKE[c/p]    e.g.  XYZ 26-06-20 50c
TICKER (no cover)              e.g.  XYZ (no cover)
```

## Expiry Color Coding

| Color | Days to expiry |
|---|---|
| Pale green | ≤ 7 days |
| Pale yellow | ≤ 14 days |
| Pale red | ≤ 21 days |
| Pale blue | ≤ 28 days |
| Gray | > 28 days |

A small yellow rectangle in the left margin of a row indicates the option is in-the-money.

## Margin Calculation

```
Margin ($k) = strike × contracts ÷ 10
```

## Configuration

| Parameter | Default | Description |
|---|---|---|
| `MaximumMarginBasis` | $250,000 | Total margin ceiling ($1k increments) |
| `MarginMultiplier` | 1.5 | Multiplier applied to individual position margins (0.5 – 4.0) |

Configuration is stored in the `config` table and editable from the bottom panel of the UI.

## Maintenance

Expired `CALL`/`PUT` positions are automatically soft-closed (status → `CLOSED`) on the Monday after expiration.
