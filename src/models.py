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
    long_shares: int | None
    long_cost: float | None
    strike2: float | None = None  # spread: protective leg; straddle: put strike (0 = same as call)

    @classmethod
    def from_row(cls, row) -> Position:
        """Construct from a sqlite3.Row (or any mapping)."""
        keys = row.keys()
        # Support both old column name (pre-migration) and new name
        if "strike2" in keys:
            s2 = row["strike2"]
        elif "long_strike" in keys:
            s2 = row["long_strike"]
        else:
            s2 = None
        return cls(
            id=row["id"],
            symbol=row["symbol"],
            option_type=row["option_type"],
            strike=row["strike"],
            expiration=row["expiration"],
            quantity=row["quantity"],
            open_date=row["open_date"],
            long_shares=row["long_shares"],
            long_cost=row["long_cost"],
            strike2=s2,
        )
