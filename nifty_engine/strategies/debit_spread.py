"""Debit Spread strategy (Phase 10).

Two flavours:
  - Bull Call Spread: Buy ATM CE + Sell higher-strike OTM CE (bullish)
  - Bear Put Spread:  Buy ATM PE + Sell lower-strike OTM PE (bearish)

When to use (per spec):
  - Directional conviction exists
  - VIX is expensive (long premium gets IV-crushed) — spread offsets IV sensitivity
  - Defined risk improves risk-adjusted return vs outright long
  - Liquidity adequate on both legs

Per spec section 2: "Use when directional conviction exists + option premium/IV
makes outright buying unattractive + defined risk improves expected risk-adjusted return."

Risk profile (per spec):
  - Max loss = net debit paid (defined)
  - Max gain = spread width - net debit (capped)
  - Breakeven = long strike + net debit (call) / long strike - net debit (put)
"""
from __future__ import annotations

from typing import Optional

from ..models import (
    MarketRegime, MarketSnapshot, OptionType, OptionQuote,
    RegimeAssessment, StrategyEvaluation, StrategyName, VolatilityRegime,
)
from .base import StrategyBase


class DebitSpreadStrategy(StrategyBase):
    STRATEGY_NAME = StrategyName.DEBIT_SPREAD
    OPTION_TYPE = None     # depends on direction (CE for bull, PE for bear)

    # Eligible regimes — same as Long Call/Put (directional conviction required)
    ELIGIBLE_REGIMES = {
        MarketRegime.STRONG_BULL, MarketRegime.BULL, MarketRegime.WEAK_BULL,
        MarketRegime.STRONG_BEAR, MarketRegime.BEAR, MarketRegime.WEAK_BEAR,
        MarketRegime.BREAKOUT,
    }

    # Volatility regimes — spreads are PREFERRED when VIX is expensive
    # (where Long CE/PE refuses to trade because of IV crush risk)
    PREFERRED_VOL = {VolatilityRegime.HIGH_VOL, VolatilityRegime.VOL_EXPANSION}
    ELIGIBLE_VOL = {
        VolatilityRegime.NORMAL_VOL, VolatilityRegime.HIGH_VOL,
        VolatilityRegime.VOL_EXPANSION,
    }

    # Spread thresholds (read from config)
    MIN_SPREAD_WIDTH = 50.0
    MAX_SPREAD_WIDTH = 200.0

    # Expected-move multiplier for spread (lower than outright — capped reward)
    EXPECTED_MOVE_ATR_MULT = 1.0
    # Holding horizon in 5-min bars, used to scale PER-DAY theta (2026-08-19).
    # Matches Long CE/PE's EXPECTED_MOVE_HORIZON_BARS so all three strategies
    # price theta over the same ~30-minute window.
    THETA_HORIZON_BARS = 6

    def __init__(self, instrument: str = "nifty") -> None:
        super().__init__(instrument)
        # Read spread-specific config
        self._min_width = float(self._cfg.get("min_spread_width", 50.0))
        self._max_width = float(self._cfg.get("max_spread_width", 200.0))
        self._preferred_when_vix_expensive = bool(
            self._cfg.get("preferred_when_vix_expensive", True)
        )

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
                f"regime {regime.market_regime.value} not eligible (need directional conviction)"
            )

        if regime.volatility_regime not in self.ELIGIBLE_VOL:
            return self._not_eligible(
                f"volatility {regime.volatility_regime.value} not eligible "
                f"(spreads need at least NORMAL_VOL)"
            )

        # Determine direction from regime
        if regime.market_regime.value in ("STRONG_BULL", "BULL", "WEAK_BULL", "BREAKOUT"):
            direction = "BULLISH"
        else:
            direction = "BEARISH"

        spot = snapshot.index.ltp
        if spot <= 0:
            return self._not_eligible("spot price invalid")

        # Need both ATM and OTM strikes in the chain
        from ..data.option_chain import OptionChainBuilder
        chain = OptionChainBuilder.filter_atm_window(snapshot.option_chain, spot, n_each_side=5)
        if len(chain) < 4:
            return self._not_eligible("insufficient option chain for spread construction")

        strikes = sorted({q.strike for q in chain})
        atm = OptionChainBuilder.find_atm_strike(chain, spot)
        if atm is None:
            return self._not_eligible("cannot locate ATM strike")

        # Check that we have at least one OTM strike within max_width
        if direction == "BULLISH":
            otm_strikes = [s for s in strikes if s > atm and (s - atm) <= self._max_width]
        else:
            otm_strikes = [s for s in strikes if s < atm and (atm - s) <= self._max_width]
        if not otm_strikes:
            return self._not_eligible(
                f"no OTM strikes within {self._max_width}pt width"
            )

        # Estimate spread value for the most likely width (100pt default)
        test_width = 100.0
        if test_width < self._min_width:
            test_width = self._min_width
        option_type = OptionType.CE if direction == "BULLISH" else OptionType.PE
        long_q = OptionChainBuilder.pick_quote_by_type(chain, atm, option_type)
        if long_q is None or long_q.ltp <= 0:
            return self._not_eligible("no long leg quote at ATM")

        # Find the short leg at atm ± test_width
        if direction == "BULLISH":
            short_strike = atm + test_width
        else:
            short_strike = atm - test_width
        short_q = OptionChainBuilder.pick_quote_by_type(chain, short_strike, option_type)
        if short_q is None or short_q.ltp <= 0:
            # Fall back to nearest OTM strike
            if direction == "BULLISH":
                short_strike = min(otm_strikes, key=lambda s: abs(s - (atm + test_width)))
            else:
                short_strike = max(otm_strikes, key=lambda s: abs((atm - test_width) - s))
            short_q = OptionChainBuilder.pick_quote_by_type(chain, short_strike, option_type)
            if short_q is None or short_q.ltp <= 0:
                return self._not_eligible("no valid short leg")
            test_width = abs(short_strike - atm)

        # Spread economics
        net_debit = long_q.ltp - short_q.ltp
        if net_debit <= 0:
            return self._not_eligible(
                f"net debit non-positive ({net_debit:.2f}) — short leg too expensive"
            )

        from ..utils.config_helpers import get_lot_size
        qty = get_lot_size(self._instrument)
        max_loss = net_debit * qty
        max_gain = (test_width - net_debit) * qty
        if max_gain <= 0:
            return self._not_eligible(
                f"max gain non-positive — width {test_width} ≤ net debit {net_debit:.2f}"
            )

        # Estimate cost (2 legs = 2 round-trips)
        cost = self._cost_model.estimate_spread_round_trip_cost(
            long_q.ltp, short_q.ltp, qty,
        )

        # Expected gain = delta-driven move over holding period
        # For spreads, the net delta is (long_delta - short_delta) which is
        # much smaller than outright delta. So we use net delta * expected_move.
        atr = snapshot.index.atr or (spot * 0.005)
        expected_move = atr * self.EXPECTED_MOVE_ATR_MULT
        long_delta = abs(long_q.delta) if long_q.delta is not None else 0.5
        short_delta = abs(short_q.delta) if short_q.delta is not None else 0.3
        net_delta = long_delta - short_delta     # call spread; for put spread, signs flip but magnitude similar
        expected_premium_gain = max(0.0, net_delta * expected_move)

        # Theta decay — much smaller for spreads than outright (short leg pays you).
        # Same PER-DAY -> holding-period scaling fix as the long strategies
        # (2026-08-19): this previously multiplied by a bare 1.0 ("1 bar"),
        # which actually charged a full day of net theta. Uses this strategy's
        # own horizon; unlike Long CE/PE it has no EXPECTED_MOVE_HORIZON_BARS,
        # so the 6-bar (~30min) horizon is stated explicitly here to match the
        # 1.0x ATR expected-move window above.
        long_theta = abs(long_q.theta or 0.0)
        short_theta = abs(short_q.theta or 0.0)
        net_theta = long_theta - short_theta
        expected_theta_loss = self._theta_loss_for_horizon(max(0.0, net_theta), self.THETA_HORIZON_BARS)

        gross_per_unit = max(0.0, expected_premium_gain - expected_theta_loss)
        gross_total = gross_per_unit * qty
        expected_net = gross_total - cost

        # R/R for spreads = max_gain / max_loss (defined risk profile)
        rr = max_gain / max_loss if max_loss > 0 else 0.0

        confidence = self._confidence(regime, long_q, short_q, expected_net, rr, direction)

        # Vol preference bonus — preferred when VIX expensive
        vol_bonus_reason = ""
        if regime.volatility_regime in self.PREFERRED_VOL and self._preferred_when_vix_expensive:
            # Add a small confidence bonus for spreads in expensive VIX
            confidence = min(1.0, confidence + 0.05)
            vol_bonus_reason = " (VIX expensive → spread preferred over outright long)"

        # net_delta drives the spread's P&L sensitivity to underlying move,
        # same role delta plays for outright long strategies
        required_move = self._required_move_points(net_delta, expected_theta_loss, cost, qty)

        reasons = [
            f"regime={regime.market_regime.value} (conf {regime.confidence:.2f})",
            f"vol={regime.volatility_regime.value}{vol_bonus_reason}",
            f"direction={direction} → {'Bull Call Spread' if direction == 'BULLISH' else 'Bear Put Spread'}",
            f"long={long_q.symbol} strike={long_q.strike} ₹{long_q.ltp:.2f} delta={long_delta:.2f}",
            f"short={short_q.symbol} strike={short_q.strike} ₹{short_q.ltp:.2f} delta={short_delta:.2f}",
            f"net_debit=₹{net_debit:.2f}/unit  width={test_width}pt",
            f"max_loss=₹{max_loss:.0f}  max_gain=₹{max_gain:.0f}  R/R={rr:.2f}",
            f"expected_move={expected_move:.1f}pts  net_delta={net_delta:.2f}",
            f"theta_loss={expected_theta_loss:.2f}  gross={gross_total:.0f}  cost={cost:.0f}",
            f"expected_net={expected_net:.0f}  conf={confidence:.2f}",
        ]
        if required_move is not None:
            reasons.append(
                f"required_move={required_move:.1f}pts to clear net≥{self.min_expected_net_value:.0f} "
                f"(expected_move={expected_move:.1f}pts)"
            )
        elif net_delta <= 0:
            reasons.append(
                "required_move=n/a (net_delta<=0 — short leg delta exceeds long leg; "
                "spread cannot be pushed into eligibility by underlying move alone)"
            )

        eligible = (
            expected_net >= self.min_expected_net_value
            and confidence >= self._min_conf
            and rr >= self._min_rr
            and test_width >= self._min_width
        )
        if not eligible:
            reasons.append(
                f"NOT ELIGIBLE: needs net≥{self.min_expected_net_value} "
                f"conf≥{self._min_conf} rr≥{self._min_rr} "
                f"width≥{self._min_width}pt"
            )

        return StrategyEvaluation(
            strategy=self.STRATEGY_NAME,
            enabled=True,
            eligible=eligible,
            direction=direction,
            expected_gross_pnl=gross_total,
            expected_loss=max_loss,
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
    def _confidence(
        regime: RegimeAssessment,
        long_q: OptionQuote,
        short_q: OptionQuote,
        env: float,
        rr: float,
        direction: str,
    ) -> float:
        """Confidence blend for spreads.

        Spread confidence is slightly LOWER than outright long at the same
        regime confidence because the reward is capped. But it's HIGHER when
        VIX is expensive (where outright long would be blocked).
        """
        from ..data.option_chain import OptionChainBuilder
        base = regime.confidence
        long_liq = OptionChainBuilder.liquidity_score(long_q)
        short_liq = OptionChainBuilder.liquidity_score(short_q)
        liq = (long_liq + short_liq) / 2.0
        edge_score = min(1.0, max(0.0, env / 5000.0))
        rr_score = min(1.0, rr / 2.5)        # spreads cap at R/R ~2.5 typically
        # Weighted blend — same shape as long_call but with liq of both legs
        return max(0.0, min(1.0, 0.40 * base + 0.25 * liq + 0.20 * edge_score + 0.15 * rr_score))
