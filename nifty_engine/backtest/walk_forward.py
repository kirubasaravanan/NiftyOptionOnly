"""Walk-forward validation framework (Phase 7).

Avoids overfitting by:
  1. Split historical data into TRAIN + VALIDATE + OUT-OF-SAMPLE windows
  2. For each window: optimise params on TRAIN, evaluate on VALIDATE
  3. Roll forward; final performance = average across all OUT-OF-SAMPLE windows
  4. Parameters must NOT be optimised on the same data used to judge performance
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Optional

from .engine import BacktestEngine, BacktestConfig, BacktestResult


@dataclass
class WalkForwardResult:
    windows: list[dict] = field(default_factory=list)    # per-window results
    total_oos_trades: int = 0
    oos_win_rate: float = 0.0
    oos_expectancy: float = 0.0
    oos_total_return_pct: float = 0.0
    oos_max_drawdown_pct: float = 0.0
    robustness_score: float = 0.0       # 0-1; higher = more stable
    notes: list[str] = field(default_factory=list)


class WalkForwardValidator:
    """Run rolling-window backtests to verify strategy robustness."""

    def __init__(
        self,
        start_date: date,
        end_date: date,
        train_days: int = 90,
        validate_days: int = 30,
        step_days: int = 30,
        capital: float = 1_000_000.0,
    ) -> None:
        self.start = start_date
        self.end = end_date
        self.train_days = train_days
        self.validate_days = validate_days
        self.step_days = step_days
        self.capital = capital

    def run(self) -> WalkForwardResult:
        windows = []
        cursor = self.start
        while cursor + timedelta(days=self.train_days + self.validate_days) <= self.end:
            train_start = cursor
            train_end = train_start + timedelta(days=self.train_days)
            val_start = train_end
            val_end = val_start + timedelta(days=self.validate_days)

            # Train window — used for parameter optimisation (Phase 7+)
            # In Phase 6 we just verify the engine runs end-to-end on each window
            # without crashing and produces sensible trade counts.
            bt_cfg = BacktestConfig(
                start_date=val_start,
                end_date=min(val_end, self.end),
                capital=self.capital,
            )
            engine = BacktestEngine(bt_cfg)
            result = engine.run()
            windows.append({
                "train_start": train_start.isoformat(),
                "train_end": train_end.isoformat(),
                "validate_start": val_start.isoformat(),
                "validate_end": val_end.isoformat(),
                "oos_trades": result.trade_count,
                "oos_return_pct": result.total_return_pct,
                "oos_max_dd_pct": result.max_drawdown_pct,
                "oos_win_rate": result.win_rate,
                "oos_expectancy": result.expectancy,
                "error": result.error,
            })
            cursor = cursor + timedelta(days=self.step_days)

        # Aggregate
        ok_windows = [w for w in windows if not w.get("error")]
        total_trades = sum(w["oos_trades"] for w in ok_windows)
        win_rates = [w["oos_win_rate"] for w in ok_windows if w["oos_trades"] > 0]
        exps = [w["oos_expectancy"] for w in ok_windows if w["oos_trades"] > 0]
        rets = [w["oos_return_pct"] for w in ok_windows]
        dds = [w["oos_max_dd_pct"] for w in ok_windows]

        avg_win = sum(win_rates) / max(1, len(win_rates))
        avg_exp = sum(exps) / max(1, len(exps))
        avg_ret = sum(rets) / max(1, len(rets))
        avg_dd = sum(dds) / max(1, len(dds))

        # Robustness score: positive expectancy + low variance in returns
        import statistics
        ret_var = statistics.pvariance(rets) if len(rets) > 1 else 0.0
        # Higher return + lower variance => higher score
        robustness = max(0.0, min(1.0, (avg_ret / 10.0) / (1.0 + ret_var / 100.0)))

        notes = []
        if total_trades < 10:
            notes.append("LOW TRADE COUNT — strategy may be too restrictive")
        if avg_dd > 15:
            notes.append("HIGH DRAWDOWN — risk limits may need tightening")
        if ret_var > 100:
            notes.append("HIGH RETURN VARIANCE — strategy unstable across windows")

        return WalkForwardResult(
            windows=windows,
            total_oos_trades=total_trades,
            oos_win_rate=avg_win,
            oos_expectancy=avg_exp,
            oos_total_return_pct=avg_ret,
            oos_max_drawdown_pct=avg_dd,
            robustness_score=robustness,
            notes=notes,
        )
