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
from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

load_dotenv()

from .engine import Engine
from .models import RunMode
from .backtest import BacktestEngine, BacktestConfig
from .journal import DecisionLogger, TradeLogger, PerformanceAnalytics
from .utils.time_utils import ist_now, is_market_open

app = FastAPI(
    title="Adaptive NIFTY Options Trading System",
    version="0.1.0",
    description="Phase 1-6: data, features, regime, Long CE/PE + NO-TRADE, paper mode + backtesting",
)

# CORS — allow the Next.js frontend on any port
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

RUNS_DIR = os.environ.get("ENGINE_RUNS_DIR", "/home/z/my-project/runs")
CAPITAL = float(os.environ.get("ENGINE_CAPITAL", "1000000"))


# ---------- models for API payloads ----------

class BacktestRequest(BaseModel):
    start_date: str           # "YYYY-MM-DD"
    end_date: str
    capital: float = 1_000_000.0
    interval: str = "day"


class ConfigUpdate(BaseModel):
    name: str                  # "risk" | "strategies" | "costs" | "trading_hours" | "broker"
    content: str               # YAML text


# ---------- endpoints ----------

@app.get("/api/health")
def health():
    """Quick liveness check — also reports broker & market state."""
    from .data import DhanBroker
    b = DhanBroker()
    return {
        "ok": True,
        "broker_connected": b.is_connected(),
        "market_open": is_market_open(),
        "ist_time": ist_now().isoformat(),
        "capital": CAPITAL,
    }


@app.get("/api/snapshot")
def snapshot():
    """Return the current market snapshot."""
    from .data import DhanBroker
    b = DhanBroker()
    snap = b.get_snapshot()
    return _serialize_snapshot(snap)


@app.get("/api/decision")
def decision():
    """Run one decision cycle and return the Decision."""
    engine = Engine(mode=RunMode.PAPER, capital=CAPITAL, runs_dir=RUNS_DIR)
    d = engine.run_cycle()
    return _serialize_decision(d)


@app.get("/api/status")
def status():
    """Risk engine state + open positions + capital."""
    engine = Engine(mode=RunMode.PAPER, capital=CAPITAL, runs_dir=RUNS_DIR)
    return {
        "risk": engine.risk.status(),
        "open_positions": [
            {
                "strategy": p.strategy.value,
                "option": p.option.symbol,
                "strike": p.option.strike,
                "option_type": p.option.option_type.value,
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


@app.get("/api/config")
def list_configs():
    """List all config files with their raw YAML content."""
    from .config import CONFIG_DIR
    out = {}
    for path in CONFIG_DIR.glob("*.yaml"):
        out[path.stem] = path.read_text(encoding="utf-8")
    return out


@app.put("/api/config/{name}")
def update_config(name: str, body: ConfigUpdate):
    """Update a YAML config file. Name must match a known file."""
    from .config import CONFIG_DIR, reload
    allowed = {"risk", "strategies", "costs", "trading_hours", "broker"}
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
    """Push a fresh Decision to the client every N seconds."""
    await websocket.accept()
    interval = 5
    try:
        while True:
            try:
                engine = Engine(mode=RunMode.PAPER, capital=CAPITAL, runs_dir=RUNS_DIR)
                d = engine.run_cycle()
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
        "option": {
            "symbol": d.option.symbol,
            "strike": d.option.strike,
            "option_type": d.option.option_type.value,
            "ltp": d.option.ltp,
            "iv": d.option.iv,
            "delta": d.option.delta,
            "expiry": d.option.expiry.isoformat(),
        } if d.option else None,
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
    uvicorn.run(app, host="0.0.0.0", port=port, log_level="info")


if __name__ == "__main__":
    main()
