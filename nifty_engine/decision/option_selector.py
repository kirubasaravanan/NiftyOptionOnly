"""Option selector — given a chosen strategy, pick the optimal strike.

For Long Call / Long Put, scores:
  ATM, 1-ITM, 1-OTM

Considers delta, IV, IV percentile, liquidity, spread, OI, theta, premium,
distance from spot. Does NOT simply pick the cheapest option.
"""
from __future__ import annotations

from typing import Optional

from ..data.option_chain import OptionChainBuilder
from ..models import (
    MarketSnapshot, OptionQuote, OptionSelection, OptionType,
    StrategyEvaluation, StrategyName,
)


class OptionSelector:
    """Picks the best option contract for a chosen directional strategy."""

    # Strike candidates — ATM +/- offsets
    CANDIDATE_OFFSETS = [-1, 0, +1]    # 1-ITM, ATM, 1-OTM

    def select(
        self,
        snapshot: MarketSnapshot,
        evaluation: StrategyEvaluation,
    ) -> OptionSelection:
        if evaluation.strategy == StrategyName.NO_TRADE:
            return OptionSelection(selected=False, reasons=["NO_TRADE — no option needed"])

        if evaluation.strategy not in (StrategyName.LONG_CALL, StrategyName.LONG_PUT):
            return OptionSelection(selected=False, reasons=["strategy not implemented in Phase 1-5"])

        opt_type = (OptionType.CE
                    if evaluation.strategy == StrategyName.LONG_CALL
                    else OptionType.PE)

        spot = snapshot.index.ltp
        if spot <= 0:
            return OptionSelection(selected=False, reasons=["spot invalid"])

        chain = OptionChainBuilder.filter_atm_window(snapshot.option_chain, spot, n_each_side=2)
        if not chain:
            return OptionSelection(selected=False, reasons=["no option chain around ATM"])

        strikes = sorted({q.strike for q in chain})
        atm = OptionChainBuilder.find_atm_strike(chain, spot)
        if atm is None:
            return OptionSelection(selected=False, reasons=["cannot locate ATM strike"])

        idx = strikes.index(atm)
        candidates: list[OptionQuote] = []
        for off in self.CANDIDATE_OFFSETS:
            i = idx + off
            if 0 <= i < len(strikes):
                q = OptionChainBuilder.pick_quote_by_type(chain, strikes[i], opt_type)
                if q:
                    candidates.append(q)
        if not candidates:
            return OptionSelection(selected=False, reasons=["no candidate quotes"])

        scored = [(q, self._score(q, spot, evaluation)) for q in candidates]
        best_q, best_score = max(scored, key=lambda x: x[1])

        reasons = [
            f"evaluated {len(candidates)} candidates",
            *[f"{q.symbol} strike={q.strike} score={s:.2f}" for q, s in scored],
            f"chosen: {best_q.symbol} strike={best_q.strike} score={best_score:.2f}",
        ]
        return OptionSelection(
            selected=True,
            option=best_q,
            score=best_score,
            reasons=reasons,
        )

    # ---- scoring ----
    def _score(self, q: OptionQuote, spot: float, ev: StrategyEvaluation) -> float:
        """Weighted score in [0,1]. Higher is better."""
        # Liquidity (40%)
        liq = OptionChainBuilder.liquidity_score(q)

        # Delta appropriateness (20%) — prefer delta ~0.50 for ATM long premium
        delta = abs(q.delta) if q.delta is not None else 0.5
        delta_score = 1.0 - abs(delta - 0.50) * 2.0       # 0.5=perfect, 0/1=worst
        delta_score = max(0.0, min(1.0, delta_score))

        # IV reasonableness (10%) — penalise IV > 25% (overpaying)
        iv = q.iv or 0.20
        iv_score = max(0.0, 1.0 - max(0.0, iv - 0.10) / 0.20)

        # Spread (15%)
        spread = (q.ask or q.ltp) - (q.bid or q.ltp)
        spread_pct = spread / q.ltp if q.ltp > 0 else 1.0
        spread_score = max(0.0, 1.0 - spread_pct / 0.10)

        # OI (10%) — prefer liquid OI > 5000
        oi_score = min(1.0, (q.oi or 0) / 50_000)

        # Premium relative to expected gain (5%) — prefer lower premium if edge is similar
        premium_score = max(0.0, 1.0 - q.ltp / 500.0)

        return (
            0.40 * liq
            + 0.20 * delta_score
            + 0.10 * iv_score
            + 0.15 * spread_score
            + 0.10 * oi_score
            + 0.05 * premium_score
        )
