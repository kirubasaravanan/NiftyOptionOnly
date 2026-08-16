"""Strategy selector — picks the strategy with the best expected_net_value
that also passes its hard filters.

Decision logic (per spec point 28):
  1. Evaluate every enabled strategy
  2. Filter to those with eligible=True
  3. Apply confirmation-score adjustment (Layer 3 features modulate confidence)
  4. Among eligible, pick the one with the highest expected_net_value
  5. If no eligible strategy remains -> NO_TRADE

Phase 10 addition: when VIX is expensive (HIGH_VOL / VOL_EXPANSION), prefer
DEBIT_SPREAD over outright Long CE/PE because spreads offset IV sensitivity.
"""
from __future__ import annotations

from typing import Optional

from ..models import (
    Decision, DecisionAction, MarketSnapshot, RegimeAssessment,
    StrategyEvaluation, StrategyName, VolatilityRegime,
)
from ..strategies import LongCallStrategy, LongPutStrategy, NoTradeStrategy, DebitSpreadStrategy
from ..utils.time_utils import bucket_threshold_multiplier
from ..features.correlation import ConfirmationScore


class StrategySelector:
    """Picks the best strategy each cycle."""

    def __init__(self) -> None:
        self._strategies = [
            LongCallStrategy(),
            LongPutStrategy(),
            DebitSpreadStrategy(),
            NoTradeStrategy(),
        ]

    @property
    def strategies(self):
        return list(self._strategies)

    def select(
        self,
        snapshot: MarketSnapshot,
        regime: RegimeAssessment,
        confirmation: Optional[ConfirmationScore] = None,
    ) -> tuple[StrategyEvaluation, list[StrategyEvaluation]]:
        """Return (chosen, all_evaluations).

        If `confirmation` is provided, the chosen strategy's confidence_score
        is adjusted by the confirmation score (raise on alignment, lower on
        divergence). If the adjusted confidence falls below the strategy's
        minimum threshold, the strategy is marked ineligible.
        """
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
            # Apply confirmation-score adjustment to eligible directional strategies
            if confirmation is not None and ev.eligible and ev.strategy != StrategyName.NO_TRADE:
                ev = self._apply_confirmation(ev, confirmation, s)
            all_evals.append(ev)

        # Apply time-bucket threshold multiplier (midday chop -> higher bar)
        mult = bucket_threshold_multiplier()
        if mult > 1.0:
            for ev in all_evals:
                if ev.strategy == StrategyName.NO_TRADE:
                    continue
                if ev.eligible:
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
            # Attach confirmation reasons to NO_TRADE so the journal captures WHY
            if confirmation is not None and confirmation.reasons:
                no_trade.reasons.extend(confirmation.reasons)
            return no_trade, all_evals

        # Phase 10: when VIX is expensive, prefer DEBIT_SPREAD over outright long
        # even if outright long has slightly higher expected_net_value.
        if regime.volatility_regime in (VolatilityRegime.HIGH_VOL, VolatilityRegime.VOL_EXPANSION):
            spread_evals = [e for e in eligible if e.strategy == StrategyName.DEBIT_SPREAD]
            outright_evals = [e for e in eligible if e.strategy in (StrategyName.LONG_CALL, StrategyName.LONG_PUT)]
            if spread_evals and outright_evals:
                spread_best = max(spread_evals, key=lambda e: e.expected_net_value)
                outright_best = max(outright_evals, key=lambda e: e.expected_net_value)
                # If spread's expected_net is within 80% of outright, prefer spread
                # (lower IV risk, defined risk profile, smaller theta bleed)
                if spread_best.expected_net_value >= 0.80 * outright_best.expected_net_value:
                    spread_best.reasons.append(
                        f"PREFERRED over {outright_best.strategy.value} in expensive VIX "
                        f"(spread ₹{spread_best.expected_net_value:.0f} vs outright ₹{outright_best.expected_net_value:.0f})"
                    )
                    chosen = spread_best
                    # Re-sort the remaining eligible list
                    eligible_filtered = [e for e in eligible if e is not chosen]
                    return chosen, all_evals

        # Default: pick highest expected_net_value; tiebreak by confidence
        chosen = max(eligible, key=lambda e: (e.expected_net_value, e.confidence_score))
        return chosen, all_evals

    @staticmethod
    def _apply_confirmation(
        ev: StrategyEvaluation,
        confirmation: ConfirmationScore,
        strategy,
    ) -> StrategyEvaluation:
        """Adjust strategy evaluation based on cross-market confirmation.

        Positive confirmation score raises confidence; negative lowers it.
        If confidence drops below strategy minimum, mark ineligible.
        """
        original_conf = ev.confidence_score
        # Confidence adjustment — scale confirmation score by 0.4 so the
        # adjustment is meaningful but not overwhelming (max ±0.4 swing).
        adjustment = confirmation.score * 0.4
        adjusted_conf = max(0.0, min(1.0, original_conf + adjustment))
        ev.confidence_score = adjusted_conf

        if confirmation.score < 0:
            ev.reasons.append(
                f"confirmation score {confirmation.score:+.2f} "
                f"(conf {original_conf:.2f} -> {adjusted_conf:.2f})"
            )
            for r in confirmation.reasons[:3]:  # top 3 reasons
                ev.reasons.append(f"  - {r}")
            # If divergence is strong enough to drop below minimum confidence, mark ineligible
            if adjusted_conf < strategy._min_conf:
                ev.eligible = False
                ev.reasons.append(
                    f"BLOCKED: confirmation divergence lowered confidence below min {strategy._min_conf}"
                )
        elif confirmation.score > 0:
            ev.reasons.append(
                f"confirmation score {confirmation.score:+.2f} "
                f"(conf {original_conf:.2f} -> {adjusted_conf:.2f})"
            )
            for r in confirmation.reasons[:3]:
                ev.reasons.append(f"  - {r}")
        return ev
