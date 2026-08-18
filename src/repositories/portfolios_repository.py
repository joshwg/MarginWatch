"""Repository: all SQL for the portfolios table.

Rules enforced here rather than in each UI:
  * at most MAX_PORTFOLIOS portfolios
  * names are unique ignoring case (the column is UNIQUE COLLATE NOCASE too)
  * exactly one portfolio is the default; it cannot be deleted
  * deleting a portfolio moves its positions to the default portfolio
"""

from __future__ import annotations

import sqlite3

import db
from models import Portfolio

MAX_PORTFOLIOS = 10
MULTIPLIER_MIN, MULTIPLIER_MAX = 0.5, 4.0
MAX_MARGIN_LIMIT = 10_000_000


class PortfolioError(ValueError):
    """A rule violation the caller should show to the user."""


def list_portfolios() -> list[Portfolio]:
    """All portfolios in creation order (the default is flagged, not sorted first)."""
    with db.get_connection() as conn:
        rows = conn.execute("SELECT * FROM portfolios ORDER BY id").fetchall()
    return [Portfolio.from_row(r) for r in rows]


def get_portfolio(pid: int) -> Portfolio | None:
    with db.get_connection() as conn:
        row = conn.execute("SELECT * FROM portfolios WHERE id=?", (pid,)).fetchone()
    return Portfolio.from_row(row) if row else None


def get_default() -> Portfolio:
    """The default portfolio.  init_db() guarantees there is exactly one."""
    with db.get_connection() as conn:
        row = conn.execute("SELECT * FROM portfolios WHERE is_default=1").fetchone()
    return Portfolio.from_row(row)


def _validate(name: str, max_margin, multiplier) -> tuple[str, int, float]:
    name = (name or "").strip()
    if not name:
        raise PortfolioError("Portfolio name is required")
    try:
        max_margin = int(max_margin)
        multiplier = float(multiplier)
    except (TypeError, ValueError):
        raise PortfolioError("Max margin and multiplier must be numeric") from None
    if not (0 <= max_margin <= MAX_MARGIN_LIMIT):
        raise PortfolioError(f"Max margin must be 0–{MAX_MARGIN_LIMIT:,}")
    if not (MULTIPLIER_MIN <= multiplier <= MULTIPLIER_MAX):
        raise PortfolioError(f"Multiplier must be {MULTIPLIER_MIN}–{MULTIPLIER_MAX}")
    return name, max_margin, multiplier


def _name_taken(conn, name: str, exclude_id: int | None = None) -> bool:
    row = conn.execute(
        "SELECT id FROM portfolios WHERE name=? COLLATE NOCASE AND id IS NOT ?",
        (name, exclude_id),
    ).fetchone()
    return row is not None


def create(name: str, max_margin, multiplier, *, make_default: bool = False) -> int:
    """Insert a portfolio and return its id."""
    name, max_margin, multiplier = _validate(name, max_margin, multiplier)
    with db.get_connection() as conn:
        count = conn.execute("SELECT COUNT(*) FROM portfolios").fetchone()[0]
        if count >= MAX_PORTFOLIOS:
            raise PortfolioError(f"At most {MAX_PORTFOLIOS} portfolios are allowed")
        if _name_taken(conn, name):
            raise PortfolioError(f'A portfolio named "{name}" already exists')
        try:
            cur = conn.execute(
                "INSERT INTO portfolios (name, max_margin, multiplier, is_default)"
                " VALUES (?,?,?,0)",
                (name, max_margin, multiplier),
            )
        except sqlite3.IntegrityError:
            raise PortfolioError(f'A portfolio named "{name}" already exists') from None
        pid = cur.lastrowid
        if make_default or count == 0:
            conn.execute("UPDATE portfolios SET is_default = (id = ?)", (pid,))
        conn.commit()
    return pid


def update(pid: int, name: str, max_margin, multiplier, *, make_default: bool = False) -> None:
    """Rename / re-size a portfolio; optionally make it the default."""
    name, max_margin, multiplier = _validate(name, max_margin, multiplier)
    with db.get_connection() as conn:
        if conn.execute("SELECT 1 FROM portfolios WHERE id=?", (pid,)).fetchone() is None:
            raise PortfolioError("Portfolio not found")
        if _name_taken(conn, name, exclude_id=pid):
            raise PortfolioError(f'A portfolio named "{name}" already exists')
        try:
            conn.execute(
                "UPDATE portfolios SET name=?, max_margin=?, multiplier=? WHERE id=?",
                (name, max_margin, multiplier, pid),
            )
        except sqlite3.IntegrityError:
            raise PortfolioError(f'A portfolio named "{name}" already exists') from None
        if make_default:
            conn.execute("UPDATE portfolios SET is_default = (id = ?)", (pid,))
        conn.commit()


def set_default(pid: int) -> None:
    """Make *pid* the default portfolio (new positions land here)."""
    with db.get_connection() as conn:
        if conn.execute("SELECT 1 FROM portfolios WHERE id=?", (pid,)).fetchone() is None:
            raise PortfolioError("Portfolio not found")
        conn.execute("UPDATE portfolios SET is_default = (id = ?)", (pid,))
        conn.commit()


def delete(pid: int) -> int:
    """Delete a portfolio, moving its positions to the default.

    Returns the number of positions moved.  The default portfolio cannot be
    deleted — make another one the default first.
    """
    with db.get_connection() as conn:
        row = conn.execute("SELECT is_default FROM portfolios WHERE id=?", (pid,)).fetchone()
        if row is None:
            raise PortfolioError("Portfolio not found")
        if row["is_default"]:
            raise PortfolioError("The default portfolio cannot be deleted")
        default_id = conn.execute(
            "SELECT id FROM portfolios WHERE is_default=1"
        ).fetchone()[0]
        cur = conn.execute(
            "UPDATE positions SET portfolio_id=? WHERE portfolio_id=?", (default_id, pid)
        )
        moved = cur.rowcount
        conn.execute("DELETE FROM portfolios WHERE id=?", (pid,))
        conn.commit()
    return moved
