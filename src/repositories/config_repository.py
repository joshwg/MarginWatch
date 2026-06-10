"""Config table repository: load and save application settings."""

import db


def load() -> dict[str, str]:
    """Return all config rows as a name→value dict."""
    with db.get_connection() as conn:
        rows = conn.execute("SELECT name, value FROM config").fetchall()
    return {row["name"]: row["value"] for row in rows}


def save(max_margin: int, multiplier: float, risk_free_pct: float) -> None:
    """Persist MaximumMarginBasis, MarginMultiplier, and RiskFreeRate to the config table."""
    with db.get_connection() as conn:
        for name, value in [
            ("MaximumMarginBasis", str(max_margin)),
            ("MarginMultiplier",   str(multiplier)),
            ("RiskFreeRate",       str(risk_free_pct)),
        ]:
            conn.execute(
                "INSERT OR REPLACE INTO config (name, value) VALUES (?, ?)",
                (name, value),
            )
        conn.commit()


def save_sort(sort_key: str) -> None:
    """Persist the sort choice (e.g. 'alpha' or 'expiry') to the config table."""
    with db.get_connection() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO config (name, value) VALUES (?, ?)",
            ("SortOrder", sort_key),
        )
        conn.commit()
