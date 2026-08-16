"""Strategy selector — picks the strategy with the best expected_net_value
that also passes its hard filters.

Decision logic (per spec point 28):
  1. Evaluate every enabled strategy
  2. Filter to those with eligible=True
  3. Among eligible, pick the one with the highest expected_net_value
  4. If no eligible strategy remains -> NO_TRADE
"""
from __future__ import annotations

from typing import Optional

from ..models import (
    Decision, DecisionAction, MarketSnapshot, RegimeAssessment,
    StrategyEvaluation, StrategyName,
)
from ..strategies import LongCallStrategy, LongPutStrategy, NoTradeStrategy
from ..utils.time_utils import bucket_threshold_multiplier


class StrategySelector:
    """Picks the best strategy each cycle."""

    def __init__(self) -> None:
        self._strategies = [
            LongCallStrategy(),
            LongPutStrategy(),
            NoTradeStrategy(),
        ]

    @property
    def strategies(self):
        return list(self._strategies)

    def select(
        self,
        snapshot: MarketSnapshot,
        regime: RegimeAssessment,
    ) -> tuple[StrategyEvaluation, list[StrategyEvaluation]]:
        """Return (chosen, all_evaluations)."""
        all_evals = []
        for s in self._strategies:
            if not s.enabled:
                all_evals.append(StrategyEvaluation(
                    strategy=s.name,
                    enabled=False,
                    eligible=False,
                    reasons=["disabled in config"],
                ))
                continue
            try:
                ev = s.evaluate(snapshot, regime)
            except Exception as exc:  # never let one strategy break the cycle
                ev = StrategyEvaluation(
                    strategy=s.name,
                    enabled=True,
                    eligible=False,
                    reasons=[f"evaluation error: {type(exc).__name__}: {exc}"],
                )
            all_evals.append(ev)

        # Apply time-bucket threshold multiplier (midday chop -> higher bar)
        mult = bucket_threshold_multiplier()
        if mult > 1.0:
            for ev in all_evals:
                if ev.strategy == StrategyName.NO_TRADE:
                    continue
                if ev.eligible and ev.expected_net_value < (ev.expected_net_value * mult):
                    # Effectively raise the bar by re-checking against multiplied threshold
                    s_min = next((s for s in self._strategies if s.name == ev.strategy), None)
                    if s_min is not None:
                        raised_min = s_min.min_expected_net_value * mult
                        if ev.expected_net_value < raised_min:
                            ev.eligible = False
                            ev.reasons.append(
                                f"midday filter: net {ev.expected_net_value:.0f} "
                                f"< raised min {raised_min:.0f} (x{mult})"
                            )

        eligible = [e for e in all_evals if e.eligible and e.strategy != StrategyName.NO_TRADE]
        if not eligible:
            no_trade = next(
                (e for e in all_evals if e.strategy == StrategyName.NO_TRADE), None
            )
            if no_trade is None:
                no_trade = StrategyEvaluation(
                    strategy=StrategyName.NO_TRADE,
                    enabled=True, eligible=True,
                    reasons=["no eligible strategy — NO_TRADE fallback"],
                )
            return no_trade, all_evals

        # Pick highest expected_net_value; tiebreak by confidence
        chosen = max(eligible, key=lambda e: (e.expected_net_value, e.confidence_score))
        return chosen, all_evals
