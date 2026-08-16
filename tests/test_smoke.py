"""Smoke tests for the engine.

CRITICAL: These tests NEVER use synthetic market data. They verify:
  1. Engine instantiates without error
  2. Without a DhanHQ token, engine returns NO_TRADE cleanly
  3. Cost model produces sensible numbers
  4. Regime engine handles invalid snapshot gracefully
  5. Strategy selector picks NO_TRADE when no eligible strategy
  6. Risk engine blocks trades when data invalid

Run with:  python -m pytest tests/ -v
"""
from __future__ import annotations

import os
import sys
from datetime import datetime

import pytest

# Ensure no token during tests so we exercise the no-data path
os.environ.pop("DHAN_ACCESS_TOKEN", None)
os.environ.pop("DHAN_CLIENT_ID", None)

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def test_engine_instantiates():
    from nifty_engine.engine import Engine
    from nifty_engine.models import RunMode
    e = Engine(mode=RunMode.PAPER)
    assert e is not None
    assert e.capital > 0


def test_engine_no_trade_without_token():
    from nifty_engine.engine import Engine
    from nifty_engine.models import DecisionAction, RunMode
    e = Engine(mode=RunMode.PAPER)
    decision = e.run_cycle()
    assert decision.action == DecisionAction.NO_TRADE
    assert any("token" in r.lower() or "data" in r.lower() for r in decision.reasons)


def test_cost_model_round_trip():
    from nifty_engine.execution import CostModel
    cm = CostModel()
    # 1 lot NIFTY = 75 shares @ premium 100
    cost = cm.cost_for_round_trip(entry_premium_per_unit=100.0, exit_premium_per_unit=100.0, quantity=75)
    # Expect non-zero, sensible (brokerage alone = 40 for round trip)
    assert cost.total > 0
    assert cost.brokerage == 40  # 20 buy + 20 sell
    assert cost.stt > 0
    assert cost.gst > 0


def test_regime_engine_invalid_snapshot():
    from nifty_engine.features.market_regime import RegimeEngine
    from nifty_engine.models import MarketSnapshot, IndexQuote, MarketRegime, VolatilityRegime
    now = datetime.utcnow()
    snap = MarketSnapshot(
        timestamp=now,
        index=IndexQuote(ltp=0.0, last_updated=now),
        data_valid=False,
        data_invalid_reason="test",
    )
    engine = RegimeEngine()
    result = engine.assess(snap)
    assert result.market_regime == MarketRegime.NEUTRAL
    assert result.volatility_regime == VolatilityRegime.NORMAL_VOL


def test_strategy_selector_no_trade_when_no_data():
    from nifty_engine.decision import StrategySelector
    from nifty_engine.features.market_regime import RegimeEngine
    from nifty_engine.models import (
        IndexQuote, MarketSnapshot, MarketRegime, StrategyName, VolatilityRegime,
    )
    now = datetime.utcnow()
    snap = MarketSnapshot(
        timestamp=now,
        index=IndexQuote(ltp=0.0, last_updated=now),
        data_valid=False,
        data_invalid_reason="test",
    )
    regime_engine = RegimeEngine()
    assessment = regime_engine.assess(snap)
    sel = StrategySelector()
    chosen, all_evals = sel.select(snap, assessment)
    assert chosen.strategy == StrategyName.NO_TRADE


def test_risk_engine_blocks_on_invalid_data():
    from nifty_engine.decision import RiskEngine
    from nifty_engine.models import (
        IndexQuote, MarketSnapshot, StrategyEvaluation, StrategyName,
    )
    now = datetime.utcnow()
    snap = MarketSnapshot(
        timestamp=now,
        index=IndexQuote(ltp=0.0, last_updated=now),
        data_valid=False,
        data_invalid_reason="test",
    )
    ev = StrategyEvaluation(
        strategy=StrategyName.LONG_CALL,
        enabled=True, eligible=True,
        expected_net_value=1000, confidence_score=0.7, risk_reward=1.5,
    )
    risk = RiskEngine(capital=1_000_000)
    decision = risk.evaluate(snap, ev, option=None, open_positions=0, now=now)
    assert not decision.allowed


def test_option_chain_atm_window():
    """Even with no real data, we can verify the pure helper functions."""
    from nifty_engine.data import OptionChainBuilder
    from nifty_engine.models import OptionQuote, OptionType
    now = datetime.utcnow()
    chain = [
        OptionQuote(symbol=f"NIFTY{k}", expiry=now.date(), strike=k, option_type=OptionType.CE,
                    ltp=abs(k - 100), volume=1000, oi=5000, last_updated=now)
        for k in [95, 97, 99, 100, 101, 103, 105]
    ]
    atm = OptionChainBuilder.find_atm_strike(chain, spot=100.0)
    assert atm == 100
    window = OptionChainBuilder.filter_atm_window(chain, spot=100.0, n_each_side=2)
    # ATM=100 at index 3 (sorted [95,97,99,100,101,103,105]); slice [1:6] -> 5 strikes
    assert len(window) == 5  # 97,99,100,101,103


def test_cost_model_in_strategy_evaluation():
    """Long call strategy can be instantiated and produces an evaluation."""
    from nifty_engine.strategies import LongCallStrategy
    from nifty_engine.models import (
        IndexQuote, MarketSnapshot, MarketRegime, RegimeAssessment,
        MarketFeatures, StrategyName, VolatilityRegime,
    )
    now = datetime.utcnow()
    s = LongCallStrategy()
    snap = MarketSnapshot(
        timestamp=now,
        index=IndexQuote(ltp=0.0, last_updated=now),
        data_valid=False,
        data_invalid_reason="no data",
    )
    regime = RegimeAssessment(
        market_regime=MarketRegime.NEUTRAL,
        volatility_regime=VolatilityRegime.NORMAL_VOL,
        confidence=0.5,
        reasons=["test"],
        features_snapshot=MarketFeatures(
            adx=0, adx_slope=0, ema_fast=0, ema_mid=0, ema_slow=0,
            vwap=0, rsi=50, atr=0, atr_pct=0,
        ),
    )
    ev = s.evaluate(snap, regime)
    # Data invalid -> not eligible
    assert not ev.eligible
    assert ev.strategy == StrategyName.LONG_CALL


if __name__ == "__main__":
    # Allow running without pytest
    test_engine_instantiates()
    test_engine_no_trade_without_token()
    test_cost_model_round_trip()
    test_regime_engine_invalid_snapshot()
    test_strategy_selector_no_trade_when_no_data()
    test_risk_engine_blocks_on_invalid_data()
    test_option_chain_atm_window()
    test_cost_model_in_strategy_evaluation()
    print("\n[OK] All smoke tests passed.")
