"""Order manager — paper mode first, live mode wired in Phase 9.

In paper mode:
  * Order 'fills' immediately at the current quote mid.
  * Slippage is applied using the cost model.
  * Position is held in memory and journal is written.

In live mode (Phase 9):
  * Order is submitted via broker adapter.
  * Fill is confirmed by polling order status.
  * Position is reconciled with broker book.

Both modes share the SAME decision engine — only execution differs.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional

from ..models import OptionQuote, Position, RunMode


@dataclass
class OrderRequest:
    option: Optional[OptionQuote] = None     # single-leg
    long_leg: Optional[OptionQuote] = None   # multi-leg (Phase 10+)
    short_leg: Optional[OptionQuote] = None  # multi-leg (Phase 10+)
    side: str = "BUY"
    lots: int = 1
    lot_size: int = 75  # default; should be overridden by get_lot_size() at call sites
    strategy: str = ""
    stop_loss: Optional[float] = None
    take_profit: Optional[float] = None
    reason: str = ""
    # For spreads:
    net_debit: Optional[float] = None          # per unit
    spread_width: Optional[float] = None
    max_loss: Optional[float] = None
    max_gain: Optional[float] = None
    breakeven: Optional[float] = None


@dataclass
class OrderResult:
    success: bool
    order_id: str
    fill_price: Optional[float] = None
    filled_quantity: int = 0
    error: Optional[str] = None
    timestamp: datetime = field(default_factory=datetime.utcnow)
    slippage_applied: float = 0.0


class OrderManager:
    """Submits orders. Paper mode fills instantly at mid; live mode (Phase 9)
    delegates to broker adapter."""

    def __init__(self, mode: RunMode, broker=None):
        self._mode = mode
        self._broker = broker
        # Paper mode in-memory position book
        self._positions: list[Position] = []

    @property
    def mode(self) -> RunMode:
        return self._mode

    @property
    def positions(self) -> list[Position]:
        return list(self._positions)

    def submit(self, req: OrderRequest) -> OrderResult:
        """Submit an order. Paper: instant fill at mid with slippage applied."""
        order_id = f"PAPER-{uuid.uuid4().hex[:12]}" if self._mode == RunMode.PAPER else f"LIVE-{uuid.uuid4().hex[:12]}"

        if self._mode == RunMode.PAPER:
            return self._paper_fill(req, order_id)
        if self._mode == RunMode.LIVE:
            return self._live_submit(req, order_id)
        return OrderResult(
            success=False, order_id=order_id,
            error=f"unsupported run mode: {self._mode}",
        )

    def close_position(self, position: Position, exit_price: Optional[float] = None) -> OrderResult:
        """Close an existing paper position (full close)."""
        price = exit_price or position.current_price or position.entry_price
        # Apply slippage on exit too (1 point against us)
        slipped = max(0.0, price - 1.0)
        position.current_price = slipped
        position.status = "EXITED"
        lot_size = self._lot_size_for_position(position)
        return OrderResult(
            success=True,
            order_id=f"PAPER-EXIT-{uuid.uuid4().hex[:12]}",
            fill_price=slipped,
            filled_quantity=position.lots * lot_size,
            slippage_applied=1.0,
        )

    def partial_close(self, position: Position, lots_to_close: int, exit_price: Optional[float] = None) -> OrderResult:
        """Partially close a position — reduce lots but keep position OPEN.

        Used by the REDUCE thesis state:
          - If position has 2+ lots and thesis enters REDUCE, close half (round down)
          - If position has 1 lot, REDUCE should call close_position() instead

        Returns OrderResult for the closed portion. The remaining lots stay
        in the original Position object with updated state.
        """
        if lots_to_close >= position.lots:
            # Can't partially close more than we have — full close instead
            return self.close_position(position, exit_price)

        price = exit_price or position.current_price or position.entry_price
        slipped = max(0.0, price - 1.0)
        lot_size = self._lot_size_for_position(position)

        # Record the closed portion's P&L for journaling
        closed_qty = lots_to_close * lot_size
        # The position keeps tracking the remaining lots
        original_lots = position.lots
        position.lots = original_lots - lots_to_close

        # Adjust unrealised P&L to reflect remaining position size
        if position.unrealised_pnl != 0 and original_lots > 0:
            position.unrealised_pnl = position.unrealised_pnl * (position.lots / original_lots)

        return OrderResult(
            success=True,
            order_id=f"PAPER-PARTIAL-{uuid.uuid4().hex[:12]}",
            fill_price=slipped,
            filled_quantity=closed_qty,
            slippage_applied=1.0,
        )

    # ---- internal ----
    def _paper_fill(self, req: OrderRequest, order_id: str) -> OrderResult:
        # Phase 10: multi-leg spread
        if req.long_leg is not None and req.short_leg is not None:
            return self._paper_fill_spread(req, order_id)
        # Single-leg
        q = req.option
        if q is None or q.ltp <= 0:
            return OrderResult(
                success=False, order_id=order_id,
                error="option LTP invalid — cannot paper-fill",
            )
        # Mid-price fill with conservative slippage of 1 point against us
        mid = ((q.bid or q.ltp) + (q.ask or q.ltp)) / 2 if (q.bid and q.ask) else q.ltp
        # FIX: we pay 1 point OVER mid (slippage is adverse to us)
        # Previously this subtracted 1pt (giving us a better fill) — inverted.
        fill = max(0.05, mid + 1.0)   # we pay 1 point over mid
        qty = req.lots * req.lot_size

        position = Position(
            strategy=req.strategy if req.strategy else "LONG_CALL",
            option=q,
            lots=req.lots,
            entry_price=fill,
            entry_time=datetime.utcnow(),
            side=req.side,
            stop_loss=req.stop_loss,
            take_profit=req.take_profit,
            current_price=fill,
            unrealised_pnl=0.0,
        )
        self._positions.append(position)
        return OrderResult(
            success=True, order_id=order_id,
            fill_price=fill,
            filled_quantity=qty,
            slippage_applied=1.0,
        )

    def _paper_fill_spread(self, req: OrderRequest, order_id: str) -> OrderResult:
        """Paper-fill a 2-leg spread.

        For a debit spread:
          - Buy long leg at ask (we pay)
          - Sell short leg at bid (we receive)
          - Net debit = long_fill - short_fill
        """
        long_q = req.long_leg
        short_q = req.short_leg
        if long_q is None or short_q is None:
            return OrderResult(success=False, order_id=order_id, error="missing legs")
        if long_q.ltp <= 0:
            return OrderResult(success=False, order_id=order_id, error="long leg LTP invalid")
        if short_q.ltp <= 0:
            return OrderResult(success=False, order_id=order_id, error="short leg LTP invalid")

        # Apply slippage: we pay 1pt extra on long, receive 1pt less on short
        long_fill = max(0.05, long_q.ltp + 1.0)
        short_fill = max(0.05, short_q.ltp - 1.0)
        net_debit = max(0.01, long_fill - short_fill)
        qty = req.lots * req.lot_size

        position = Position(
            strategy=req.strategy if req.strategy else "DEBIT_SPREAD",
            option=long_q,                         # backward-compat: long leg as primary
            long_leg=long_q,
            short_leg=short_q,
            lots=req.lots,
            entry_price=net_debit,                  # net debit per unit
            entry_time=datetime.utcnow(),
            side="BUY",
            stop_loss=req.stop_loss,
            take_profit=req.take_profit,
            current_price=net_debit,
            long_leg_current=long_fill,
            short_leg_current=short_fill,
            spread_width=req.spread_width,
            max_loss=req.max_loss,
            max_gain=req.max_gain,
            breakeven=req.breakeven,
            unrealised_pnl=0.0,
        )
        self._positions.append(position)
        return OrderResult(
            success=True, order_id=order_id,
            fill_price=net_debit,
            filled_quantity=qty,
            slippage_applied=2.0,   # 1pt per leg
        )

    def _live_submit(self, req: OrderRequest, order_id: str) -> OrderResult:
        """Phase 9 — submit via broker adapter, poll status, confirm fill."""
        if self._broker is None:
            return OrderResult(
                success=False, order_id=order_id,
                error="live broker not configured (Phase 9)",
            )
        # TODO Phase 9: implement using self._broker.place_order(...)
        return OrderResult(
            success=False, order_id=order_id,
            error="live execution not implemented in Phase 1-5",
        )

    # ---- position management helpers ----
    def mark_to_market(self, current_quotes: dict[str, OptionQuote]) -> None:
        """Update unrealised PnL on all open paper positions."""
        for p in self._positions:
            if p.status != "OPEN":
                continue
            q = current_quotes.get(p.option.symbol)
            if not q:
                continue
            p.current_price = q.ltp
            diff = (q.ltp - p.entry_price) * (1 if p.side == "BUY" else -1)
            p.unrealised_pnl = diff * p.lots * self._lot_size_for_position(p)

    def open_positions_count(self) -> int:
        return sum(1 for p in self._positions if p.status == "OPEN")

    @staticmethod
    def _lot_size_for_position(_pos) -> int:
        """Get lot size from config (single source of truth)."""
        try:
            from ..utils.config_helpers import get_lot_size
            return get_lot_size()
        except Exception:
            return 75

    def total_unrealised_pnl(self) -> float:
        return sum(p.unrealised_pnl for p in self._positions if p.status == "OPEN")

    def find_position_for_strategy(self, strategy: str) -> Optional[Position]:
        for p in self._positions:
            if p.status == "OPEN" and p.strategy == strategy:
                return p
        return None
