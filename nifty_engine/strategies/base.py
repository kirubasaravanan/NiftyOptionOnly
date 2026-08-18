"""Strategy interface — every strategy implements this contract."""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Optional

from ..config import load as load_config
from ..execution import CostModel
from ..models import (
    MarketSnapshot, OptionType, RegimeAssessment, StrategyEvaluation,
    StrategyName,
)


class StrategyBase(ABC):
    """Base class for all strategies.

    CONVENTIONS:
      * `enabled` is read from strategies.yaml
      * `min_expected_net_value` is the NO-TRADE threshold — if expected
        net < this, the strategy returns eligible=False
      * `min_confidence_score` filters out low-conviction setups
      * `min_risk_reward` ensures asymmetric reward
      * Strategies NEVER hard-code directional thresholds; they consume
        features computed by the regime engine.

    To add a new strategy:
      1. Subclass StrategyBase
      2. Set STRATEGY_NAME
      3. Implement evaluate()
      4. Add an entry in strategies.yaml
    """

    STRATEGY_NAME: StrategyName = StrategyName.NO_TRADE
    OPTION_TYPE: Optional[OptionType] = None     # CE for call, PE for put, None for neutral

    # Config name per instrument (2026-08-18) — "nifty" reads the original
    # strategies.yaml unchanged; other instruments read their own
    # strategies_{instrument}.yaml (literal copies pending validation).
    _CONFIG_NAME_BY_INSTRUMENT = {
        "nifty": "strategies",
        "banknifty": "strategies_banknifty",
        "sensex": "strategies_sensex",
    }

    def __init__(self, instrument: str = "nifty") -> None:
        self._instrument = instrument.lower()
        config_name = self._CONFIG_NAME_BY_INSTRUMENT.get(self._instrument, "strategies")
        self._cfg = load_config(config_name)["strategies"].get(
            self.STRATEGY_NAME.value.lower(), {}
        )
        self._enabled = bool(self._cfg.get("enabled", False))
        self._min_env = float(self._cfg.get("min_expected_net_value", 0.0))
        self._min_conf = float(self._cfg.get("min_confidence_score", 0.0))
        self._min_rr = float(self._cfg.get("min_risk_reward", 0.0))
        self._cost_model = CostModel()

    @property
    def name(self) -> StrategyName:
        return self.STRATEGY_NAME

    @property
    def enabled(self) -> bool:
        return self._enabled

    @property
    def min_expected_net_value(self) -> float:
        return self._min_env

    @abstractmethod
    def evaluate(
        self,
        snapshot: MarketSnapshot,
        regime: RegimeAssessment,
    ) -> StrategyEvaluation:
        """Score this strategy. Returns eligible=True iff all hard filters pass."""
        ...

    # ---- helpers shared by all strategies ----
    def _not_eligible(self, *reasons: str) -> StrategyEvaluation:
        return StrategyEvaluation(
            strategy=self.STRATEGY_NAME,
            enabled=self._enabled,
            eligible=False,
            expected_net_value=0.0,
            confidence_score=0.0,
            risk_reward=0.0,
            reasons=list(reasons),
        )

    def _estimate_cost(
        self,
        entry_premium: float,
        quantity: int,
    ) -> float:
        return self._cost_model.estimate_round_trip_cost(entry_premium, quantity)

    def _required_move_points(
        self,
        delta: float,
        theta_loss: float,
        cost: float,
        qty: int,
    ) -> Optional[float]:
        """Underlying move (points) needed for this evaluation to clear
        min_expected_net_value, holding delta/theta_loss/cost/qty fixed.

        Derived from expected_net = (delta * move - theta_loss) * qty - cost,
        solved for move where expected_net == min_expected_net_value. Pass
        net_delta for spreads (long_delta - short_delta). Returns None when
        delta <= 0 — no move size clears the bar in that case.
        """
        if delta <= 0 or qty <= 0:
            return None
        return theta_loss / delta + (self.min_expected_net_value + cost) / (delta * qty)
