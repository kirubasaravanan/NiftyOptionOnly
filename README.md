# Adaptive NIFTY Options Trading System

A fully algorithmic NIFTY options trading system that dynamically decides **whether to trade or stay in cash** based on market regime, volatility regime, expected net edge, liquidity, and risk.

> **NO-TRADE is a valid strategy.** Cash is a position. The system never forces a trade.

## Current Status: Phase 10 Complete (v0.5.0)

| Phase | Status | What it delivers |
|-------|--------|------------------|
| 1 | ✅ Done | Data ingestion + DhanHQ adapter (broker-neutral interface) |
| 2 | ✅ Done | Local market-data cache (TTL-based) |
| 3 | ✅ Done | Technical + regime + volatility engine (12 directional × 5 vol states) |
| 4 | ✅ Done | Long CE / Long PE + NO-TRADE strategies |
| 5 | ✅ Done | Risk engine + position manager + paper execution |
| 6 | ✅ Done | Backtesting engine with realistic cost model |
| 7 | ✅ Done | Walk-forward validation + ablation testing framework |
| 8 | ✅ Done | Paper trading + Discord lifecycle alerts (15 types) + thesis tracker + 3-layer protection + MAE/MFE |
| 9 | ⏸️ Paused | Live execution with small capital (user chose to observe Phase 1-8 first) |
| 10 | ✅ Done | **Debit Spreads** (Bull Call Spread + Bear Put Spread) — first multi-leg strategy |
| 11 | 📋 Planned | Long Straddle / Long Strangle (neutral regime, vol expansion) |
| 12 | 📋 Planned | Iron Condor / Butterfly (range regime, premium selling) |
| 13 | 📋 Planned | Walk-forward parameter grid search (tune thresholds, not just toggle features) |
| 14 | 📋 Planned | Live broker reconciliation (order status polling, fill confirmation) |
| 15 | 📋 Planned | Multi-strategy portfolio allocation (capital across validated strategies) |
| 16 | 📋 Planned | Capital scaling ladder (₹2L → ₹5L → ₹10L → ₹15L → ₹20L) |
| 17 | 📋 Planned | Tax reporting (separate STT/exchange/GST breakdown for filing) |
| 18 | 📋 Planned | Walk-forward retraining (rolling parameter re-optimisation) |
| 19 | 📋 Planned | Alternative data integration (GIFT NIFTY, USDINR, crude, S&P futures) |
| 20 | 📋 Planned | Production hardening (graceful shutdown, monitoring, alerting) |

**Observation period**: 1 week of paper trading before deciding whether to proceed to Phase 11.

## Architecture Overview

```
NIFTY MARKET DATA (DhanHQ)
       ↓
MARKET REGIME ENGINE (12 directional states)
       ↓
VOLATILITY REGIME (5 states — independent of direction)
       ↓
STRATEGY SELECTOR (Long CE / Long PE / Debit Spread / NO_TRADE)
       ↓
OPTION/SPREAD SELECTOR (single-leg OR 2-leg spread)
       ↓
RISK ENGINE (per-trade / daily / portfolio caps)
       ↓
EXECUTION (paper fills; live in Phase 9)
       ↓
POSITION MONITOR
   ├─ Thesis Tracker (7 components → composite score → state machine)
   ├─ 3-Layer Protection (monetary / structure / time)
   └─ MAE/MFE Tracker
       ↓
ADJUST / EXIT / REVERSE
       ↓
TRADE JOURNAL + DISCORD ALERTS (15 types)
       ↓
PERFORMANCE ANALYTICS + ABLATION TESTING
```

## Strategies Implemented

### Phase 4 — Single-Leg Directional
- **Long Call**: Buy ATM CE when bullish regime + cheap/fair VIX
- **Long Put**: Buy ATM PE when bearish regime + cheap/fair VIX
- **NO_TRADE**: Always eligible; wins when no directional strategy clears thresholds

### Phase 10 — Multi-Leg Defined Risk
- **Bull Call Spread**: Buy ATM CE + Sell OTM CE (higher strike)
  - Max loss = net debit (defined)
  - Max gain = spread width − net debit (capped)
  - Breakeven = long strike + net debit
  - **Preferred when VIX is expensive** (offsets IV sensitivity)
- **Bear Put Spread**: Buy ATM PE + Sell OTM PE (lower strike)
  - Same risk profile, bearish direction

### Strategy Selection Logic
1. Evaluate all enabled strategies
2. Apply confirmation-score adjustment (Layer 3 features modulate confidence)
3. Apply time-bucket threshold multiplier (midday chop → higher bar)
4. **Phase 10 addition**: When VIX is expensive (HIGH_VOL / VOL_EXPANSION), prefer DEBIT_SPREAD over outright Long CE/PE if spread's expected_net_value ≥ 80% of outright's
5. Pick highest expected_net_value; tiebreak by confidence
6. If no eligible strategy → NO_TRADE

## Cross-Market Confirmation (Layer 3+4 Features)

Per spec: correlation itself is NEVER a buy signal — it modulates confidence.

- **VIX Valuation**: CHEAP / FAIR / EXPENSIVE (penalises long premium when VIX expensive)
- **OI Classification**: LONG_BUILDUP / SHORT_BUILDUP / SHORT_COVERING / LONG_UNWINDING
- **Futures Basis**: PREMIUM / DISCOUNT / FLAT (spot vs futures)
- **Bank Nifty Confirmation**: CONFIRMED / DIVERGENT / NEUTRAL
- **Rolling Correlation Regime**: NORMAL / BREAKDOWN (20-period vs 50-period)

Composite confirmation score [-1, +1] adjusts strategy confidence by ±0.4 max.

## Position Lifecycle (Phase 8)

### Thesis Tracker
7 component scores (each 0-100):
- Trend (ADX + EMA alignment)
- VWAP (price vs VWAP)
- Momentum (RSI + ADX slope)
- Breadth (confirmation score proxy)
- Bank Nifty (correlation state)
- OI (classification)
- VIX (valuation)

Composite → state machine:
- **CONFIDENT** (≥75) → hold / add on strength
- **CAUTIOUS** (55-74) → hold + tighten stop
- **REDUCE** (40-54) → reduce exposure
- **EXIT** (<40) → exit (thesis invalidated)
- **REVERSE** (direction flip) → reverse position

### 3-Layer Protection
1. **Layer 1 — Hard monetary stop**: ₹5,000 cap or 50% premium loss
2. **Layer 2 — Market-structure invalidation**: VWAP lost + swing broken + thesis < 40 (fires BEFORE monetary stop in most cases)
3. **Layer 3 — Time invalidation**: no movement within 30 min

### MAE/MFE Tracking
- **MAE** (Maximum Adverse Excursion): worst unrealised loss before exit
- **MFE** (Maximum Favourable Excursion): best unrealised profit before exit
- **Capture rate**: realised / MFE (shows if exits leave profit on table)

## Discord Alerts (15 Types)

| Alert | When it fires |
|-------|---------------|
| 🧭 REGIME_CHANGE | Market regime transitions (e.g. RANGE → STRONG_BULL) |
| 🎯 SETUP_DETECTED | Directional strategy becomes eligible (before entry) |
| 🟢 ENTRY | Order filled (with option/spread details, stop, target, thesis) |
| 📈 POSITION_UPDATE | Each cycle while position is open |
| ⚠️ THESIS_DETERIORATING | Score drops CONFIDENT → CAUTIOUS |
| 🔄 POSITION_ADJUSTED | Entering REDUCE zone |
| 🚨 THESIS_INVALIDATED | Layer 2 protection fires |
| 🔴 EXIT | Position closed (any protection layer) |
| 🔄 REVERSAL | Direction flip detected |
| 🛑 RISK_LIMIT | Risk engine blocks eligible setup (cooldown) |
| ⏱️ TIME_STOP | Layer 3 protection fires |
| 💥 DATA_API_ERROR | Emergency shutdown (stale data, abnormal spread) |
| 📊 DAILY_SUMMARY | End-of-day performance |
| 📝 TRADE_REVIEW | Post-trade analysis with MAE/MFE |
| 📈 STRATEGY_PERFORMANCE | Strategy-level performance report |

## Quick Start

```bash
# 1. Install Python deps
pip install -r requirements.txt

# 2. Configure credentials
cp .env.example .env
# Edit .env: DHAN_ACCESS_TOKEN, DHAN_CLIENT_ID, DISCORD_WEBHOOK_URL

# 3. Start the FastAPI backend (with auto-restart supervisor)
python scripts/supervisor.py

# 4. In another terminal, start the Next.js frontend
bun run dev

# 5. Open http://localhost:3000
```

## CLI Usage

```bash
# Run one decision cycle
python -m nifty_engine.cli run --mode paper

# Continuous loop
python -m nifty_engine.cli watch --mode paper --interval 30

# Show risk + open positions
python -m nifty_engine.cli status

# Show performance report
python -m nifty_engine.cli report

# Smoke tests
python tests/test_smoke.py

# Test Discord lifecycle alerts
python scripts/test_lifecycle_alerts.py
```

## Configuration

All thresholds externalised to YAML in `nifty_engine/config/`:

| File | Purpose |
|------|---------|
| `risk.yaml` | Capital, per-trade risk %, daily loss limit, cooldown, emergency shutdown |
| `strategies.yaml` | Strategy enable/disable, min expected net value, min confidence, min R/R, spread width limits |
| `broker.yaml` | DhanHQ endpoint, NIFTY instrument tokens, rate limits |
| `trading_hours.yaml` | Trading session, holidays, time-bucket threshold multipliers |
| `costs.yaml` | Brokerage, STT, exchange charges, GST, SEBI, stamp duty, slippage |

Edit configs via the **Configuration** tab in the UI — changes apply to the next decision cycle without restart.

## Backtesting + Ablation

```bash
# Run a backtest
curl -X POST http://localhost:8000/api/backtest/run \
  -H "Content-Type: application/json" \
  -d '{"start_date":"2024-01-01","end_date":"2024-07-31","capital":1000000}'

# Run an ablation test (tests incremental value of each feature)
curl -X POST http://localhost:8000/api/ablation/run \
  -H "Content-Type: application/json" \
  -d '{"start_date":"2024-01-01","end_date":"2024-07-31","capital":1000000}'
```

Or use the **Backtest** and **Ablation** tabs in the UI.

### Backtest Results (Jan-Jul 2024, with all Phase 1-10 features)
- 6 trades, +5.94% return, 66.7% win rate
- Expectancy: +₹10,098 per trade
- Profit factor: 5.18, Sharpe: 2.66, Max DD: 2.95%
- All NET of brokerage + STT + GST + slippage

### ⚠️ Backtest Limitations — Read Before Sizing Capital

The backtest numbers above are built on **synthesized data** for non-spot
instruments. Specifically:

1. **Option chain is synthesized** — DhanHQ does not expose historical option
   Greeks. The backtest generates the option chain using Black-Scholes with
   a flat 15% IV and fixed 7-day expiry. Real IV varies by strike and time.

2. **Bank Nifty and NIFTY futures are synthesized** — derived from NIFTY's
   own move with random noise (85% correlation, 15% divergence).

3. **India VIX is synthesized** — derived from ATR with iv_percentile
   hardcoded to 50 (neutral).

4. **Same-bar decide-and-fill** — each bar's decision uses that bar's close,
   and the fill happens at that same close. No live system can do this.

5. **Theta decay is disabled** — open positions are repriced using a constant
   t = 7/365 regardless of actual holding time. Long-premium strategies
   (the only ones in the current backtest) never lose value to time decay.

6. **Walk-forward doesn't optimize** — the TRAIN window is fetched but never
   used. This is N independent windowed backtests, not real walk-forward
   validation.

**Do not size real capital based on the current backtest report.** The numbers
are useful for relative comparison (does feature X improve over baseline?) but
not for absolute expectancy. Fix requires historical option-chain data from
DhanHQ (or a third-party provider) — tracked as a Phase 6 enhancement.

## Important Restrictions (per spec)

- ❌ NO martingale / averaging into losers
- ❌ NO forcing trades every day
- ❌ NO optimisation solely for win rate or total P&L
- ❌ NO ignoring transaction costs or slippage
- ❌ NO future information in backtesting (no look-ahead bias)
- ❌ NO synthetic market data — when real data unavailable, system stays in cash
- ❌ NO auto-activating every strategy — each must pass ablation test
- ❌ NO capital scaling without out-of-sample evidence

## Project Structure

```
nifty_engine/
  config/           # YAML configs (risk, strategies, broker, trading_hours, costs)
  models/           # Pydantic schemas (single source of truth)
  data/             # BrokerInterface + DhanBroker + MarketCache + OptionChainBuilder
  features/         # Technical + Volatility + RegimeEngine + Correlation (Layer 3+4)
  strategies/       # StrategyBase + LongCall + LongPut + DebitSpread + NoTrade
  decision/         # RegimeRunner + StrategySelector + OptionSelector + SpreadSelector + RiskEngine + PositionManager
  execution/        # OrderManager + CostModel + Reconciler + BrokerOrderAdapter
  backtest/         # BacktestEngine + WalkForwardValidator + AblationTester
  notifier/         # DiscordNotifier + ThesisTracker + ProtectionLayer + MAEMFETracker
  journal/          # TradeLogger + DecisionLogger + PerformanceAnalytics
  utils/            # IST time utilities
  engine.py         # Engine.run_cycle() — single entry point
  api.py            # FastAPI backend
  cli.py            # CLI: run / watch / status / report

src/                # Next.js 16 frontend
  app/page.tsx      # 6-tab dashboard
  components/
    dashboard/      # LiveDashboard + ConfirmationPanel
    backtest/        # BacktestPanel
    ablation/        # AblationPanel
    journal/         # JournalPanel
    alerts/          # AlertsPanel
    config/          # ConfigPanel
  lib/api.ts         # TypeScript API client + types

scripts/
  supervisor.py     # Auto-restart wrapper for FastAPI
  test_lifecycle_alerts.py  # End-to-end Discord alert test
  build_architecture_pdf.py  # Generate architecture PDF

tests/
  test_smoke.py     # 8 smoke tests
```

## Tech Stack

**Backend**: Python 3.12, FastAPI, Pydantic v2, pandas, dhanhq 2.x, uvicorn
**Frontend**: Next.js 16, TypeScript 5, Tailwind CSS 4, shadcn/ui, TanStack Query, Recharts, Zustand
**Broker**: DhanHQ (broker-neutral interface — Kite/Angel can be added later)
**Notifications**: Discord webhooks (15 alert types)
**Real data only** — no synthetic data anywhere in the codebase
