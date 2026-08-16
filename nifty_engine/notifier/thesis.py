"""Thesis Score tracker — per-position, dynamic.

Per spec:
  Initial thesis at entry, then re-evaluate every cycle.
  Score drops below threshold -> reduce
  Score drops further -> exit
  Score flips sign -> reverse

Components (each scored 0-100):
  Trend           (based on ADX + EMA alignment)
  VWAP            (price vs VWAP)
  Momentum        (RSI direction + ADX slope)
  Breadth         (Bank Nifty confirmation + market direction)
  Bank Nifty      (correlation state)
  OI              (OI classification - long buildup etc.)
  VIX             (VIX valuation - cheap/fair/expensive)

Composite thesis score is weighted average — weights configurable.

Position state machine:
  CONFIDENT  (>=75)   -> hold / add on strength
  CAUTIOUS   (55-74)  -> hold, tighten stop
  REDUCE     (40-54)  -> reduce exposure
  EXIT       (<40)    -> exit (thesis invalidated)
  REVERSE    (flip)   -> reverse direction
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Optional

from ..features.correlation import ConfirmationScore
from ..models import MarketSnapshot, RegimeAssessment


class PositionState(str, Enum):
    CONFIDENT = "CONFIDENT"        # thesis strong, hold or add
    CAUTIOUS = "CAUTIOUS"          # thesis weakening, hold + monitor
    REDUCE = "REDUCE"              # thesis deteriorating, reduce exposure
    EXIT = "EXIT"                  # thesis invalidated
    REVERSE = "REVERSE"            # thesis has flipped, reverse direction


@dataclass
class ThesisScore:
    """Composite thesis score with breakdown."""
    trend: float          # 0-100
    vwap: float            # 0-100
    momentum: float        # 0-100
    breadth: float         # 0-100
    banknifty: float      # 0-100
    oi: float              # 0-100
    vix: float             # 0-100
    composite: float       # weighted average 0-100
    state: PositionState
    direction: str         # "BULLISH" | "BEARISH" | "NEUTRAL"
    changed_from: Optional[PositionState] = None  # previous state
    reasons: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "trend": round(self.trend, 1),
            "vwap": round(self.vwap, 1),
            "momentum": round(self.momentum, 1),
            "breadth": round(self.breadth, 1),
            "banknifty": round(self.banknifty, 1),
            "oi": round(self.oi, 1),
            "vix": round(self.vix, 1),
            "composite": round(self.composite, 1),
            "state": self.state.value,
            "direction": self.direction,
            "changed_from": self.changed_from.value if self.changed_from else None,
            "reasons": self.reasons,
        }


# Component weights — must sum to 1.0
DEFAULT_WEIGHTS = {
    "trend": 0.20,
    "vwap": 0.15,
    "momentum": 0.15,
    "breadth": 0.10,
    "banknifty": 0.15,
    "oi": 0.10,
    "vix": 0.15,
}

# State thresholds
THRESHOLD_CONFIDENT = 75.0
THRESHOLD_CAUTIOUS = 55.0
THRESHOLD_REDUCE = 40.0
THRESHOLD_EXIT = 30.0


class ThesisTracker:
    """Per-position thesis score tracker.

    Lifecycle:
      init_at_entry(regime, confirmation, direction) -> initial ThesisScore
      update(snapshot, regime, confirmation) -> new ThesisScore (with state transition)
    """

    def __init__(self, weights: Optional[dict] = None) -> None:
        self.weights = weights or DEFAULT_WEIGHTS
        self.last_state: Optional[PositionState] = None
        self.last_score: Optional[ThesisScore] = None
        self.entry_direction: Optional[str] = None

    # ---- entry ----
    def init_at_entry(
        self,
        snapshot: MarketSnapshot,
        regime: RegimeAssessment,
        confirmation: ConfirmationScore,
        direction: str,
    ) -> ThesisScore:
        self.entry_direction = direction
        return self.update(snapshot, regime, confirmation, direction, is_entry=True)

    # ---- periodic update ----
    def update(
        self,
        snapshot: MarketSnapshot,
        regime: RegimeAssessment,
        confirmation: ConfirmationScore,
        direction: Optional[str] = None,
        is_entry: bool = False,
    ) -> ThesisScore:
        # Compute each component 0-100
        trend = self._score_trend(snapshot, regime, direction or self.entry_direction or "BULLISH")
        vwap = self._score_vwap(snapshot, direction or self.entry_direction or "BULLISH")
        momentum = self._score_momentum(snapshot, regime, direction or self.entry_direction or "BULLISH")
        breadth = self._score_breadth(confirmation, regime, direction or self.entry_direction or "BULLISH")
        banknifty = self._score_banknifty(confirmation, direction or self.entry_direction or "BULLISH")
        oi = self._score_oi(confirmation, direction or self.entry_direction or "BULLISH")
        vix = self._score_vix(confirmation, direction or self.entry_direction or "BULLISH")

        # Composite — weighted average
        composite = (
            trend * self.weights["trend"]
            + vwap * self.weights["vwap"]
            + momentum * self.weights["momentum"]
            + breadth * self.weights["breadth"]
            + banknifty * self.weights["banknifty"]
            + oi * self.weights["oi"]
            + vix * self.weights["vix"]
        )

        # Determine state
        prev_state = self.last_state
        new_state = self._classify_state(composite, direction or self.entry_direction or "BULLISH")

        # Detect direction flip -> REVERSE
        if (direction and self.entry_direction and direction != self.entry_direction
                and direction != "NEUTRAL"):
            new_state = PositionState.REVERSE

        reasons: list[str] = []
        if is_entry:
            reasons.append(f"Initial thesis established: {direction}")
        if prev_state and prev_state != new_state:
            reasons.append(f"State transition: {prev_state.value} -> {new_state.value}")
        # Add component-level reasons
        if trend < 50:
            reasons.append(f"Trend score weak ({trend:.0f})")
        if banknifty < 50:
            reasons.append(f"Bank Nifty not confirming ({banknifty:.0f})")
        if vix < 50:
            reasons.append(f"VIX unfavourable ({vix:.0f})")
        if momentum < 50:
            reasons.append(f"Momentum fading ({momentum:.0f})")

        score = ThesisScore(
            trend=trend, vwap=vwap, momentum=momentum, breadth=breadth,
            banknifty=banknifty, oi=oi, vix=vix,
            composite=composite, state=new_state,
            direction=direction or self.entry_direction or "NEUTRAL",
            changed_from=prev_state if (prev_state and prev_state != new_state) else None,
            reasons=reasons,
        )
        self.last_state = new_state
        self.last_score = score
        return score

    # ---- component scorers ----
    @staticmethod
    def _score_trend(snapshot: MarketSnapshot, regime: RegimeAssessment, direction: str) -> float:
        adx = snapshot.index.adx or 0.0
        # ADX 0-50 maps to score 0-100 (ADX > 25 = strong trend)
        adx_score = min(100.0, adx * 4.0)
        # Regime confirmation
        regime_bonus = 0.0
        if direction == "BULLISH" and regime.market_regime.value in ("STRONG_BULL", "BULL"):
            regime_bonus = 20.0
        elif direction == "BEARISH" and regime.market_regime.value in ("STRONG_BEAR", "BEAR"):
            regime_bonus = 20.0
        elif regime.market_regime.value == "NEUTRAL":
            regime_bonus = -10.0
        return max(0.0, min(100.0, adx_score + regime_bonus))

    @staticmethod
    def _score_vwap(snapshot: MarketSnapshot, direction: str) -> float:
        spot = snapshot.index.ltp
        vwap = snapshot.index.vwap or spot
        if vwap <= 0:
            return 50.0
        # Distance above/below VWAP, scaled
        dist_pct = (spot - vwap) / vwap * 100
        if direction == "BULLISH":
            return max(0.0, min(100.0, 50 + dist_pct * 50))
        elif direction == "BEARISH":
            return max(0.0, min(100.0, 50 - dist_pct * 50))
        return 50.0

    @staticmethod
    def _score_momentum(snapshot: MarketSnapshot, regime: RegimeAssessment, direction: str) -> float:
        rsi = snapshot.index.rsi or 50.0
        adx_slope = snapshot.index.adx_slope or 0.0
        # RSI 50 + rising = strong momentum for bullish; 50 + falling = bearish
        if direction == "BULLISH":
            rsi_score = max(0.0, min(100.0, (rsi - 30) * 2.5))   # 30->0, 70->100
        elif direction == "BEARISH":
            rsi_score = max(0.0, min(100.0, (70 - rsi) * 2.5))
        else:
            rsi_score = 50.0
        # ADX slope adds 0-20
        slope_bonus = max(-20.0, min(20.0, adx_slope * 10.0))
        return max(0.0, min(100.0, rsi_score + slope_bonus))

    @staticmethod
    def _score_breadth(confirmation: ConfirmationScore, regime: RegimeAssessment, direction: str) -> float:
        # Without true breadth data, use confirmation score as proxy
        # Positive confirmation = broad confirmation
        return max(0.0, min(100.0, 50 + confirmation.score * 50))

    @staticmethod
    def _score_banknifty(confirmation: ConfirmationScore, direction: str) -> float:
        bn_state = confirmation.banknifty_confirmation.correlation_state
        if bn_state == "CONFIRMED":
            return 85.0
        elif bn_state == "DIVERGENT":
            return 20.0
        elif bn_state == "NEUTRAL":
            return 50.0
        return 30.0  # UNKNOWN — neutral-low

    @staticmethod
    def _score_oi(confirmation: ConfirmationScore, direction: str) -> float:
        ce = confirmation.oi_classification.ce_classification
        pe = confirmation.oi_classification.pe_classification
        if direction == "BULLISH":
            if ce == "LONG_BUILDUP":
                return 85.0
            elif pe == "SHORT_BUILDUP":
                return 75.0
            elif ce == "LONG_UNWINDING":
                return 25.0
            elif pe == "SHORT_COVERING":
                return 60.0
        elif direction == "BEARISH":
            if pe == "LONG_BUILDUP":
                return 85.0
            elif ce == "SHORT_BUILDUP":
                return 75.0
            elif pe == "LONG_UNWINDING":
                return 25.0
        return 50.0  # NEUTRAL / UNKNOWN

    @staticmethod
    def _score_vix(confirmation: ConfirmationScore, direction: str) -> float:
        val = confirmation.vix_valuation.valuation
        if val == "CHEAP":
            return 90.0      # great for long premium
        elif val == "FAIR":
            return 65.0
        elif val == "EXPENSIVE":
            return 25.0     # IV crush risk
        return 50.0  # UNKNOWN

    @staticmethod
    def _classify_state(composite: float, direction: str) -> PositionState:
        if composite >= THRESHOLD_CONFIDENT:
            return PositionState.CONFIDENT
        elif composite >= THRESHOLD_CAUTIOUS:
            return PositionState.CAUTIOUS
        elif composite >= THRESHOLD_REDUCE:
            return PositionState.REDUCE
        elif composite >= THRESHOLD_EXIT:
            return PositionState.EXIT
        else:
            return PositionState.EXIT
