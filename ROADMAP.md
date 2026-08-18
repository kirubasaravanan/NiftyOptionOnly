# Long-Term Roadmap — Multi-Instrument Automated Options Portfolio

**Status**: Planning document. Not yet started. Captured 2026-08-17 for incremental execution.
**Relationship to `worklog.md`**: `worklog.md` and the README's Phase 1–20 table track feature depth *within* the current NIFTY-only engine (more strategies, better risk handling, live execution). This document tracks a different, orthogonal axis of growth: *which instruments the system trades* and *how capital gets allocated across them once there's more than one*. The two roadmaps run in parallel — this one assumes the NIFTY engine keeps maturing per the existing phase table while these stages layer on top.

**Goal, stated plainly**: not "make money from NIFTY options" — build an automated options portfolio that continuously searches for the best statistically validated opportunity across instruments while controlling correlated risk. No instrument is inherently profitable; the edge comes from market selection + regime detection + strategy selection + execution + risk management + cost control, combined.

## The gate: NIFTY is the MVP

Explicit decision (2026-08-17): **NIFTY-only is the MVP. Nothing past Stage 1 gets built unless NIFTY is actually proven profitable first** — not "acceptable backtest metrics," actual demonstrated profitability under real conditions. This is a harder, simpler bar than "positive expectancy + acceptable drawdown + stable out-of-sample" and it's the one that governs: if NIFTY doesn't prove out, the rest of this document doesn't get built. No sunk-cost continuation into BANKNIFTY/SENSEX/stocks/portfolio-optimizer just because the architecture was planned — the plan only activates on proof, not on schedule.

What "proven profitable" should mean in practice for this gate, given everything found in this session: real trades, real DhanHQ market data, over a long enough sample to mean something (not the 6–10 trades seen in one afternoon) — and explicitly *not* the current backtest engine's numbers, since those rest on synthesized option-chain/Bank Nifty/futures data and are already flagged as unreliable for sizing any decision. Until that backtest gap is closed or the paper/live track record itself is long enough to stand on its own, "profitable" needs to be measured from the trade journal, not the backtest report.

**Explicit, conscious exception (2026-08-18)**: the "Expiry-Day Strategy Coverage" research track below is authorized to start now, in parallel with continued NIFTY MVP observation — not queued behind the gate. This is a deliberate scope decision, not a drift. The gate still fully applies to *trading*: no new strategy (Bear Call Spread, Bull Put Spread, Iron Condor, or anything else born from this track) goes live or into paper execution until it clears its own out-of-sample validation. What's unblocked is the *engineering and research* work — building the coverage engine, the expiry-specific regime logic, and the backtest infrastructure to test it honestly — not trading on it early.

**Second, larger conscious exception (2026-08-18, later same day)**: BANKNIFTY + SENSEX data-layer/config/engine support built and live-verified — see "BANKNIFTY + SENSEX build" section below. This overrides the gate's own "nothing past Stage 1" language for *infrastructure*, the same way the expiry-day exception did — **trading discipline is unchanged**: both instruments run in paper mode with unvalidated, copied-from-NIFTY thresholds, and neither should be treated as trustworthy for sizing or go-live decisions until independently validated per the "Known roadblocks" section below. This was an explicit, conscious user decision (confirmed via AskUserQuestion after an explicit regression-risk callout), not a drift.

---

## Why instrument order matters

The question isn't "which option has the highest profit potential" — it's *which market has enough liquidity, predictable behavior, good data, manageable execution costs, and enough repeatable opportunity for an algorithm to extract an edge*. Ranked for automation potential:

| Instrument | Automation potential | Liquidity | Strategy diversity | Note |
|---|---|---|---|---|
| NIFTY options | ★★★★★ | ★★★★★ | ★★★★★ | Best starting point — current build |
| BANKNIFTY options | ★★★★★ | ★★★★★ | ★★★★★ | Excellent, but larger moves cut both ways |
| SENSEX options | ★★★★ | ★★★★ | ★★★★ | Good secondary market |
| FINNIFTY options | ★★★★ | ★★★ | ★★★★ | More limited |
| MIDCPNIFTY options | ★★★ | ★★★ | ★★★ | More specialized |
| Stock options | ★★★ | Varies greatly | ★★★★★ | Selective — needs a liquidity-qualified universe, not blanket coverage |
| US index options | ★★★★★ | ★★★★★ | ★★★★★ | Excellent, but access/data complexity |
| Commodity options | ★★★ | Varies | ★★★ | Not the first choice |

**NIFTY stays V1** — liquidity, narrow spreads in active contracts, deep participation, continuous intraday movement, rich option-chain data, multiple expiries, good historical data availability, and (for a regime-based system specifically) enough distinct market conditions to actually test trend → range → breakout → reversal → vol expansion → vol contraction, which is exactly the regime taxonomy the engine already classifies against.

**BANKNIFTY** is the natural second instrument — larger moves help option buying overcome premium/theta/spread/slippage/charges, but the same larger moves mean larger adverse excursions too, so its risk engine needs to be more sophisticated than a copy-paste of NIFTY's. Important: parameters (ADX thresholds, etc.) must be independently optimized and validated per instrument, not inherited — same architecture, instrument-specific parameters.

**Stock options** are where the real opportunity density is (earnings moves, sector rotation, gap moves, corporate events, unusual IV, large directional trends) but also where automation gets hard (varying liquidity/spreads/lot sizes, corporate actions, stock-specific event risk, occasional poor execution). Not "automate all NSE stock options" — build a liquidity-qualified universe (minimum volume, minimum OI, maximum spread, minimum traded value → eligible universe → regime/strategy engine runs only against that filtered set).

---

## Staged rollout — do not skip stages

Jumping from NIFTY straight to 20 instruments invites multiple-testing / overfitting: search enough instruments × enough parameter combinations and something will look great historically by pure chance. Each stage must prove out before the next one starts, and a new instrument is added only if it improves the *portfolio* (not just its own standalone P&L) after costs.

- **Stage 1 — NIFTY only.** Prove positive expectancy, acceptable drawdown, robust execution, realistic slippage, stable out-of-sample results. *(This is where the system is today — Phases 1–10 complete, Phase 9 live execution intentionally paused pending the observation period.)*
- **Stage 2 — Add BANKNIFTY.**
- **Stage 3 — Add SENSEX.**
- **Stage 4 — Add a carefully selected stock-option universe** (liquidity-filtered, not blanket).
- **Stage 5 — Portfolio optimizer.** The system scans everything and asks *"where is the highest-quality opportunity after costs, liquidity, correlation, and risk?"*, then allocates capital automatically instead of holding predetermined capital per instrument.

Mapped onto strategy depth (extends the README's existing Phase 11+ table):
- **V1 — Prove the engine**: NIFTY only. Long CE, Long PE, Debit spreads, NO_TRADE. *(current state)*
- **V2 — Add volatility strategies**: Long straddle, Long strangle — only where the data actually supports them.
- **V3 — Add instruments**: BANKNIFTY, SENSEX.
- **V4 — Add selected stock options**: liquid contracts only.
- **V5 — Portfolio optimizer**: cross-instrument opportunity scanning + correlation-adjusted capital allocation.

---

## Target architecture once multi-instrument (V3+)

Not four independent, parallel strategies unaware of each other:

```
NIFTY Strategy   BANKNIFTY Strategy   SENSEX Strategy   Stock Strategy   ← NOT this
```

But a single pipeline where capital goes to the best opportunity, not a fixed slice per instrument:

```
                 MARKET DATA
                     |
             OPPORTUNITY SCANNER
                     |
        +------------+------------+
        |            |            |
      NIFTY       BANKNIFTY     SENSEX   (+ stock universe, V4+)
        |            |            |
        +------------+------------+
                     |
              REGIME ENGINE
                     |
             STRATEGY ENGINE
                     |
             EDGE CALCULATOR
                     |
             LIQUIDITY FILTER
                     |
              RISK ENGINE
                     |
           PORTFOLIO ALLOCATION
                     |
                 EXECUTION
                     |
            POSITION MANAGER
```

Illustrative decision point: at 09:45, NIFTY shows expected edge 0.18R, BANKNIFTY 0.31R, SENSEX 0.12R, Stock A 0.42R, Stock B 0.05R → trade Stock A, provided it passes all risk/liquidity constraints. This is the systematic-desk pattern: evaluate opportunity quality across the whole universe every cycle, not "always trade instrument X."

An earlier, simpler two-instrument version of the same idea (before the full scanner/allocator exists):

```
NIFTY Engine        BANKNIFTY Engine
        \                /
         Portfolio Risk Engine
                  |
          Capital Allocation
```

---

## Correlation risk — the part that's easy to get wrong

Once trading multiple instruments, they cannot be sized independently. If NIFTY, BANKNIFTY, and SENSEX all signal Buy CE at the same time, that isn't three independent opportunities — they're highly correlated expressions of the same underlying India-equity-market risk. Naively:

```
NIFTY CE       ₹5,000 risk
BANKNIFTY CE   ₹5,000 risk
SENSEX CE      ₹5,000 risk
Raw risk = ₹15,000
```

But with high correlation, the *effective* portfolio risk is much closer to one concentrated directional India-equity bet, not three separately-sized ₹5,000 positions. The portfolio engine needs correlation-adjusted exposure — the exact calculation should come from a real portfolio-risk model, not a fixed correlation multiplier bolted on.

---

## Guardrails for the whole roadmap

- No instrument is inherently profitable — don't expect NIFTY, BANKNIFTY, or anything else to "just work" on its own. The edge is the combination of market selection + regime detection + strategy selection + execution + risk management + cost control.
- Add an instrument only if it produces incremental positive expectancy or diversification *after costs*, evaluated at the portfolio level — never add one purely to increase trade count.
- Parameters (ADX thresholds, vol-regime cutoffs, etc.) are instrument-specific and must be independently validated, even when the underlying architecture is shared.
- Each stage needs its own out-of-sample validation before the next stage starts — this is the direct guard against multi-instrument overfitting.

---

## Expiry-Day Strategy Coverage — parallel research track (started 2026-08-18)

**Origin**: on 18-Aug-2026 (a real NIFTY weekly expiry day), the live engine correctly read a strong, real bearish trend (`BEAR`/`STRONG_BEAR`, ADX 29–43 through the morning) but rejected every candidate trade on expected-value grounds — theta decay exceeded the expected premium gain for both Long Put (`expected_net = -₹354`) and the Bear Put Spread (`expected_net = -₹703`), given only a ~20–30 point expected move. Manually priced an equivalent Bear Call Spread against the live chain that morning: best case was positive (~₹1,512 after costs), but breakeven sat only ~45 points from spot on a day that had already moved 133 points — an asymmetric, gamma-exposed trade, not an obviously good one. This is what triggered the track below: **the system didn't fail — it correctly has no strategy that can monetize "real trend, insufficient magnitude, high theta" — and expiry days concentrate that exact regime.**

**Framing, and the discipline that must not slip**: this is a *strategy coverage problem*, not a signal problem. The regime/confirmation engine is working. The fix is not "make Long Put more aggressive" or "override NO_TRADE on expiry days" — both would weaken a filter that's currently doing its job correctly. The fix is giving the system a strategy family (defined-risk credit spreads) capable of having positive expected value in a regime long-premium strategies structurally can't monetize, validated the same rigorous way as everything else in this codebase, not adopted because one day's numbers looked tempting.

### The plan

1. **Leave the existing Long CE / Long PE / Debit Spread engine and its thresholds untouched.** Today's rejections are the correct output, not a bug to route around.
2. **Build a Strategy Coverage Engine.** Reframe the question from *"should I buy a CE/PE?"* to *"given today's regime, which strategy family — if any — has positive expected net value?"* Candidates to evaluate, not build all at once: Long CE, Long PE, Debit Spread (existing), Bear Call Spread, Bull Put Spread, Iron Condor, Long Straddle/Strangle (new), No Trade (always a valid outcome).
3. **Never trust a new strategy off one day's numbers.** Every candidate needs expected net P&L (gross − brokerage − STT − exchange charges − GST − stamp duty − realistic slippage) evaluated out-of-sample across normal days, expiry days, trend days, range days, high/low VIX, high/low IV, morning vs. afternoon, and varying days-to-expiry. A strategy that only looks good on 18-Aug-2026 doesn't ship. **This inherits the backtest engine's existing synthetic-option-chain problem (see "The gate" section above) — that gap has to be closed, or at minimum explicitly worked around, before any of this validation can be trusted. Don't validate new strategies against the same unreliable backtest data already flagged as unsuitable for sizing decisions.**
4. **Add an Expiry/Time Regime as its own dimension**, not a parameter tweak. Theta, gamma, and IV behave qualitatively differently at 0–1 DTE than on a normal day — `days_to_expiry == 0` should route through logic aware of that, not just a scaled threshold.
5. **Add a `Required Move` calculation** alongside the existing `Expected Move` — "how many points does the underlying need to move for this specific structure to clear costs and theta," computed from real option pricing/Greeks and the existing cost model, not a fixed multiplier. `Expected move > Required move → candidate. Expected move < Required move → NO_TRADE.` This is largely already implicit in the existing `expected_net_value`/`risk_reward` math; the work here is making it an explicit, first-class, surfaced number (including in Discord alerts) rather than a buried intermediate.
6. **Give credit/defined-risk strategies their own complete risk framework — do not bolt them onto the existing one.** Long options fail via theta (bounded, slow). Credit spreads fail via gamma (can be fast, especially into a 0DTE close). Needs, before any live/paper execution: max loss per trade (already known at entry — verify it's enforced, not just computed), max daily loss across the credit book, max simultaneous spreads, underlying-price invalidation levels, a gamma-specific protection layer (the existing 3-layer protection was designed around long-premium payoffs — verify it actually behaves correctly for a position that loses fastest near the *short* strike, not away from it), a time-based exit, an emergency exit, and explicit no-martingale / no-uncontrolled-averaging enforcement for the credit book specifically (today's investigation found the existing no-martingale check only covers long-premium direction matching — verify it extends correctly to credit positions before trusting it).

### Target architecture

```
                 NIFTY DATA
                     |
             MARKET REGIME
                     |
            VOLATILITY REGIME
                     |
             EXPIRY / TIME REGIME
                     |
          +----------+----------+
          |                     |
     LONG-PREMIUM          CREDIT / DEFINED-RISK
       ENGINE                    ENGINE
          |                     |
   CE / PE / Debit       Bear Call / Bull Put /
    (existing,                Iron Condor
     unchanged)                 (new)
          +----------+----------+
                     |
              EXPECTED NET EV
                     |
               RISK FILTER
                     |
              LIQUIDITY FILTER
                     |
             PORTFOLIO EXPOSURE
                     |
        +------------+------------+
        |            |            |
       TRADE       ADJUST       NO TRADE
```

Long Straddle/Strangle only gets added later, and only if backtesting shows the volatility-expansion detection can reliably identify cases where expected movement exceeds the combined premium of both legs — reasoning from today's own numbers, a 2-leg long-premium structure pays theta on both legs and would need to clear an even higher bar than the single-leg Long Put that already failed today.

### First increment (proposed, not yet started)

Given "start now" means starting the *initiative*, not six engines simultaneously — the lowest-risk, highest-immediate-value first step is **#5, the `Required Move` calculation**, added as a diagnostic/transparency addition to the *existing* Long CE / Long PE / Debit Spread evaluations. It doesn't change trading behavior (no new strategy, no new risk surface), it's built from data and cost-model logic that already exists, and it directly answers the question that came up repeatedly during today's live observation ("what's actually missing") with a real number instead of a re-derived explanation each time. Everything else in this section (Strategy Coverage Engine, Expiry Regime, new strategy families, the credit-specific risk framework) follows only after that's in and, per point 3, only after backtest validation each new piece actually holds up out-of-sample.

**Status (2026-08-18, later same day)**: Required Move shipped and live-verified (`required_move_points` on `StrategyEvaluation`, wired into `long_call.py`/`long_put.py`/`debit_spread.py`). Explicit decision made after seeing it work: **hold scope here.** Run only Long CE / Long PE / Debit Spread for some days and see whether they produce a real positive trade-journal track record before building anything else in this section — Bear Call Spread / Bull Put Spread / Iron Condor / the Strategy Coverage Engine itself all stay queued, not started.

### Weekly Option-Cycle Regime — deferred research idea, not started (2026-08-18)

**Origin**: a second external note (same day) proposed generalizing "expiry day" into a full weekly-cycle regime — treating Wednesday (fresh weekly contract, most time value) through Tuesday (0DTE expiry) as five qualitatively different day-phases, each with its own strategy-fit hypothesis, plus a Friday "weekend-IV module" (does Friday IV overprice the realized Saturday–Monday gap?) and new metrics: `DTE`, `TRADING_DAYS_TO_EXPIRY`, `CALENDAR_DAYS_TO_EXPIRY`, `TIME_TO_EXPIRY`, `EXPIRY_SESSION_PHASE`, and `MOVE_CONSUMPTION` (expected weekly move already travelled vs. remaining).

**What's worth keeping from it:**
- The underlying claim — theta/gamma/IV behave qualitatively differently across the weekly cycle, so one directional rule (bullish→CE, bearish→PE) shouldn't apply uniformly every day — is a real generalization of point 4 above (Expiry/Time Regime), not a competing idea.
- `MOVE_CONSUMPTION` specifically is the one concrete, cheaply-buildable piece here — a natural sibling to `Required Move`: Required Move asks "how far must price move from here," MOVE_CONSUMPTION asks "how much of the week's move budget is already spent." If this track resumes, this is the first thing worth building, the same way Required Move was the first increment above.

**What's flagged, not accepted at face value:**
- The note contradicts itself — it proposes five separate day-of-week modules, then later argues day-of-week alone should never trigger a trade and that continuous `DTE`/`TRADING_DAYS_TO_EXPIRY` is the right primitive instead. The second framing is the better one if this ever gets built; the day-by-day table is illustrative, not a spec.
- The Friday weekend-IV-overpricing claim is asserted, not checked. It needs real historical NIFTY Friday-IV-vs-realized-Monday-gap data before it's anything more than a plausible guess — exactly the theory-vs-real-data gap already called out for the expiry-day work above.
- This is strictly larger in scope than the credit-spread work already queued and not yet started. It does not jump the queue.

**Decision: observe before coding.** `runs/decisions/decisions.jsonl` already timestamps every decision cycle (regime, volatility, `expected_net_value`, reasons) — day-of-week and days-to-expiry patterns can be reconstructed retroactively from that journal once enough real sessions accumulate. Ongoing market-watch checks will keep half an eye on day-of-week context (fresh-weekly Wednesday vs. late-cycle Monday/Tuesday) as a free byproduct of monitoring that's already happening. This whole track — weekly-cycle modules and `MOVE_CONSUMPTION` alike — stays behind both the NIFTY MVP gate and the still-unstarted credit-spread/Strategy-Coverage-Engine work directly above it. Revisit only once there's real multi-week journal data to check the hypotheses against, not on a fixed timer.

**Observational data collection added 2026-08-18 to make that possible**: `runs/decisions/decisions.jsonl` never stored the option chain itself, only spot/vix — not enough to reconstruct what a credit spread would have looked like at a given moment. Added `MarketSnapshotLogger` (`nifty_engine/journal/__init__.py`) writing to `runs/snapshots/market_snapshots.jsonl`: a compact ATM-window chain slice (±10 strikes, full quote incl. delta/gamma/theta/vega/oi) saved on every regime change plus a 15-minute fallback so stable stretches still get sampled, wired into `engine.py`'s `run_cycle()` via `_maybe_save_market_snapshot()`. **Purely observational — wrapped in try/except so a logging failure can never affect a trading decision, and nothing in the trading path reads this file.** This is what will actually let the weekly-cycle and credit-spread hypotheses get checked against real chain data later, not just spot/regime history.

**Second gap closed same day**: `decisions.jsonl` itself only ever recorded the *chosen* strategy — on a NO_TRADE day (i.e. every day so far) that's always the synthetic NO_TRADE result, which has no `expected_net_value` or `required_move_points` of its own. LONG_PUT's and DEBIT_SPREAD's actual near-miss numbers — the ones being hand-reported in conversation each check — were computed every cycle and then thrown away. Added `all_evaluations` (every candidate strategy's eligible/direction/expected_net_value/confidence/risk_reward/`required_move_points`) and `confirmation_summary` (VIX valuation, OI call/put walls, futures basis, BankNifty correlation) to `DecisionRecord`, populated via new `Engine._summarise_evaluations()`/`_summarise_confirmation()` helpers at the existing `log_decision()` call site — no new trigger logic needed, this rides the decision cycle that already happens every check. Now every single cycle (not just regime-change moments) has a full record of how close each strategy came and what the cross-market context looked like. Verified against live data in a scratch dir before deploying; no open positions at restart.

**Design correction, locked in now even though the build stays deferred (2026-08-18)**: if this ever gets built, it must be **DTE-relative and instrument-agnostic, not day-name-based** — no `if day_of_week == "FRIDAY"` anywhere. Reasoning: this roadmap already plans BANKNIFTY/FINNIFTY/stock expansion (see "Staged rollout" above), and those instruments don't share NIFTY's expiry day — a hardcoded day-name mapping would need a full rewrite the moment a second instrument is added. Worse, even NIFTY's own weekly expiry day has been reassigned by SEBI/NSE more than once historically, so hardcoding "Tuesday" isn't even stable for NIFTY alone long-term. The fix costs nothing extra: `get_option_chain()` in `dhan_client.py` already calls DhanHQ's `expiry_list()` every single cycle and takes the nearest live expiry date — `days_to_expiry = expiry_date - now` is already computable from data the system fetches today, for any instrument, regardless of which weekday the exchange currently assigns it. So the one and only new model this needs is something like `ExpirySessionState` (calendar/trading days-to-expiry, hours-to-expiry, a small derived phase enum) computed once per cycle from that existing expiry date — never a per-weekday module or a hardcoded calendar.

---

## Known roadblocks (reviewed 2026-08-17, before first commit)

Grounded in what this session's live testing actually found in the codebase, not generic multi-instrument advice. Kept here so the plan doesn't get committed as if these don't exist.

**Instrument-priority refinement (2026-08-18, once Stage 2 does start)**: user's own reasoning — prioritize BANKNIFTY's monthly-cycle infrastructure over SENSEX's weekly-cycle infrastructure, because monthly is the expiry structure almost everything else in the roadmap shares (BANKNIFTY, FINNIFTY, MIDCPNIFTY, and *all* individual stock options are monthly-only in India), while weekly-cycle work only ever pays off for two instruments in this entire roadmap — NIFTY and SENSEX. Building proper monthly-cycle handling once carries straight into Stage 4 (stock options) later; weekly-cycle work doesn't reuse nearly as far. Cross-checked against a pasted external note: its core premise (SEBI's Nov-2024 rule restricting each exchange to one weekly index product — NSE kept NIFTY weekly and moved BANKNIFTY/FINNIFTY/MIDCPNIFTY to monthly, BSE kept SENSEX weekly) matches known regulatory history and is consistent with why the DTE-relative, `expiry_list()`-driven design (see Expiry-Day Coverage section above) was the right call — it never needs to hardcode which day any of this falls on. Two claims in that note are asserted, not verified: that SENSEX correlates too highly with NIFTY to count as real diversification, and that BANKNIFTY offers genuinely different sector-concentration exposure — both plausible, neither checked against real correlation data yet. One claim is sound on option-pricing first principles alone: a monthly BANKNIFTY position 15–20 days from expiry has a days-scale prediction horizon where theta is a small fraction of daily movement, structurally unlike NIFTY's 0DTE problem.

**Resolved 2026-08-18 (see "BANKNIFTY + SENSEX build" section below) — was: "blocks Stage 2 from starting at all":**
- ~~The data layer is hardcoded to NIFTY, not parameterized.~~ Fixed: `nifty_engine/instruments.py` (`InstrumentConfig`) + `DhanBroker(instrument=...)`, verified live for both instruments.
- ~~Config is one global flat set.~~ Fixed: `risk_banknifty.yaml`/`strategies_banknifty.yaml`/`broker_banknifty.yaml` (+ `_sensex`), literal copies of NIFTY's values — **still need independent validation**, this only fixed the mechanism to hold separate values, not the values themselves.
- ~~Positions carry no instrument tag.~~ Fixed: `Position`/`OrderRequest.instrument` field added.
- `max_open_positions` is still a single cap **per Engine instance** — not a bug, since each instrument now runs its own Engine with its own `RiskEngine`/`OrderManager`, but it means there is still zero cross-instrument portfolio awareness (see "Not started at all" below — that part is genuinely not done).

**Will resurface fresh, not transfer over:**
- Today's five live-only bugs shared one shape — a field silently defaulting to something "safe-looking" (`x or 0.0`, `x or spot`) instead of surfacing as missing data. None of these were catchable by static review or backtesting, only by running against real live data. Expect a fresh round of the same bug class the first time BANKNIFTY/SENSEX are wired to real DhanHQ data — budget real time for it, don't assume "same architecture" means "same reliability."
- DhanHQ burst-rate-limiting (confirmed today from NIFTY's own 6-call cycle) scales roughly multiplicatively with instrument count. Needs actual engineering (batching/staggering/shared cache), not "run the loop three times."

**Not started at all, larger than it looks in the architecture diagram:**
- The "Portfolio Risk Engine" / "Capital Allocation" boxes are a 100%-new build — the current risk engine has zero correlation modeling and zero cross-instrument awareness today. Likely the single largest engineering investment in this whole roadmap.
- `_live_submit()` (real order placement) is still a stub for even one instrument. Worth remembering the roadmap describes a system making real capital-allocation calls across instruments while it currently can't place one real order anywhere.
- Paper positions live only in process memory and are wiped on every restart — tolerable for single-instrument observation, a bigger problem for tracking correlated multi-instrument exposure over time.

**Stage 4 (stock options) specifically:**
- No corporate-action handling (splits/bonuses/strike adjustments) anywhere in the `Position`/`OrderRequest` model.
- Stocks are mostly monthly expiry, not weekly like NIFTY — the "always pick the nearest expiry" logic behind today's fixes was built and verified against NIFTY's weekly cadence and hasn't been validated against a monthly cycle at all.

**Process risk the plan already names, worth reinforcing:** the regime engine's ADX/EMA thresholds are marked in the code itself as "TODO-for-validation" placeholders from Phase 1–5 — never walk-forward validated even once, for NIFTY alone. This is exactly why the MVP gate above matters: validate NIFTY for real before any of the above gets built, not in parallel with it.

---

## BANKNIFTY + SENSEX build (2026-08-18)

**What's live**: separate `Engine` instances per instrument (`nifty_engine/instruments.py`'s `InstrumentConfig` — NIFTY/BANKNIFTY/SENSEX, all verified to work through DhanHQ's identical `IDX_I` API shape), each with its own `DhanBroker`, `StrategySelector`, `RiskEngine`, `OrderManager`, and — critically — its own `runs_dir` so journals never mix (`runs/banknifty/`, `runs/sensex/`, completely separate from NIFTY's own `runs/decisions/decisions.jsonl`). New API endpoints: `/api/banknifty/{health,snapshot,decision,status}` and the same for `/api/sensex/...`, mirroring the core NIFTY endpoints. Every existing NIFTY endpoint is untouched — verified via zero-regression checks after each phase.

**Why this was faster/safer than the 2026-08-17 roadblocks review expected**: that review assumed BANKNIFTY/SENSEX would need real new integration work. Live verification during planning found both work through the *exact same* DhanHQ call shape NIFTY already uses (same `IDX_I` exchange segment, same `option_chain()`/`expiry_list()`/`intraday_minute_data()` calls) — BANKNIFTY (security_id 25) confirmed monthly expiries on Tuesdays, SENSEX (security_id 51) confirmed weekly expiries, both with full real option chains and real ADX/RSI-computable candle history. This was a parameterization refactor, not new per-instrument integration code.

**What's explicitly NOT done, and matters before either instrument is trusted for anything real:**
- **Lot sizes are placeholders.** `broker_banknifty.yaml`/`broker_sensex.yaml` have lot_size guesses (30/20) that need verifying against live Dhan funds/margin or instrument-master data before either instrument's numbers mean anything for real sizing.
- **Risk/strategy thresholds are literal copies of NIFTY's tuning**, not independently validated — exactly the gap the roadblocks review originally warned about, still open. `min_expected_net_value`, ADX thresholds, everything — all NIFTY's numbers, unverified for these instruments.
- **Zero cross-instrument portfolio risk.** Each instrument's `RiskEngine`/`max_open_positions` cap is independent — if NIFTY, BANKNIFTY, and SENSEX all go bullish simultaneously, nothing today treats that as one concentrated directional bet. This is still the single largest unbuilt piece (per the "Not started at all" section above).
- **Discord alerts don't carry an instrument tag.** The notifier is process-wide; a "Regime Change" alert from BANKNIFTY looks identical to one from NIFTY in Discord/`/api/alerts` today. Flagged, not fixed.
- **No frontend/dashboard visibility** for the new instruments — API/backend only.
- The known live-only-bug-class risk from the roadblocks review (silent `x or default` fields) hasn't specifically resurfaced yet because BANKNIFTY/SENSEX have only been running for one live-verification pass — budget real time for this once they run for real sessions, per the original warning.

**Design principle for the still-unbuilt Portfolio Risk Engine, from a pasted external note (2026-08-18), locked in now even though the build stays deferred**: don't think of NIFTY/BANKNIFTY/SENSEX as three independent tradable instruments — think of them as one Indian equity market with three expressions of it. The portfolio engine's question should be *"where is the single best risk-adjusted opportunity right now"* (capital rotation — pick the best one, size down or skip the correlated others), not *"can I trade all three at once"* (capital multiplication — which understates real combined risk, exactly as the existing "Correlation risk" section above already warns). This reinforces, doesn't replace, that section.

**`/api/portfolio/snapshot` added same day**: a lightweight cross-instrument comparison endpoint — reads each instrument's already-cached decision (NIFTY/BANKNIFTY/SENSEX), reports regime/action/expected_net_value plus a simple directional headcount (reusing the same BULLISH/BEARISH heuristic `Engine._compute_confirmation()` already uses). Purely observational — triggers no extra engine cycles, gates nothing, is explicitly *not* the portfolio risk engine described above, just the visibility layer that makes it possible to check the correlation question empirically before building the real thing. First live call already found all three instruments simultaneously `BREAKOUT`/`BULLISH` — a concrete, real instance of the exact correlated-exposure scenario this whole section warns about.

---

## Next incremental step

Stage 1 (NIFTY only) is where the system already is. Nothing below gets started until the MVP gate above is actually cleared — see that section for what "proven profitable" needs to mean here. That's the natural gate to revisit before opening this roadmap's Stage 2, and the roadblocks above are what "Stage 2" needs to clear technically once it does.
