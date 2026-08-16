"""Strategies layer.

Each strategy implements StrategyBase.evaluate(), which receives a
MarketSnapshot + RegimeAssessment and produces a StrategyEvaluation.

The selector picks the strategy with the highest positive
expected_net_value that also passes its hard filters. If none qualify,
NO_TRADE wins.
"""
from __future__ import annotations

from .base import StrategyBase
from .long_call import LongCallStrategy
from .long_put import LongPutStrategy
from .no_trade import NoTradeStrategy

__all__ = [
    "StrategyBase",
    "LongCallStrategy", "LongPutStrategy", "NoTradeStrategy",
]
