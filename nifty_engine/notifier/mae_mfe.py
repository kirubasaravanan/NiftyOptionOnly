"""MAE/MFE tracker — Maximum Adverse / Favourable Excursion.

Per spec:
  MAE = max loss incurred before trade eventually closed (winning or losing)
  MFE = max profit captured before trade was exited

These tell us:
  - Whether stops are too tight (high MAE before eventual winner)
  - Whether exits are leaving too much profit behind (MFE >> actual exit profit)
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional

from ..models import Position


@dataclass
class MAEMFERecord:
    """Per-trade MAE/MFE stats."""
    trade_id: str
    entry_price: float
    exit_price: Optional[float] = None
    entry_time: Optional[datetime] = None
    exit_time: Optional[datetime] = None
    # In INR — track the worst unrealised loss and best unrealised profit
    mae_inr: float = 0.0          # always <= 0 (adverse = loss)
    mfe_inr: float = 0.0          # always >= 0 (favourable = profit)
    mae_pct_of_premium: float = 0.0
    mfe_pct_of_premium: float = 0.0
    # Final outcome
    realized_pnl: float = 0.0
    # How much of MFE we captured
    capture_rate: float = 0.0     # realized / mfe
    # Time of MAE / MFE
    mae_time: Optional[datetime] = None
    mfe_time: Optional[datetime] = None
    # Underlying spot at MAE / MFE
    mae_spot: Optional[float] = None
    mfe_spot: Optional[float] = None

    def to_dict(self) -> dict:
        return {
            "trade_id": self.trade_id,
            "entry_price": self.entry_price,
            "exit_price": self.exit_price,
            "mae_inr": round(self.mae_inr, 2),
            "mfe_inr": round(self.mfe_inr, 2),
            "mae_pct_of_premium": round(self.mae_pct_of_premium * 100, 2),
            "mfe_pct_of_premium": round(self.mfe_pct_of_premium * 100, 2),
            "realized_pnl": round(self.realized_pnl, 2),
            "capture_rate": round(self.capture_rate * 100, 2),
        }


class MAEMFETracker:
    """Per-position MAE/MFE tracker.

    Lifecycle:
      init_at_entry(position) -> start tracking
      update(position, current_price, spot) -> update MAE/MFE each cycle
      finalize(position, exit_price, realized_pnl) -> return MAEMFERecord
    """

    def __init__(self) -> None:
        self._trade_id: Optional[str] = None
        self._entry_price: float = 0.0
        self._entry_time: Optional[datetime] = None
        self._mae: float = 0.0
        self._mfe: float = 0.0
        self._mae_time: Optional[datetime] = None
        self._mfe_time: Optional[datetime] = None
        self._mae_spot: Optional[float] = None
        self._mfe_spot: Optional[float] = None
        self._lot_size = 75

    def init_at_entry(self, position: Position, trade_id: Optional[str] = None) -> None:
        self._trade_id = trade_id or f"trade-{int(position.entry_time.timestamp() if hasattr(position.entry_time, 'timestamp') else 0)}"
        self._entry_price = position.entry_price
        self._entry_time = position.entry_time
        self._mae = 0.0
        self._mfe = 0.0
        self._mae_time = None
        self._mfe_time = None
        self._mae_spot = None
        self._mfe_spot = None

    def update(self, position: Position, current_price: float, spot: float, ts: Optional[datetime] = None) -> None:
        """Called each cycle while position is open."""
        ts = ts or datetime.utcnow()
        qty = position.lots * self._lot_size
        unrealised = (current_price - self._entry_price) * qty
        if position.side == "SELL":
            unrealised = -unrealised

        if unrealised < self._mae:
            self._mae = unrealised
            self._mae_time = ts
            self._mae_spot = spot
        if unrealised > self._mfe:
            self._mfe = unrealised
            self._mfe_time = ts
            self._mfe_spot = spot

    def finalize(self, position: Position, exit_price: float, realized_pnl: float) -> MAEMFERecord:
        qty = position.lots * self._lot_size
        premium_cost = self._entry_price * qty
        mae_pct = (self._mae / premium_cost) if premium_cost > 0 else 0
        mfe_pct = (self._mfe / premium_cost) if premium_cost > 0 else 0
        capture = (realized_pnl / self._mfe) if self._mfe > 0 else 0

        return MAEMFERecord(
            trade_id=self._trade_id or "unknown",
            entry_price=self._entry_price,
            exit_price=exit_price,
            entry_time=self._entry_time,
            exit_time=datetime.utcnow(),
            mae_inr=self._mae,
            mfe_inr=self._mfe,
            mae_pct_of_premium=mae_pct,
            mfe_pct_of_premium=mfe_pct,
            realized_pnl=realized_pnl,
            capture_rate=max(0.0, min(1.0, capture)),
            mae_time=self._mae_time,
            mfe_time=self._mfe_time,
            mae_spot=self._mae_spot,
            mfe_spot=self._mfe_spot,
        )
