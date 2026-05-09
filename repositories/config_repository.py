"""Config table repository: load and save application settings."""

import db


def load() -> dict[str, str]:
    """Return all config rows as a name→value dict."""
    with db.get_connection() as conn:
        rows = conn.execute("SELECT name, value FROM config").fetchall()
    return {row["name"]: row["value"] for row in rows}


def save(max_margin: int, multiplier: float) -> None:
    """Persist MaximumMarginBasis and MarginMultiplier to the config table."""
    with db.get_connection() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO config (name, value) VALUES (?, ?)",
            ("MaximumMarginBasis", str(max_margin)),
        )
        conn.execute(
            "INSERT OR REPLACE INTO config (name, value) VALUES (?, ?)",
            ("MarginMultiplier", str(multiplier)),
        )
        conn.commit()
