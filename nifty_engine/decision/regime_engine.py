"""Regime engine runner — wraps the features layer RegimeEngine and exposes
a single .assess(snapshot) call for the decision pipeline."""
from __future__ import annotations

from ..features.market_regime import RegimeEngine as FeaturesRegimeEngine
from ..models import MarketSnapshot, RegimeAssessment


class RegimeEngineRunner:
    """Adapter so the decision layer doesn't import features directly."""

    def __init__(self) -> None:
        self._engine = FeaturesRegimeEngine()

    def assess(self, snapshot: MarketSnapshot) -> RegimeAssessment:
        return self._engine.assess(snapshot)
