---
Task ID: 1
Agent: main (super-z)
Task: Build Adaptive NIFTY Options Trading System — Phase 1-5 (data, features, regime, Long CE/PE + NO-TRADE, paper mode). Real data only — DhanHQ adapter wired but token optional. On holiday/no-token, system emits NO-TRADE and exits cleanly.

Work Log:
- Created project structure under /home/z/my-project/nifty_engine/ with config/, models/, data/, features/, strategies/, decision/, execution/, journal/, utils/
- Wrote 5 YAML configs: risk, strategies, broker, trading_hours, costs — all thresholds externalised
- Defined pydantic schemas (models/__init__.py) as the single source of truth for data contracts across layers
- Implemented BrokerInterface (abstract) + DhanBroker concrete adapter — never fabricates data; returns data_valid=False snapshot on any failure
- Built OptionChainBuilder (ATM window filter, OI structure, max-pain, liquidity score) and MarketCache (TTL=5s)
- Implemented TechnicalCalculator (EMA / RSI / ATR / ADX with slope / VWAP / relative volume) — pure functions, no TA-Lib dep
- Implemented VolatilityCalculator (ATM IV aggregation, IV/VIX percentile, expansion/contraction detection)
- Implemented RegimeEngine combining direction (12 states) + volatility (5 states) with reasons + confidence
- Wrote StrategyBase ABC + LongCallStrategy + LongPutStrategy + NoTradeStrategy — all thresholds read from config
- Wrote decision layer: StrategySelector (picks highest expected_net_value, NO_TRADE fallback), OptionSelector (scores ATM/ITM/OTM by delta/IV/liquidity/OI/spread/premium), RiskEngine (per-trade/daily/portfolio caps, cooldowns, emergency shutdown, no-martingale enforcement), PositionManager (HOLD/ADD/REDUCE/MOVE_STOP/TAKE_PROFIT/EXIT/REVERSE — no averaging losers)
- Wrote execution layer: CostModel (full NSE NIFTY cost stack — brokerage/STT/exchange/GST/SEBI/stamp/slippage/spread), OrderManager (paper fills at mid-1pt slippage; live stubbed for Phase 9), Reconciler (stale data / abnormal spread detection)
- Wrote journal: TradeLogger + DecisionLogger (JSONL append-only) + PerformanceAnalytics (win rate / expectancy / profit factor / max drawdown / P&L by strategy/regime/volatility)
- Wrote Engine.run_cycle() orchestrator following spec section 28 decision hierarchy
- Wrote CLI: `run`, `watch`, `status`, `report` commands with rich console output
- Wrote 8 smoke tests in tests/test_smoke.py — all pass; verified no-token path emits NO-TRADE
- Generated architecture PDF deliverable: download/NIFTY_Adaptive_Options_System_Architecture.pdf (7 pages, includes pipeline diagram, layer responsibilities, cost model table, dev phases, restrictions, CLI usage)

Stage Summary:
- Engine instantiates and runs cleanly with no token / on holiday — emits NO-TRADE with full reasons + journals to runs/decisions/decisions.jsonl
- Smoke tests pass: 8/8
- Architecture PDF saved to /home/z/my-project/download/
- Ready for next phase: when DhanHQ token is provided and market reopens, `python -m nifty_engine.cli watch --mode paper --interval 30` will produce live decision cycles
- All thresholds externalised to YAML — strategy code contains NO hard-coded trading rules (per spec point: "Don't let Z.ai decide the strategy rules by itself")
- Cost model is the single source of truth for NET P&L — used identically in backtest, paper, and live
- NO synthetic data anywhere in the codebase — when real data is unavailable, system stays in cash
