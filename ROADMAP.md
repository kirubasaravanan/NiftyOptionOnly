# Long-Term Roadmap — Multi-Instrument Automated Options Portfolio

**Status**: Planning document. Not yet started. Captured 2026-08-17 for incremental execution.
**Relationship to `worklog.md`**: `worklog.md` and the README's Phase 1–20 table track feature depth *within* the current NIFTY-only engine (more strategies, better risk handling, live execution). This document tracks a different, orthogonal axis of growth: *which instruments the system trades* and *how capital gets allocated across them once there's more than one*. The two roadmaps run in parallel — this one assumes the NIFTY engine keeps maturing per the existing phase table while these stages layer on top.

**Goal, stated plainly**: not "make money from NIFTY options" — build an automated options portfolio that continuously searches for the best statistically validated opportunity across instruments while controlling correlated risk. No instrument is inherently profitable; the edge comes from market selection + regime detection + strategy selection + execution + risk management + cost control, combined.

## The gate: NIFTY is the MVP

Explicit decision (2026-08-17): **NIFTY-only is the MVP. Nothing past Stage 1 gets built unless NIFTY is actually proven profitable first** — not "acceptable backtest metrics," actual demonstrated profitability under real conditions. This is a harder, simpler bar than "positive expectancy + acceptable drawdown + stable out-of-sample" and it's the one that governs: if NIFTY doesn't prove out, the rest of this document doesn't get built. No sunk-cost continuation into BANKNIFTY/SENSEX/stocks/portfolio-optimizer just because the architecture was planned — the plan only activates on proof, not on schedule.

What "proven profitable" should mean in practice for this gate, given everything found in this session: real trades, real DhanHQ market data, over a long enough sample to mean something (not the 6–10 trades seen in one afternoon) — and explicitly *not* the current backtest engine's numbers, since those rest on synthesized option-chain/Bank Nifty/futures data and are already flagged as unreliable for sizing any decision. Until that backtest gap is closed or the paper/live track record itself is long enough to stand on its own, "profitable" needs to be measured from the trade journal, not the backtest report.

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

## Known roadblocks (reviewed 2026-08-17, before first commit)

Grounded in what this session's live testing actually found in the codebase, not generic multi-instrument advice. Kept here so the plan doesn't get committed as if these don't exist.

**Blocks Stage 2 (BANKNIFTY) from starting at all, regardless of NIFTY's profitability outcome:**
- The data layer is hardcoded to NIFTY, not parameterized. `NIFTY_INDEX_SECURITY_ID`/`NIFTY_INDEX_EXCHANGE`/`NIFTY_FUT_SECURITY_ID` are module constants in `dhan_client.py`; `get_option_chain(underlying=...)` takes a parameter it doesn't actually use for ID lookup. Adding BANKNIFTY means rewriting the data layer to be instrument-parameterized first, not pointing existing code at a new symbol.
- Config (`risk.yaml`, `strategies.yaml`, etc.) is one global flat set, with no mechanism to hold two instruments' independently-tuned thresholds at once.
- Positions carry no instrument tag and `max_open_positions` is a single global cap, not per-instrument. The risk engine can't reason about combined NIFTY+BANKNIFTY exposure until `Position` gets an instrument field and the cap logic is reworked.

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

## Next incremental step

Stage 1 (NIFTY only) is where the system already is. Nothing below gets started until the MVP gate above is actually cleared — see that section for what "proven profitable" needs to mean here. That's the natural gate to revisit before opening this roadmap's Stage 2, and the roadblocks above are what "Stage 2" needs to clear technically once it does.
