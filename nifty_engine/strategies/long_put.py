"""Long Put strategy — mirror of Long Call.

Buys a NIFTY PE when the regime is bearish AND the expected net value
(after all costs) clears the minimum threshold.
"""
from __future__ import annotations

from typing import Optional

from ..models import (
    MarketRegime, MarketSnapshot, OptionType, OptionQuote,
    RegimeAssessment, StrategyEvaluation, StrategyName, VolatilityRegime,
)
from .base import StrategyBase


class LongPutStrategy(StrategyBase):
    STRATEGY_NAME = StrategyName.LONG_PUT
    OPTION_TYPE = OptionType.PE

    ELIGIBLE_REGIMES = {
        MarketRegime.STRONG_BEAR, MarketRegime.BEAR, MarketRegime.WEAK_BEAR,
        MarketRegime.BREAKOUT,
    }
    ELIGIBLE_VOL = {
        VolatilityRegime.LOW_VOL, VolatilityRegime.NORMAL_VOL,
        VolatilityRegime.VOL_CONTRACTION,
    }

    EXPECTED_MOVE_HORIZON_BARS = 6
    EXPECTED_MOVE_ATR_MULT = 1.5
    RISK_USING_STOP_FRACTION = 0.50

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
                f"regime {regime.market_regime.value} not eligible for LONG_PUT"
            )

        if regime.volatility_regime not in self.ELIGIBLE_VOL:
            return self._not_eligible(
                f"volatility {regime.volatility_regime.value} not eligible "
                f"(avoid long premium in expanding IV environments)"
            )

        spot = snapshot.index.ltp
        if spot <= 0:
            return self._not_eligible("spot price invalid")

        option = self._pick_option(snapshot, spot)
        if option is None:
            return self._not_eligible("no suitable PE in option chain")

        atr = snapshot.index.atr or (spot * 0.005)
        expected_move = atr * self.EXPECTED_MOVE_ATR_MULT
        # PE delta is negative; |delta| * move = expected gain
        delta = abs(option.delta) if (option.delta is not None and -1 < option.delta < 0) else 0.5
        expected_premium_gain = max(0.0, delta * expected_move)

        theta = abs(option.theta or 0.0)
        expected_theta_loss = theta * (self.EXPECTED_MOVE_HORIZON_BARS / 6.0)

        gross_per_unit = max(0.0, expected_premium_gain - expected_theta_loss)
        from ..utils.config_helpers import get_lot_size
        qty = get_lot_size()
        gross_total = gross_per_unit * qty
        cost = self._estimate_cost(option.ltp, qty)

        expected_net = gross_total - cost
        risk = option.ltp * qty * self.RISK_USING_STOP_FRACTION
        reward = expected_net
        rr = reward / risk if risk > 0 else 0.0

        confidence = self._confidence(regime, option, expected_net, rr)

        required_move = self._required_move_points(delta, expected_theta_loss, cost, qty)

        reasons = [
            f"regime={regime.market_regime.value} (conf {regime.confidence:.2f})",
            f"vol={regime.volatility_regime.value}",
            f"strike={option.strike} |delta|={delta:.2f} iv={(option.iv or 0)*100:.1f}%",
            f"expected_move={expected_move:.1f}pts  premium_gain={expected_premium_gain:.2f}",
            f"theta_loss={expected_theta_loss:.2f}  gross={gross_total:.0f}  cost={cost:.0f}",
            f"expected_net={expected_net:.0f}  risk={risk:.0f}  R/R={rr:.2f}  conf={confidence:.2f}",
        ]
        if required_move is not None:
            reasons.append(
                f"required_move={required_move:.1f}pts to clear net≥{self.min_expected_net_value:.0f} "
                f"(expected_move={expected_move:.1f}pts)"
            )

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
            direction="BEARISH",
            expected_gross_pnl=gross_total,
            expected_loss=risk,
            probability_of_success=confidence,
            risk_reward=rr,
            confidence_score=confidence,
            transaction_cost_estimate=cost,
            slippage_estimate=cost * 0.3,
            expected_net_value=expected_net,
            required_move_points=required_move,
            reasons=reasons,
        )

    @staticmethod
    def _pick_option(snapshot: MarketSnapshot, spot: float) -> Optional[OptionQuote]:
        from ..data.option_chain import OptionChainBuilder
        chain = OptionChainBuilder.filter_atm_window(snapshot.option_chain, spot, n_each_side=3)
        atm = OptionChainBuilder.find_atm_strike(chain, spot)
        if atm is None:
            return None
        return OptionChainBuilder.pick_quote_by_type(chain, atm, OptionType.PE)

    @staticmethod
    def _confidence(regime: RegimeAssessment, option: OptionQuote, env: float, rr: float) -> float:
        from ..data.option_chain import OptionChainBuilder
        base = regime.confidence
        liq = OptionChainBuilder.liquidity_score(option)
        edge_score = min(1.0, max(0.0, env / 5000.0))
        rr_score = min(1.0, rr / 3.0)
        return max(0.0, min(1.0, 0.45 * base + 0.20 * liq + 0.20 * edge_score + 0.15 * rr_score))
