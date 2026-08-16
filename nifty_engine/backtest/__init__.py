"""Phase 6 — Backtesting engine + walk-forward validation framework.

The same strategy / decision / cost / journal code paths used in paper and
live mode are reused here. Only the data source differs: historical candles
instead of live snapshots.

CRITICAL: NO look-ahead bias. Every cycle's decision uses only data available
up to and including that cycle's timestamp.
"""
from __future__ import annotations

from .engine import BacktestEngine, BacktestConfig, BacktestResult
from .walk_forward import WalkForwardValidator, WalkForwardResult

__all__ = [
    "BacktestEngine", "BacktestConfig", "BacktestResult",
    "WalkForwardValidator", "WalkForwardResult",
]
