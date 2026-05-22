import os
import sqlite3

DB_DIR = os.path.normpath(os.path.join(os.path.dirname(__file__), "..", "data"))
DB_PATH = os.path.join(DB_DIR, "marginwatch.db")

DEFAULT_CONFIG = {
    "MaximumMarginBasis": "250000",
    "MarginMultiplier": "1.5",
}

_CREATE_POSITIONS = """
    CREATE TABLE IF NOT EXISTS positions (
        id          INTEGER PRIMARY KEY AUTOINCREMENT,
        -- CALL        : naked call
        -- PUT         : naked put
        -- STOCK       : long stock; strike=0 means no cover written,
        --               strike>0 means a covered call at that strike/expiration
        -- CALL_SPREAD : vertical call spread; strike=short leg, long_strike=long leg
        -- PUT_SPREAD  : vertical put spread; strike=short leg, long_strike=long leg
        symbol      TEXT    NOT NULL,
        option_type TEXT    NOT NULL CHECK(option_type IN ('CALL', 'PUT', 'STOCK', 'CALL_SPREAD', 'PUT_SPREAD')),
        strike      REAL    NOT NULL DEFAULT 0,
        expiration  TEXT    NOT NULL DEFAULT '9999-12-31',
        quantity    INTEGER NOT NULL DEFAULT 0,   -- short contracts (CALL/PUT) or lots/100 (STOCK)
        open_date   TEXT    NOT NULL,
        long_shares  INTEGER,   -- actual share count for STOCK rows
        long_cost    REAL,      -- per-share cost basis for STOCK rows
        long_strike  REAL       -- set for vertical spreads: the protective long leg's strike
    )
"""


def get_connection() -> sqlite3.Connection:
    os.makedirs(DB_DIR, exist_ok=True)
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

    # If the existing schema is missing STOCK or new columns, recreate it
    needs_recreate = "'STOCK'" not in schema_sql or "'CALL_SPREAD'" not in schema_sql

    if needs_recreate:
        conn.execute("ALTER TABLE positions RENAME TO _positions_old")
        conn.execute(_CREATE_POSITIONS)
        # Copy all columns that exist in the old table so optional columns are never silently dropped.
        old_cols = {row[1] for row in conn.execute("PRAGMA table_info(_positions_old)")}
        base_cols = ["id", "symbol", "option_type", "strike", "expiration",
                     "quantity", "open_date"]
        optional_cols = ["long_shares", "long_cost", "long_strike"]
        copy_cols = base_cols + [c for c in optional_cols if c in old_cols]
        cols_sql = ", ".join(copy_cols)
        conn.execute(f"INSERT INTO positions ({cols_sql}) SELECT {cols_sql} FROM _positions_old")
        conn.execute("DROP TABLE _positions_old")
        return

    # Just add missing columns / drop removed columns on existing schema
    existing_cols = {row[1] for row in conn.execute("PRAGMA table_info(positions)")}
    for col, defn in [("long_shares", "INTEGER"), ("long_cost", "REAL"), ("long_strike", "REAL")]:
        if col not in existing_cols:
            conn.execute(f"ALTER TABLE positions ADD COLUMN {col} {defn}")
    # over zealous dropping columns.  We don't need to do this
    # if "close_date" in existing_cols:
    #     conn.execute("ALTER TABLE positions DROP COLUMN close_date")
    # if "status" in existing_cols:
    #     conn.execute("ALTER TABLE positions DROP COLUMN status")


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

        for name, value in DEFAULT_CONFIG.items():
            conn.execute(
                "INSERT OR IGNORE INTO config (name, value) VALUES (?, ?)",
                (name, value),
            )

        conn.commit()


if __name__ == "__main__":
    init_db()
    print(f"Database initialized at {DB_PATH}")
