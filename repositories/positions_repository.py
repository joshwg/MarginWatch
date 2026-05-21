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


def merge_stock_positions(symbol: str, expiration: str, strike: float) -> None:
    """Merge OPEN STOCK rows that are eligible to merge with the given position.

    Eligible rows: same symbol, and (anchor or candidate has no cover) OR
    both share the same expiration+strike.
    The surviving row is the first covered row if one exists, otherwise the first row.
    Does nothing if fewer than 2 eligible rows exist.
    """
    anchor_has_cover = bool(strike)
    with db.get_connection() as conn:
        all_rows = conn.execute(
            "SELECT id, long_shares, long_cost, strike, expiration FROM positions"
            " WHERE status='OPEN' AND option_type='STOCK' AND symbol=?",
            (symbol,),
        ).fetchall()

        merge_rows = []
        for r in all_rows:
            row_has_cover = bool(r["strike"])
            if not anchor_has_cover or not row_has_cover:
                merge_rows.append(r)
            elif r["strike"] == strike and r["expiration"] == expiration:
                merge_rows.append(r)

        if len(merge_rows) < 2:
            return

        total_shares = sum(r["long_shares"] or 0 for r in merge_rows)
        total_cost = sum(
            (r["long_shares"] or 0) * (r["long_cost"] or 0.0) for r in merge_rows
        )
        avg_cost = total_cost / total_shares if total_shares else 0.0

        covered = [r for r in merge_rows if r["strike"]]
        keep_id = (covered[0] if covered else merge_rows[0])["id"]
        drop_ids = [r["id"] for r in merge_rows if r["id"] != keep_id]

        conn.execute(
            "UPDATE positions SET long_shares=?, long_cost=? WHERE id=?",
            (total_shares, avg_cost, keep_id),
        )
        conn.executemany("DELETE FROM positions WHERE id=?", [(rid,) for rid in drop_ids])
        conn.commit()
