"""Position domain logic: predicates, display helpers, and business calculations."""

from __future__ import annotations

from datetime import date

from models import Position


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _format_strike(strike: float) -> str:
    """Return a clean string for a strike price (no trailing .0)."""
    return str(int(strike)) if strike == int(strike) else str(strike)


# ---------------------------------------------------------------------------
# Predicates
# ---------------------------------------------------------------------------

def is_stock(pos: Position) -> bool:
    return pos.option_type == "STOCK"


def is_call(pos: Position) -> bool:
    return pos.option_type == "CALL"


def is_put(pos: Position) -> bool:
    return pos.option_type == "PUT"


def has_covered_call(pos: Position) -> bool:
    """True when pos is a STOCK position with a covered call written (strike > 0)."""
    return pos.option_type == "STOCK" and bool(pos.strike)


def pricing_option_type(pos: Position) -> str:
    """Option type string for pricing lookups.
    STOCK rows price as CALL (the covered call written against them).
    """
    return "CALL" if is_stock(pos) else pos.option_type


# ---------------------------------------------------------------------------
# Business calculations
# ---------------------------------------------------------------------------

def theta_dollars(pos: Position, theta) -> float | None:
    """Daily theta in dollars for a short position, or None if theta unavailable.
    Negated because positive quantity = short contracts (positive theta decay benefits us).
    """
    if theta is None:
        return None
    return -theta * 100 * pos.quantity


def is_profitable(pos: Position, price) -> bool:
    """True when a STOCK position's current price exceeds its cost basis."""
    return (
        is_stock(pos)
        and price is not None
        and (pos.long_cost or 0.0) > 0
        and price > pos.long_cost
    )


def is_itm(pos: Position, current_price) -> bool:
    """True when the option leg is in-the-money."""
    if current_price is None:
        return False
    if is_call(pos):
        return current_price > pos.strike
    if is_put(pos):
        return current_price < pos.strike
    if has_covered_call(pos):
        return current_price > pos.strike
    return False


def margin_k(pos: Position) -> float:
    """Margin in $k.
    STOCK (covered or not): long_shares × long_cost ÷ 1000.
    CALL/PUT naked: strike × qty × 100 shares ÷ 1000 → $k.
    """
    if is_stock(pos):
        shares = pos.long_shares or 0
        cost = pos.long_cost or 0.0
        return shares * cost / 1000.0
    return pos.strike * pos.quantity / 10.0  # strike × qty × 100 shares ÷ 1000 → $k


# ---------------------------------------------------------------------------
# Display helpers
# ---------------------------------------------------------------------------

def position_abbrev(pos: Position) -> str:
    """Return the display abbreviation for a position."""
    sym = pos.symbol
    if is_stock(pos):
        if not pos.strike:
            return f"{sym} (no cover)"
        exp = date.fromisoformat(pos.expiration)
        return f"{sym}{exp.strftime('%y-%m-%d')} {_format_strike(pos.strike)}c"
    exp = date.fromisoformat(pos.expiration)
    cp = "c" if is_call(pos) else "p"
    return f"{sym}{exp.strftime('%y-%m-%d')} {_format_strike(pos.strike)}{cp}"


def days_to_expiry(pos: Position) -> int:
    """Days until expiry. STOCK with no cover returns a large sentinel."""
    if is_stock(pos) and not pos.strike:
        return 9999
    return (date.fromisoformat(pos.expiration) - date.today()).days


def display_quantity(pos: Position) -> int:
    """Display quantity for the row.
    CALL/PUT: contracts written.
    STOCK no cover: lots (long_shares ÷ 100).
    STOCK with cover: contracts of covered calls written.
    """
    if is_stock(pos):
        if not pos.strike:
            return (pos.long_shares or 0) // 100
        return pos.quantity
    return pos.quantity
