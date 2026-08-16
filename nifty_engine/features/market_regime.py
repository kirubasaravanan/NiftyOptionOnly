"""Market regime engine.

Combines technical + volatility features into a single
(market_regime, volatility_regime, confidence) assessment.

THRESHOLDS ARE READ FROM CONFIG — never hard-coded here.
The engine emits reasons for every classification so the journal
captures WHY each decision was made.
"""
from __future__ import annotations

from typing import Optional

from ..models import (
    MarketFeatures, MarketRegime, MarketSnapshot, RegimeAssessment,
    VolatilityRegime,
)
from ..config import load as load_config
from .volatility import VolatilityCalculator


class RegimeEngine:
    """Stateless regime classifier — input features, output RegimeAssessment."""

    def __init__(self) -> None:
        # Future: thresholds loaded from config/regime.yaml once validated.
        # For Phase 1-5 we keep them as named constants here, clearly marked
        # as TODO-for-validation, to make walk-forward tuning explicit.
        self._adx_strong = 25.0      # ADX > 25 = trending
        self._adx_weak = 18.0        # ADX < 18 = range-bound
        self._rsi_overbought = 70.0
        self._rsi_oversold = 30.0
        self._slope_strong = 1.5     # ADX rising by 1.5+ over 5 bars = strengthening

    # ---- public API ----
    def assess(self, snapshot: MarketSnapshot, df=None) -> RegimeAssessment:
        if not snapshot.data_valid:
            return RegimeAssessment(
                market_regime=MarketRegime.NEUTRAL,
                volatility_regime=VolatilityRegime.NORMAL_VOL,
                confidence=0.0,
                reasons=["data invalid — defaulting to NEUTRAL/NORMAL"],
                features_snapshot=self._empty_features(snapshot),
            )

        # Volatility regime
        vol_feats = VolatilityCalculator.compute(snapshot)
        # Direction regime — uses index features if already attached, else
        # synthesises from snapshot fields (works without historical df in
        # paper mode).
        market_regime, direction_reasons, conf = self._classify_direction(snapshot)

        all_reasons = list(direction_reasons) + list(vol_feats.reasons)
        return RegimeAssessment(
            market_regime=market_regime,
            volatility_regime=vol_feats.vol_regime,
            confidence=conf,
            reasons=all_reasons,
            features_snapshot=self._snapshot_features(snapshot, vol_feats),
        )

    # ---- direction classification ----
    def _classify_direction(self, s: MarketSnapshot):
        idx = s.index
        spot = idx.ltp
        reasons: list[str] = []
        adx = idx.adx or 0.0
        adx_slope = idx.adx_slope or 0.0
        rsi = idx.rsi or 50.0
        ema_fast = idx.ema_fast or spot
        ema_mid = idx.ema_mid or spot
        ema_slow = idx.ema_slow or spot
        vwap = idx.vwap or spot
        prev_close = idx.prev_close or spot

        # Trend alignment score
        bullish_align = (
            ema_fast > ema_mid > ema_slow
            and spot > vwap
            and spot >= prev_close
        )
        bearish_align = (
            ema_fast < ema_mid < ema_slow
            and spot < vwap
            and spot <= prev_close
        )

        adx_trending = adx >= self._adx_strong
        adx_ranging = adx < self._adx_weak
        strengthening = adx_slope >= self._slope_strong
        weakening = adx_slope <= -self._slope_strong

        if adx_trending and bullish_align:
            if strengthening and rsi < self._rsi_overbought:
                regime = MarketRegime.STRONG_BULL
                reasons.append("strong bull trend (ADX high, EMAs aligned up, ADX rising)")
                conf = 0.80
            elif rsi >= self._rsi_overbought:
                regime = MarketRegime.BULL
                reasons.append("bull trend but RSI overbought — momentum stretched")
                conf = 0.60
            else:
                regime = MarketRegime.BULL
                reasons.append("bull trend (ADX high, EMAs aligned up)")
                conf = 0.70
        elif adx_trending and bearish_align:
            if strengthening and rsi > self._rsi_oversold:
                regime = MarketRegime.STRONG_BEAR
                reasons.append("strong bear trend (ADX high, EMAs aligned down, ADX rising)")
                conf = 0.80
            elif rsi <= self._rsi_oversold:
                regime = MarketRegime.BEAR
                reasons.append("bear trend but RSI oversold — momentum stretched")
                conf = 0.60
            else:
                regime = MarketRegime.BEAR
                reasons.append("bear trend (ADX high, EMAs aligned down)")
                conf = 0.70
        elif adx_ranging:
            regime = MarketRegime.RANGE
            reasons.append(f"range-bound (ADX {adx:.1f} below {self._adx_weak})")
            conf = 0.65
        elif bullish_align and not adx_trending:
            regime = MarketRegime.WEAK_BULL
            reasons.append("weak bull (EMAs aligned up but ADX not strong)")
            conf = 0.55
        elif bearish_align and not adx_trending:
            regime = MarketRegime.WEAK_BEAR
            reasons.append("weak bear (EMAs aligned down but ADX not strong)")
            conf = 0.55
        elif weakening and adx_trending:
            regime = MarketRegime.REVERSAL
            reasons.append("trend weakening (ADX falling) — potential reversal")
            conf = 0.50
        else:
            regime = MarketRegime.NEUTRAL
            reasons.append("no clear edge — mixed signals")
            conf = 0.40

        # Breakout overlay
        breakout = self._detect_breakout(s, spot)
        if breakout:
            regime, br = breakout
            reasons.append(br)

        return regime, reasons, conf

    @staticmethod
    def _detect_breakout(snapshot: MarketSnapshot, spot: float):
        """Returns (regime, reason) if a clear breakout / reversal pattern exists."""
        # Use OI structure as supplementary evidence
        from ..data.option_chain import OptionChainBuilder
        oi_summary = OptionChainBuilder.oi_structure_summary(
            snapshot.option_chain, spot,
        )
        if not oi_summary:
            return None
        call_wall = oi_summary.get("call_wall")
        put_wall = oi_summary.get("put_wall")
        if call_wall and spot > call_wall:
            return MarketRegime.BREAKOUT, f"broke above call wall ({call_wall}) — short-covering"
        if put_wall and spot < put_wall:
            return MarketRegime.BREAKOUT, f"broke below put wall ({put_wall}) — long-unwind"
        return None

    # ---- feature snapshots ----
    @staticmethod
    def _snapshot_features(snapshot: MarketSnapshot, vol_feats) -> MarketFeatures:
        idx = snapshot.index
        return MarketFeatures(
            adx=idx.adx or 0.0,
            adx_slope=idx.adx_slope or 0.0,
            ema_fast=idx.ema_fast or idx.ltp,
            ema_mid=idx.ema_mid or idx.ltp,
            ema_slow=idx.ema_slow or idx.ltp,
            vwap=idx.vwap or idx.ltp,
            rsi=idx.rsi or 50.0,
            atr=idx.atr or 0.0,
            atr_pct=(idx.atr or 0.0) / idx.ltp if idx.ltp > 0 else 0.0,
            relative_volume=1.0,
            india_vix=vol_feats.vix,
            iv_rank=vol_feats.iv_percentile,
            iv_percentile=vol_feats.iv_percentile,
        )

    @staticmethod
    def _empty_features(snapshot: MarketSnapshot) -> MarketFeatures:
        idx = snapshot.index
        return MarketFeatures(
            adx=0.0, adx_slope=0.0,
            ema_fast=idx.ltp, ema_mid=idx.ltp, ema_slow=idx.ltp,
            vwap=idx.ltp, rsi=50.0, atr=0.0, atr_pct=0.0,
        )
