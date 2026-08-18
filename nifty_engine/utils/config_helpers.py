"""Config helpers — single source of truth for lot_size and other broker constants."""
from __future__ import annotations

from ..config import load as load_config

# Cache lot size per instrument — it rarely changes (NSE changes it maybe
# once a year). Keyed by lowercase instrument name ("nifty", "banknifty",
# "sensex") so each instrument's own broker_*.yaml is cached independently.
_lot_size_cache: dict[str, int] = {}

_CONFIG_NAME_BY_INSTRUMENT = {
    "nifty": "broker",
    "banknifty": "broker_banknifty",
    "sensex": "broker_sensex",
}


def get_lot_size(instrument: str = "nifty") -> int:
    """Get an instrument's lot size from its broker_*.yaml (single source of
    truth). Defaults to NIFTY for backward compatibility with every existing
    call site — those keep working unchanged.

    All strategies, risk engine, order manager, and cost model should use
    this function instead of hardcoding 75.
    """
    instrument = instrument.lower()
    if instrument in _lot_size_cache:
        return _lot_size_cache[instrument]
    config_name = _CONFIG_NAME_BY_INSTRUMENT.get(instrument, "broker")
    try:
        cfg = load_config(config_name)
        lot_size = int(cfg["dhan"]["options"]["lot_size"])
    except Exception:
        lot_size = 75  # sensible fallback
    _lot_size_cache[instrument] = lot_size
    return lot_size


def reset_cache() -> None:
    """Call after config is updated via the UI."""
    global _lot_size_cache
    _lot_size_cache = {}
