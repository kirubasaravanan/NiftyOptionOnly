# Adaptive NIFTY Options Trading System

A fully algorithmic NIFTY options trading system that dynamically decides **whether to trade or stay in cash** based on market regime, volatility regime, expected net edge, liquidity, and risk.

> **NO-TRADE is a valid strategy.** Cash is a position. The system never forces a trade.

## Phase 1-5 (this build)

- **Data layer:** DhanHQ adapter behind a broker-neutral interface (Kite / Angel / Fyers can be added later). Real data only — never synthetic.
- **Market cache:** in-memory TTL cache to avoid redundant broker calls.
- **Regime engine:** classifies direction (12 states) and volatility (5 states) independently.
- **Strategies:** Long Call, Long Put, NO_TRADE — all gated by configurable thresholds.
- **Risk engine:** per-trade / daily / portfolio caps, cooldowns, emergency shutdown. No martingale, no averaging into losers.
- **Cost model:** full NSE NIFTY options cost stack — brokerage, STT, exchange charges, GST, SEBI, stamp duty, slippage, spread impact.
- **Journal:** every cycle (including NO-TRADE) is journaled to JSONL.
- **Performance analytics:** win rate, expectancy, profit factor, max drawdown, P&L breakdowns by strategy / regime / volatility.
- **CLI:** `run`, `watch`, `status`, `report`.

## Quick start

```bash
# 1. Install deps
pip install -r requirements.txt

# 2. (When market reopens) add your DhanHQ credentials
cp .env.example .env
# Edit .env: DHAN_ACCESS_TOKEN=...  DHAN_CLIENT_ID=...

# 3. Run one decision cycle (today is a holiday -> NO-TRADE)
python -m nifty_engine.cli run --mode paper

# 4. Continuous loop
python -m nifty_engine.cli watch --mode paper --interval 30

# 5. Show risk + open positions
python -m nifty_engine.cli status

# 6. Show performance report
python -m nifty_engine.cli report

# 7. Smoke tests
python tests/test_smoke.py
```

## Today (holiday behaviour)

Running the engine without a token or on a holiday produces a clean NO-TRADE:

```
 ACTION         NO_TRADE
 STRATEGY       NO_TRADE
 REGIME         NEUTRAL
 VOLATILITY     NORMAL_VOL
 LOTS           0
 EXPECTED NET   ₹0
 EXPECTED RISK  ₹0
 CONFIDENCE     1.00

Reasons:
  - reconciliation failed: DHAN_ACCESS_TOKEN / DHAN_CLIENT_ID not set
    OR dhanhq not installed
```

This is correct behaviour — the spec says NO-TRADE is always valid when real data is unavailable.

## Architecture

```
nifty_engine/
  config/        # YAML configs (risk, strategies, broker, trading_hours, costs)
  models/        # Pydantic schemas (single source of truth)
  data/          # BrokerInterface + DhanBroker + MarketCache + OptionChainBuilder
  features/      # Technical (ADX/RSI/EMA/ATR/VWAP) + Volatility + RegimeEngine
  strategies/    # StrategyBase + LongCall + LongPut + NoTrade
  decision/      # RegimeRunner + StrategySelector + OptionSelector + RiskEngine + PositionManager
  execution/     # OrderManager + CostModel + Reconciler + BrokerOrderAdapter
  journal/       # TradeLogger + DecisionLogger + PerformanceAnalytics
  utils/         # IST time utilities, market open / holiday detection
  engine.py      # Engine.run_cycle() — single entry point
  cli.py         # CLI: run / watch / status / report
```

Full architecture spec: [`download/NIFTY_Adaptive_Options_System_Architecture.pdf`](download/NIFTY_Adaptive_Options_System_Architecture.pdf)

## Decision hierarchy (per cycle)

1. Fetch market snapshot (index + VIX + option chain)
2. Reconciliation gate (stale data, abnormal spread, API failure → NO-TRADE / SHUTDOWN)
3. Trading-hours gate (holiday, weekend, PRE_OPEN, POST_CLOSE → NO-TRADE)
4. Regime assessment (direction + volatility, with reasons)
5. Strategy selection (highest expected_net_value that clears thresholds)
6. Option selection (score ATM / 1-ITM / 1-OTM by delta / IV / liquidity / OI / spread)
7. Risk evaluation (position sizing, stop, target)
8. Execute (paper fills instantly at mid-1pt slippage; live in Phase 9)
9. Journal (decision + snapshot summary)

## Configurable thresholds (no hard-coded trading rules)

| File | What lives there |
|------|-------------------|
| `config/risk.yaml` | Capital, per-trade risk %, daily loss limit, cooldown, emergency shutdown triggers |
| `config/strategies.yaml` | Strategy enable/disable, min expected net value, min confidence, min R/R |
| `config/broker.yaml` | DhanHQ endpoint, NIFTY instrument token, rate limits |
| `config/trading_hours.yaml` | Trading session, holidays, time-bucket threshold multipliers |
| `config/costs.yaml` | Brokerage, STT, exchange charges, GST, SEBI, stamp duty, slippage rates |

## What is forbidden (by design)

- Martingale, averaging into losing positions
- Forcing a trade every day
- Reporting gross P&L as profit (only NET matters)
- Using future information in backtesting
- Synthetic / mock market data
- Hard-coded trading thresholds in strategy code
- Auto-activating every options strategy

## Next steps (when market reopens)

1. Set `DHAN_ACCESS_TOKEN` and `DHAN_CLIENT_ID` in `.env`
2. Run `watch` during a live session to capture real decision cycles
3. Build Phase 6 (backtesting engine with DhanHQ historical candle API)
4. Walk-forward validate Long Call / Long Put thresholds before scaling capital
5. Only after positive out-of-sample expectancy, enable Phase 10 (Debit Spreads)
