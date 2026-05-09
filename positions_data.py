"""Pure data helpers for position display, pricing, and cleanup.

option_type semantics
---------------------
CALL  : naked call  — margin applies, itm when price > strike
PUT   : naked put   — margin applies, itm when price < strike
STOCK : long stock
        strike == 0  → no covered call written yet ("no cover")
        strike  > 0  → covered call written at that strike/expiration
"""

from datetime import date

import yfinance as yf


# ---------------------------------------------------------------------------
# Display helpers
# ---------------------------------------------------------------------------

def position_abbrev(row) -> str:
    """Return the display abbreviation for a position row."""
    sym = row["symbol"]
    if row["option_type"] == "STOCK":
        if not row["strike"]:
            return f"{sym} (no cover)"
        # covered call — show the call details
        exp = date.fromisoformat(row["expiration"])
        strike = row["strike"]
        strike_str = str(int(strike)) if strike == int(strike) else str(strike)
        return f"{sym}{exp.strftime('%y-%m-%d')} {strike_str}c"
    exp = date.fromisoformat(row["expiration"])
    cp = "c" if row["option_type"] == "CALL" else "p"
    strike = row["strike"]
    strike_str = str(int(strike)) if strike == int(strike) else str(strike)
    return f"{sym}{exp.strftime('%y-%m-%d')} {strike_str}{cp}"


def days_to_expiry(row) -> int:
    """Days until expiry. STOCK with no cover returns a large number."""
    if row["option_type"] == "STOCK" and not row["strike"]:
        return 9999
    return (date.fromisoformat(row["expiration"]) - date.today()).days


def expiry_color(days: int) -> str:
    if days < 0:
        return "#5C5A97"   # a purple — already expired
    elif days <= 7:
        return "#A5CDAA"   # a green
    elif days <= 14:
        return "#F2E1A9"   # a yellow
    elif days <= 21:
        return "#ffc8c8"   # a red / pink
    elif days <= 28:
        return "#4682B4"   # a blue
    else:
        return "#d4d4d4"   # gray


def text_color(bg_hex: str) -> str:
    """Return '#000000' or '#ffffff' for readable contrast on bg_hex."""
    h = bg_hex.lstrip("#")
    r, g, b = int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)
    brightness = (r * 299 + g * 587 + b * 114) / 1000
    return "#000000" if brightness > 155 else "#ffffff"


def margin_k(row) -> float:
    """Margin in $k.
    CALL/PUT naked: strike × contracts ÷ 10.
    STOCK (covered or not): long_shares × long_cost ÷ 1000.
    """
    if row["option_type"] == "STOCK":
        shares = row["long_shares"] or 0
        cost = row["long_cost"] or 0.0
        return shares * cost / 1000.0
    return row["strike"] * row["quantity"] / 10.0


def display_quantity(row) -> int:
    """CALL/PUT: contracts written.
    STOCK no cover (strike=0): lots (long_shares÷100).
    STOCK with cover (strike>0): contracts written (quantity).
    Over-covered exposure is handled by a separate naked CALL row.
    """
    if row["option_type"] == "STOCK":
        if not row["strike"]:
            return (row["long_shares"] or 0) // 100
        return row["quantity"]   # contracts of covered calls written
    return row["quantity"]


# ---------------------------------------------------------------------------
# Market data (yfinance)
# ---------------------------------------------------------------------------

def fetch_last_price(symbol: str):
    """Return the last traded price for *symbol*, or None on failure."""
    try:
        return yf.Ticker(symbol).fast_info.last_price
    except Exception:
        return None


def fetch_option_last_price(symbol: str, expiration_iso: str,
                             strike: float, option_type: str):
    """Return the last option price for the given contract, or None."""
    try:
        chain = yf.Ticker(symbol).option_chain(expiration_iso)
        df = chain.calls if option_type in ("CALL", "STOCK") else chain.puts
        match = df[df["strike"] == float(strike)]
        if not match.empty:
            return float(match.iloc[0]["lastPrice"])
    except Exception:
        pass
    return None


def is_itm(row, current_price) -> bool:
    """True when the option leg is in-the-money."""
    if current_price is None:
        return False
    ot = row["option_type"]
    if ot == "CALL":
        return current_price > row["strike"]
    if ot == "PUT":
        return current_price < row["strike"]
    if ot == "STOCK" and row["strike"]:      # covered call written
        return current_price > row["strike"]
    return False


# ---------------------------------------------------------------------------
# Maintenance
# ---------------------------------------------------------------------------

def cleanup_expired(conn) -> None:
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
