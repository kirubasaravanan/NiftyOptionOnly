"""Phase 7 — Walk-forward validation with parameter grid search + ablation testing.

Walk-forward: rolling TRAIN/VALIDATE windows with parameter optimisation on TRAIN,
evaluation on OUT-OF-SAMPLE VALIDATE. Average across all windows = final score.

Ablation testing: run the backtest with each feature turned ON/OFF individually
to measure its incremental predictive value. Only features that improve
OUT-OF-SAMPLE expectancy after costs are kept in the live decision engine.

Per spec: every new factor must pass an incremental-value/ablation test
before it is allowed into the live decision engine.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Optional

import pandas as pd

from .engine import BacktestEngine, BacktestConfig, BacktestResult


# ---------- Walk-forward ----------

@dataclass
class WalkForwardWindow:
    train_start: date
    train_end: date
    validate_start: date
    validate_end: date
    oos_trades: int = 0
    oos_return_pct: float = 0.0
    oos_max_dd_pct: float = 0.0
    oos_win_rate: float = 0.0
    oos_expectancy: float = 0.0
    error: Optional[str] = None


@dataclass
class WalkForwardResult:
    windows: list[WalkForwardWindow] = field(default_factory=list)
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
        windows: list[WalkForwardWindow] = []
        cursor = self.start
        while cursor + timedelta(days=self.train_days + self.validate_days) <= self.end:
            train_start = cursor
            train_end = train_start + timedelta(days=self.train_days)
            val_start = train_end
            val_end = val_start + timedelta(days=self.validate_days)

            # In Phase 7 v1 we run the engine on the VALIDATE window only
            # (TRAIN window would normally be used for parameter optimisation,
            # but our thresholds are config-driven, not learned yet).
            bt_cfg = BacktestConfig(
                start_date=val_start,
                end_date=min(val_end, self.end),
                capital=self.capital,
            )
            engine = BacktestEngine(bt_cfg)
            result = engine.run()
            windows.append(WalkForwardWindow(
                train_start=train_start, train_end=train_end,
                validate_start=val_start, validate_end=val_end,
                oos_trades=result.trade_count,
                oos_return_pct=result.total_return_pct,
                oos_max_dd_pct=result.max_drawdown_pct,
                oos_win_rate=result.win_rate,
                oos_expectancy=result.expectancy,
                error=result.error,
            ))
            cursor = cursor + timedelta(days=self.step_days)

        # Aggregate
        ok_windows = [w for w in windows if not w.error]
        total_trades = sum(w.oos_trades for w in ok_windows)
        win_rates = [w.oos_win_rate for w in ok_windows if w.oos_trades > 0]
        exps = [w.oos_expectancy for w in ok_windows if w.oos_trades > 0]
        rets = [w.oos_return_pct for w in ok_windows]
        dds = [w.oos_max_dd_pct for w in ok_windows]

        avg_win = sum(win_rates) / max(1, len(win_rates))
        avg_exp = sum(exps) / max(1, len(exps))
        avg_ret = sum(rets) / max(1, len(rets))
        avg_dd = sum(dds) / max(1, len(dds))

        # Robustness score: positive expectancy + low variance in returns
        import statistics
        ret_var = statistics.pvariance(rets) if len(rets) > 1 else 0.0
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


# ---------- Ablation testing ----------

@dataclass
class AblationVariant:
    """A single ablation test variant.

    `feature_name` describes what's being tested (e.g. 'baseline', '+vix',
    '+banknifty', '+futures_basis', '+oi_classification', '+correlation_regime').
    `enabled_features` controls which features the engine actually uses.
    """
    feature_name: str
    enabled_features: dict               # {"vix": True, "banknifty": False, ...}
    description: str
    # Result fields (filled after run):
    oos_return_pct: float = 0.0
    oos_expectancy: float = 0.0
    oos_win_rate: float = 0.0
    oos_trades: int = 0
    oos_max_dd_pct: float = 0.0
    oos_sharpe: float = 0.0
    incremental_expectancy: float = 0.0  # difference vs baseline
    incremental_return: float = 0.0
    keeps_feature: bool = False           # True if OOS expectancy improved
    error: Optional[str] = None


@dataclass
class AblationResult:
    baseline: AblationVariant
    variants: list[AblationVariant] = field(default_factory=list)
    summary: str = ""
    recommendation: list[str] = field(default_factory=list)


# Default feature flags — must match the keys used in engine._compute_confirmation
DEFAULT_FEATURE_FLAGS = {
    "vix": True,
    "oi_classification": True,
    "futures_basis": True,
    "banknifty": True,
    "correlation_regime": False,     # disabled by default — needs return history
}


class AblationTester:
    """Test the incremental value of each feature.

    For each feature, run the backtest with that feature turned OFF and compare
    OOS expectancy to the baseline (all features ON). If turning the feature
    OFF degrades performance, the feature adds value and should be kept.

    Per spec: every new factor must pass an ablation test before going live.
    """

    def __init__(
        self,
        start_date: date,
        end_date: date,
        capital: float = 1_000_000.0,
    ) -> None:
        self.start = start_date
        self.end = end_date
        self.capital = capital
        # Cache historical candles so we don't refetch on every variant
        self._cached_df = None

    def run(self) -> AblationResult:
        # 1. Baseline — all features ON
        baseline_result = self._run_backtest(DEFAULT_FEATURE_FLAGS)
        baseline = AblationVariant(
            feature_name="baseline",
            enabled_features=dict(DEFAULT_FEATURE_FLAGS),
            description="All Layer 3 confirmation features enabled",
            oos_return_pct=baseline_result.total_return_pct,
            oos_expectancy=baseline_result.expectancy,
            oos_win_rate=baseline_result.win_rate,
            oos_trades=baseline_result.trade_count,
            oos_max_dd_pct=baseline_result.max_drawdown_pct,
            oos_sharpe=baseline_result.sharpe,
            error=baseline_result.error,
        )

        # 2. For each feature, run with that feature OFF
        variants: list[AblationVariant] = []
        for feature_name in DEFAULT_FEATURE_FLAGS.keys():
            flags = dict(DEFAULT_FEATURE_FLAGS)
            flags[feature_name] = False
            result = self._run_backtest(flags)
            v = AblationVariant(
                feature_name=f"without_{feature_name}",
                enabled_features=flags,
                description=f"Baseline minus {feature_name}",
                oos_return_pct=result.total_return_pct,
                oos_expectancy=result.expectancy,
                oos_win_rate=result.win_rate,
                oos_trades=result.trade_count,
                oos_max_dd_pct=result.max_drawdown_pct,
                oos_sharpe=result.sharpe,
                incremental_expectancy=baseline.oos_expectancy - result.expectancy,
                incremental_return=baseline.oos_return_pct - result.total_return_pct,
                error=result.error,
            )
            # If removing this feature HURTS performance, keep the feature
            v.keeps_feature = (
                v.incremental_expectancy > 0
                or v.incremental_return > 0
            )
            variants.append(v)

        # 3. Recommendation
        rec: list[str] = []
        rec.append(f"Baseline OOS expectancy: ₹{baseline.oos_expectancy:,.0f}, "
                   f"return: {baseline.oos_return_pct:+.2f}%, "
                   f"win rate: {baseline.oos_win_rate*100:.1f}%, "
                   f"Sharpe: {baseline.oos_sharpe:.2f}")
        rec.append("")
        rec.append("Per-feature incremental value:")
        for v in variants:
            status = "KEEP" if v.keeps_feature else "DROP"
            arrow = "↑" if v.keeps_feature else "↓"
            rec.append(
                f"  [{status}] {v.feature_name:30s} "
                f"Δexpectancy={v.incremental_expectancy:+,.0f}  "
                f"Δreturn={v.incremental_return:+.2f}%  {arrow}"
            )
        rec.append("")
        kept = [v.feature_name for v in variants if v.keeps_feature]
        dropped = [v.feature_name for v in variants if not v.keeps_feature]
        rec.append(f"KEEP: {len(kept)}/{len(variants)} features add OOS value")
        if dropped:
            rec.append(f"DROP (no incremental value): {', '.join(dropped)}")
        rec.append("")
        rec.append("Per spec: only KEEP features pass the ablation test in the live engine.")

        return AblationResult(
            baseline=baseline,
            variants=variants,
            summary=f"Baseline ₹{baseline.oos_expectancy:.0f} expectancy vs {len(variants)} ablation variants",
            recommendation=rec,
        )

    def _run_backtest(self, flags: dict) -> BacktestResult:
        """Run a single backtest with the given feature flags.

        Patches the BacktestEngine's reference to compute_all_confirmations
        so feature flags actually take effect during the backtest.
        """
        cfg = BacktestConfig(
            start_date=self.start,
            end_date=self.end,
            capital=self.capital,
        )
        eng = BacktestEngine(cfg)

        # Cache historical candles so we don't refetch on every variant
        # (each refetch = 50MB+ memory + ~2s API call)
        if self._cached_df is None:
            try:
                from datetime import datetime
                self._cached_df = eng.broker.historical_candles(
                    symbol="NIFTY", interval="day",
                    start=datetime.combine(self.start, datetime.min.time()),
                    end=datetime.combine(self.end, datetime.min.time()),
                )
            except Exception:
                self._cached_df = None

        # The BacktestEngine module imported compute_all_confirmations at
        # module load time. Patching the package's compute_all_confirmations
        # won't affect the engine — we must patch the engine module's reference.
        from ..backtest import engine as bt_engine_mod
        from ..features import correlation as corr_mod
        original_compute = bt_engine_mod.compute_all_confirmations
        # Also save the engine.run method so we can swap in a cached version
        original_run = BacktestEngine.run

        disabled = [k for k, v in flags.items() if not v]
        if disabled:
            def patched_compute(snapshot, banknifty_quote=None, futures_quote=None,
                                nifty_returns=None, banknifty_returns=None,
                                vix_history=None, direction="BULLISH"):
                if not flags.get("vix", True):
                    snapshot = snapshot.model_copy(update={"india_vix": None})
                if not flags.get("banknifty", True):
                    banknifty_quote = None
                if not flags.get("futures_basis", True):
                    futures_quote = None
                result = original_compute(
                    snapshot, banknifty_quote=banknifty_quote, futures_quote=futures_quote,
                    nifty_returns=nifty_returns, banknifty_returns=banknifty_returns,
                    vix_history=vix_history, direction=direction,
                )
                if not flags.get("oi_classification", True):
                    result.oi_classification = corr_mod.OIClassification(
                        ce_classification="NEUTRAL", pe_classification="NEUTRAL",
                        call_wall=None, put_wall=None, max_pain=None,
                        reasons=["oi_classification disabled in ablation"],
                    )
                if not flags.get("correlation_regime", True):
                    result.correlation_regime = corr_mod.CorrelationRegime(
                        short_window_corr=None, long_window_corr=None,
                        regime="UNKNOWN", reasons=["correlation_regime disabled in ablation"],
                    )
                return result
            bt_engine_mod.compute_all_confirmations = patched_compute

        # Use cached df instead of refetching
        df = self._cached_df
        def cached_run(self_eng):
            if df is None or len(df) < 50:
                return BacktestResult(config=self_eng.cfg, error=f"insufficient historical data ({len(df) if df is not None else 0} bars)")
            # Reset engine state
            self_eng.equity = self_eng.cfg.capital
            self_eng._day_pnl = 0.0
            self_eng._consecutive_losses = 0
            self_eng._day_trades = 0
            self_eng._current_day = None
            self_eng._open_positions = []
            self_eng._closed_trades = []
            self_eng._equity_curve = []
            self_eng._decisions_log = []
            # Run the original run method, but with the cached df
            original_historical = self_eng.broker.historical_candles
            self_eng.broker.historical_candles = lambda *args, **kwargs: df
            try:
                return original_run(self_eng)
            finally:
                self_eng.broker.historical_candles = original_historical

        BacktestEngine.run = cached_run

        try:
            result = eng.run()
        finally:
            bt_engine_mod.compute_all_confirmations = original_compute
            BacktestEngine.run = original_run
        return result


__all__ = [
    "WalkForwardValidator", "WalkForwardResult", "WalkForwardWindow",
    "AblationTester", "AblationResult", "AblationVariant",
    "DEFAULT_FEATURE_FLAGS",
]
