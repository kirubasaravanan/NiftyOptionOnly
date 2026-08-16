"""Pydantic schemas for the entire engine.

These are the data contracts between layers. Keeping them in one place
makes it easy to validate every cross-layer boundary.
"""
from __future__ import annotations

from datetime import datetime, time, date
from enum import Enum
from typing import Optional, Literal

from pydantic import BaseModel, Field, ConfigDict


# ---------- enums ----------

class MarketRegime(str, Enum):
    STRONG_BULL = "STRONG_BULL"
    BULL = "BULL"
    WEAK_BULL = "WEAK_BULL"
    NEUTRAL = "NEUTRAL"
    WEAK_BEAR = "WEAK_BEAR"
    BEAR = "BEAR"
    STRONG_BEAR = "STRONG_BEAR"
    RANGE = "RANGE"
    BREAKOUT = "BREAKOUT"
    REVERSAL = "REVERSAL"


class VolatilityRegime(str, Enum):
    LOW_VOL = "LOW_VOL"
    NORMAL_VOL = "NORMAL_VOL"
    HIGH_VOL = "HIGH_VOL"
    VOL_EXPANSION = "VOL_EXPANSION"
    VOL_CONTRACTION = "VOL_CONTRACTION"


class StrategyName(str, Enum):
    LONG_CALL = "LONG_CALL"
    LONG_PUT = "LONG_PUT"
    LONG_STRADDLE = "LONG_STRADDLE"
    LONG_STRANGLE = "LONG_STRANGLE"
    DEBIT_SPREAD = "DEBIT_SPREAD"
    IRON_CONDOR = "IRON_CONDOR"
    BUTTERFLY = "BUTTERFLY"
    NO_TRADE = "NO_TRADE"


class DecisionAction(str, Enum):
    ENTER = "ENTER"
    HOLD = "HOLD"
    ADD = "ADD"
    REDUCE = "REDUCE"
    MOVE_STOP = "MOVE_STOP"
    TAKE_PROFIT = "TAKE_PROFIT"
    EXIT = "EXIT"
    REVERSE = "REVERSE"
    NO_TRADE = "NO_TRADE"


class OptionType(str, Enum):
    CE = "CE"
    PE = "PE"


class RunMode(str, Enum):
    BACKTEST = "backtest"
    PAPER = "paper"
    LIVE = "live"


class TimeBucket(str, Enum):
    PRE_OPEN = "PRE_OPEN"
    OPEN = "OPEN"
    MORNING = "MORNING"
    MIDDAY = "MIDDAY"
    AFTERNOON = "AFTERNOON"
    CLOSING = "CLOSING"
    POST_CLOSE = "POST_CLOSE"


# ---------- market data ----------

class OptionQuote(BaseModel):
    model_config = ConfigDict(frozen=False)
    symbol: str
    exchange: str = "NSE"
    expiry: date
    strike: float
    option_type: OptionType
    ltp: float
    bid: Optional[float] = None
    ask: Optional[float] = None
    bid_qty: Optional[int] = None
    ask_qty: Optional[int] = None
    volume: int = 0
    oi: int = 0
    oi_change: int = 0
    iv: Optional[float] = None              # annualised IV (0-1)
    delta: Optional[float] = None
    gamma: Optional[float] = None
    theta: Optional[float] = None
    vega: Optional[float] = None
    last_updated: datetime


class IndexQuote(BaseModel):
    model_config = ConfigDict(frozen=False)
    symbol: str = "NIFTY"
    ltp: float
    prev_close: Optional[float] = None
    open: Optional[float] = None
    high: Optional[float] = None
    low: Optional[float] = None
    volume: int = 0
    vwap: Optional[float] = None
    last_updated: datetime
    # computed features populated by technical layer
    atr: Optional[float] = None
    adx: Optional[float] = None
    adx_slope: Optional[float] = None
    rsi: Optional[float] = None
    ema_fast: Optional[float] = None
    ema_mid: Optional[float] = None
    ema_slow: Optional[float] = None


class IndiaVIX(BaseModel):
    ltp: float
    prev_close: Optional[float] = None
    iv_percentile: Optional[float] = None   # rank of current VIX vs last N sessions
    last_updated: datetime


class MarketSnapshot(BaseModel):
    """A point-in-time snapshot of everything the engine needs to decide."""
    timestamp: datetime
    index: IndexQuote
    india_vix: Optional[IndiaVIX] = None
    option_chain: list[OptionQuote] = Field(default_factory=list)
    market_open: bool = True
    data_valid: bool = True                  # if False, engine MUST NO-TRADE
    data_invalid_reason: Optional[str] = None
    time_bucket: Optional[TimeBucket] = None


# ---------- features ----------

class MarketFeatures(BaseModel):
    """Computed features used by regime engine & strategy selector."""
    adx: float
    adx_slope: float
    ema_fast: float
    ema_mid: float
    ema_slow: float
    vwap: float
    rsi: float
    atr: float
    atr_pct: float                            # ATR / spot
    relative_volume: float = 1.0
    opening_range_high: Optional[float] = None
    opening_range_low: Optional[float] = None
    india_vix: Optional[float] = None
    iv_rank: Optional[float] = None           # 0-100
    iv_percentile: Optional[float] = None


class RegimeAssessment(BaseModel):
    market_regime: MarketRegime
    volatility_regime: VolatilityRegime
    confidence: float                          # 0-1
    reasons: list[str] = Field(default_factory=list)
    features_snapshot: MarketFeatures


# ---------- strategies ----------

class StrategyEvaluation(BaseModel):
    """Output of a strategy's evaluate() — used by selector to pick best."""
    strategy: StrategyName
    enabled: bool
    eligible: bool                             # passes hard filters
    direction: Optional[Literal["BULLISH", "BEARISH", "NEUTRAL"]] = None
    expected_gross_pnl: float = 0.0
    expected_loss: float = 0.0
    probability_of_success: float = 0.0
    risk_reward: float = 0.0
    confidence_score: float = 0.0
    transaction_cost_estimate: float = 0.0
    slippage_estimate: float = 0.0
    expected_net_value: float = 0.0            # the master number
    reasons: list[str] = Field(default_factory=list)


class OptionSelection(BaseModel):
    """The chosen contract + rationale."""
    selected: bool
    option: Optional[OptionQuote] = None
    score: float = 0.0
    reasons: list[str] = Field(default=list)


class Decision(BaseModel):
    """The final decision emitted by the engine per cycle."""
    timestamp: datetime
    action: DecisionAction
    strategy: StrategyName
    regime: MarketRegime
    volatility: VolatilityRegime
    option: Optional[OptionQuote] = None
    lots: int = 0
    premium_per_lot: float = 0.0
    total_premium: float = 0.0
    expected_net_value: float = 0.0
    expected_risk: float = 0.0
    confidence: float = 0.0
    stop_loss: Optional[float] = None
    take_profit: Optional[float] = None
    reasons: list[str] = Field(default_factory=list)
    explainability_block: str = ""             # human-readable explanation


# ---------- positions & journal ----------

class Position(BaseModel):
    """Live position in the book (paper or live)."""
    strategy: StrategyName
    option: OptionQuote
    lots: int
    entry_price: float
    entry_time: datetime
    side: Literal["BUY", "SELL"] = "BUY"
    stop_loss: Optional[float] = None
    take_profit: Optional[float] = None
    current_price: Optional[float] = None
    unrealised_pnl: float = 0.0
    status: Literal["OPEN", "EXITED"] = "OPEN"


class TradeRecord(BaseModel):
    """A completed trade, written to the journal."""
    trade_id: str
    entry_time: datetime
    exit_time: Optional[datetime] = None
    strategy: StrategyName
    regime_at_entry: MarketRegime
    vol_regime_at_entry: VolatilityRegime
    option_symbol: str
    strike: float
    option_type: OptionType
    expiry: date
    lots: int
    entry_price: float
    exit_price: Optional[float] = None
    gross_pnl: float = 0.0
    charges: float = 0.0
    net_pnl: float = 0.0
    slippage: float = 0.0
    entry_reasons: list[str] = Field(default_factory=list)
    exit_reason: Optional[str] = None
    holding_minutes: Optional[int] = None


class DecisionRecord(BaseModel):
    """Every cycle's decision (including NO-TRADE) — recorded."""
    timestamp: datetime
    action: DecisionAction
    strategy: StrategyName
    regime: MarketRegime
    volatility: VolatilityRegime
    confidence: float
    expected_net_value: float
    reasons: list[str]
    snapshot_summary: dict


__all__ = [
    "MarketRegime", "VolatilityRegime", "StrategyName", "DecisionAction",
    "OptionType", "RunMode", "TimeBucket",
    "OptionQuote", "IndexQuote", "IndiaVIX", "MarketSnapshot",
    "MarketFeatures", "RegimeAssessment",
    "StrategyEvaluation", "OptionSelection", "Decision",
    "Position", "TradeRecord", "DecisionRecord",
]
