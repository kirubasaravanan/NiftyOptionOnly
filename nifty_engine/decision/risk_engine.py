"""Risk engine — position sizing, daily-loss caps, cooldowns, emergency stops.

Returns a RiskDecision that says: allowed/not-allowed, max_lots permitted,
and the stop-loss & take-profit levels for the chosen option.

All thresholds come from config/risk.yaml. The risk engine NEVER allows
martingale or averaging into losers.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Optional

from ..config import load as load_config
from ..models import (
    DecisionAction, MarketSnapshot, OptionQuote, StrategyEvaluation,
)


@dataclass
class RiskDecision:
    allowed: bool
    reason: str
    max_lots: int = 0
    stop_loss: Optional[float] = None
    take_profit: Optional[float] = None
    max_premium_exposure: float = 0.0


class RiskEngine:
    """Stateful — tracks daily PnL, consecutive losses, cooldowns."""

    # Config name per instrument (2026-08-18) — see StrategyBase for the
    # same pattern; other instruments read risk_{instrument}.yaml (literal
    # copies pending independent validation).
    _CONFIG_NAME_BY_INSTRUMENT = {
        "nifty": "risk",
        "banknifty": "risk_banknifty",
        "sensex": "risk_sensex",
    }

    def __init__(self, capital: float = 1_000_000.0, instrument: str = "nifty") -> None:
        self._instrument = instrument.lower()
        config_name = self._CONFIG_NAME_BY_INSTRUMENT.get(self._instrument, "risk")
        self._cfg = load_config(config_name)
        self._capital = float(capital)
        self._day_pnl: float = 0.0
        self._day_trades: int = 0
        self._consecutive_losses: int = 0
        self._cooldown_until: Optional[datetime] = None
        self._open_exposure: float = 0.0
        self._day_start: Optional[datetime] = None

    # ---- public API ----
    def reset_day(self, now: datetime) -> None:
        """Called at session start."""
        self._day_pnl = 0.0
        self._day_trades = 0
        self._cooldown_until = None
        self._day_start = now

    def record_trade_result(self, net_pnl: float, now: datetime) -> None:
        """Called after a position closes."""
        self._day_pnl += net_pnl
        self._day_trades += 1
        if net_pnl < 0:
            self._consecutive_losses += 1
            if self._consecutive_losses >= self._cfg["cooldown"]["after_consecutive_losses"]:
                self._cooldown_until = now + timedelta(
                    minutes=self._cfg["cooldown"]["duration_minutes"]
                )
        else:
            self._consecutive_losses = 0

    def add_open_exposure(self, premium: float) -> None:
        self._open_exposure += premium

    def release_open_exposure(self, premium: float) -> None:
        self._open_exposure = max(0.0, self._open_exposure - premium)

    # ---- gate ----
    def evaluate(
        self,
        snapshot: MarketSnapshot,
        evaluation: StrategyEvaluation,
        option: Optional[OptionQuote],
        open_positions: int,
        now: datetime,
    ) -> RiskDecision:
        if not snapshot.data_valid:
            return RiskDecision(allowed=False, reason=f"data invalid: {snapshot.data_invalid_reason}")

        # Cooldown
        if self._cooldown_until and now < self._cooldown_until:
            return RiskDecision(
                allowed=False,
                reason=f"in cooldown until {self._cooldown_until.isoformat()} "
                       f"(consecutive losses hit limit)",
            )

        # Daily loss cap
        max_day_loss = self._capital * (self._cfg["daily"]["max_loss_pct"] / 100.0)
        if self._day_pnl <= -max_day_loss:
            return RiskDecision(
                allowed=False,
                reason=f"daily loss limit hit ({self._day_pnl:.0f} <= -{max_day_loss:.0f})",
            )

        # Max trades / day
        if self._day_trades >= self._cfg["daily"]["max_trades"]:
            return RiskDecision(
                allowed=False,
                reason=f"max trades/day hit ({self._day_trades})",
            )

        # Max open positions
        if open_positions >= self._cfg["portfolio"]["max_open_positions"]:
            return RiskDecision(
                allowed=False,
                reason=f"max open positions ({open_positions})",
            )

        if option is None:
            return RiskDecision(allowed=False, reason="no option provided to risk engine")

        # Per-trade risk cap
        max_risk_per_trade = self._capital * (self._cfg["per_trade"]["max_risk_pct"] / 100.0)
        max_premium_per_trade = self._capital * (self._cfg["per_trade"]["max_premium_pct"] / 100.0)
        # For long options, risk == premium
        per_unit_risk = option.ltp
        from ..utils.config_helpers import get_lot_size
        lot_size = get_lot_size(self._instrument)
        # compute max lots
        lots_by_risk = int(max_risk_per_trade // (per_unit_risk * lot_size)) if per_unit_risk > 0 else 0
        lots_by_premium = int(max_premium_per_trade // (per_unit_risk * lot_size)) if per_unit_risk > 0 else 0
        # Portfolio exposure cap
        remaining_exposure = max(
            0.0,
            self._capital * (self._cfg["portfolio"]["max_exposure_pct"] / 100.0) - self._open_exposure,
        )
        lots_by_portfolio = int(remaining_exposure // (per_unit_risk * lot_size)) if per_unit_risk > 0 else 0

        max_lots = max(0, min(lots_by_risk, lots_by_premium, lots_by_portfolio))
        max_lots = min(max_lots, 5)  # sanity cap for v1

        if max_lots < 1:
            return RiskDecision(
                allowed=False,
                reason=(
                    f"position size = 0 lots "
                    f"(risk_cap={lots_by_risk} prem_cap={lots_by_premium} "
                    f"port_cap={lots_by_portfolio})"
                ),
                max_lots=0,
            )

        # Stop-loss & take-profit — ATR-based, conservative defaults
        spot = snapshot.index.ltp
        atr = snapshot.index.atr or (spot * 0.005)
        # Stop on the underlying — exit if NIFTY moves 1 ATR against us
        # Take-profit at 2 ATR in our favour (R/R = 1:2)
        if evaluation.direction == "BULLISH":
            spot_stop = spot - atr
            spot_target = spot + 2 * atr
        elif evaluation.direction == "BEARISH":
            spot_stop = spot + atr
            spot_target = spot - 2 * atr
        else:
            spot_stop = spot
            spot_target = spot

        # Translate to option premium (rough — uses delta)
        delta = abs(option.delta) if option.delta is not None else 0.5
        stop_premium = max(0.05, option.ltp - delta * atr)
        target_premium = option.ltp + delta * 2 * atr

        return RiskDecision(
            allowed=True,
            reason=f"sized {max_lots} lot(s) within all risk caps",
            max_lots=max_lots,
            stop_loss=round(stop_premium, 2),
            take_profit=round(target_premium, 2),
            max_premium_exposure=per_unit_risk * lot_size * max_lots,
        )

    # ---- status ----
    def status(self) -> dict:
        return {
            "capital": self._capital,
            "day_pnl": self._day_pnl,
            "day_trades": self._day_trades,
            "consecutive_losses": self._consecutive_losses,
            "cooldown_until": self._cooldown_until.isoformat() if self._cooldown_until else None,
            "open_exposure": self._open_exposure,
        }
