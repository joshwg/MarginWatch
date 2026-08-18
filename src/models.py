"""Typed domain objects: a position row and a portfolio."""

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
    portfolio_id: int | None = None  # portfolios.id (None only before the migration backfill)

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
            portfolio_id=row["portfolio_id"] if "portfolio_id" in keys else None,
        )


@dataclass
class Portfolio:
    """A margin account.  Capacity is max_margin × multiplier, in dollars."""
    id: int
    name: str
    max_margin: int
    multiplier: float
    is_default: bool

    @property
    def abbrev(self) -> str:
        """Short label for tight spaces: the first three characters of the name."""
        return self.name[:3]

    @property
    def capacity_k(self) -> float:
        """Margin capacity in $k — the figure "Avail" is measured against."""
        return self.max_margin / 1000 * self.multiplier

    @classmethod
    def from_row(cls, row) -> Portfolio:
        return cls(
            id=row["id"],
            name=row["name"],
            max_margin=int(row["max_margin"]),
            multiplier=float(row["multiplier"]),
            is_default=bool(row["is_default"]),
        )
