import os
import sqlite3

from config import DATA_DIR

DB_PATH = str(DATA_DIR / "marginwatch.db")

_CREATE_POSITIONS = """
    CREATE TABLE IF NOT EXISTS positions (
        id          INTEGER PRIMARY KEY AUTOINCREMENT,
        -- CALL        : naked call
        -- PUT         : naked put
        -- STOCK       : long stock; strike=0 means no cover written,
        --               strike>0 means a covered call at that strike/expiration
        -- CALL_SPREAD : vertical call spread; strike=short leg, strike2=long leg
        -- PUT_SPREAD  : vertical put spread; strike=short leg, strike2=long leg
        -- STRADDLE    : short straddle/strangle; strike=call strike, strike2=put strike
        --               (if strike2=0 the put strike equals the call strike)
        symbol      TEXT    NOT NULL,
        option_type TEXT    NOT NULL CHECK(option_type IN ('CALL', 'PUT', 'STOCK', 'CALL_SPREAD', 'PUT_SPREAD', 'STRADDLE')),
        strike      REAL    NOT NULL DEFAULT 0,
        expiration  TEXT    NOT NULL DEFAULT '9999-12-31',
        quantity    INTEGER NOT NULL DEFAULT 0,   -- short contracts (CALL/PUT) or lots/100 (STOCK)
        open_date   TEXT    NOT NULL,
        long_shares  INTEGER,   -- actual share count for STOCK rows
        long_cost    REAL,      -- per-share cost basis for STOCK rows
        strike2      REAL,      -- spread: protective long leg strike; straddle: put strike
        portfolio_id INTEGER    -- portfolios.id
    )
"""

# Portfolios: each has its own margin capacity (max_margin × multiplier).
# Exactly one is the default — where new positions land and where the
# positions of a deleted portfolio are moved.  Names are unique ignoring case.
_CREATE_PORTFOLIOS = """
    CREATE TABLE IF NOT EXISTS portfolios (
        id          INTEGER PRIMARY KEY AUTOINCREMENT,
        name        TEXT    NOT NULL UNIQUE COLLATE NOCASE,
        max_margin  INTEGER NOT NULL DEFAULT 250000,
        multiplier  REAL    NOT NULL DEFAULT 1.5,
        is_default  INTEGER NOT NULL DEFAULT 0
    )
"""


def get_connection() -> sqlite3.Connection:
    os.makedirs(str(DATA_DIR), exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def _migrate_positions(conn: sqlite3.Connection) -> None:
    schema_row = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name='positions'"
    ).fetchone()

    if schema_row is None:
        return  # table doesn't exist yet; CREATE TABLE handles it

    schema_sql = schema_row[0]

    # Very old schema missing STOCK/CALL_SPREAD: drop and recreate (pre-deployment data)
    if "'STOCK'" not in schema_sql or "'CALL_SPREAD'" not in schema_sql:
        conn.execute("DROP TABLE positions")
        return

    # Add any missing optional columns (new installs with old schema)
    existing_cols = {row[1] for row in conn.execute("PRAGMA table_info(positions)")}
    for col, defn in [("long_shares", "INTEGER"), ("long_cost", "REAL"), ("strike2", "REAL"),
                      ("portfolio_id", "INTEGER")]:
        if col not in existing_cols:
            conn.execute(f"ALTER TABLE positions ADD COLUMN {col} {defn}")


def _ensure_default_portfolio(conn: sqlite3.Connection) -> None:
    """With no portfolios at all, create "Main" and make it the default."""
    if conn.execute("SELECT COUNT(*) FROM portfolios").fetchone()[0] == 0:
        conn.execute(
            "INSERT INTO portfolios (name, max_margin, multiplier, is_default)"
            " VALUES ('Main', 250000, 1.5, 1)"
        )


def init_db() -> None:
    with get_connection() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS config (
                name  TEXT PRIMARY KEY NOT NULL,
                value TEXT NOT NULL
            )
        """)

        _migrate_positions(conn)
        conn.execute(_CREATE_POSITIONS)
        conn.execute(_CREATE_PORTFOLIOS)
        _ensure_default_portfolio(conn)
        conn.commit()


if __name__ == "__main__":
    init_db()
    print(f"Database initialized at {DB_PATH}")
