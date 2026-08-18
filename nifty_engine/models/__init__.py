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
    security_id: Optional[int] = None       # DhanHQ's own instrument ID — required
                                             # to place/track/reconcile a real order
                                             # against this exact contract. None for
                                             # backtest/synthetic quotes.
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
    required_move_points: Optional[float] = None  # underlying move (pts) needed to clear
                                                    # min_expected_net_value, all else equal
                                                    # (transparency; not itself a trade signal)
    reasons: list[str] = Field(default_factory=list)


class OptionSelection(BaseModel):
    """The chosen contract + rationale (single-leg strategies)."""
    selected: bool
    option: Optional[OptionQuote] = None
    score: float = 0.0
    reasons: list[str] = Field(default=list)


class SpreadSelection(BaseModel):
    """The chosen 2-leg spread + rationale (debit spreads, straddles, etc.)."""
    selected: bool
    long_leg: Optional[OptionQuote] = None
    short_leg: Optional[OptionQuote] = None
    net_debit: float = 0.0           # net premium paid per unit
    spread_width: float = 0.0        # strike difference between legs
    max_loss: float = 0.0            # = net_debit * qty (defined risk)
    max_gain: float = 0.0            # = (spread_width - net_debit) * qty
    breakeven: float = 0.0           # long strike + net_debit (for call spread)
    score: float = 0.0
    reasons: list[str] = Field(default=list)


class Decision(BaseModel):
    """The final decision emitted by the engine per cycle."""
    timestamp: datetime
    action: DecisionAction
    strategy: StrategyName
    regime: MarketRegime
    volatility: VolatilityRegime
    option: Optional[OptionQuote] = None             # single-leg
    long_leg: Optional[OptionQuote] = None           # multi-leg (Phase 10+)
    short_leg: Optional[OptionQuote] = None          # multi-leg (Phase 10+)
    lots: int = 0
    premium_per_lot: float = 0.0
    total_premium: float = 0.0
    expected_net_value: float = 0.0
    expected_risk: float = 0.0
    max_loss: Optional[float] = None                  # defined risk for spreads
    max_gain: Optional[float] = None                  # capped reward for spreads
    breakeven: Optional[float] = None                 # spread breakeven
    confidence: float = 0.0
    stop_loss: Optional[float] = None
    take_profit: Optional[float] = None
    reasons: list[str] = Field(default_factory=list)
    explainability_block: str = ""             # human-readable explanation


# ---------- positions & journal ----------

class Position(BaseModel):
    """Live position in the book (paper or live).

    For single-leg (Long Call/Put): use `option` + `entry_price`.
    For multi-leg spreads (Phase 10+): use `long_leg` + `short_leg` +
    `net_debit_per_unit`. The `option` field will be set to the long leg
    for backward-compat with downstream code that expects a single option.
    """
    strategy: StrategyName
    instrument: str = "NIFTY"                        # NIFTY | BANKNIFTY | SENSEX (2026-08-18)
    option: Optional[OptionQuote] = None            # single-leg OR long leg of spread
    long_leg: Optional[OptionQuote] = None           # multi-leg (Phase 10+)
    short_leg: Optional[OptionQuote] = None          # multi-leg (Phase 10+)
    lots: int
    entry_price: float                                # for single-leg: premium; for spread: net debit
    entry_time: datetime
    regime_at_entry: Optional[MarketRegime] = None   # for the trade journal (TradeRecord requires it)
    vol_regime_at_entry: Optional[VolatilityRegime] = None
    side: Literal["BUY", "SELL"] = "BUY"
    stop_loss: Optional[float] = None
    take_profit: Optional[float] = None
    current_price: Optional[float] = None            # single-leg current premium OR spread net debit
    long_leg_current: Optional[float] = None         # multi-leg current long leg premium
    short_leg_current: Optional[float] = None        # multi-leg current short leg premium
    spread_width: Optional[float] = None             # multi-leg strike width
    max_loss: Optional[float] = None                 # defined risk for spreads
    max_gain: Optional[float] = None                 # capped reward for spreads
    breakeven: Optional[float] = None                # spread breakeven
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
    # Observational (2026-08-18): every candidate strategy's own numbers for
    # this cycle, not just whichever one got chosen — the chosen one is almost
    # always the synthetic NO_TRADE result, which has no expected_net_value or
    # required_move_points of its own. Without this, a bearish strategy's
    # near-miss numbers (e.g. LONG_PUT's expected_net / required_move on a day
    # that never traded) were computed every cycle and then discarded.
    all_evaluations: list[dict] = Field(default_factory=list)
    # Cross-market confirmation (VIX valuation, OI walls, futures basis,
    # BankNifty correlation) — also computed every cycle and previously only
    # used transiently to adjust confidence, never persisted.
    confirmation_summary: Optional[dict] = None


__all__ = [
    "MarketRegime", "VolatilityRegime", "StrategyName", "DecisionAction",
    "OptionType", "RunMode", "TimeBucket",
    "OptionQuote", "IndexQuote", "IndiaVIX", "MarketSnapshot",
    "MarketFeatures", "RegimeAssessment",
    "StrategyEvaluation", "OptionSelection", "SpreadSelection", "Decision",
    "Position", "TradeRecord", "DecisionRecord",
]
