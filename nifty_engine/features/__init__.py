"""Features layer — pure computations on market data.

No IO, no broker calls. Given a snapshot, produce features used by the
regime engine and strategy selector.
"""
from __future__ import annotations

from .technical import TechnicalFeatures
from .volatility import VolatilityFeatures
from .market_regime import RegimeEngine

__all__ = ["TechnicalFeatures", "VolatilityFeatures", "RegimeEngine"]
