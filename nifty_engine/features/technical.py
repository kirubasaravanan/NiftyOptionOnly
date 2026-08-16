"""Technical indicator computations — pure functions on price series.

These implementations are deliberately self-contained (no TA-Lib dep)
so behaviour is fully transparent and reproducible across backtest / paper
/ live modes.

All thresholds are read from config — NEVER hard-coded in the strategy.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence

import numpy as np
import pandas as pd


@dataclass
class TechnicalFeatures:
    """Container for the technical features computed from a price series."""
    adx: float
    adx_slope: float
    ema_fast: float
    ema_mid: float
    ema_slow: float
    vwap: float
    rsi: float
    atr: float
    atr_pct: float
    relative_volume: float


class TechnicalCalculator:
    """Pure-function calculator — no state."""

    EMA_FAST = 9
    EMA_MID = 21
    EMA_SLOW = 50
    RSI_PERIOD = 14
    ATR_PERIOD = 14
    ADX_PERIOD = 14
    RV_LOOKBACK = 20

    @classmethod
    def compute(cls, df: pd.DataFrame) -> TechnicalFeatures:
        """Compute features from a OHLCV DataFrame.

        df must contain columns: open, high, low, close, volume
        Last row = most recent bar.
        """
        if df is None or len(df) < max(cls.EMA_SLOW, 30):
            raise ValueError("insufficient price history for feature computation")

        close = df["close"].astype(float)
        high = df["high"].astype(float)
        low = df["low"].astype(float)
        vol = df["volume"].astype(float) if "volume" in df else pd.Series(0.0, index=df.index)

        ema_fast = cls._ema(close, cls.EMA_FAST).iloc[-1]
        ema_mid = cls._ema(close, cls.EMA_MID).iloc[-1]
        ema_slow = cls._ema(close, cls.EMA_SLOW).iloc[-1]
        rsi = cls._rsi(close, cls.RSI_PERIOD).iloc[-1]
        atr = cls._atr(high, low, close, cls.ATR_PERIOD).iloc[-1]
        atr_pct = atr / close.iloc[-1] if close.iloc[-1] > 0 else 0.0
        adx, adx_slope = cls._adx(high, low, close, cls.ADX_PERIOD)
        vwap = cls._vwap(df)
        rv = cls._relative_volume(vol, cls.RV_LOOKBACK)

        return TechnicalFeatures(
            adx=adx,
            adx_slope=adx_slope,
            ema_fast=ema_fast,
            ema_mid=ema_mid,
            ema_slow=ema_slow,
            vwap=vwap,
            rsi=rsi,
            atr=atr,
            atr_pct=atr_pct,
            relative_volume=rv,
        )

    # ---- implementations ----
    @staticmethod
    def _ema(s: pd.Series, period: int) -> pd.Series:
        return s.ewm(span=period, adjust=False, min_periods=period).mean()

    @staticmethod
    def _rsi(close: pd.Series, period: int) -> pd.Series:
        delta = close.diff()
        gain = delta.clip(lower=0.0)
        loss = (-delta).clip(lower=0.0)
        # Wilder smoothing
        avg_gain = gain.ewm(alpha=1.0 / period, adjust=False, min_periods=period).mean()
        avg_loss = loss.ewm(alpha=1.0 / period, adjust=False, min_periods=period).mean()
        rs = avg_gain / avg_loss.replace(0.0, np.nan)
        rsi = 100.0 - (100.0 / (1.0 + rs))
        return rsi.fillna(50.0)

    @staticmethod
    def _atr(high: pd.Series, low: pd.Series, close: pd.Series, period: int) -> pd.Series:
        prev_close = close.shift(1)
        tr = pd.concat([
            (high - low).abs(),
            (high - prev_close).abs(),
            (low - prev_close).abs(),
        ], axis=1).max(axis=1)
        return tr.ewm(alpha=1.0 / period, adjust=False, min_periods=period).mean()

    @classmethod
    def _adx(cls, high, low, close, period):
        """Return (adx_value, adx_slope).

        ADX measures trend strength (not direction). Slope tells us whether
        trend is strengthening or weakening.
        """
        plus_dm = (high.diff()).where((high.diff() > -low.diff()) & (high.diff() > 0), 0.0)
        minus_dm = (-low.diff()).where((-low.diff() > high.diff()) & (-low.diff() > 0), 0.0)
        tr = pd.concat([
            (high - low).abs(),
            (high - close.shift(1)).abs(),
            (low - close.shift(1)).abs(),
        ], axis=1).max(axis=1)

        atr = tr.ewm(alpha=1.0 / period, adjust=False, min_periods=period).mean()
        plus_di = 100.0 * (plus_dm.ewm(alpha=1.0 / period, adjust=False, min_periods=period).mean() / atr.replace(0.0, np.nan))
        minus_di = 100.0 * (minus_dm.ewm(alpha=1.0 / period, adjust=False, min_periods=period).mean() / atr.replace(0.0, np.nan))
        dx = 100.0 * (plus_di - minus_di).abs() / (plus_di + minus_di).replace(0.0, np.nan)
        adx = dx.ewm(alpha=1.0 / period, adjust=False, min_periods=period).mean()

        adx_val = float(adx.iloc[-1]) if not np.isnan(adx.iloc[-1]) else 0.0
        slope_val = 0.0
        if len(adx) >= 5 and not np.isnan(adx.iloc[-5]):
            slope_val = float(adx.iloc[-1] - adx.iloc[-5])
        return adx_val, slope_val

    @staticmethod
    def _vwap(df: pd.DataFrame) -> float:
        """Intraday VWAP — resets at session start.

        Requires df to have time index or a 'time' column. Falls back to
        typical price if volume missing.
        """
        if "volume" not in df or df["volume"].sum() == 0:
            typical = (df["high"] + df["low"] + df["close"]) / 3.0
            return float(typical.iloc[-1])
        typical = (df["high"] + df["low"] + df["close"]) / 3.0
        cum_v = df["volume"].cumsum()
        cum_pv = (typical * df["volume"]).cumsum()
        if cum_v.iloc[-1] == 0:
            return float(df["close"].iloc[-1])
        return float(cum_pv.iloc[-1] / cum_v.iloc[-1])

    @staticmethod
    def _relative_volume(vol: pd.Series, lookback: int) -> float:
        if len(vol) < lookback + 1:
            return 1.0
        avg = vol.iloc[-(lookback + 1):-1].mean()
        if not avg or avg == 0:
            return 1.0
        return float(vol.iloc[-1] / avg)


def attach_features_to_index(index, df: pd.DataFrame):
    """Mutates an IndexQuote in place by populating computed fields."""
    try:
        feats = TechnicalCalculator.compute(df)
    except ValueError:
        return index
    index.atr = feats.atr
    index.adx = feats.adx
    index.adx_slope = feats.adx_slope
    index.rsi = feats.rsi
    index.ema_fast = feats.ema_fast
    index.ema_mid = feats.ema_mid
    index.ema_slow = feats.ema_slow
    index.vwap = feats.vwap
    return index
