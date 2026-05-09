# MarginWatch

A small desktop app for tracking naked option and covered-call positions and their margin requirements.

## Requirements

- Python 3.10+
- WSL2 (Windows) — all commands below assume WSL

## Setup

```bash
python3 -m venv venv
venv/bin/pip install yfinance
venv/bin/python db.py        # initialize the database
```

## Running

```bash
DISPLAY=:0 venv/bin/python main.py
```

> Requires an X server (WSLg on Windows 11, or VcXsrv/X410 on Windows 10).

## Project Structure

| File | Purpose |
|---|---|
| `main.py` | Tkinter UI — positions panel + config panel |
| `db.py` | SQLite schema, migrations, connection helper |
| `positions_data.py` | Display helpers, yfinance price fetching, expiry cleanup |
| `data/marginwatch.db` | SQLite database (created on first run) |

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
