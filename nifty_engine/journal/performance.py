"""Performance analytics — computed from journal data."""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from typing import Optional, TYPE_CHECKING

from ..models import TradeRecord

if TYPE_CHECKING:
    from . import TradeLogger


@dataclass
class PerformanceReport:
    total_trades: int = 0
    winners: int = 0
    losers: int = 0
    win_rate: float = 0.0
    gross_pnl: float = 0.0
    net_pnl: float = 0.0
    avg_winner: float = 0.0
    avg_loser: float = 0.0
    expectancy: float = 0.0
    profit_factor: float = 0.0
    max_drawdown: float = 0.0
    total_charges: float = 0.0
    total_slippage: float = 0.0
    avg_holding_minutes: float = 0.0
    by_strategy: dict = field(default_factory=dict)
    by_regime: dict = field(default_factory=dict)
    by_volatility: dict = field(default_factory=dict)


class PerformanceAnalytics:
    """Aggregates trades into a PerformanceReport."""

    def __init__(self, trade_logger: "TradeLogger") -> None:
        self._logger = trade_logger

    def report(self) -> PerformanceReport:
        trades = self._logger.all_trades()
        if not trades:
            return PerformanceReport()

        winners = [t for t in trades if t.net_pnl > 0]
        losers = [t for t in trades if t.net_pnl <= 0]
        gross = sum(t.gross_pnl for t in trades)
        net = sum(t.net_pnl for t in trades)
        charges = sum(t.charges for t in trades)
        slip = sum(t.slippage for t in trades)
        avg_win = sum(t.net_pnl for t in winners) / max(1, len(winners))
        avg_lose = sum(t.net_pnl for t in losers) / max(1, len(losers))
        expectancy = (len(winners) / len(trades)) * avg_win + (len(losers) / len(trades)) * avg_lose
        gross_profit = sum(t.net_pnl for t in winners)
        gross_loss = abs(sum(t.net_pnl for t in losers))
        profit_factor = gross_profit / gross_loss if gross_loss > 0 else float("inf")

        # max drawdown on cumulative PnL
        cum = 0.0
        peak = 0.0
        max_dd = 0.0
        for t in trades:
            cum += t.net_pnl
            if cum > peak:
                peak = cum
            dd = peak - cum
            if dd > max_dd:
                max_dd = dd

        hold_times = [t.holding_minutes for t in trades if t.holding_minutes is not None]
        avg_hold = sum(hold_times) / max(1, len(hold_times))

        return PerformanceReport(
            total_trades=len(trades),
            winners=len(winners),
            losers=len(losers),
            win_rate=len(winners) / len(trades),
            gross_pnl=gross,
            net_pnl=net,
            avg_winner=avg_win,
            avg_loser=avg_lose,
            expectancy=expectancy,
            profit_factor=profit_factor,
            max_drawdown=max_dd,
            total_charges=charges,
            total_slippage=slip,
            avg_holding_minutes=avg_hold,
            by_strategy=self._group(trades, lambda t: t.strategy.value),
            by_regime=self._group(trades, lambda t: t.regime_at_entry.value),
            by_volatility=self._group(trades, lambda t: t.vol_regime_at_entry.value),
        )

    @staticmethod
    def _group(trades: list[TradeRecord], key_fn) -> dict:
        out: dict[str, dict] = defaultdict(lambda: {"count": 0, "net_pnl": 0.0})
        for t in trades:
            k = key_fn(t)
            out[k]["count"] += 1
            out[k]["net_pnl"] += t.net_pnl
        return dict(out)
