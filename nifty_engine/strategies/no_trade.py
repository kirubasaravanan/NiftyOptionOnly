"""NO_TRADE strategy — always eligible, always evaluated.

This is the most important strategy in the system. When no directional
strategy clears its thresholds, NO_TRADE wins and the system stays in
cash. Cash IS a position.
"""
from __future__ import annotations

from ..models import (
    MarketSnapshot, RegimeAssessment, StrategyEvaluation, StrategyName,
)
from .base import StrategyBase


class NoTradeStrategy(StrategyBase):
    STRATEGY_NAME = StrategyName.NO_TRADE

    def evaluate(
        self,
        snapshot: MarketSnapshot,
        regime: RegimeAssessment,
    ) -> StrategyEvaluation:
        # NO_TRADE is always enabled and always eligible. The selector
        # picks it when no other strategy has positive expected_net_value
        # OR when risk / liquidity / time gates fail.
        reasons: list[str] = []
        if not snapshot.data_valid:
            reasons.append(f"data invalid ({snapshot.data_invalid_reason})")
        if regime.confidence < 0.5:
            reasons.append(f"low regime confidence ({regime.confidence:.2f})")
        if not reasons:
            reasons.append("no directional strategy cleared thresholds — cash is the safest position")

        return StrategyEvaluation(
            strategy=StrategyName.NO_TRADE,
            enabled=True,
            eligible=True,
            direction="NEUTRAL",
            expected_gross_pnl=0.0,
            expected_loss=0.0,
            probability_of_success=1.0,
            risk_reward=0.0,
            confidence_score=1.0,
            transaction_cost_estimate=0.0,
            slippage_estimate=0.0,
            expected_net_value=0.0,         # baseline; never beats positive edges
            reasons=reasons,
        )
