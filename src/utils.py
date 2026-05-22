import os
from datetime import date, timedelta


def windows_downloads_dir() -> str:
    """Return the Windows Downloads folder path, trying common WSL mount points."""
    win_user = os.environ.get("USERNAME") or os.environ.get("USER", "")
    candidates = [
        f"/mnt/c/Users/{win_user}/Downloads",
        f"/c/Users/{win_user}/Downloads",
        os.path.join(os.path.expanduser("~"), "Downloads"),
    ]
    for path in candidates:
        if os.path.isdir(path):
            return path
    return candidates[-1]


def next_option_friday() -> date:
    """Return the default expiration Friday.

    Sun–Thu  → this coming Friday.
    Fri      → next week's Friday (skip today).
    Sat      → next week's Friday (this week's already passed).
    """
    today = date.today()
    wd = today.weekday()   # Mon=0 … Sun=6
    if wd == 4:            # Friday
        days = 7
    elif wd == 5:          # Saturday
        days = 6
    else:                  # Sun(6) through Thu(0–3)
        days = (4 - wd) % 7
    return today + timedelta(days=days)

def parse_float(value, default=0.0):
    try:
        return float(value)
    except (ValueError, TypeError):
        return default

def parse_int(value, default=0):
    try:
        return int(value)
    except (ValueError, TypeError):
        return default
    