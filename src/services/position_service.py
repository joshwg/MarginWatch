"""Position domain logic: predicates, display helpers, and business calculations."""

from __future__ import annotations

from collections import Counter
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


def is_call_spread(pos: Position) -> bool:
    return pos.option_type == "CALL_SPREAD"


def is_put_spread(pos: Position) -> bool:
    return pos.option_type == "PUT_SPREAD"


def has_covered_call(pos: Position) -> bool:
    """True when pos is a STOCK position with a covered call written (strike > 0)."""
    return pos.option_type == "STOCK" and bool(pos.strike)


def is_spread(pos: Position) -> bool:
    """True when pos is a vertical spread (CALL_SPREAD or PUT_SPREAD)."""
    return pos.option_type in ("CALL_SPREAD", "PUT_SPREAD")


def is_straddle(pos: Position) -> bool:
    """True when pos is a short straddle or strangle."""
    return pos.option_type == "STRADDLE"


def is_credit_spread(pos: Position) -> bool:
    """True when the spread is a credit spread (margin required).

    Bear call spread: short strike < long strike (sold lower, bought higher).
    Bull put spread:  short strike > long strike (sold higher, bought lower).
    Debit spreads are the opposite and carry no margin.
    """
    if not is_spread(pos):
        return False
    if is_call_spread(pos):
        return pos.strike < (pos.strike2 or 0)
    return pos.strike > (pos.strike2 or 0)


def pricing_option_type(pos: Position) -> str:
    """Option type string for pricing lookups.
    STOCK rows price as CALL (the covered call written against them).
    CALL_SPREAD/PUT_SPREAD price as CALL/PUT for each leg.
    """
    if is_stock(pos):
        return "CALL"
    if is_call_spread(pos):
        return "CALL"
    if is_put_spread(pos):
        return "PUT"
    return pos.option_type


# ---------------------------------------------------------------------------
# Business calculations
# ---------------------------------------------------------------------------

def theta_dollars(pos: Position, theta, long_theta=None) -> float | None:
    """Daily theta in dollars, or None if theta unavailable.

    For naked positions: short gain = -theta × 100 × qty.
    For spreads: net = short gain + long loss = (-theta_short + theta_long) × 100 × qty.
    Both thetas are negative from the pricing model; seller gains, buyer loses.
    """
    if theta is None:
        return None
    short_gain = -theta * 100 * pos.quantity
    if is_spread(pos) and long_theta is not None:
        return short_gain + (long_theta * 100 * pos.quantity)
    return short_gain


def is_profitable(pos: Position, price) -> bool:
    """True when a STOCK position's current price exceeds its cost basis."""
    return (
        is_stock(pos)
        and price is not None
        and (pos.long_cost or 0.0) > 0
        and price > pos.long_cost
    )


def is_itm(pos: Position, current_price) -> bool:
    """True when the short leg is in-the-money."""
    if current_price is None:
        return False
    if is_call(pos) or is_call_spread(pos):
        return current_price > pos.strike
    if is_put(pos) or is_put_spread(pos):
        return current_price < pos.strike
    if has_covered_call(pos):
        return current_price > pos.strike
    if is_straddle(pos):
        # ITM if stock has moved outside the call/put range
        put_k = pos.strike2
        return current_price > pos.strike or current_price < put_k
    return False


def margin_k(pos: Position) -> float:
    """Margin in $k.
    STOCK (covered or not): long_shares × long_cost ÷ 1000.
    CALL naked: strike × 50% × qty × 100 ÷ 1000 → $k.
    PUT naked: strike × qty × 100 ÷ 1000 → $k.
    STRADDLE: call_strike × 50% × qty × 100 ÷ 1000 → $k.
    Credit spread: |strike2 − strike| × qty × 100 ÷ 1000 → $k.
    Debit spread: 0 (max loss is the debit paid, not tracked here).
    """
    if is_stock(pos):
        shares = pos.long_shares or 0
        cost = pos.long_cost or 0.0
        return shares * cost / 1000.0
    if is_spread(pos):
        if not is_credit_spread(pos):
            return 0.0
        width = abs((pos.strike2 or 0.0) - pos.strike)
        return width * pos.quantity / 10.0
    if is_straddle(pos):
        return pos.strike * 0.5 * pos.quantity / 10.0
    if pos.option_type == "CALL":
        return pos.strike * 0.5 * pos.quantity / 10.0
    return pos.strike * pos.quantity / 10.0


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
    cp = "c" if pos.option_type in ("CALL", "CALL_SPREAD") else "p"
    if is_spread(pos):
        return f"{sym}{exp.strftime('%y-%m-%d')} {_format_strike(pos.strike)}/{_format_strike(pos.strike2)}{cp}"
    return f"{sym}{exp.strftime('%y-%m-%d')} {_format_strike(pos.strike)}{cp}"


def spread_leg_abbrevs(pos: Position) -> tuple[str, str]:
    """Return (short_leg_line, long_leg_line) for two-line display of a spread."""
    exp = date.fromisoformat(pos.expiration)
    cp = "c" if is_call_spread(pos) else "p"
    short_line = f"{pos.symbol}{exp.strftime('%y-%m-%d')} {_format_strike(pos.strike)}{cp}"
    long_line  = f"{pos.symbol}{exp.strftime('%y-%m-%d')} {_format_strike(pos.strike2)}{cp}"
    return short_line, long_line


def straddle_leg_abbrevs(pos: Position) -> tuple[str, str]:
    """Return (call_leg_line, put_leg_line) for two-line display of a straddle."""
    exp = date.fromisoformat(pos.expiration)
    put_strike = pos.strike2
    call_line = f"{pos.symbol}{exp.strftime('%y-%m-%d')} {_format_strike(pos.strike)}c"
    put_line  = f"{pos.symbol}{exp.strftime('%y-%m-%d')} {_format_strike(put_strike)}p"
    return call_line, put_line


def days_to_expiry(pos: Position) -> int:
    """Days until expiry. STOCK with no cover returns a large sentinel."""
    if is_stock(pos) and not pos.strike:
        return 9999
    return (date.fromisoformat(pos.expiration) - date.today()).days  # type: ignore[arg-type]


def can_merge_stock(p1: Position, p2: Position) -> bool:
    """True if two STOCK positions are eligible to merge.

    Same symbol always required. If at least one has no cover (strike == 0), any
    same-symbol pair qualifies. If both have cover, they must share expiration and strike.
    """
    if p1.symbol != p2.symbol:
        return False
    if not has_covered_call(p1) or not has_covered_call(p2):
        return True
    return p1.expiration == p2.expiration and p1.strike == p2.strike


def mergeable_stock_groups(positions: list[Position]) -> set[tuple]:
    """Return (symbol, expiration, strike) keys of STOCK positions that have a merge partner."""
    by_symbol: dict[str, list[Position]] = {}
    for p in positions:
        if is_stock(p):
            by_symbol.setdefault(p.symbol, []).append(p)

    result: set[tuple] = set()
    for group in by_symbol.values():
        for i, p1 in enumerate(group):
            for p2 in group[i + 1:]:
                if can_merge_stock(p1, p2):
                    result.add((p1.symbol, p1.expiration or "", p1.strike or 0.0))
                    result.add((p2.symbol, p2.expiration or "", p2.strike or 0.0))
    return result


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
