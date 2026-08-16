"""Phase 6+7 — Backtesting engine + walk-forward validation + ablation testing.

The same strategy / decision / cost / journal code paths used in paper and
live mode are reused here. Only the data source differs: historical candles
instead of live snapshots.

CRITICAL: NO look-ahead bias. Every cycle's decision uses only data available
up to and including that cycle's timestamp.

Per spec section 26: every new factor must pass an incremental-value
ablation test before it is allowed into the live decision engine.
"""
from __future__ import annotations

from .engine import BacktestEngine, BacktestConfig, BacktestResult
from .walk_forward import (
    WalkForwardValidator, WalkForwardResult,
    AblationTester, AblationResult, AblationVariant,
    DEFAULT_FEATURE_FLAGS,
)

__all__ = [
    "BacktestEngine", "BacktestConfig", "BacktestResult",
    "WalkForwardValidator", "WalkForwardResult",
    "AblationTester", "AblationResult", "AblationVariant",
    "DEFAULT_FEATURE_FLAGS",
]
