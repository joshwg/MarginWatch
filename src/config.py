"""
config.py – Load and save MarginWatch application settings from ../data/marginwatch.cfg

Format: one NAME=VALUE pair per line; blank lines and lines starting with
# are ignored.  Unknown keys are silently ignored on load.
"""

from pathlib import Path
from typing import Any

DATA_DIR    = Path("../data")
CONFIG_FILE = DATA_DIR / "marginwatch.cfg"

DEFAULTS: dict[str, Any] = {
    "MaximumMarginBasis": 250000,
    "MarginMultiplier":   1.5,
    "RiskFreeRate":       4.5,   # percent, e.g. 4.5 means r=0.045
    "SortOrder":          "expiry",
}

_TYPE_MAP: dict[str, type] = {k: type(v) for k, v in DEFAULTS.items()}


def load_config() -> dict:
    """Read marginwatch.cfg and return a fully-populated config dict.

    Missing keys fall back to DEFAULTS.  If the file does not exist it is
    created from defaults so the user can see and edit it immediately.
    """
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    cfg = DEFAULTS.copy()

    if not CONFIG_FILE.exists():
        save_config(cfg)
        return cfg

    for line in CONFIG_FILE.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            continue
        key, _, raw = line.partition("=")
        key = key.strip()
        raw = raw.strip()
        if key not in DEFAULTS:
            continue
        try:
            cfg[key] = _TYPE_MAP[key](raw)
        except (ValueError, TypeError):
            pass   # keep the default

    return cfg


def save_config(cfg: dict) -> None:
    """Persist cfg to marginwatch.cfg (only keys present in DEFAULTS are written)."""
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    lines = [f"{k}={cfg.get(k, DEFAULTS[k])}" for k in DEFAULTS]
    CONFIG_FILE.write_text("\n".join(lines) + "\n", encoding="utf-8")
