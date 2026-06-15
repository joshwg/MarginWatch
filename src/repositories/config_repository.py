"""Config repository: load and save application settings via config.py."""

import config


def load() -> dict[str, str]:
    """Return config as a string-value dict for backward compatibility."""
    return {k: str(v) for k, v in config.load_config().items()}


def save(max_margin: int, multiplier: float, risk_free_pct: float) -> None:
    """Persist MaximumMarginBasis, MarginMultiplier, and RiskFreeRate."""
    cfg = config.load_config()
    cfg["MaximumMarginBasis"] = int(max_margin)
    cfg["MarginMultiplier"]   = float(multiplier)
    cfg["RiskFreeRate"]       = float(risk_free_pct)
    config.save_config(cfg)


def save_sort(sort_key: str) -> None:
    """Persist the sort choice (e.g. 'alpha' or 'expiry')."""
    cfg = config.load_config()
    cfg["SortOrder"] = sort_key
    config.save_config(cfg)
