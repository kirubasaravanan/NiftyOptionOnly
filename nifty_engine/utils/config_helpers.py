"""Config helpers — single source of truth for lot_size and other broker constants."""
from __future__ import annotations

from ..config import load as load_config

# Cache the lot size — it rarely changes (NSE changes it maybe once a year)
_lot_size_cache: int | None = None


def get_lot_size() -> int:
    """Get NIFTY lot size from broker.yaml (single source of truth).

    All strategies, risk engine, order manager, and cost model should use
    this function instead of hardcoding 75.
    """
    global _lot_size_cache
    if _lot_size_cache is not None:
        return _lot_size_cache
    try:
        cfg = load_config("broker")
        _lot_size_cache = int(cfg["dhan"]["options"]["lot_size"])
    except Exception:
        _lot_size_cache = 75  # sensible fallback
    return _lot_size_cache


def reset_cache() -> None:
    """Call after config is updated via the UI."""
    global _lot_size_cache
    _lot_size_cache = None
