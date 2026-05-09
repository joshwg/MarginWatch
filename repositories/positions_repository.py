"""Repository: all SQL for the positions table."""

from __future__ import annotations

from datetime import date

import db
from models import Position


def _cleanup_expired(conn) -> None:
    """On Mondays, soft-close OPEN CALL/PUT rows whose expiration has passed."""
    if date.today().weekday() != 0:   # 0 = Monday
        return
    today = date.today().isoformat()
    conn.execute(
        "UPDATE positions SET status='CLOSED'"
        " WHERE status='OPEN' AND option_type IN ('CALL','PUT') AND expiration < ?",
        (today,),
    )
    conn.commit()


def get_open_positions() -> list[Position]:
    """Run expiry cleanup then return all OPEN positions."""
    with db.get_connection() as conn:
        _cleanup_expired(conn)
        rows = conn.execute("SELECT * FROM positions WHERE status='OPEN'").fetchall()
    return [Position.from_row(r) for r in rows]


def get_position(row_id: int) -> Position | None:
    """Return a single Position by id, or None."""
    with db.get_connection() as conn:
        row = conn.execute(
            "SELECT * FROM positions WHERE id=?", (row_id,)
        ).fetchone()
    return Position.from_row(row) if row else None


def insert_position(d: dict) -> None:
    """Insert a new OPEN position from a dialog result dict."""
    today = date.today().isoformat()
    with db.get_connection() as conn:
        conn.execute(
            "INSERT INTO positions"
            " (symbol, option_type, strike, expiration, quantity,"
            "  open_date, long_shares, long_cost)"
            " VALUES (?,?,?,?,?,?,?,?)",
            (d["symbol"], d["option_type"], d["strike"], d["expiration"],
             d["quantity"], today, d["long_shares"], d["long_cost"]),
        )
        conn.commit()


def update_position(row_id: int, d: dict) -> None:
    """Update an existing position from a dialog result dict."""
    with db.get_connection() as conn:
        conn.execute(
            "UPDATE positions SET symbol=?, option_type=?, strike=?,"
            " expiration=?, quantity=?, long_shares=?, long_cost=?"
            " WHERE id=?",
            (d["symbol"], d["option_type"], d["strike"], d["expiration"],
             d["quantity"], d["long_shares"], d["long_cost"], row_id),
        )
        conn.commit()


def delete_position(row_id: int) -> None:
    """Hard-delete a position row."""
    with db.get_connection() as conn:
        conn.execute("DELETE FROM positions WHERE id=?", (row_id,))
        conn.commit()


def merge_stock_positions(symbol: str) -> None:
    """Merge all OPEN STOCK rows for symbol into one using weighted-avg cost basis.

    Does nothing if fewer than 2 matching rows exist.
    """
    with db.get_connection() as conn:
        stock_rows = conn.execute(
            "SELECT id, long_shares, long_cost FROM positions"
            " WHERE status='OPEN' AND option_type='STOCK' AND symbol=?",
            (symbol,)
        ).fetchall()
        if len(stock_rows) < 2:
            return
        total_shares = sum(r["long_shares"] or 0 for r in stock_rows)
        total_cost = sum(
            (r["long_shares"] or 0) * (r["long_cost"] or 0.0)
            for r in stock_rows
        )
        avg_cost = total_cost / total_shares if total_shares else 0.0
        keep_id = stock_rows[0]["id"]
        drop_ids = [r["id"] for r in stock_rows[1:]]
        conn.execute(
            "UPDATE positions SET long_shares=?, long_cost=? WHERE id=?",
            (total_shares, avg_cost, keep_id)
        )
        conn.executemany(
            "DELETE FROM positions WHERE id=?",
            [(rid,) for rid in drop_ids]
        )
        conn.commit()
