"""Spread option selector — picks the optimal 2-leg spread structure.

For DebitSpreadStrategy (Phase 10):
  - Bull Call Spread: Buy ATM/ITM CE + Sell OTM CE (higher strike)
  - Bear Put Spread:  Buy ATM/ITM PE + Sell OTM PE (lower strike)

Selection logic:
  1. Find ATM strike
  2. Try different spread widths (50, 100, 150, 200 NIFTY points)
  3. For each width, compute:
     - net_debit = long_premium - short_premium
     - max_loss = net_debit * qty (defined risk)
     - max_gain = (width - net_debit) * qty
     - breakeven = long_strike + net_debit (call) / long_strike - net_debit (put)
     - score = weighted avg of liquidity, delta, R/R, IV reasonableness
  4. Pick the spread with the best score that has positive max_gain
"""
from __future__ import annotations

from typing import Optional

from ..data.option_chain import OptionChainBuilder
from ..models import (
    MarketSnapshot, OptionQuote, OptionType, SpreadSelection,
    StrategyEvaluation, StrategyName,
)


class SpreadSelector:
    """Picks the best 2-leg debit spread for a directional view."""

    # Spread widths to try (in NIFTY points)
    DEFAULT_WIDTHS = [50, 100, 150, 200]
    # How far OTM the long leg should be (negative = ITM, 0 = ATM, positive = OTM)
    LONG_LEG_OFFSET = 0      # ATM by default

    def select(
        self,
        snapshot: MarketSnapshot,
        evaluation: StrategyEvaluation,
    ) -> SpreadSelection:
        """Pick the optimal debit spread.

        Returns SpreadSelection with selected=True if a valid spread exists.
        """
        if evaluation.strategy == StrategyName.NO_TRADE:
            return SpreadSelection(selected=False, reasons=["NO_TRADE — no spread needed"])

        if evaluation.strategy != StrategyName.DEBIT_SPREAD:
            return SpreadSelection(selected=False, reasons=[f"strategy {evaluation.strategy.value} not a spread"])

        spot = snapshot.index.ltp
        if spot <= 0:
            return SpreadSelection(selected=False, reasons=["spot invalid"])

        direction = evaluation.direction or "NEUTRAL"
        if direction not in ("BULLISH", "BEARISH"):
            return SpreadSelection(selected=False, reasons=[f"direction {direction} not supported for debit spread"])

        option_type = OptionType.CE if direction == "BULLISH" else OptionType.PE

        chain = OptionChainBuilder.filter_atm_window(snapshot.option_chain, spot, n_each_side=5)
        if not chain:
            return SpreadSelection(selected=False, reasons=["no option chain around ATM"])

        strikes = sorted({q.strike for q in chain})
        atm = OptionChainBuilder.find_atm_strike(chain, spot)
        if atm is None:
            return SpreadSelection(selected=False, reasons=["cannot locate ATM strike"])

        try:
            atm_idx = strikes.index(atm)
        except ValueError:
            return SpreadSelection(selected=False, reasons=["ATM strike not in strikes list"])

        # Long leg = ATM (or slightly ITM)
        long_q = OptionChainBuilder.pick_quote_by_type(chain, atm, option_type)
        if long_q is None:
            return SpreadSelection(selected=False, reasons=["no long leg quote at ATM"])

        # Try different widths — pick the one with best R/R after costs
        candidates: list[tuple[float, SpreadSelection, float]] = []  # (score, selection, width)
        for width in self.DEFAULT_WIDTHS:
            sel = self._build_spread(chain, strikes, atm_idx, long_q, option_type, width, direction, spot)
            if sel is not None:
                score = self._score_spread(sel, snapshot, direction)
                candidates.append((score, sel, width))

        if not candidates:
            return SpreadSelection(selected=False, reasons=["no valid spread candidates"])

        # Pick highest score
        best_score, best_sel, best_width = max(candidates, key=lambda x: x[0])
        best_sel.score = best_score
        best_sel.reasons.append(
            f"chosen width={best_width} score={best_score:.2f} "
            f"R/R={best_sel.max_gain / best_sel.max_loss if best_sel.max_loss > 0 else 0:.2f}"
        )
        return best_sel

    # ---- internal helpers ----
    def _build_spread(
        self,
        chain: list[OptionQuote],
        strikes: list[float],
        atm_idx: int,
        long_q: OptionQuote,
        option_type: OptionType,
        width: float,
        direction: str,
        spot: float,
    ) -> Optional[SpreadSelection]:
        """Build a single spread candidate with the given width."""
        # For BULLISH (call spread): short leg = ATM + width (higher strike)
        # For BEARISH (put spread): short leg = ATM - width (lower strike)
        if direction == "BULLISH":
            short_strike = long_q.strike + width
        else:
            short_strike = long_q.strike - width

        if short_strike not in strikes:
            return None

        short_q = OptionChainBuilder.pick_quote_by_type(chain, short_strike, option_type)
        if short_q is None:
            return None
        if short_q.ltp <= 0:
            return None

        # Net debit per unit (we pay long premium, receive short premium)
        net_debit = long_q.ltp - short_q.ltp
        if net_debit <= 0:
            # Short leg is more expensive than long — not a valid debit spread
            return None

        # 1 lot = 75 units
        qty = 75
        max_loss = net_debit * qty           # = total premium paid
        max_gain = (width - net_debit) * qty  # spread value at expiry if ITM

        if max_gain <= 0:
            return None       # no profit potential

        # Breakeven
        if direction == "BULLISH":
            breakeven = long_q.strike + net_debit
        else:
            breakeven = long_q.strike - net_debit

        return SpreadSelection(
            selected=True,
            long_leg=long_q,
            short_leg=short_q,
            net_debit=net_debit,
            spread_width=width,
            max_loss=max_loss,
            max_gain=max_gain,
            breakeven=breakeven,
            reasons=[
                f"long {long_q.symbol} @ ₹{long_q.ltp:.2f} strike={long_q.strike}",
                f"short {short_q.symbol} @ ₹{short_q.ltp:.2f} strike={short_q.strike}",
                f"net debit ₹{net_debit:.2f}/unit = ₹{max_loss:.0f} per lot",
                f"max gain ₹{max_gain:.0f} (if NIFTY {'>='+str(short_strike) if direction=='BULLISH' else '<='+str(short_strike)} at expiry)",
                f"breakeven {breakeven:.0f} (NIFTY)",
                f"R/R = {max_gain/max_loss:.2f}",
            ],
        )

    @staticmethod
    def _score_spread(
        sel: SpreadSelection,
        snapshot: MarketSnapshot,
        direction: str,
    ) -> float:
        """Score a spread candidate — higher is better.

        Components:
          - R/R (40%) — reward-to-risk ratio (capped at 3.0)
          - Liquidity (25%) — combined volume of both legs
          - Delta alignment (15%) — long delta close to 0.50, short delta close to 0.30
          - Width reasonableness (10%) — prefer 100pt width over 50pt or 200pt
          - Breakeven proximity (10%) — closer to spot = better
        """
        # R/R
        rr = sel.max_gain / sel.max_loss if sel.max_loss > 0 else 0
        rr_score = min(1.0, rr / 3.0)

        # Liquidity
        long_vol = sel.long_leg.volume if sel.long_leg else 0
        short_vol = sel.short_leg.volume if sel.short_leg else 0
        liq_score = min(1.0, (long_vol + short_vol) / 20_000)

        # Delta alignment
        long_delta = abs(sel.long_leg.delta) if sel.long_leg and sel.long_leg.delta else 0.5
        short_delta = abs(sel.short_leg.delta) if sel.short_leg and sel.short_leg.delta else 0.3
        delta_score_long = 1.0 - abs(long_delta - 0.50) * 2.0
        delta_score_short = 1.0 - abs(short_delta - 0.30) * 3.0
        delta_score = max(0.0, (delta_score_long + delta_score_short) / 2.0)

        # Width reasonableness — prefer 100
        if sel.spread_width == 100:
            width_score = 1.0
        elif sel.spread_width == 50:
            width_score = 0.7
        elif sel.spread_width == 150:
            width_score = 0.8
        elif sel.spread_width == 200:
            width_score = 0.6
        else:
            width_score = 0.5

        # Breakeven proximity
        spot = snapshot.index.ltp
        if spot > 0:
            dist_pct = abs(sel.breakeven - spot) / spot
            be_score = max(0.0, 1.0 - dist_pct * 20)      # 5% away = 0
        else:
            be_score = 0.5

        return (
            0.40 * rr_score
            + 0.25 * liq_score
            + 0.15 * delta_score
            + 0.10 * width_score
            + 0.10 * be_score
        )
