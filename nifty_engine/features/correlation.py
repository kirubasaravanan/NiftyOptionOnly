"""Cross-market correlation & context features (Layer 3 + 4).

Per spec: correlation itself should NOT be a buy signal — it should be a
confirmation/regime feature. The strategy selector uses these to adjust
confidence (raise on confirmation, lower on divergence).

Layers covered here:
  Layer 3 — Market confirmation: Bank Nifty, breadth, futures basis, India VIX
  Layer 4 — Cross-market: USDINR, crude, S&P futures, GIFT NIFTY (stubs for V2)
  Rolling correlation regime detector (normal vs breakdown)
  OI classification (long buildup / short covering / long unwinding / short buildup)
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Optional
import math

import pandas as pd
import numpy as np

from ..models import MarketSnapshot, OptionQuote, OptionType
from ..data.option_chain import OptionChainBuilder


# ---------- India VIX valuation ----------

@dataclass
class VIXValuation:
    """India VIX relative valuation — is option premium expensive or cheap?"""
    vix: Optional[float]                          # in % (e.g. 14.5 = 14.5%)
    vix_percentile: Optional[float]              # 0-100 — rank vs last 60 sessions
    vix_change: Optional[float]                  # change vs prev close
    iv_vix_gap: Optional[float]                  # ATM IV - VIX/100. +ve = options expensive
    valuation: str                               # "CHEAP" | "FAIR" | "EXPENSIVE" | "UNKNOWN"
    reasons: list[str] = field(default_factory=list)


class VIXValuationCalculator:
    """Classify whether option premium is cheap/expensive relative to VIX."""

    VIX_LOW_PCT = 25       # below 25th percentile = cheap
    VIX_HIGH_PCT = 75      # above 75th = expensive
    IV_VIX_GAP_THRESHOLD = 0.04  # ATM IV 4% above VIX/100 = options expensive

    @classmethod
    def compute(
        cls,
        snapshot: MarketSnapshot,
        vix_history: Optional[list[float]] = None,
    ) -> VIXValuation:
        vix_data = snapshot.india_vix
        if vix_data is None or vix_data.ltp <= 0:
            return VIXValuation(
                vix=None, vix_percentile=None, vix_change=None,
                iv_vix_gap=None, valuation="UNKNOWN",
                reasons=["VIX data unavailable"],
            )

        vix_pct = vix_data.iv_percentile
        if vix_pct is None and vix_history:
            sorted_v = sorted(vix_history)
            rank = sum(1 for x in sorted_v if x <= vix_data.ltp)
            vix_pct = 100.0 * rank / max(1, len(sorted_v))

        vix_change = None
        if vix_data.prev_close and vix_data.prev_close > 0:
            vix_change = vix_data.ltp - vix_data.prev_close

        # ATM IV from option chain
        atm_iv = cls._atm_iv(snapshot)
        iv_vix_gap = None
        if atm_iv is not None:
            iv_vix_gap = atm_iv - (vix_data.ltp / 100.0)

        # Classify
        reasons = []
        if vix_pct is not None:
            if vix_pct < cls.VIX_LOW_PCT:
                valuation = "CHEAP"
                reasons.append(f"VIX percentile {vix_pct:.0f} < {cls.VIX_LOW_PCT} — options cheap")
            elif vix_pct > cls.VIX_HIGH_PCT:
                valuation = "EXPENSIVE"
                reasons.append(f"VIX percentile {vix_pct:.0f} > {cls.VIX_HIGH_PCT} — options expensive")
            else:
                valuation = "FAIR"
                reasons.append(f"VIX percentile {vix_pct:.0f} — fair")
        else:
            valuation = "UNKNOWN"

        if iv_vix_gap is not None and iv_vix_gap > cls.IV_VIX_GAP_THRESHOLD:
            valuation = "EXPENSIVE" if valuation != "EXPENSIVE" else valuation
            reasons.append(
                f"ATM IV ({atm_iv*100:.1f}%) is {iv_vix_gap*100:.1f}% above VIX ({vix_data.ltp:.1f}%) — "
                "options overpriced relative to realised vol"
            )

        if vix_change is not None:
            direction = "rising" if vix_change > 0 else "falling"
            reasons.append(f"VIX {direction} ({vix_change:+.2f})")

        return VIXValuation(
            vix=vix_data.ltp,
            vix_percentile=vix_pct,
            vix_change=vix_change,
            iv_vix_gap=iv_vix_gap,
            valuation=valuation,
            reasons=reasons,
        )

    @staticmethod
    def _atm_iv(snapshot: MarketSnapshot) -> Optional[float]:
        spot = snapshot.index.ltp
        if spot <= 0 or not snapshot.option_chain:
            return None
        strikes = sorted({q.strike for q in snapshot.option_chain})
        if not strikes:
            return None
        atm = min(strikes, key=lambda s: abs(s - spot))
        ivs = [q.iv for q in snapshot.option_chain
               if q.strike == atm and q.iv is not None and q.iv > 0]
        if not ivs:
            return None
        return float(sum(ivs) / len(ivs))


# ---------- OI classification ----------

@dataclass
class OIClassification:
    """For each leg (CE / PE), classify the price+OI+volume dynamics.

    Long buildup    — price ↑ + OI ↑ + volume ↑    (new longs entering)
    Short buildup   — price ↓ + OI ↑ + volume ↑    (new shorts entering)
    Short covering  — price ↑ + OI ↓ + volume ↑    (shorts buying back)
    Long unwinding  — price ↓ + OI ↓ + volume ↑    (longs exiting)
    Neutral         — anything else
    """
    ce_classification: str
    pe_classification: str
    call_wall: Optional[float]            # strike with highest CE OI (resistance)
    put_wall: Optional[float]             # strike with highest PE OI (support)
    max_pain: Optional[float]
    reasons: list[str] = field(default_factory=list)


class OIClassifier:
    """Classify OI dynamics for the option chain."""

    @classmethod
    def classify(
        cls,
        snapshot: MarketSnapshot,
        prev_oi_by_strike: Optional[dict] = None,
    ) -> OIClassification:
        spot = snapshot.index.ltp
        prev_close = snapshot.index.prev_close or spot

        chain = snapshot.option_chain
        if not chain or spot <= 0:
            return OIClassification(
                ce_classification="UNKNOWN",
                pe_classification="UNKNOWN",
                call_wall=None, put_wall=None, max_pain=None,
                reasons=["insufficient option chain data"],
            )

        # Aggregate CE and PE
        ce_total_oi = sum(q.oi for q in chain if q.option_type == OptionType.CE)
        pe_total_oi = sum(q.oi for q in chain if q.option_type == OptionType.PE)
        ce_total_oi_change = sum(q.oi_change for q in chain if q.option_type == OptionType.CE)
        pe_total_oi_change = sum(q.oi_change for q in chain if q.option_type == OptionType.PE)
        ce_total_vol = sum(q.volume for q in chain if q.option_type == OptionType.CE)
        pe_total_vol = sum(q.volume for q in chain if q.option_type == OptionType.PE)

        price_up = spot > prev_close

        # Classification rules
        # OI up + price up = long buildup (for CE — bullish; for PE — bearish)
        # OI up + price down = short buildup (for CE — bearish; for PE — bullish)
        # OI down + price up = short covering
        # OI down + price down = long unwinding
        ce_class = cls._classify_leg(price_up, ce_total_oi_change > 0, ce_total_vol > 0)
        pe_class = cls._classify_leg(not price_up, pe_total_oi_change > 0, pe_total_vol > 0)

        # OI structure
        oi_summary = OptionChainBuilder.oi_structure_summary(chain, spot)
        call_wall = oi_summary.get("call_wall")
        put_wall = oi_summary.get("put_wall")
        max_pain = oi_summary.get("max_pain")

        # PCR (Put-Call ratio by OI)
        pcr = pe_total_oi / ce_total_oi if ce_total_oi > 0 else None

        reasons = []
        reasons.append(f"CE OI={ce_total_oi:,} change={ce_total_oi_change:+,} vol={ce_total_vol:,} -> {ce_class}")
        reasons.append(f"PE OI={pe_total_oi:,} change={pe_total_oi_change:+,} vol={pe_total_vol:,} -> {pe_class}")
        if pcr is not None:
            reasons.append(f"PCR (OI) = {pcr:.2f} {'(bullish)' if pcr > 1.3 else '(bearish)' if pcr < 0.7 else '(neutral)'}")
        if call_wall:
            reasons.append(f"Call wall (resistance): {call_wall}")
        if put_wall:
            reasons.append(f"Put wall (support): {put_wall}")
        if max_pain:
            reasons.append(f"Max pain: {max_pain}")

        return OIClassification(
            ce_classification=ce_class,
            pe_classification=pe_class,
            call_wall=call_wall,
            put_wall=put_wall,
            max_pain=max_pain,
            reasons=reasons,
        )

    @staticmethod
    def _classify_leg(price_up: bool, oi_up: bool, vol_up: bool) -> str:
        """Classify a leg's OI dynamics.

        For CE: price_up means CE prices rose (bullish for CE)
        For PE: we pass not price_up so PE classification uses inverse logic
        """
        if not vol_up:
            return "NEUTRAL"
        if oi_up and price_up:
            return "LONG_BUILDUP"
        if oi_up and not price_up:
            return "SHORT_BUILDUP"
        if not oi_up and price_up:
            return "SHORT_COVERING"
        if not oi_up and not price_up:
            return "LONG_UNWINDING"
        return "NEUTRAL"


# ---------- Futures basis ----------

@dataclass
class FuturesBasis:
    """NIFTY spot vs futures basis.

    basis = futures - spot
    basis_pct = basis / spot * 100

    Premium (positive basis) = bullish positioning
    Discount (negative basis) = bearish positioning
    Rising basis = strengthening
    Falling basis = weakening
    """
    spot: float
    futures: Optional[float]
    basis: Optional[float]
    basis_pct: Optional[float]
    interpretation: str                    # "PREMIUM" | "DISCOUNT" | "FLAT" | "UNKNOWN"
    reasons: list[str] = field(default_factory=list)


class FuturesBasisCalculator:
    @classmethod
    def compute(
        cls,
        snapshot: MarketSnapshot,
        futures_quote: Optional[dict] = None,
    ) -> FuturesBasis:
        spot = snapshot.index.ltp
        if spot <= 0:
            return FuturesBasis(
                spot=0.0, futures=None, basis=None, basis_pct=None,
                interpretation="UNKNOWN", reasons=["spot invalid"],
            )
        if futures_quote is None or futures_quote.get("ltp") is None:
            return FuturesBasis(
                spot=spot, futures=None, basis=None, basis_pct=None,
                interpretation="UNKNOWN", reasons=["futures data unavailable"],
            )
        fut = float(futures_quote["ltp"])
        basis = fut - spot
        basis_pct = (basis / spot) * 100 if spot > 0 else 0.0

        if basis_pct > 0.10:
            interpretation = "PREMIUM"
            reason = f"futures at +{basis_pct:.3f}% premium — bullish positioning"
        elif basis_pct < -0.10:
            interpretation = "DISCOUNT"
            reason = f"futures at {basis_pct:.3f}% discount — bearish positioning"
        else:
            interpretation = "FLAT"
            reason = f"futures basis flat ({basis_pct:+.3f}%)"

        return FuturesBasis(
            spot=spot, futures=fut, basis=basis, basis_pct=basis_pct,
            interpretation=interpretation, reasons=[reason],
        )


# ---------- Bank Nifty confirmation ----------

@dataclass
class BankNiftyConfirmation:
    """Bank Nifty confirms or diverges from NIFTY direction.

    Confirmation: NIFTY ↑ + Bank Nifty ↑ (or both ↓)
    Divergence:   NIFTY ↑ + Bank Nifty ↓ (or vice versa)
    """
    nifty_change_pct: float
    banknifty_change_pct: Optional[float]
    correlation_state: str             # "CONFIRMED" | "DIVERGENT" | "UNKNOWN"
    reasons: list[str] = field(default_factory=list)


class BankNiftyCalculator:
    @classmethod
    def compute(
        cls,
        snapshot: MarketSnapshot,
        banknifty_quote: Optional[dict] = None,
    ) -> BankNiftyConfirmation:
        spot = snapshot.index.ltp
        prev_close = snapshot.index.prev_close or spot
        nifty_change_pct = ((spot - prev_close) / prev_close * 100) if prev_close > 0 else 0.0

        if banknifty_quote is None or banknifty_quote.get("ltp") is None:
            return BankNiftyConfirmation(
                nifty_change_pct=nifty_change_pct,
                banknifty_change_pct=None,
                correlation_state="UNKNOWN",
                reasons=["Bank Nifty data unavailable"],
            )

        bn_ltp = float(banknifty_quote["ltp"])
        bn_prev = float(banknifty_quote.get("prev_close") or bn_ltp)
        bn_change_pct = ((bn_ltp - bn_prev) / bn_prev * 100) if bn_prev > 0 else 0.0

        # Both move same direction (within 0.1% tolerance) -> confirmed
        if (nifty_change_pct > 0.05 and bn_change_pct > 0.05) or \
           (nifty_change_pct < -0.05 and bn_change_pct < -0.05):
            state = "CONFIRMED"
            reason = f"NIFTY {nifty_change_pct:+.2f}% + BankNifty {bn_change_pct:+.2f}% — same direction"
        elif (nifty_change_pct > 0.05 and bn_change_pct < -0.05) or \
             (nifty_change_pct < -0.05 and bn_change_pct > 0.05):
            state = "DIVERGENT"
            reason = f"NIFTY {nifty_change_pct:+.2f}% vs BankNifty {bn_change_pct:+.2f}% — DIVERGENCE"
        else:
            state = "NEUTRAL"
            reason = f"NIFTY {nifty_change_pct:+.2f}% BankNifty {bn_change_pct:+.2f}% — minor movement"

        return BankNiftyConfirmation(
            nifty_change_pct=nifty_change_pct,
            banknifty_change_pct=bn_change_pct,
            correlation_state=state,
            reasons=[reason],
        )


# ---------- Rolling correlation regime ----------

@dataclass
class CorrelationRegime:
    """Detects whether NIFTY↔BankNifty correlation is normal or breaking down.

    Uses rolling correlation over two windows (short + long).
    If short-window corr deviates significantly from long-window corr, regime = BREAKDOWN.
    """
    short_window_corr: Optional[float]
    long_window_corr: Optional[float]
    regime: str                          # "NORMAL" | "BREAKDOWN" | "UNKNOWN"
    reasons: list[str] = field(default_factory=list)


class CorrelationRegimeDetector:
    THRESHOLD = 0.30     # if |short - long| > 0.30, declare breakdown

    @classmethod
    def detect(
        cls,
        nifty_returns: Optional[pd.Series] = None,
        banknifty_returns: Optional[pd.Series] = None,
        short_window: int = 20,
        long_window: int = 50,
    ) -> CorrelationRegime:
        if nifty_returns is None or banknifty_returns is None:
            return CorrelationRegime(
                short_window_corr=None, long_window_corr=None,
                regime="UNKNOWN", reasons=["insufficient history for rolling correlation"],
            )
        if len(nifty_returns) < long_window or len(banknifty_returns) < long_window:
            return CorrelationRegime(
                short_window_corr=None, long_window_corr=None,
                regime="UNKNOWN",
                reasons=[f"need {long_window} bars, got {min(len(nifty_returns), len(banknifty_returns))}"],
            )

        # Align indexes
        nifty_returns = nifty_returns.dropna()
        banknifty_returns = banknifty_returns.dropna()
        common_idx = nifty_returns.index.intersection(banknifty_returns.index)
        if len(common_idx) < long_window:
            return CorrelationRegime(
                short_window_corr=None, long_window_corr=None,
                regime="UNKNOWN", reasons=["insufficient overlapping data"],
            )
        n = nifty_returns.loc[common_idx]
        b = banknifty_returns.loc[common_idx]

        short_corr = float(n.tail(short_window).corr(b.tail(short_window)))
        long_corr = float(n.tail(long_window).corr(b.tail(long_window)))

        if math.isnan(short_corr) or math.isnan(long_corr):
            return CorrelationRegime(
                short_window_corr=None, long_window_corr=None,
                regime="UNKNOWN", reasons=["correlation computed as NaN"],
            )

        delta = short_corr - long_corr
        if abs(delta) > cls.THRESHOLD:
            regime = "BREAKDOWN"
            reason = f"rolling corr changed {delta:+.2f} (short={short_corr:.2f}, long={long_corr:.2f}) — relationship unstable"
        else:
            regime = "NORMAL"
            reason = f"rolling corr stable (short={short_corr:.2f}, long={long_corr:.2f}, Δ={delta:+.2f})"

        return CorrelationRegime(
            short_window_corr=short_corr,
            long_window_corr=long_corr,
            regime=regime,
            reasons=[reason],
        )


# ---------- Composite confirmation score ----------

@dataclass
class ConfirmationScore:
    """Composite confirmation score in [-1, +1].

    Used by the strategy selector to ADJUST confidence on a candidate signal.
    Positive score = stronger conviction (more confirmations align).
    Negative score = divergence / caution (lower conviction).
    Zero = neutral.

    The score itself is NEVER a buy signal — it only modulates the strategy
    selector's confidence_score for eligible strategies.
    """
    score: float
    vix_valuation: VIXValuation
    oi_classification: OIClassification
    futures_basis: FuturesBasis
    banknifty_confirmation: BankNiftyConfirmation
    correlation_regime: CorrelationRegime
    reasons: list[str] = field(default_factory=list)


class ConfirmationScoreCalculator:
    """Blends all Layer 3 features into a single [-1, +1] confidence multiplier."""

    @classmethod
    def compute(
        cls,
        snapshot: MarketSnapshot,
        vix_valuation: VIXValuation,
        oi_classification: OIClassification,
        futures_basis: FuturesBasis,
        banknifty_confirmation: BankNiftyConfirmation,
        correlation_regime: CorrelationRegime,
        direction: str = "BULLISH",       # "BULLISH" | "BEARISH" | "NEUTRAL"
    ) -> ConfirmationScore:
        score = 0.0
        reasons = []

        # 1. VIX valuation (max ±0.25)
        if vix_valuation.valuation == "CHEAP":
            score += 0.20
            reasons.append("VIX cheap — favourable for long premium (+0.20)")
        elif vix_valuation.valuation == "EXPENSIVE":
            score -= 0.25
            reasons.append("VIX expensive — penalise long premium (-0.25)")
        elif vix_valuation.valuation == "FAIR":
            score += 0.05
            reasons.append("VIX fair (+0.05)")

        # 2. Bank Nifty confirmation (max ±0.30)
        if banknifty_confirmation.correlation_state == "CONFIRMED":
            score += 0.30 if direction != "NEUTRAL" else 0.10
            reasons.append(f"Bank Nifty confirms {direction} (+0.30)")
        elif banknifty_confirmation.correlation_state == "DIVERGENT":
            score -= 0.30
            reasons.append("Bank Nifty diverges — caution (-0.30)")

        # 3. Futures basis (max ±0.20)
        if direction == "BULLISH" and futures_basis.interpretation == "PREMIUM":
            score += 0.20
            reasons.append("Futures at premium confirms bullish (+0.20)")
        elif direction == "BEARISH" and futures_basis.interpretation == "DISCOUNT":
            score += 0.20
            reasons.append("Futures at discount confirms bearish (+0.20)")
        elif direction == "BULLISH" and futures_basis.interpretation == "DISCOUNT":
            score -= 0.15
            reasons.append("Futures at discount contradicts bullish (-0.15)")
        elif direction == "BEARISH" and futures_basis.interpretation == "PREMIUM":
            score -= 0.15
            reasons.append("Futures at premium contradicts bearish (-0.15)")

        # 4. OI classification (max ±0.15)
        if direction == "BULLISH":
            if oi_classification.ce_classification == "LONG_BUILDUP":
                score += 0.15
                reasons.append("CE long buildup — bullish positioning (+0.15)")
            elif oi_classification.pe_classification == "SHORT_BUILDUP":
                score += 0.10
                reasons.append("PE short buildup — bearish on puts = bullish (+0.10)")
            elif oi_classification.ce_classification == "LONG_UNWINDING":
                score -= 0.15
                reasons.append("CE long unwinding — bulls exiting (-0.15)")
        elif direction == "BEARISH":
            if oi_classification.pe_classification == "LONG_BUILDUP":
                score += 0.15
                reasons.append("PE long buildup — bearish positioning (+0.15)")
            elif oi_classification.ce_classification == "SHORT_BUILDUP":
                score += 0.10
                reasons.append("CE short buildup — bearish (+0.10)")
            elif oi_classification.pe_classification == "LONG_UNWINDING":
                score -= 0.15
                reasons.append("PE long unwinding — bears exiting (-0.15)")

        # 5. Correlation regime (max ±0.10)
        if correlation_regime.regime == "BREAKDOWN":
            score -= 0.10
            reasons.append("Correlation breakdown — relationship unstable (-0.10)")
        elif correlation_regime.regime == "NORMAL":
            score += 0.05
            reasons.append("Correlation normal (+0.05)")

        # Clamp
        score = max(-1.0, min(1.0, score))

        return ConfirmationScore(
            score=score,
            vix_valuation=vix_valuation,
            oi_classification=oi_classification,
            futures_basis=futures_basis,
            banknifty_confirmation=banknifty_confirmation,
            correlation_regime=correlation_regime,
            reasons=reasons,
        )


# ---------- Public convenience function ----------

def compute_all_confirmations(
    snapshot: MarketSnapshot,
    banknifty_quote: Optional[dict] = None,
    futures_quote: Optional[dict] = None,
    nifty_returns: Optional[pd.Series] = None,
    banknifty_returns: Optional[pd.Series] = None,
    vix_history: Optional[list[float]] = None,
    direction: str = "BULLISH",
) -> ConfirmationScore:
    """One-shot: compute VIX valuation + OI classification + futures basis +
    Bank Nifty confirmation + correlation regime + composite score."""
    vix_val = VIXValuationCalculator.compute(snapshot, vix_history)
    oi_cls = OIClassifier.classify(snapshot)
    fb = FuturesBasisCalculator.compute(snapshot, futures_quote)
    bnc = BankNiftyCalculator.compute(snapshot, banknifty_quote)
    cr = CorrelationRegimeDetector.detect(nifty_returns, banknifty_returns)
    return ConfirmationScoreCalculator.compute(
        snapshot, vix_val, oi_cls, fb, bnc, cr, direction,
    )


__all__ = [
    "VIXValuation", "VIXValuationCalculator",
    "OIClassification", "OIClassifier",
    "FuturesBasis", "FuturesBasisCalculator",
    "BankNiftyConfirmation", "BankNiftyCalculator",
    "CorrelationRegime", "CorrelationRegimeDetector",
    "ConfirmationScore", "ConfirmationScoreCalculator",
    "compute_all_confirmations",
]
