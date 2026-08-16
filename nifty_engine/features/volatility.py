"""Volatility regime features.

Two sources:
  1. India VIX (when available)
  2. Option-chain IVs (always available when chain is valid)

Direction != Volatility — both are needed.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from ..models import IndiaVIX, MarketSnapshot, OptionQuote, OptionType
from ..models import VolatilityRegime


@dataclass
class VolatilityFeatures:
    atm_iv: Optional[float]
    iv_percentile: Optional[float]            # rank of current ATM IV vs N sessions
    iv_slope: Optional[float]                 # change in ATM IV vs previous bar
    vix: Optional[float]
    vix_iv_percentile: Optional[float]
    vol_regime: VolatilityRegime
    reasons: list[str]


class VolatilityCalculator:
    """Pure-function calculator."""

    # Thresholds — configurable, these are sensible defaults only.
    # Adjust only after walk-forward validation.
    LOW_VOL_IV = 0.10          # < 10% IV
    HIGH_VOL_IV = 0.22         # > 22% IV
    VOL_EXPANSION_DELTA = 0.015    # IV up by > 1.5% in a bar
    VOL_CONTRACTION_DELTA = -0.015

    @classmethod
    def compute(
        cls,
        snapshot: MarketSnapshot,
        prev_atm_iv: Optional[float] = None,
        iv_history: Optional[list[float]] = None,
    ) -> VolatilityFeatures:
        atm_iv = cls._atm_iv(snapshot)
        vix_val = snapshot.india_vix.ltp / 100.0 if snapshot.india_vix else None  # VIX is in %
        vix_pct = snapshot.india_vix.iv_percentile if snapshot.india_vix else None

        iv_pct = None
        if iv_history and atm_iv is not None:
            sorted_ivs = sorted(iv_history)
            rank = sum(1 for x in sorted_ivs if x <= atm_iv)
            iv_pct = 100.0 * rank / max(1, len(sorted_ivs))

        slope = None
        if prev_atm_iv is not None and atm_iv is not None:
            slope = atm_iv - prev_atm_iv

        regime, reasons = cls._classify(atm_iv, slope, vix_val, iv_pct, vix_pct)
        return VolatilityFeatures(
            atm_iv=atm_iv,
            iv_percentile=iv_pct,
            iv_slope=slope,
            vix=vix_val,
            vix_iv_percentile=vix_pct,
            vol_regime=regime,
            reasons=reasons,
        )

    @staticmethod
    def _atm_iv(snapshot: MarketSnapshot) -> Optional[float]:
        spot = snapshot.index.ltp
        if spot <= 0 or not snapshot.option_chain:
            return None
        # Find ATM strike (closest to spot)
        strikes = sorted({q.strike for q in snapshot.option_chain})
        if not strikes:
            return None
        atm_strike = min(strikes, key=lambda s: abs(s - spot))
        # Average IV of CE and PE at ATM
        ivs = [
            q.iv for q in snapshot.option_chain
            if q.strike == atm_strike and q.iv is not None and q.iv > 0
        ]
        if not ivs:
            return None
        return float(sum(ivs) / len(ivs))

    @classmethod
    def _classify(
        cls,
        atm_iv: Optional[float],
        slope: Optional[float],
        vix: Optional[float],
        iv_pct: Optional[float],
        vix_pct: Optional[float],
    ) -> tuple[VolatilityRegime, list[str]]:
        reasons: list[str] = []

        # Direction of change beats absolute level — expansion / contraction
        if slope is not None:
            if slope >= cls.VOL_EXPANSION_DELTA:
                reasons.append(f"IV expanding (+{slope:.3f})")
                return VolatilityRegime.VOL_EXPANSION, reasons
            if slope <= cls.VOL_CONTRACTION_DELTA:
                reasons.append(f"IV contracting ({slope:.3f})")
                return VolatilityRegime.VOL_CONTRACTION, reasons

        # Fall back to absolute IV level
        ref = vix if vix is not None else atm_iv
        pct = vix_pct if vix_pct is not None else iv_pct
        if ref is None:
            reasons.append("IV/VIX data unavailable — defaulting to NORMAL_VOL")
            return VolatilityRegime.NORMAL_VOL, reasons

        if ref < cls.LOW_VOL_IV:
            reasons.append(f"low volatility ({ref:.3f})")
            return VolatilityRegime.LOW_VOL, reasons
        if ref > cls.HIGH_VOL_IV:
            reasons.append(f"high volatility ({ref:.3f})")
            return VolatilityRegime.HIGH_VOL, reasons
        if pct is not None and pct > 80:
            reasons.append(f"IV percentile high ({pct:.0f})")
            return VolatilityRegime.HIGH_VOL, reasons
        if pct is not None and pct < 20:
            reasons.append(f"IV percentile low ({pct:.0f})")
            return VolatilityRegime.LOW_VOL, reasons

        reasons.append(f"normal volatility ({ref:.3f})")
        return VolatilityRegime.NORMAL_VOL, reasons
