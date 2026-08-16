"""Position manager — decides HOLD / ADD / REDUCE / MOVE_STOP / TAKE_PROFIT
/ EXIT / REVERSE for existing open positions.

Rules (per spec):
  * Adding to losing positions is FORBIDDEN (no martingale, no averaging down).
  * Adding to winning positions is allowed only when:
      - Original entry conditions still hold
      - Risk caps still permit additional exposure
  * Stop moves are one-way (tighter, never looser).
"""
from __future__ import annotations

from typing import Optional

from ..models import (
    DecisionAction, MarketSnapshot, Position, RegimeAssessment,
    StrategyEvaluation, StrategyName,
)


class PositionManager:
    """Evaluates existing open positions each cycle."""

    # If unrealised loss reaches X% of entry premium -> EXIT
    STOP_LOSS_PCT_OF_PREMIUM = 0.50     # exit if down 50% on premium
    # If unrealised gain reaches X% of entry premium -> TAKE_PROFIT
    TAKE_PROFIT_PCT_OF_PREMIUM = 1.00   # exit if up 100% on premium
    # If theta decay has eaten X% of remaining time-value -> consider EXIT
    TIME_DECAY_EXIT_PCT = 0.50

    def manage(
        self,
        position: Position,
        snapshot: MarketSnapshot,
        regime: RegimeAssessment,
        new_evaluation: Optional[StrategyEvaluation],
    ) -> tuple[DecisionAction, list[str]]:
        """Return (action, reasons). Never ADD to a losing position."""
        reasons: list[str] = []
        if position.status != "OPEN":
            return DecisionAction.HOLD, ["position not open"]

        current = position.current_price or position.entry_price
        pnl_per_unit = (current - position.entry_price) * (1 if position.side == "BUY" else -1)
        pnl_pct = pnl_per_unit / position.entry_price if position.entry_price > 0 else 0.0

        # 1. Hard stop-loss on premium
        if pnl_pct <= -self.STOP_LOSS_PCT_OF_PREMIUM:
            return DecisionAction.EXIT, [
                f"hard stop hit: pnl {pnl_pct*100:.1f}% <= "
                f"-{self.STOP_LOSS_PCT_OF_PREMIUM*100:.0f}%"
            ]

        # 2. Take-profit
        if pnl_pct >= self.TAKE_PROFIT_PCT_OF_PREMIUM:
            return DecisionAction.TAKE_PROFIT, [
                f"take-profit hit: pnl {pnl_pct*100:.1f}% >= "
                f"{self.TAKE_PROFIT_PCT_OF_PREMIUM*100:.0f}%"
            ]

        # 3. Trailing stop — move stop closer as profit grows
        if pnl_pct >= 0.30 and position.stop_loss is not None:
            # 1 ATR trailing — but for now we keep it simple: trail to entry
            new_stop = max(position.stop_loss, position.entry_price * 0.95)
            if new_stop > position.stop_loss:
                position.stop_loss = new_stop
                return DecisionAction.MOVE_STOP, [
                    f"trail stop to {new_stop:.2f} (pnl {pnl_pct*100:.1f}%)"
                ]

        # 4. Regime reversal — exit if regime has clearly flipped
        if new_evaluation is not None:
            if position.strategy == StrategyName.LONG_CALL and new_evaluation.direction == "BEARISH":
                return DecisionAction.EXIT, [
                    "regime flipped to bearish — exit long call"
                ]
            if position.strategy == StrategyName.LONG_PUT and new_evaluation.direction == "BULLISH":
                return DecisionAction.EXIT, [
                    "regime flipped to bullish — exit long put"
                ]
            # New evaluation eligible in same direction AND position is profitable?
            #   -> ADD only if winning.
            if (new_evaluation.eligible
                    and new_evaluation.direction == (
                        "BULLISH" if position.strategy == StrategyName.LONG_CALL else "BEARISH")
                    and pnl_pct > 0.05):
                # ADD to winner — actual lot sizing done by RiskEngine
                return DecisionAction.ADD, [
                    f"add to winner (pnl {pnl_pct*100:.1f}%, new signal eligible)"
                ]

        # 5. Default — hold
        return DecisionAction.HOLD, [
            f"holding (pnl {pnl_pct*100:.1f}%, regime={regime.market_regime.value})"
        ]
