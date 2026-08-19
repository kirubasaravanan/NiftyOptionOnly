"""Shadow EV model — Stage 1 (observation only), added 2026-08-19.

WHY THIS EXISTS
---------------
The live strategies compute `expected_net_value` as a 30-minute point
estimate (`delta x 1.5*ATR - theta`). That number does not describe the
trade the system would actually place, which is: enter, then exit at a stop
(50% of premium) or a target. Every gate in the system therefore filters on
a quantity that doesn't correspond to the position being taken.

Priced against the REAL exit structure, a driftless random walk gives
EV = 0 before costs and negative after — which is the correct efficient-
market baseline. So the system's only possible edge is directional
prediction (drift), and that edge is currently absent from the EV maths
entirely: `confidence` gates eligibility but is never used as a probability.

WHAT THIS MODULE DOES
---------------------
Computes, per cycle, what a structural-EV model WOULD conclude, and logs it.
It changes no decision. It is not read by any trading code path.

THE POINT OF THE LOGGING
------------------------
It records the predicted direction plus the stop/target levels expressed as
UNDERLYING price levels. That means a later resolver can replay recorded
spot history and answer, without a single real trade having been placed:

    "when the regime engine said BEARISH with confidence 0.6, how often did
     the underlying reach the target before the stop?"

That measured hit-rate is the only honest source for `p_win`. Wiring the
existing `confidence` score straight into `p_win` would be the tempting
shortcut and is exactly what this module is designed to avoid: confidence is
a blend of regime/liquidity/edge/RR that was never calibrated as a
probability, and shifting it from 0.33 to 0.50 flips the system from never
trading to trading everything. That parameter has to be earned from data.

The goal is a system that is profitable AFTER brokerage, STT, exchange
charges, GST, stamp duty and slippage — so every EV here is net of the full
cost model, never gross.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Optional

# Reward:risk multiples to evaluate. The live config's implied structure is
# far below 1.0 (it risks ~4.7x what it targets); these bracket the range
# worth studying so the log can show which structure actually held up.
REWARD_RISK_MULTIPLES = (1.0, 2.0, 3.0)

# p_win values to price EV at. Deliberately includes the random-walk baseline
# (computed per-candidate) plus fixed points, so the log shows how sensitive
# the conclusion is to this single unproven parameter.
PROBE_P_WINS = (0.35, 0.40, 0.50, 0.60)


def compute_shadow_ev(
    *,
    instrument: str,
    strategy: str,
    direction: Optional[str],
    spot: float,
    premium: float,
    delta: float,
    quantity: int,
    cost_round_trip: float,
    stop_fraction: float,
    regime: str,
    confidence: float,
    atr: Optional[float] = None,
) -> Optional[dict]:
    """Price the ACTUAL stop/target structure, for several target sizes.

    Returns None when inputs can't support a meaningful calculation, rather
    than substituting defaults — a silently-defaulted number here would be
    worse than no number, since the whole purpose is honest measurement.
    """
    if premium <= 0 or delta <= 0 or quantity <= 0 or spot <= 0:
        return None
    if direction not in ("BULLISH", "BEARISH"):
        return None

    # Loss side is fixed by the stop: a fraction of premium.
    loss_per_unit = premium * stop_fraction
    loss_at_stop = loss_per_unit * quantity
    if loss_at_stop <= 0:
        return None

    # Underlying move required to reach that stop, via delta.
    underlying_to_stop = loss_per_unit / delta
    sign = 1.0 if direction == "BULLISH" else -1.0
    spot_stop_level = spot - sign * underlying_to_stop

    structures = []
    for rr in REWARD_RISK_MULTIPLES:
        gain_at_target = loss_at_stop * rr
        underlying_to_target = (gain_at_target / quantity) / delta
        spot_target_level = spot + sign * underlying_to_target

        # Driftless first-passage baseline: with no edge, the chance of
        # touching target before stop is just the distance ratio. This is
        # the number any claimed edge must beat.
        p_baseline = underlying_to_stop / (underlying_to_stop + underlying_to_target)
        # Win rate at which EV net of costs is exactly zero.
        p_breakeven = (loss_at_stop + cost_round_trip) / (gain_at_target + loss_at_stop)

        ev_by_p = {
            f"{p:.2f}": round(p * gain_at_target - (1.0 - p) * loss_at_stop - cost_round_trip, 2)
            for p in PROBE_P_WINS
        }
        ev_by_p["baseline"] = round(
            p_baseline * gain_at_target - (1.0 - p_baseline) * loss_at_stop - cost_round_trip, 2
        )

        structures.append({
            "reward_risk": rr,
            "gain_at_target": round(gain_at_target, 2),
            "underlying_to_target": round(underlying_to_target, 2),
            "spot_target_level": round(spot_target_level, 2),
            "p_baseline": round(p_baseline, 4),
            "p_breakeven": round(p_breakeven, 4),
            # Edge over the random walk that this structure demands. Smaller
            # = less predictive skill required = more robust.
            "edge_required_pp": round((p_breakeven - p_baseline) * 100, 2),
            "ev_net_by_p_win": ev_by_p,
        })

    return {
        "instrument": instrument,
        "strategy": strategy,
        "direction": direction,
        "regime": regime,
        # Logged so a resolver can later test whether confidence actually
        # predicts anything — NOT used as a probability here.
        "confidence": round(confidence, 4),
        "spot_at_eval": round(spot, 2),
        "premium": round(premium, 2),
        "delta": round(delta, 4),
        "atr": round(atr, 2) if atr else None,
        "quantity": quantity,
        "cost_round_trip": round(cost_round_trip, 2),
        "stop_fraction": stop_fraction,
        "loss_at_stop": round(loss_at_stop, 2),
        "underlying_to_stop": round(underlying_to_stop, 2),
        "spot_stop_level": round(spot_stop_level, 2),
        "structures": structures,
        # Set by the resolver later; present here so the schema is stable.
        "outcome": None,
    }


class ShadowEVLogger:
    """Appends shadow-EV records to runs/shadow/shadow_ev.jsonl.

    Separate file from decisions/trades so research output can never be
    mistaken for, or contaminate, the real decision journal.
    """

    def __init__(self, runs_dir: str | Path = "/home/z/my-project/runs") -> None:
        self._dir = Path(runs_dir) / "shadow"
        self._dir.mkdir(parents=True, exist_ok=True)
        self._path = self._dir / "shadow_ev.jsonl"

    def log(self, timestamp: str, records: list[dict]) -> None:
        if not records:
            return
        with open(self._path, "a", encoding="utf-8") as f:
            for r in records:
                f.write(json.dumps({"timestamp": timestamp, **r}, default=str) + "\n")
