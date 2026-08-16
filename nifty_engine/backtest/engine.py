"""Backtesting engine.

Replays historical candles through the SAME decision pipeline used in
paper/live mode. No look-ahead bias: each cycle uses only past data.

Strategy:
  1. Fetch daily or intraday NIFTY candles via DhanHQ HistoricalData API.
  2. Walk through each candle, build a MarketSnapshot from historical OHLCV.
  3. Run regime + strategy selector + risk engine.
  4. Track positions, apply cost model on entry/exit.
  5. Emit trades + equity curve + performance metrics.

NOTE: Historical option-chain data is hard to obtain via DhanHQ API.
For Phase 6 v1 we run the backtest on the UNDERLYING (NIFTY) only — the
engine still evaluates Long Call / Long Put strategies and produces
expected_net_value estimates using a simplified Black-Scholes option
pricing model so strategy evaluation logic is exercised end-to-end.
Once historical option-chain data is available, we plug it in unchanged.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, date
from pathlib import Path
from typing import Optional

import pandas as pd
import numpy as np

from ..config import load as load_config
from ..data import DhanBroker
from ..data.broker_interface import NoDataError
from ..decision import (
    OptionSelector, PositionManager, RegimeEngineRunner,
    RiskEngine, StrategySelector,
)
from ..execution import CostModel
from ..features.technical import TechnicalCalculator, attach_features_to_index
from ..features.market_regime import RegimeEngine
from ..features.volatility import VolatilityCalculator
from ..models import (
    Decision, DecisionAction, IndexQuote, IndiaVIX, MarketSnapshot,
    OptionQuote, OptionType, Position, RunMode, StrategyName,
    TradeRecord, VolatilityRegime, MarketRegime,
)
from ..utils.time_utils import to_ist


# ---------- Black-Scholes for synthetic option pricing on backtest ----------
def _norm_cdf(x: float) -> float:
    from math import erf, sqrt
    return 0.5 * (1.0 + erf(x / sqrt(2.0)))


def bs_option_price(spot, strike, t, iv, r, opt_type: OptionType) -> float:
    """Black-Scholes price for a European option."""
    from math import log, sqrt, exp
    if t <= 0 or iv <= 0:
        # Intrinsic value at expiry
        if opt_type == OptionType.CE:
            return max(0.0, spot - strike)
        return max(0.0, strike - spot)
    d1 = (log(spot / strike) + (r + 0.5 * iv * iv) * t) / (iv * sqrt(t))
    d2 = d1 - iv * sqrt(t)
    if opt_type == OptionType.CE:
        return spot * _norm_cdf(d1) - strike * exp(-r * t) * _norm_cdf(d2)
    return strike * exp(-r * t) * _norm_cdf(-d2) - spot * _norm_cdf(-d1)


def bs_greeks(spot, strike, t, iv, r, opt_type: OptionType) -> dict:
    from math import log, sqrt, exp
    if t <= 0 or iv <= 0:
        return {"delta": 0.0, "gamma": 0.0, "theta": 0.0, "vega": 0.0}
    d1 = (log(spot / strike) + (r + 0.5 * iv * iv) * t) / (iv * sqrt(t))
    d2 = d1 - iv * sqrt(t)
    pdf_d1 = exp(-0.5 * d1 * d1) / sqrt(2.0 * np.pi)
    gamma = pdf_d1 / (spot * iv * sqrt(t))
    vega = spot * sqrt(t) * pdf_d1 / 100.0  # per 1% IV change
    if opt_type == OptionType.CE:
        delta = _norm_cdf(d1)
        theta = (-(spot * pdf_d1 * iv) / (2 * sqrt(t)) - r * strike * exp(-r * t) * _norm_cdf(d2)) / 365.0
    else:
        delta = _norm_cdf(d1) - 1.0
        theta = (-(spot * pdf_d1 * iv) / (2 * sqrt(t)) + r * strike * exp(-r * t) * _norm_cdf(-d2)) / 365.0
    return {"delta": delta, "gamma": gamma, "theta": theta, "vega": vega}


# ---------- backtest config & result ----------

@dataclass
class BacktestConfig:
    start_date: date
    end_date: date
    interval: str = "day"           # "day" | "1min" | "5min"
    capital: float = 1_000_000.0
    risk_per_trade_pct: float = 1.5
    daily_loss_limit_pct: float = 3.0
    strategy_filter: Optional[list[StrategyName]] = None  # None = all enabled
    cost_apply: bool = True


@dataclass
class BacktestResult:
    config: BacktestConfig
    trades: list[TradeRecord] = field(default_factory=list)
    decisions: list[dict] = field(default_factory=list)
    equity_curve: list[dict] = field(default_factory=list)
    final_equity: float = 0.0
    total_return_pct: float = 0.0
    max_drawdown_pct: float = 0.0
    total_charges: float = 0.0
    total_slippage: float = 0.0
    trade_count: int = 0
    win_rate: float = 0.0
    expectancy: float = 0.0
    profit_factor: float = 0.0
    sharpe: float = 0.0
    by_strategy: dict = field(default_factory=dict)
    by_regime: dict = field(default_factory=dict)
    error: Optional[str] = None


# ---------- backtest engine ----------

class BacktestEngine:
    """Replays historical candles through the decision pipeline."""

    def __init__(self, config: BacktestConfig) -> None:
        self.cfg = config
        self.broker = DhanBroker()
        self.regime = RegimeEngine()
        self.selector = StrategySelector()
        self.option_selector = OptionSelector()
        self.risk = RiskEngine(capital=config.capital)
        self.position_manager = PositionManager()
        self.cost_model = CostModel()

        self.equity = config.capital
        self._day_pnl: float = 0.0
        self._consecutive_losses: int = 0
        self._day_trades: int = 0
        self._current_day: Optional[date] = None
        self._open_positions: list[Position] = []
        self._closed_trades: list[TradeRecord] = []
        self._equity_curve: list[dict] = []
        self._decisions_log: list[dict] = []

    def run(self) -> BacktestResult:
        # 1. Fetch historical candles
        try:
            df = self.broker.historical_candles(
                symbol="NIFTY",
                interval=self.cfg.interval,
                start=datetime.combine(self.cfg.start_date, datetime.min.time()),
                end=datetime.combine(self.cfg.end_date, datetime.min.time()),
            )
        except NoDataError as exc:
            return BacktestResult(config=self.cfg, error=f"historical fetch failed: {exc}")
        except Exception as exc:
            return BacktestResult(config=self.cfg, error=f"historical fetch error: {type(exc).__name__}: {exc}")

        if df is None or len(df) < 50:
            return BacktestResult(config=self.cfg, error=f"insufficient historical data ({len(df) if df is not None else 0} bars)")

        # 2. Walk through candles
        lookback = 60
        for i in range(lookback, len(df)):
            window = df.iloc[i - lookback:i + 1]
            bar = df.iloc[i]
            ts = window.index[-1]

            # New day reset
            bar_date = ts.date() if hasattr(ts, 'date') else ts
            if self._current_day != bar_date:
                self._current_day = bar_date
                self._day_pnl = 0.0
                self._day_trades = 0

            # Build snapshot
            snapshot = self._build_snapshot(window, bar, ts)
            if snapshot is None:
                continue

            # Manage existing positions first (mark-to-market, exits)
            self._manage_positions(snapshot)

            # Assess regime
            regime_assessment = self.regime.assess(snapshot)

            # Select strategy
            chosen_eval, all_evals = self.selector.select(snapshot, regime_assessment)

            # Option selection
            opt_sel = self.option_selector.select(snapshot, chosen_eval)

            # Risk evaluation
            if chosen_eval.eligible and opt_sel.selected:
                risk_decision = self.risk.evaluate(
                    snapshot, chosen_eval, opt_sel.option,
                    open_positions=len(self._open_positions),
                    now=ts,
                )
            else:
                risk_decision = None

            # Execute
            if (chosen_eval.eligible and opt_sel.selected
                    and risk_decision and risk_decision.allowed):
                self._enter_position(
                    chosen_eval, opt_sel.option, risk_decision, snapshot, regime_assessment, ts,
                )
                action = DecisionAction.ENTER
            else:
                action = DecisionAction.NO_TRADE

            self._decisions_log.append({
                "timestamp": ts.isoformat() if hasattr(ts, 'isoformat') else str(ts),
                "action": action.value,
                "strategy": chosen_eval.strategy.value if chosen_eval else "NO_TRADE",
                "regime": regime_assessment.market_regime.value,
                "volatility": regime_assessment.volatility_regime.value,
                "confidence": chosen_eval.confidence_score if chosen_eval else 0.0,
                "expected_net": chosen_eval.expected_net_value if chosen_eval else 0.0,
                "spot": snapshot.index.ltp,
            })

            # Equity curve point
            unrealised = sum(p.unrealised_pnl for p in self._open_positions)
            self._equity_curve.append({
                "timestamp": ts.isoformat() if hasattr(ts, 'isoformat') else str(ts),
                "equity": self.equity + unrealised,
                "spot": snapshot.index.ltp,
                "open_positions": len(self._open_positions),
            })

        # Close any remaining open positions at last close
        if self._open_positions:
            last_bar = df.iloc[-1]
            self._force_close_all(last_bar["close"], df.index[-1])

        # Build result
        return self._build_result()

    # ---- snapshot building from historical bars ----
    def _build_snapshot(self, window: pd.DataFrame, bar, ts) -> Optional[MarketSnapshot]:
        try:
            idx_quote = IndexQuote(
                symbol="NIFTY",
                ltp=float(bar["close"]),
                prev_close=float(window["close"].iloc[-2]) if len(window) >= 2 else None,
                open=float(bar["open"]),
                high=float(bar["high"]),
                low=float(bar["low"]),
                volume=int(bar["volume"]),
                last_updated=ts,
            )
            # Attach technical features (ADX/RSI/EMA/ATR/VWAP)
            try:
                attach_features_to_index(idx_quote, window)
            except Exception:
                pass

            # Synthesize ATM option chain for backtest (since historical option
            # data is unavailable from DhanHQ API).
            chain = self._synthesize_option_chain(idx_quote, ts)

            snapshot = MarketSnapshot(
                timestamp=ts,
                index=idx_quote,
                india_vix=None,
                option_chain=chain,
                market_open=True,
                data_valid=True,
                time_bucket=None,
            )
            return snapshot
        except Exception:
            return None

    def _synthesize_option_chain(self, idx: IndexQuote, ts) -> list[OptionQuote]:
        """Generate ATM +/- 5 strikes of CE and PE using Black-Scholes.

        NOT used for live trading — only to exercise the strategy pipeline on
        historical underlying data. The IV assumption (15%) is conservative;
        once real historical option data is wired in, this method is removed.
        """
        spot = idx.ltp
        if spot <= 0:
            return []
        atm = int(round(spot / 50.0) * 50)
        iv = 0.15  # 15% default IV — replaceable with historical IV when available
        r = 0.07   # risk-free rate
        # Use 7 days to expiry as default for short-term directional trades
        t = 7.0 / 365.0
        strikes = [atm + i * 50 for i in range(-5, 6)]
        chain = []
        for k in strikes:
            for ot in (OptionType.CE, OptionType.PE):
                price = bs_option_price(spot, k, t, iv, r, ot)
                greeks = bs_greeks(spot, k, t, iv, r, ot)
                chain.append(OptionQuote(
                    symbol=f"NIFTY{ts.date().strftime('%y%b').upper()}{k}{ot.value}",
                    exchange="NSE",
                    expiry=ts.date() + timedelta(days=7),
                    strike=float(k),
                    option_type=ot,
                    ltp=max(0.05, price),
                    bid=max(0.05, price - 1.0),
                    ask=price + 1.0,
                    volume=10_000,
                    oi=50_000,
                    iv=iv,
                    delta=greeks["delta"],
                    gamma=greeks["gamma"],
                    theta=greeks["theta"],
                    vega=greeks["vega"],
                    last_updated=ts,
                ))
        return chain

    # ---- position management ----
    def _enter_position(self, eval_, option, risk_decision, snapshot, regime, ts):
        lots = risk_decision.max_lots
        if lots < 1:
            return
        # Apply cost on entry
        qty = lots * 75
        entry_cost_breakdown = self.cost_model.cost_for_leg(
            option.ltp, qty, "BUY", apply_slippage=True, apply_spread=True,
        )
        self.equity -= entry_cost_breakdown.total
        self._day_trades += 1

        pos = Position(
            strategy=eval_.strategy,
            option=option,
            lots=lots,
            entry_price=option.ltp,
            entry_time=ts,
            side="BUY",
            stop_loss=risk_decision.stop_loss,
            take_profit=risk_decision.take_profit,
            current_price=option.ltp,
            unrealised_pnl=0.0,
        )
        self._open_positions.append(pos)

    def _manage_positions(self, snapshot):
        """Mark-to-market + check exits."""
        spot = snapshot.index.ltp
        ts = snapshot.timestamp
        for pos in list(self._open_positions):
            if pos.status != "OPEN":
                continue
            # Reprice the option using BS with current spot
            new_price = bs_option_price(
                spot, pos.option.strike, 7.0 / 365.0, 0.15, 0.07, pos.option.option_type,
            )
            pos.current_price = new_price
            pos.unrealised_pnl = (new_price - pos.entry_price) * pos.lots * 75

            action, reasons = self.position_manager.manage(
                pos, snapshot, self.regime.assess(snapshot), None,
            )
            if action in (DecisionAction.EXIT, DecisionAction.TAKE_PROFIT):
                self._exit_position(pos, new_price, ts, action.value, reasons)

    def _exit_position(self, pos: Position, exit_price: float, ts, reason: str, exit_reasons: list):
        pos.status = "EXITED"
        # Apply cost on exit
        qty = pos.lots * 75
        exit_cost = self.cost_model.cost_for_leg(
            exit_price, qty, "SELL", apply_slippage=True, apply_spread=True,
        )
        gross = (exit_price - pos.entry_price) * qty
        net = gross - exit_cost.total
        self.equity += net
        self._day_pnl += net
        if net < 0:
            self._consecutive_losses += 1
        else:
            self._consecutive_losses = 0

        self.risk.record_trade_result(net, ts)
        self._closed_trades.append(TradeRecord(
            trade_id=f"BT-{len(self._closed_trades)+1:04d}",
            entry_time=pos.entry_time,
            exit_time=ts,
            strategy=pos.strategy,
            regime_at_entry=MarketRegime.NEUTRAL,    # not tracked per-trade yet
            vol_regime_at_entry=VolatilityRegime.NORMAL_VOL,
            option_symbol=pos.option.symbol,
            strike=pos.option.strike,
            option_type=pos.option.option_type,
            expiry=pos.option.expiry,
            lots=pos.lots,
            entry_price=pos.entry_price,
            exit_price=exit_price,
            gross_pnl=gross,
            charges=exit_cost.total,
            net_pnl=net,
            slippage=exit_cost.slippage + exit_cost.bid_ask_spread_impact,
            exit_reason=reason,
            holding_minutes=int((ts - pos.entry_time).total_seconds() / 60) if hasattr(ts, '__sub__') else None,
        ))
        self._open_positions.remove(pos)

    def _force_close_all(self, last_spot, ts):
        """At backtest end, close any open positions at last available spot."""
        for pos in list(self._open_positions):
            exit_price = bs_option_price(
                last_spot, pos.option.strike, 1.0/365.0, 0.15, 0.07, pos.option.option_type,
            )
            self._exit_position(pos, exit_price, ts, "backtest_end", ["backtest end — forced close"])

    # ---- result building ----
    def _build_result(self) -> BacktestResult:
        trades = self._closed_trades
        if not trades:
            return BacktestResult(
                config=self.cfg,
                final_equity=self.equity,
                total_return_pct=(self.equity - self.cfg.capital) / self.cfg.capital * 100,
                error="no trades generated — strategies may have been too restrictive",
            )
        winners = [t for t in trades if t.net_pnl > 0]
        losers = [t for t in trades if t.net_pnl <= 0]
        gross = sum(t.gross_pnl for t in trades)
        net = sum(t.net_pnl for t in trades)
        charges = sum(t.charges for t in trades)
        slip = sum(t.slippage for t in trades)
        avg_win = sum(t.net_pnl for t in winners) / max(1, len(winners))
        avg_lose = sum(t.net_pnl for t in losers) / max(1, len(losers))
        expectancy = (len(winners)/len(trades)) * avg_win + (len(losers)/len(trades)) * avg_lose
        gp = sum(t.net_pnl for t in winners)
        gl = abs(sum(t.net_pnl for t in losers))
        profit_factor = gp / gl if gl > 0 else float("inf")

        # Max drawdown from equity curve
        eq = [p["equity"] for p in self._equity_curve] or [self.cfg.capital]
        peak = eq[0]
        max_dd = 0.0
        for v in eq:
            if v > peak:
                peak = v
            dd = (peak - v) / peak
            if dd > max_dd:
                max_dd = dd

        # Sharpe (rough — daily returns)
        try:
            eq_series = pd.Series(eq)
            rets = eq_series.pct_change().dropna()
            sharpe = float((rets.mean() / rets.std()) * (252 ** 0.5)) if len(rets) > 1 and rets.std() > 0 else 0.0
        except Exception:
            sharpe = 0.0

        from collections import defaultdict
        by_strat = defaultdict(lambda: {"count": 0, "net_pnl": 0.0})
        for t in trades:
            by_strat[t.strategy.value]["count"] += 1
            by_strat[t.strategy.value]["net_pnl"] += t.net_pnl

        return BacktestResult(
            config=self.cfg,
            trades=trades,
            decisions=self._decisions_log,
            equity_curve=self._equity_curve,
            final_equity=self.equity,
            total_return_pct=(self.equity - self.cfg.capital) / self.cfg.capital * 100,
            max_drawdown_pct=max_dd * 100,
            total_charges=charges,
            total_slippage=slip,
            trade_count=len(trades),
            win_rate=len(winners) / len(trades),
            expectancy=expectancy,
            profit_factor=profit_factor,
            sharpe=sharpe,
            by_strategy=dict(by_strat),
        )
