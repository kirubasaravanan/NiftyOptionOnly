"""FastAPI backend exposing the engine, backtester, and journal.

Endpoints:
  GET  /api/health                -> { ok, broker_connected, market_open, time }
  GET  /api/snapshot              -> current MarketSnapshot
  GET  /api/decision              -> run one decision cycle, return Decision
  GET  /api/status                -> risk engine + open positions
  GET  /api/journal/decisions    -> all decision records
  GET  /api/journal/trades        -> all trade records
  GET  /api/performance           -> PerformanceReport
  POST /api/backtest/run          -> run backtest, return BacktestResult
  GET  /api/config                -> dump all YAML configs
  PUT  /api/config/{name}         -> update a config file
  WS   /ws/live                   -> pushes Decision every N seconds
"""
from __future__ import annotations

import asyncio
import json
import os
import threading
import time
from datetime import date, datetime
from pathlib import Path
from typing import Optional

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

load_dotenv()

from .engine import Engine
from .models import RunMode
from .backtest import BacktestEngine, BacktestConfig, AblationTester
from .journal import DecisionLogger, TradeLogger, PerformanceAnalytics
from .utils.time_utils import ist_now, is_market_open

app = FastAPI(
    title="Adaptive NIFTY Options Trading System",
    version="0.1.0",
    description="Phase 1-6: data, features, regime, Long CE/PE + NO-TRADE, paper mode + backtesting",
)

# CORS — restrict to localhost origins only (not wildcard).
# The frontend uses Next.js rewrites (same-origin /api/*), so the browser
# never makes cross-origin requests to the backend directly.
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:3000",
        "http://127.0.0.1:3000",
        "http://localhost:81",
        "http://127.0.0.1:81",
    ],
    allow_credentials=False,
    allow_methods=["GET", "POST", "PUT"],
    allow_headers=["Content-Type"],
)

RUNS_DIR = os.environ.get("ENGINE_RUNS_DIR", "/home/z/my-project/runs")
CAPITAL = float(os.environ.get("ENGINE_CAPITAL", "1000000"))


# Singleton broker — instantiating DhanBroker is expensive (creates DhanContext,
# fetches security list). Reuse one instance across all requests.
_broker_singleton = None

# Singleton engine — the engine holds position state, risk state, thesis
# tracker, protection layer, MAE/MFE tracker. If we rebuild it per request,
# all of that state is lost. This singleton persists across the API's lifetime.
_engine_singleton = None
_engine_lock = threading.Lock()


def get_broker():
    global _broker_singleton
    if _broker_singleton is None:
        from .data import DhanBroker
        _broker_singleton = DhanBroker()
    return _broker_singleton


def get_engine():
    """Get the singleton Engine instance.

    CRITICAL: The engine holds position state (order_manager), risk state
    (risk engine), and position trackers (thesis, protection, MAE/MFE).
    Rebuilding it per request would wipe all of that. This singleton ensures
    state persists across API calls.
    """
    global _engine_singleton
    if _engine_singleton is None:
        with _engine_lock:
            if _engine_singleton is None:
                _engine_singleton = Engine(
                    mode=RunMode.PAPER, capital=CAPITAL, runs_dir=RUNS_DIR,
                    broker=get_broker(),
                )
    return _engine_singleton


# Cached snapshot (30s TTL) — building a snapshot involves 4 broker API calls
_cached_snapshot = None
_cached_snapshot_ts = 0.0

# Cached decision (30s TTL)
_cached_decision = None
_cached_decision_ts = 0.0


def get_cached_snapshot():
    """Get a cached snapshot (refresh every 30s) to keep the API responsive.

    Each refresh makes 4 broker API calls (NIFTY, VIX, BankNifty, NIFTY futures).
    These are network-bound and ~1-2s each, so cache aggressively.

    Thread-safe via a lock — only one thread refreshes at a time.
    """
    import time
    global _cached_snapshot, _cached_snapshot_ts
    if not hasattr(get_cached_snapshot, '_lock'):
        get_cached_snapshot._lock = threading.Lock()
    lock = get_cached_snapshot._lock
    now = time.time()
    if _cached_snapshot is not None and (now - _cached_snapshot_ts) <= 30.0:
        return _cached_snapshot
    with lock:
        if _cached_snapshot is not None and (time.time() - _cached_snapshot_ts) <= 30.0:
            return _cached_snapshot
        try:
            b = get_broker()
            _cached_snapshot = b.get_snapshot()
            _cached_snapshot_ts = time.time()
        except Exception as e:
            print(f"[snapshot refresh error] {type(e).__name__}: {e}", flush=True)
    return _cached_snapshot


def get_cached_decision():
    """Get a cached decision (refresh every 30s).

    Uses the singleton engine so position/risk state persists across calls.
    Thread-safe via a lock.
    """
    import time
    global _cached_decision, _cached_decision_ts
    if not hasattr(get_cached_decision, '_lock'):
        get_cached_decision._lock = threading.Lock()
    lock = get_cached_decision._lock
    now = time.time()
    if _cached_decision is not None and (now - _cached_decision_ts) <= 30.0:
        return _cached_decision
    with lock:
        if _cached_decision is not None and (time.time() - _cached_decision_ts) <= 30.0:
            return _cached_decision
        try:
            # Use singleton engine — preserves position/risk state across calls
            engine = get_engine()
            _cached_decision = engine.run_cycle()
            _cached_decision_ts = time.time()
        except Exception as e:
            print(f"[decision refresh error] {type(e).__name__}: {e}", flush=True)
    return _cached_decision


# ---------- multi-instrument singletons (2026-08-18): BANKNIFTY + SENSEX ----------
# Parallel to the NIFTY singletons above — separate DhanBroker + Engine per
# instrument, own runs_dir (so journals never mix with NIFTY's own
# runs/decisions/decisions.jsonl etc.), own risk/strategies config
# (risk_banknifty.yaml etc. — literal copies of NIFTY's values, unvalidated).
# NIFTY's get_broker()/get_engine()/get_cached_snapshot()/get_cached_decision()
# above are completely untouched by this.
from .instruments import BANKNIFTY, SENSEX

_instrument_state = {
    "banknifty": {
        "instrument": BANKNIFTY, "broker": None, "engine": None,
        "engine_lock": threading.Lock(),
        "cached_snapshot": None, "cached_snapshot_ts": 0.0,
        "cached_decision": None, "cached_decision_ts": 0.0,
        "snapshot_lock": threading.Lock(), "decision_lock": threading.Lock(),
    },
    "sensex": {
        "instrument": SENSEX, "broker": None, "engine": None,
        "engine_lock": threading.Lock(),
        "cached_snapshot": None, "cached_snapshot_ts": 0.0,
        "cached_decision": None, "cached_decision_ts": 0.0,
        "snapshot_lock": threading.Lock(), "decision_lock": threading.Lock(),
    },
}


def _get_instrument_broker(key: str):
    st = _instrument_state[key]
    if st["broker"] is None:
        from .data import DhanBroker
        st["broker"] = DhanBroker(instrument=st["instrument"])
    return st["broker"]


def _get_instrument_engine(key: str):
    st = _instrument_state[key]
    if st["engine"] is None:
        with st["engine_lock"]:
            if st["engine"] is None:
                st["engine"] = Engine(
                    mode=RunMode.PAPER, capital=CAPITAL,
                    runs_dir=str(Path(RUNS_DIR) / key),
                    broker=_get_instrument_broker(key),
                    instrument=st["instrument"],
                )
    return st["engine"]


def _get_instrument_cached_snapshot(key: str):
    """Same 30s-TTL caching pattern as get_cached_snapshot(), per instrument."""
    import time
    st = _instrument_state[key]
    now = time.time()
    if st["cached_snapshot"] is not None and (now - st["cached_snapshot_ts"]) <= 30.0:
        return st["cached_snapshot"]
    with st["snapshot_lock"]:
        if st["cached_snapshot"] is not None and (time.time() - st["cached_snapshot_ts"]) <= 30.0:
            return st["cached_snapshot"]
        try:
            b = _get_instrument_broker(key)
            st["cached_snapshot"] = b.get_snapshot()
            st["cached_snapshot_ts"] = time.time()
        except Exception as e:
            print(f"[{key} snapshot refresh error] {type(e).__name__}: {e}", flush=True)
    return st["cached_snapshot"]


def _get_instrument_cached_decision(key: str):
    """Same 30s-TTL caching pattern as get_cached_decision(), per instrument."""
    import time
    st = _instrument_state[key]
    now = time.time()
    if st["cached_decision"] is not None and (now - st["cached_decision_ts"]) <= 30.0:
        return st["cached_decision"]
    with st["decision_lock"]:
        if st["cached_decision"] is not None and (time.time() - st["cached_decision_ts"]) <= 30.0:
            return st["cached_decision"]
        try:
            engine = _get_instrument_engine(key)
            st["cached_decision"] = engine.run_cycle()
            st["cached_decision_ts"] = time.time()
        except Exception as e:
            print(f"[{key} decision refresh error] {type(e).__name__}: {e}", flush=True)
    return st["cached_decision"]


# ---------- models for API payloads ----------

class BacktestRequest(BaseModel):
    start_date: str           # "YYYY-MM-DD"
    end_date: str
    capital: float = 1_000_000.0
    interval: str = "day"


class AblationRequest(BaseModel):
    start_date: str
    end_date: str
    capital: float = 1_000_000.0


class ConfigUpdate(BaseModel):
    name: str                  # "risk" | "strategies" | "costs" | "trading_hours" | "broker"
    content: str               # YAML text


# ---------- endpoints ----------

@app.get("/api/health")
def health():
    """Quick liveness check — also reports broker & market state."""
    b = get_broker()
    return {
        "ok": True,
        "broker_connected": b.is_connected(),
        "market_open": is_market_open(),
        "ist_time": ist_now().isoformat(),
        "capital": CAPITAL,
    }


@app.get("/api/snapshot")
def snapshot():
    """Return the current market snapshot + cross-market confirmation."""
    from .features.correlation import (
        VIXValuationCalculator, OIClassifier, FuturesBasisCalculator,
        BankNiftyCalculator,
    )
    snap = get_cached_snapshot()

    # Compute confirmation features for display — even on holidays we can
    # show VIX valuation + Bank Nifty + futures basis (these don't require
    # the option chain which is unavailable when market is closed).
    confirmation_data = None
    try:
        bn = getattr(snap, "_banknifty_quote", None)
        nf = getattr(snap, "_nifty_futures_quote", None)
        vix_val = VIXValuationCalculator.compute(snap)
        oi_cls = OIClassifier.classify(snap) if snap.option_chain else None
        fb = FuturesBasisCalculator.compute(snap, nf)
        bnc = BankNiftyCalculator.compute(snap, bn)
        confirmation_data = {
            "vix_valuation": {
                "vix": vix_val.vix,
                "vix_percentile": vix_val.vix_percentile,
                "vix_change": vix_val.vix_change,
                "iv_vix_gap": vix_val.iv_vix_gap,
                "valuation": vix_val.valuation,
                "reasons": vix_val.reasons,
            },
            "oi_classification": {
                "ce": oi_cls.ce_classification if oi_cls else "UNKNOWN",
                "pe": oi_cls.pe_classification if oi_cls else "UNKNOWN",
                "call_wall": oi_cls.call_wall if oi_cls else None,
                "put_wall": oi_cls.put_wall if oi_cls else None,
                "max_pain": oi_cls.max_pain if oi_cls else None,
                "reasons": oi_cls.reasons if oi_cls else ["option chain unavailable (market closed)"],
            },
            "futures_basis": {
                "spot": fb.spot,
                "futures": fb.futures,
                "basis": fb.basis,
                "basis_pct": fb.basis_pct,
                "interpretation": fb.interpretation,
                "reasons": fb.reasons,
            },
            "banknifty_confirmation": {
                "nifty_change_pct": bnc.nifty_change_pct,
                "banknifty_change_pct": bnc.banknifty_change_pct,
                "correlation_state": bnc.correlation_state,
                "reasons": bnc.reasons,
            },
        }
    except Exception as e:
        confirmation_data = {"error": str(e)}

    # Also include auxiliary market data
    aux = {
        "banknifty": getattr(snap, "_banknifty_quote", None),
        "nifty_futures": getattr(snap, "_nifty_futures_quote", None),
    }
    return {**_serialize_snapshot(snap), "confirmation": confirmation_data, "aux": aux}


@app.get("/api/decision")
def decision():
    """Run one decision cycle and return the Decision (cached 30s)."""
    d = get_cached_decision()
    if d is None:
        return {"error": "decision engine unavailable", "action": "NO_TRADE"}
    return _serialize_decision(d)


@app.get("/api/alerts")
def alerts(limit: int = 50):
    """Return recent Discord alert log (newest first)."""
    from .notifier.discord import get_notifier
    n = get_notifier()
    items = n.recent_alerts(limit=limit)
    return {"count": len(items), "items": items}


@app.get("/api/thesis")
def thesis():
    """Return current thesis score for the active position (if any)."""
    # We don't currently expose this via the cached engine — return None for now.
    # When a position is open, the engine's thesis_tracker has the data.
    return {
        "active": False,
        "score": None,
        "state": None,
        "direction": None,
        "components": None,
        "reasons": [],
    }


@app.post("/api/alerts/test")
def alerts_test():
    """Send a test alert to verify the Discord webhook is working."""
    from .notifier.discord import get_notifier
    from .notifier import AlertType
    n = get_notifier()
    ok = n.send_alert(
        AlertType.DAILY_SUMMARY,
        "Test Alert from NIFTY Engine UI",
        fields={
            "Source": "Manual test from Configuration tab",
            "Timestamp": ist_now().isoformat(),
            "Webhook Configured": str(bool(n.webhook_url)),
            "Enabled": str(n.enabled),
        },
        description="If you received this alert in Discord, the webhook integration is working correctly. All 15 alert types will fire on real trade lifecycle events.",
    )
    return {"ok": ok, "sent": ok, "webhook_configured": bool(n.webhook_url), "enabled": n.enabled}


@app.get("/api/status")
def status():
    """Risk engine state + open positions + capital.

    Uses the singleton engine so open positions are visible — previously this
    created a fresh Engine on every call, so open_positions was always [].
    """
    engine = get_engine()
    return {
        "risk": engine.risk.status(),
        "open_positions": [
            {
                "strategy": p.strategy.value,
                "option": p.option.symbol if p.option else (p.long_leg.symbol if p.long_leg else "—"),
                "security_id": (p.option.security_id if p.option else (p.long_leg.security_id if p.long_leg else None)),
                "strike": (p.option.strike if p.option else (p.long_leg.strike if p.long_leg else 0)),
                "option_type": (p.option.option_type.value if p.option else (p.long_leg.option_type.value if p.long_leg else "—")),
                "lots": p.lots,
                "entry_price": p.entry_price,
                "current_price": p.current_price,
                "stop_loss": p.stop_loss,
                "take_profit": p.take_profit,
                "unrealised_pnl": p.unrealised_pnl,
                "entry_time": p.entry_time.isoformat(),
                # Phase 10: spread fields
                "is_spread": p.long_leg is not None and p.short_leg is not None,
                "long_leg": p.long_leg.symbol if p.long_leg else None,
                "long_leg_security_id": p.long_leg.security_id if p.long_leg else None,
                "short_leg": p.short_leg.symbol if p.short_leg else None,
                "short_leg_security_id": p.short_leg.security_id if p.short_leg else None,
                "spread_width": p.spread_width,
                "max_loss": p.max_loss,
                "max_gain": p.max_gain,
                "breakeven": p.breakeven,
            }
            for p in engine.order_manager.positions if p.status == "OPEN"
        ],
    }


# ---------- BANKNIFTY / SENSEX endpoints (2026-08-18) ----------
# Mirror the core NIFTY endpoints above (health/snapshot/decision/status).
# Journal/performance/backtest/config endpoints are NOT mirrored yet — out
# of scope for this pass, which is about getting real decision cycles
# running and observable for these two instruments, not full feature parity.
# NOTE: /api/alerts stays a single shared endpoint across all instruments —
# the underlying Discord notifier is process-wide and alert payloads don't
# currently carry an instrument tag, so alerts from NIFTY/BANKNIFTY/SENSEX
# are indistinguishable there today. Flagged as a known gap, not fixed here.

@app.get("/api/banknifty/health")
def banknifty_health():
    b = _get_instrument_broker("banknifty")
    return {
        "ok": True,
        "instrument": "BANKNIFTY",
        "broker_connected": b.is_connected(),
        "market_open": is_market_open(),
        "ist_time": ist_now().isoformat(),
        "capital": CAPITAL,
    }


@app.get("/api/banknifty/snapshot")
def banknifty_snapshot():
    snap = _get_instrument_cached_snapshot("banknifty")
    if snap is None:
        return {"error": "snapshot unavailable"}
    return _serialize_snapshot(snap)


@app.get("/api/banknifty/decision")
def banknifty_decision():
    d = _get_instrument_cached_decision("banknifty")
    if d is None:
        return {"error": "decision engine unavailable", "action": "NO_TRADE"}
    return _serialize_decision(d)


@app.get("/api/banknifty/status")
def banknifty_status():
    engine = _get_instrument_engine("banknifty")
    return {
        "instrument": "BANKNIFTY",
        "risk": engine.risk.status(),
        "open_positions": [
            {
                "strategy": p.strategy.value,
                "option": p.option.symbol if p.option else (p.long_leg.symbol if p.long_leg else "—"),
                "lots": p.lots,
                "entry_price": p.entry_price,
                "current_price": p.current_price,
                "stop_loss": p.stop_loss,
                "take_profit": p.take_profit,
                "unrealised_pnl": p.unrealised_pnl,
                "entry_time": p.entry_time.isoformat(),
            }
            for p in engine.order_manager.positions if p.status == "OPEN"
        ],
    }


@app.get("/api/sensex/health")
def sensex_health():
    b = _get_instrument_broker("sensex")
    return {
        "ok": True,
        "instrument": "SENSEX",
        "broker_connected": b.is_connected(),
        "market_open": is_market_open(),
        "ist_time": ist_now().isoformat(),
        "capital": CAPITAL,
    }


@app.get("/api/sensex/snapshot")
def sensex_snapshot():
    snap = _get_instrument_cached_snapshot("sensex")
    if snap is None:
        return {"error": "snapshot unavailable"}
    return _serialize_snapshot(snap)


@app.get("/api/sensex/decision")
def sensex_decision():
    d = _get_instrument_cached_decision("sensex")
    if d is None:
        return {"error": "decision engine unavailable", "action": "NO_TRADE"}
    return _serialize_decision(d)


@app.get("/api/sensex/status")
def sensex_status():
    engine = _get_instrument_engine("sensex")
    return {
        "instrument": "SENSEX",
        "risk": engine.risk.status(),
        "open_positions": [
            {
                "strategy": p.strategy.value,
                "option": p.option.symbol if p.option else (p.long_leg.symbol if p.long_leg else "—"),
                "lots": p.lots,
                "entry_price": p.entry_price,
                "current_price": p.current_price,
                "stop_loss": p.stop_loss,
                "take_profit": p.take_profit,
                "unrealised_pnl": p.unrealised_pnl,
                "entry_time": p.entry_time.isoformat(),
            }
            for p in engine.order_manager.positions if p.status == "OPEN"
        ],
    }


def _simple_direction(regime_value: Optional[str]) -> str:
    """Same heuristic Engine._compute_confirmation() already uses for its
    own direction input — reused here so this stays consistent with the
    rest of the codebase rather than inventing a second classification."""
    if regime_value in ("STRONG_BULL", "BULL", "WEAK_BULL", "BREAKOUT"):
        return "BULLISH"
    if regime_value in ("STRONG_BEAR", "BEAR", "WEAK_BEAR"):
        return "BEARISH"
    return "NEUTRAL"


@app.get("/api/portfolio/snapshot")
def portfolio_snapshot():
    """Cross-instrument comparison — added 2026-08-18 to answer 'were
    NIFTY/BANKNIFTY/SENSEX aligned right now' in one call instead of three.

    Purely observational: reads each instrument's already-cached decision
    (never triggers extra engine cycles/API calls beyond what each
    instrument's own endpoints already do) and reports a simple directional
    headcount. This is NOT a portfolio risk engine and does not gate or
    filter any instrument's own trading — that piece (correlation-aware
    capital allocation) is still unbuilt, see ROADMAP.md.
    """
    d_nifty = get_cached_decision()
    d_bank = _get_instrument_cached_decision("banknifty")
    d_sensex = _get_instrument_cached_decision("sensex")

    def _summarise(name, d):
        if d is None:
            return {"instrument": name, "error": "unavailable"}
        return {
            "instrument": name,
            "regime": d.regime.value,
            "action": d.action.value,
            "strategy": d.strategy.value,
            "expected_net_value": d.expected_net_value,
            "direction": _simple_direction(d.regime.value),
        }

    rows = [
        _summarise("NIFTY", d_nifty),
        _summarise("BANKNIFTY", d_bank),
        _summarise("SENSEX", d_sensex),
    ]
    directions = [r["direction"] for r in rows if "direction" in r]
    bullish = directions.count("BULLISH")
    bearish = directions.count("BEARISH")
    neutral = directions.count("NEUTRAL")
    aligned = bullish >= 2 or bearish >= 2
    return {
        "timestamp": ist_now().isoformat(),
        "instruments": rows,
        "direction_headcount": {"bullish": bullish, "bearish": bearish, "neutral": neutral},
        "aligned": aligned,
        "note": (
            "2+ instruments pointing the same direction — a real correlated "
            "portfolio risk engine (capital rotation, not multiplication) is "
            "still unbuilt; treat this as a visibility flag, not a risk control."
            if aligned else
            "no directional alignment across the 3 instruments right now."
        ),
    }


@app.get("/api/journal/decisions")
def journal_decisions(limit: int = 200):
    """Recent decision records (most recent first)."""
    log = DecisionLogger(runs_dir=RUNS_DIR)
    all_d = log.all_decisions()
    out = [_serialize_decision_record(r) for r in all_d[-limit:]]
    out.reverse()
    return {"count": len(out), "items": out}


@app.get("/api/journal/trades")
def journal_trades(limit: int = 200):
    """Trade records (most recent first)."""
    log = TradeLogger(runs_dir=RUNS_DIR)
    all_t = log.all_trades()
    out = [_serialize_trade(r) for r in all_t[-limit:]]
    out.reverse()
    return {"count": len(out), "items": out}


@app.get("/api/performance")
def performance():
    """Aggregate performance report from the trade journal."""
    log = TradeLogger(runs_dir=RUNS_DIR)
    pa = PerformanceAnalytics(log)
    rep = pa.report()
    return {
        "total_trades": rep.total_trades,
        "winners": rep.winners,
        "losers": rep.losers,
        "win_rate": rep.win_rate,
        "gross_pnl": rep.gross_pnl,
        "net_pnl": rep.net_pnl,
        "avg_winner": rep.avg_winner,
        "avg_loser": rep.avg_loser,
        "expectancy": rep.expectancy,
        "profit_factor": rep.profit_factor,
        "max_drawdown": rep.max_drawdown,
        "total_charges": rep.total_charges,
        "total_slippage": rep.total_slippage,
        "avg_holding_minutes": rep.avg_holding_minutes,
        "by_strategy": rep.by_strategy,
        "by_regime": rep.by_regime,
        "by_volatility": rep.by_volatility,
    }


@app.post("/api/backtest/run")
def backtest_run(req: BacktestRequest):
    """Run a backtest and return the result."""
    try:
        start = datetime.strptime(req.start_date, "%Y-%m-%d").date()
        end = datetime.strptime(req.end_date, "%Y-%m-%d").date()
    except ValueError as e:
        raise HTTPException(status_code=400, detail=f"invalid date: {e}")
    cfg = BacktestConfig(
        start_date=start, end_date=end,
        capital=req.capital, interval=req.interval,
    )
    eng = BacktestEngine(cfg)
    result = eng.run()
    return _serialize_backtest_result(result)


@app.post("/api/ablation/run")
def ablation_run(req: AblationRequest):
    """Run an ablation test — tests incremental value of each Layer 3 feature.

    Returns baseline metrics + per-feature deltas. Per spec: only features
    that improve OOS expectancy should be kept in the live engine.
    """
    try:
        start = datetime.strptime(req.start_date, "%Y-%m-%d").date()
        end = datetime.strptime(req.end_date, "%Y-%m-%d").date()
    except ValueError as e:
        raise HTTPException(status_code=400, detail=f"invalid date: {e}")
    tester = AblationTester(
        start_date=start, end_date=end, capital=req.capital,
    )
    result = tester.run()
    return {
        "baseline": {
            "feature_name": result.baseline.feature_name,
            "description": result.baseline.description,
            "oos_return_pct": result.baseline.oos_return_pct,
            "oos_expectancy": result.baseline.oos_expectancy,
            "oos_win_rate": result.baseline.oos_win_rate,
            "oos_trades": result.baseline.oos_trades,
            "oos_max_dd_pct": result.baseline.oos_max_dd_pct,
            "oos_sharpe": result.baseline.oos_sharpe,
        },
        "variants": [
            {
                "feature_name": v.feature_name,
                "description": v.description,
                "oos_return_pct": v.oos_return_pct,
                "oos_expectancy": v.oos_expectancy,
                "oos_win_rate": v.oos_win_rate,
                "oos_trades": v.oos_trades,
                "oos_max_dd_pct": v.oos_max_dd_pct,
                "oos_sharpe": v.oos_sharpe,
                "incremental_expectancy": v.incremental_expectancy,
                "incremental_return": v.incremental_return,
                "keeps_feature": v.keeps_feature,
                "error": v.error,
            }
            for v in result.variants
        ],
        "summary": result.summary,
        "recommendation": result.recommendation,
    }


@app.get("/api/config")
def list_configs():
    """List all config files with their raw YAML content."""
    from .config import CONFIG_DIR
    out = {}
    for path in CONFIG_DIR.glob("*.yaml"):
        out[path.stem] = path.read_text(encoding="utf-8")
    return out


@app.put("/api/config/{name}")
def update_config(name: str, body: ConfigUpdate, request: Request):
    """Update a YAML config file. Name must match a known file.

    SECURITY: If ENGINE_API_TOKEN is set in the environment, all write
    requests must include `Authorization: Bearer <token>` header.
    If the token is not set, writes are blocked entirely — the old
    localhost fallback was bypassable because Caddy always connects
    from localhost (making request.client.host always 127.0.0.1).
    """
    from .config import CONFIG_DIR, reload
    import os
    import secrets

    api_token = os.environ.get("ENGINE_API_TOKEN", "")
    if api_token:
        # Require bearer token
        auth_header = request.headers.get("Authorization", "")
        if not auth_header.startswith("Bearer "):
            raise HTTPException(status_code=401, detail="Missing or malformed Authorization header. Expected: Bearer <token>")
        provided = auth_header[7:]  # strip "Bearer "
        if not secrets.compare_digest(provided, api_token):
            raise HTTPException(status_code=401, detail="Invalid API token")
    else:
        # No token configured — block ALL writes.
        # The old localhost check was bypassable: Caddy proxies on localhost,
        # so request.client.host is always 127.0.0.1 regardless of who the
        # original external caller was. There is no reliable way to distinguish
        # "the Next.js rewrite called this" from "an attacker called this"
        # without a token.
        raise HTTPException(
            status_code=403,
            detail="Config writes are disabled. Set ENGINE_API_TOKEN in .env to enable authenticated writes.",
        )

    allowed = {
        "risk", "strategies", "costs", "trading_hours", "broker",
        "risk_banknifty", "strategies_banknifty", "broker_banknifty",
        "risk_sensex", "strategies_sensex", "broker_sensex",
    }
    if name not in allowed:
        raise HTTPException(status_code=400, detail=f"unknown config: {name}")
    path = CONFIG_DIR / f"{name}.yaml"
    if not path.exists():
        raise HTTPException(status_code=404, detail=f"config file not found: {name}")
    # Validate YAML before writing
    import yaml
    try:
        yaml.safe_load(body.content)
    except yaml.YAMLError as e:
        raise HTTPException(status_code=400, detail=f"invalid YAML: {e}")
    path.write_text(body.content, encoding="utf-8")
    reload()    # clear in-memory cache so next read picks up changes
    return {"ok": True, "name": name, "size": len(body.content)}


@app.websocket("/ws/live")
async def ws_live(websocket: WebSocket):
    """Push a fresh Decision to the client every N seconds.

    Uses the singleton engine so position/risk state persists.
    """
    await websocket.accept()
    interval = 30  # 30s — matches the decision cache TTL
    try:
        while True:
            try:
                d = get_cached_decision()
                if d is None:
                    await websocket.send_json({"error": "engine unavailable", "action": "NO_TRADE"})
                else:
                    await websocket.send_json(_serialize_decision(d))
            except Exception as exc:
                await websocket.send_json({"error": str(exc), "type": type(exc).__name__})
            await asyncio.sleep(interval)
    except WebSocketDisconnect:
        return
    except Exception:
        return


# ---------- serializers ----------

def _serialize_snapshot(snap):
    return {
        "timestamp": snap.timestamp.isoformat(),
        "data_valid": snap.data_valid,
        "data_invalid_reason": snap.data_invalid_reason,
        "market_open": snap.market_open,
        "time_bucket": snap.time_bucket.value if snap.time_bucket else None,
        "index": {
            "ltp": snap.index.ltp,
            "prev_close": snap.index.prev_close,
            "open": snap.index.open,
            "high": snap.index.high,
            "low": snap.index.low,
            "volume": snap.index.volume,
            "vwap": snap.index.vwap,
            "atr": snap.index.atr,
            "adx": snap.index.adx,
            "adx_slope": snap.index.adx_slope,
            "rsi": snap.index.rsi,
            "ema_fast": snap.index.ema_fast,
            "ema_mid": snap.index.ema_mid,
            "ema_slow": snap.index.ema_slow,
        },
        "india_vix": {
            "ltp": snap.india_vix.ltp,
            "iv_percentile": snap.india_vix.iv_percentile,
        } if snap.india_vix else None,
        "option_chain_size": len(snap.option_chain),
        "option_chain_sample": [
            {
                "symbol": q.symbol,
                "strike": q.strike,
                "option_type": q.option_type.value,
                "ltp": q.ltp,
                "iv": q.iv,
                "delta": q.delta,
                "theta": q.theta,
                "volume": q.volume,
                "oi": q.oi,
            }
            for q in snap.option_chain[:10]
        ],
    }


def _serialize_decision(d):
    return {
        "timestamp": d.timestamp.isoformat(),
        "action": d.action.value,
        "strategy": d.strategy.value,
        "regime": d.regime.value,
        "volatility": d.volatility.value,
        "lots": d.lots,
        "premium_per_lot": d.premium_per_lot,
        "total_premium": d.total_premium,
        "expected_net_value": d.expected_net_value,
        "expected_risk": d.expected_risk,
        "confidence": d.confidence,
        "stop_loss": d.stop_loss,
        "take_profit": d.take_profit,
        "reasons": d.reasons,
        # Phase 10: spread-specific fields
        "max_loss": getattr(d, 'max_loss', None),
        "max_gain": getattr(d, 'max_gain', None),
        "breakeven": getattr(d, 'breakeven', None),
        "option": {
            "symbol": d.option.symbol,
            "strike": d.option.strike,
            "option_type": d.option.option_type.value,
            "ltp": d.option.ltp,
            "iv": d.option.iv,
            "delta": d.option.delta,
            "expiry": d.option.expiry.isoformat(),
        } if d.option else None,
        "long_leg": {
            "symbol": d.long_leg.symbol,
            "strike": d.long_leg.strike,
            "option_type": d.long_leg.option_type.value,
            "ltp": d.long_leg.ltp,
        } if getattr(d, 'long_leg', None) else None,
        "short_leg": {
            "symbol": d.short_leg.symbol,
            "strike": d.short_leg.strike,
            "option_type": d.short_leg.option_type.value,
            "ltp": d.short_leg.ltp,
        } if getattr(d, 'short_leg', None) else None,
        "explainability": d.explainability_block,
    }


def _serialize_decision_record(r):
    return {
        "timestamp": r.timestamp.isoformat(),
        "action": r.action.value,
        "strategy": r.strategy.value,
        "regime": r.regime.value,
        "volatility": r.volatility.value,
        "confidence": r.confidence,
        "expected_net_value": r.expected_net_value,
        "reasons": r.reasons,
        "snapshot_summary": r.snapshot_summary,
    }


def _serialize_trade(t):
    return {
        "trade_id": t.trade_id,
        "entry_time": t.entry_time.isoformat(),
        "exit_time": t.exit_time.isoformat() if t.exit_time else None,
        "strategy": t.strategy.value,
        "regime_at_entry": t.regime_at_entry.value,
        "vol_regime_at_entry": t.vol_regime_at_entry.value,
        "option_symbol": t.option_symbol,
        "strike": t.strike,
        "option_type": t.option_type.value,
        "expiry": t.expiry.isoformat(),
        "lots": t.lots,
        "entry_price": t.entry_price,
        "exit_price": t.exit_price,
        "gross_pnl": t.gross_pnl,
        "charges": t.charges,
        "net_pnl": t.net_pnl,
        "slippage": t.slippage,
        "exit_reason": t.exit_reason,
        "holding_minutes": t.holding_minutes,
    }


def _serialize_backtest_result(r):
    return {
        "config": {
            "start_date": r.config.start_date.isoformat(),
            "end_date": r.config.end_date.isoformat(),
            "interval": r.config.interval,
            "capital": r.config.capital,
        },
        "final_equity": r.final_equity,
        "total_return_pct": r.total_return_pct,
        "max_drawdown_pct": r.max_drawdown_pct,
        "total_charges": r.total_charges,
        "total_slippage": r.total_slippage,
        "trade_count": r.trade_count,
        "win_rate": r.win_rate,
        "expectancy": r.expectancy,
        "profit_factor": r.profit_factor,
        "sharpe": r.sharpe,
        "by_strategy": r.by_strategy,
        "equity_curve": r.equity_curve[:500],   # cap for API payload
        "trades": [_serialize_trade(t) for t in r.trades],
        "decisions_count": len(r.decisions),
        "error": r.error,
    }


def main():
    """Run the FastAPI server."""
    import uvicorn
    port = int(os.environ.get("ENGINE_API_PORT", "8000"))
    uvicorn.run(
        app, host="0.0.0.0", port=port, log_level="info",
        timeout_keep_alive=300,
        timeout_graceful_shutdown=30,
        workers=1,
        access_log=False,
    )


if __name__ == "__main__":
    main()
