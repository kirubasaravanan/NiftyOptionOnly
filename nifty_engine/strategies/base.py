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

    # Indian equity market: 09:15-15:30 IST = 6h15m = 375 minutes.
    # With 5-minute bars that's 75 bars per trading day. Used to convert
    # DhanHQ's PER-DAY theta into the strategy's actual holding period.
    BARS_PER_TRADING_DAY = 75

    # Intraday params live in risk*.yaml so the strategies and the risk engine
    # read ONE definition. Their previous divergence is exactly what made the
    # risk_reward gate unreachable (2026-08-19).
    _RISK_CONFIG_BY_INSTRUMENT = {
        "nifty": "risk", "banknifty": "risk_banknifty", "sensex": "risk_sensex",
    }

    def _intraday_cfg(self) -> dict:
        try:
            name = self._RISK_CONFIG_BY_INSTRUMENT.get(self._instrument, "risk")
            return load_config(name).get("intraday", {}) or {}
        except Exception:
            return {}

    def _stop_distance_points(self, expected_move: float) -> float:
        """Stop distance in UNDERLYING points, derived from expected move.

        Scales with volatility and horizon, unlike a fixed premium fraction.
        The risk engine places its stop from this same definition.
        """
        frac = float(self._intraday_cfg().get("stop_fraction_of_expected_move", 0.50))
        return max(0.0, expected_move * frac)

    def _expected_move_for_horizon(
        self, atr_5min: float, horizon_bars: int, capture_fraction: float
    ) -> float:
        """Expected DIRECTIONAL move over `horizon_bars` 5-minute bars.

        FIX 2026-08-19 (intraday day-trading): the previous form was a bare
        `atr * 1.5`, which does not scale with the holding period at all —
        the horizon constant existed but never entered the move calculation,
        so a 30-minute and a 2-hour hold produced an identical expected move
        while theta correctly grew with time. That asymmetry made longer
        holds look strictly worse, which is backwards for a directional
        trade.

        Range accumulates with the square root of time for a random walk, so
        the move scale over N bars is atr * sqrt(N). `capture_fraction` is
        then how much of that range a directional entry actually captures —
        strictly below 1.0, because range counts travel in both directions
        while a directional position only monetises net displacement.

        UNVALIDATED: capture_fraction is the single least-evidenced number in
        this calculation. It should be checked against the shadow log's
        measured target-vs-stop outcomes before any weight is put on the EV
        that depends on it.
        """
        if atr_5min <= 0 or horizon_bars <= 0:
            return 0.0
        return atr_5min * (horizon_bars ** 0.5) * capture_fraction

    def _theta_loss_for_horizon(self, theta_per_day: float, horizon_bars: int) -> float:
        """Convert a PER-DAY theta into decay over `horizon_bars` 5-min bars.

        FIX 2026-08-19: this previously read `theta * (HORIZON_BARS / 6.0)`,
        which with HORIZON_BARS=6 evaluates to `theta * 1.0` — a FULL DAY of
        decay charged against a 30-minute holding period, a 12.5x
        overstatement. The comment on that line said "~30min worth", so the
        intent was clearly the fraction of a trading day; the divisor should
        have been bars-per-day (75), not 6.

        Verified against live data that DhanHQ's theta is per-day: a 6-DTE
        ATM NIFTY put at premium 97 quoted theta -6.29, i.e. ~37.7 of decay
        over its remaining life — consistent with per-day. A per-hour reading
        would consume the entire premium in 2.5 days, which is impossible for
        a 6-day option.

        Uses trading minutes (375/day), not calendar minutes (1440/day) — the
        deliberately conservative choice, since it attributes all decay to
        market hours and so charges ~4x more theta than a calendar split.
        """
        return abs(theta_per_day) * (horizon_bars / float(self.BARS_PER_TRADING_DAY))

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
