"""Configuration loader — reads YAML files in this directory."""
from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

CONFIG_DIR = Path(__file__).parent

_FILES = {
    "risk": "risk.yaml",
    "strategies": "strategies.yaml",
    "broker": "broker.yaml",
    "trading_hours": "trading_hours.yaml",
    "costs": "costs.yaml",
}

_cache: dict[str, dict[str, Any]] = {}


def load(name: str) -> dict[str, Any]:
    """Load a YAML config file by short name (cached)."""
    if name in _cache:
        return _cache[name]
    if name not in _FILES:
        raise KeyError(f"Unknown config: {name}")
    path = CONFIG_DIR / _FILES[name]
    if not path.exists():
        raise FileNotFoundError(f"Config file missing: {path}")
    with open(path, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    _cache[name] = data
    return data


def reload() -> None:
    """Clear cache (used in tests)."""
    _cache.clear()


__all__ = ["load", "reload", "CONFIG_DIR"]
