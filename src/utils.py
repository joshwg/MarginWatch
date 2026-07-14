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


try:
    from option_lib.math_util import next_option_friday
except ModuleNotFoundError:
    def next_option_friday() -> date:  # type: ignore[misc]
        """Fallback when option_lib is not installed.

        Returns the Friday of *next* calendar week.
        Mon–Thu jump an extra week so the default is never the current week's expiry.
        Fri/Sat already land on next Friday naturally; Sun is 5 days out (next week).
        """
        today = date.today()
        wd = today.weekday()  # Mon=0 … Sun=6
        if wd == 4:    # Friday   → next Friday
            days = 7
        elif wd == 5:  # Saturday → next Friday
            days = 6
        elif wd == 6:  # Sunday   → next Friday (already next week, 5 days)
            days = 5
        else:          # Mon–Thu  → next week's Friday (skip this week)
            days = 4 - wd + 7
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
    