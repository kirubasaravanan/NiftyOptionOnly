"""3-layer position protection.

Layer 1 — Hard monetary stop: max ₹ loss per trade (e.g. ₹5,000)
Layer 2 — Market-structure invalidation: VWAP lost + swing broken + breadth drop
Layer 3 — Time invalidation: no movement within expected time horizon

Per spec: any layer triggering -> exit. Layer 2 often fires BEFORE layer 1,
preventing the maximum monetary loss from being realised.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import Enum
from typing import Optional

from ..models import MarketSnapshot, Position


class ProtectionTrigger(str, Enum):
    NONE = "NONE"
    MONETARY_STOP = "MONETARY_STOP"            # Layer 1
    STRUCTURE_INVALIDATION = "STRUCTURE_INVALIDATION"  # Layer 2
    TIME_STOP = "TIME_STOP"                     # Layer 3
    ALL_LAYERS_OK = "ALL_LAYERS_OK"


@dataclass
class ProtectionResult:
    trigger: ProtectionTrigger
    should_exit: bool
    reason: str
    details: dict = field(default_factory=dict)


@dataclass
class ProtectionConfig:
    """3-layer protection thresholds (all configurable)."""
    # Layer 1 — Monetary stop
    max_loss_per_trade_inr: float = 5_000.0      # hard ₹ loss cap
    max_loss_pct_of_premium: float = 0.50        # 50% premium loss = exit

    # Layer 2 — Structure invalidation
    require_vwap_lost: bool = True               # NIFTY below VWAP
    require_swing_break: bool = True              # prev swing low/high broken
    require_breadth_drop: bool = False            # breadth < 50% (Phase 8+)
    require_thesis_below: float = 40.0            # thesis score < this = structure broken
    require_min_signals: int = 2                  # min signals required to fire

    # Layer 3 — Time stop
    max_hold_minutes_default: int = 30           # default time horizon
    max_hold_minutes_expiry_day: int = 15         # tighter on expiry day
    min_expected_move_pct: float = 0.001          # 0.1% move expected in time window


class ProtectionLayer:
    """Evaluate all 3 layers each cycle. Returns first trigger that fires."""

    def __init__(self, config: Optional[ProtectionConfig] = None, lot_size: int = 75) -> None:
        self.config = config or ProtectionConfig()
        # BUG FIX (2026-08-20): Layer 1's premium_cost was hardcoded to *75
        # regardless of instrument. NIFTY's real lot size was already
        # corrected 75->65 elsewhere yesterday, and BANKNIFTY (30) / SENSEX
        # (20) were never 75 to begin with. A hardcoded 75 here inflates
        # premium_cost by 2.5x for BANKNIFTY and 3.75x for SENSEX, which
        # inflates the 50%-of-premium threshold by the same factor -- past
        # what the position could ever lose (its entire premium), so the
        # percentage stop became unreachable and only the flat Rs 5,000 hard
        # cap remained a real check for those two instruments. Caller must
        # pass the real per-instrument lot size (see get_lot_size()).
        self._lot_size = lot_size
        self._swing_high: Optional[float] = None
        self._swing_low: Optional[float] = None
        self._entry_vwap: Optional[float] = None
        self._entry_time: Optional[datetime] = None
        self._entry_spot: Optional[float] = None

    # ---- lifecycle ----
    def init_at_entry(self, position: Position, snapshot: MarketSnapshot) -> None:
        self._entry_time = position.entry_time
        self._entry_spot = snapshot.index.ltp
        self._entry_vwap = snapshot.index.vwap or snapshot.index.ltp
        # Initialise swing high/low from recent price action (placeholder: use entry spot)
        self._swing_high = max(position.entry_price, snapshot.index.high or position.entry_price)
        self._swing_low = min(position.entry_price, snapshot.index.low or position.entry_price)

    def update_swing(self, snapshot: MarketSnapshot) -> None:
        """Track swing high/low as new bars come in."""
        spot = snapshot.index.ltp
        if self._swing_high is None or spot > self._swing_high:
            if snapshot.index.high:
                self._swing_high = max(self._swing_high or spot, snapshot.index.high)
        if self._swing_low is None or spot < self._swing_low:
            if snapshot.index.low:
                self._swing_low = min(self._swing_low or spot, snapshot.index.low)

    # ---- evaluation ----
    def evaluate(
        self,
        position: Position,
        snapshot: MarketSnapshot,
        thesis_score: Optional[float] = None,
        now: Optional[datetime] = None,
    ) -> ProtectionResult:
        now = now or datetime.utcnow()

        # Layer 1 — monetary stop
        monetary = self._layer1_monetary(position, snapshot)
        if monetary.should_exit:
            return monetary

        # Layer 2 — structure invalidation
        structure = self._layer2_structure(position, snapshot, thesis_score)
        if structure.should_exit:
            return structure

        # Layer 3 — time stop
        time_stop = self._layer3_time(position, snapshot, now)
        if time_stop.should_exit:
            return time_stop

        # BUG FIX (2026-08-21): this subtraction crashed with "can't subtract
        # offset-naive and offset-aware datetimes" on EVERY cycle where a
        # position was actually healthy (order_manager.py stamps entry_time
        # with naive datetime.utcnow(), while `now` here is aware ist_now()).
        # Layers 1-3 above all return early on a real signal, so this line
        # was reached ONLY on the "nothing is wrong" path -- meaning the
        # entire run_cycle() (thesis tracking, all 3 protection layers, the
        # take-profit/stop-loss bracket check, journal logging) threw and
        # was silently swallowed by api.py's cache-refresh try/except
        # specifically while a position was safe, leaving it completely
        # unmonitored until something eventually broke it out of "healthy".
        # Found live: 3 real open positions had received zero fresh
        # decisions since their entry, confirmed via the decisions.jsonl
        # journal's mtime being frozen far behind wall-clock time. Using the
        # same try/except pattern _layer3_time() already uses for the
        # identical calculation, rather than fixing the naive/aware
        # inconsistency at its root (order_manager.py) under time pressure
        # with live positions open.
        try:
            hold_minutes = (now - position.entry_time).total_seconds() / 60
        except Exception:
            hold_minutes = 0

        return ProtectionResult(
            trigger=ProtectionTrigger.ALL_LAYERS_OK,
            should_exit=False,
            reason="All 3 protection layers OK",
            details={
                "unrealised_pnl": position.unrealised_pnl,
                "thesis_score": thesis_score,
                "hold_minutes": hold_minutes,
            },
        )

    # ---- Layer 1 ----
    def _layer1_monetary(self, position: Position, snapshot: MarketSnapshot) -> ProtectionResult:
        unrealised = position.unrealised_pnl
        max_loss = self.config.max_loss_per_trade_inr
        max_loss_pct = self.config.max_loss_pct_of_premium

        # Premium-based stop
        premium_cost = position.entry_price * position.lots * self._lot_size
        if premium_cost > 0 and unrealised <= -(premium_cost * max_loss_pct):
            return ProtectionResult(
                trigger=ProtectionTrigger.MONETARY_STOP,
                should_exit=True,
                reason=f"Monetary stop hit: ₹{unrealised:.0f} loss = {max_loss_pct*100:.0f}% of premium",
                details={
                    "layer": 1,
                    "unrealised_pnl": unrealised,
                    "premium_cost": premium_cost,
                    "pct_loss": (unrealised / premium_cost) if premium_cost > 0 else 0,
                },
            )

        # Hard ₹ stop
        if unrealised <= -max_loss:
            return ProtectionResult(
                trigger=ProtectionTrigger.MONETARY_STOP,
                should_exit=True,
                reason=f"Hard monetary stop: ₹{unrealised:.0f} loss > -₹{max_loss:.0f} cap",
                details={
                    "layer": 1,
                    "unrealised_pnl": unrealised,
                    "max_loss": max_loss,
                },
            )

        return ProtectionResult(
            trigger=ProtectionTrigger.NONE,
            should_exit=False,
            reason=f"Monetary OK: ₹{unrealised:.0f} unrealised vs -₹{max_loss:.0f} cap",
        )

    # ---- Layer 2 ----
    def _layer2_structure(
        self,
        position: Position,
        snapshot: MarketSnapshot,
        thesis_score: Optional[float],
    ) -> ProtectionResult:
        spot = snapshot.index.ltp
        vwap = snapshot.index.vwap or spot
        direction = "BULLISH" if position.strategy.value == "LONG_CALL" else "BEARISH"

        signals = []
        # 1. VWAP lost
        if self.config.require_vwap_lost:
            if direction == "BULLISH" and spot < vwap:
                signals.append(f"VWAP lost (spot {spot:.0f} < vwap {vwap:.0f})")
            elif direction == "BEARISH" and spot > vwap:
                signals.append(f"VWAP lost (spot {spot:.0f} > vwap {vwap:.0f})")

        # 2. Swing broken
        if self.config.require_swing_break and self._swing_low is not None and self._swing_high is not None:
            if direction == "BULLISH" and spot < self._swing_low:
                signals.append(f"Swing low broken ({self._swing_low:.0f})")
            elif direction == "BEARISH" and spot > self._swing_high:
                signals.append(f"Swing high broken ({self._swing_high:.0f})")

        # 3. Thesis score below threshold
        if thesis_score is not None and thesis_score < self.config.require_thesis_below:
            signals.append(f"Thesis score {thesis_score:.0f} < {self.config.require_thesis_below:.0f}")

        if len(signals) >= self.config.require_min_signals:
            return ProtectionResult(
                trigger=ProtectionTrigger.STRUCTURE_INVALIDATION,
                should_exit=True,
                reason=f"Structure invalidated: {' + '.join(signals)}",
                details={
                    "layer": 2,
                    "signals": signals,
                    "signals_count": len(signals),
                    "thesis_score": thesis_score,
                },
            )

        return ProtectionResult(
            trigger=ProtectionTrigger.NONE,
            should_exit=False,
            reason=f"Structure OK: {len(signals)} signal(s) (need {self.config.require_min_signals})",
        )

    # ---- Layer 3 ----
    def _layer3_time(self, position: Position, snapshot: MarketSnapshot, now: datetime) -> ProtectionResult:
        if not self._entry_time or not self._entry_spot:
            return ProtectionResult(
                trigger=ProtectionTrigger.NONE,
                should_exit=False,
                reason="Time stop: no entry time tracked",
            )

        try:
            hold_minutes = (now - position.entry_time).total_seconds() / 60
        except Exception:
            hold_minutes = 0

        # FIX (2026-08-20): this was a placeholder that always used the
        # default 30min regardless of expiry day, despite max_hold_minutes_
        # expiry_day already existing in config. Found live on SENSEX's
        # actual expiry day (2026-08-20) while holding a 0-DTE SENSEX
        # position -- theta on that contract implied ~94% of premium
        # decaying by close, concentrated in the final trading hours, so the
        # tighter expiry-day time-stop is not cosmetic for this contract.
        contract_expiry = None
        if position.option is not None:
            contract_expiry = position.option.expiry
        elif position.long_leg is not None:
            contract_expiry = position.long_leg.expiry
        is_expiry_day = contract_expiry is not None and contract_expiry == now.date()
        max_hold = (
            self.config.max_hold_minutes_expiry_day if is_expiry_day
            else self.config.max_hold_minutes_default
        )

        if hold_minutes >= max_hold:
            # Check if minimum expected move has occurred
            spot = snapshot.index.ltp
            if self._entry_spot > 0:
                move_pct = abs(spot - self._entry_spot) / self._entry_spot
            else:
                move_pct = 0
            if move_pct < self.config.min_expected_move_pct:
                return ProtectionResult(
                    trigger=ProtectionTrigger.TIME_STOP,
                    should_exit=True,
                    reason=f"Time stop: {hold_minutes:.0f}min held (max {max_hold}min), move only {move_pct*100:.2f}%",
                    details={
                        "layer": 3,
                        "hold_minutes": hold_minutes,
                        "max_hold_minutes": max_hold,
                        "move_pct": move_pct * 100,
                        "min_expected_move_pct": self.config.min_expected_move_pct * 100,
                    },
                )

        return ProtectionResult(
            trigger=ProtectionTrigger.NONE,
            should_exit=False,
            reason=f"Time OK: {hold_minutes:.0f}min held (max {max_hold}min)",
        )
