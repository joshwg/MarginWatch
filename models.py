"""Typed domain object for a single position row."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class Position:
    id: int
    symbol: str
    option_type: str          # 'CALL' | 'PUT' | 'STOCK'
    strike: float
    expiration: str           # ISO-8601 date string ('yyyy-mm-dd')
    quantity: int
    open_date: str
    close_date: str | None
    status: str               # 'OPEN' | 'CLOSED'
    long_shares: int | None
    long_cost: float | None

    @classmethod
    def from_row(cls, row) -> Position:
        """Construct from a sqlite3.Row (or any mapping)."""
        return cls(
            id=row["id"],
            symbol=row["symbol"],
            option_type=row["option_type"],
            strike=row["strike"],
            expiration=row["expiration"],
            quantity=row["quantity"],
            open_date=row["open_date"],
            close_date=row["close_date"],
            status=row["status"],
            long_shares=row["long_shares"],
            long_cost=row["long_cost"],
        )
