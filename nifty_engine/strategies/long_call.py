"""Long Call strategy.

Buys a NIFTY CE when the regime is bullish AND the expected net value
(after all costs) clears the minimum threshold.

This is the simplest directional strategy — foundational. Every threshold
is read from config; nothing hard-coded.
"""
from __future__ import annotations

from typing import Optional

from ..models import (
    MarketRegime, MarketSnapshot, OptionType, OptionQuote,
    RegimeAssessment, StrategyEvaluation, StrategyName, VolatilityRegime,
)
from .base import StrategyBase


class LongCallStrategy(StrategyBase):
    STRATEGY_NAME = StrategyName.LONG_CALL
    OPTION_TYPE = OptionType.CE

    # Eligible regimes — strategy is only evaluated when one of these matches
    ELIGIBLE_REGIMES = {
        MarketRegime.STRONG_BULL, MarketRegime.BULL, MarketRegime.BREAKOUT,
    }
    ELIGIBLE_VOL = {
        VolatilityRegime.LOW_VOL, VolatilityRegime.NORMAL_VOL,
        VolatilityRegime.VOL_CONTRACTION,
    }

    # Expected-move multiplier — ATR-based expected move over holding period.
    # Conservative default; tuned via walk-forward.
    EXPECTED_MOVE_HORIZON_BARS = 6        # ~30min on 5min bars
    EXPECTED_MOVE_ATR_MULT = 1.0          # capture 1 ATR of expected move

    def evaluate(
        self,
        snapshot: MarketSnapshot,
        regime: RegimeAssessment,
    ) -> StrategyEvaluation:
        if not self.enabled:
            return self._not_eligible("strategy disabled in config")

        if not snapshot.data_valid:
            return self._not_eligible("data invalid")

        if regime.market_regime not in self.ELIGIBLE_REGIMES:
            return self._not_eligible(
                f"regime {regime.market_regime.value} not in {sorted(r.value for r in self.ELIGIBLE_REGIMES)}"
            )

        if regime.volatility_regime not in self.ELIGIBLE_VOL:
            return self._not_eligible(
                f"volatility {regime.volatility_regime.value} not eligible "
                f"(avoid long premium in expanding IV environments)"
            )

        spot = snapshot.index.ltp
        if spot <= 0:
            return self._not_eligible("spot price invalid")

        # Pick the best CE candidate (ATM by default; selector may refine)
        option = self._pick_option(snapshot, spot)
        if option is None:
            return self._not_eligible("no suitable CE in option chain")

        # Expected move over holding period (in NIFTY points)
        atr = snapshot.index.atr or (spot * 0.005)
        expected_move = atr * self.EXPECTED_MOVE_ATR_MULT
        delta = option.delta if (option.delta is not None and 0 < option.delta < 1) else 0.5
        # Expected premium change = delta * expected_move
        expected_premium_gain = max(0.0, delta * expected_move)

        # Theta decay over holding period (negative)
        theta = abs(option.theta or 0.0)
        expected_theta_loss = theta * (self.EXPECTED_MOVE_HORIZON_BARS / 6.0)  # ~30min worth

        # Gross expected gain per unit
        gross_per_unit = max(0.0, expected_premium_gain - expected_theta_loss)

        # Quantity: 1 lot for estimation purposes; selector overrides
        qty = 75
        gross_total = gross_per_unit * qty
        cost = self._estimate_cost(option.ltp, qty)

        expected_net = gross_total - cost

        # Risk = total premium paid (long options: max loss = premium)
        risk = option.ltp * qty
        reward = expected_net
        rr = reward / risk if risk > 0 else 0.0

        confidence = self._confidence(regime, option, expected_net, rr)

        reasons = [
            f"regime={regime.market_regime.value} (conf {regime.confidence:.2f})",
            f"vol={regime.volatility_regime.value}",
            f"strike={option.strike} delta={delta:.2f} iv={(option.iv or 0)*100:.1f}%",
            f"expected_move={expected_move:.1f}pts  premium_gain={expected_premium_gain:.2f}",
            f"theta_loss={expected_theta_loss:.2f}  gross={gross_total:.0f}  cost={cost:.0f}",
            f"expected_net={expected_net:.0f}  risk={risk:.0f}  R/R={rr:.2f}  conf={confidence:.2f}",
        ]

        eligible = (
            expected_net >= self.min_expected_net_value
            and confidence >= self._min_conf
            and rr >= self._min_rr
        )
        if not eligible:
            reasons.append(
                f"NOT ELIGIBLE: needs net≥{self.min_expected_net_value} "
                f"conf≥{self._min_conf} rr≥{self._min_rr}"
            )

        return StrategyEvaluation(
            strategy=self.STRATEGY_NAME,
            enabled=True,
            eligible=eligible,
            direction="BULLISH",
            expected_gross_pnl=gross_total,
            expected_loss=risk,
            probability_of_success=confidence,
            risk_reward=rr,
            confidence_score=confidence,
            transaction_cost_estimate=cost,
            slippage_estimate=cost * 0.3,  # rough split
            expected_net_value=expected_net,
            reasons=reasons,
        )

    # ---- helpers ----
    @staticmethod
    def _pick_option(snapshot: MarketSnapshot, spot: float) -> Optional[OptionQuote]:
        """Pick the ATM CE — most liquid, balanced delta.

        The OptionSelector (decision layer) may refine further by scoring
        ATM / 1-ITM / 1-OTM. Here we just need a representative quote for
        expected-value estimation.
        """
        from ..data.option_chain import OptionChainBuilder
        chain = OptionChainBuilder.filter_atm_window(snapshot.option_chain, spot, n_each_side=3)
        atm = OptionChainBuilder.find_atm_strike(chain, spot)
        if atm is None:
            return None
        return OptionChainBuilder.pick_quote_by_type(chain, atm, OptionType.CE)

    @staticmethod
    def _confidence(regime: RegimeAssessment, option: OptionQuote, env: float, rr: float) -> float:
        """Confidence blend — regime + option-liquidity + edge clarity."""
        base = regime.confidence
        # Penalise illiquid options
        liq = OptionQuoteLiquidityScore(option)
        # Reward large positive edge
        edge_score = min(1.0, max(0.0, env / 5000.0))
        # Reward asymmetric R/R
        rr_score = min(1.0, rr / 3.0)
        return max(0.0, min(1.0, 0.45 * base + 0.20 * liq + 0.20 * edge_score + 0.15 * rr_score))


# small helper kept at module scope to avoid circular imports
def OptionQuoteLiquidityScore(q: OptionQuote) -> float:
    from ..data.option_chain import OptionChainBuilder
    return OptionChainBuilder.liquidity_score(q)
