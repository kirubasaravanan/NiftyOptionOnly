"""Resolve shadow-EV predictions against recorded spot history.

Answers the only question that matters for `p_win`: when the regime engine
predicted a direction, did the underlying reach the target before the stop?

This needs NO real trades. The shadow log records stop/target as underlying
spot levels, so replaying recorded spot history resolves each prediction.

MEASUREMENT LIMITATIONS — read before trusting any number this produces
----------------------------------------------------------------------
1. SAMPLED PATH, NOT TRUE HIGH/LOW. Spot history comes from the decision
   journal, sampled roughly every 5 minutes. A level touched and reversed
   between two samples is invisible here. This biases toward OPEN/late
   resolution and can mis-order which level was hit first when both are
   crossed inside one gap. It does NOT bias systematically toward wins or
   losses, but it does make individual resolutions approximate.
2. OVERLAPPING PREDICTIONS. Every cycle logs a fresh prediction, so records
   are heavily autocorrelated — 50 predictions in a trending hour are close
   to one observation, not 50. Treat the effective sample as far smaller
   than the record count. Never read the raw hit-rate as if it were 50
   independent trials.
3. NO COSTS IN THE HIT-RATE. The hit-rate is path-only. Whether that rate is
   profitable is decided by comparing it against `p_breakeven`, which is
   already net of all charges in the shadow record.

A hit-rate from a few dozen overlapping intraday predictions on one or two
sessions is an early indication, not evidence. It becomes evidence across
many sessions and varied regimes.
"""
from __future__ import annotations

import json
from collections import defaultdict
from pathlib import Path
from typing import Optional

TARGET = "TARGET"
STOP = "STOP"
OPEN = "OPEN"


def load_spot_history(decisions_path: Path) -> list[tuple[str, float]]:
    """(timestamp, spot) from a decision journal, valid-data cycles only."""
    out: list[tuple[str, float]] = []
    if not decisions_path.exists():
        return out
    with open(decisions_path, "r", encoding="utf-8") as f:
        for line in f:
            if not line.strip():
                continue
            try:
                d = json.loads(line)
            except Exception:
                continue
            ss = d.get("snapshot_summary") or {}
            if not ss.get("data_valid"):
                continue
            spot = ss.get("spot")
            ts = d.get("timestamp")
            if spot and ts and spot > 0:
                out.append((ts, float(spot)))
    out.sort(key=lambda x: x[0])
    return out


def resolve_one(
    record: dict,
    structure: dict,
    spot_history: list[tuple[str, float]],
) -> tuple[str, Optional[str]]:
    """Walk forward from the prediction; report which level was reached first.

    Returns (outcome, resolved_at_timestamp).
    """
    ts0 = record["timestamp"]
    direction = record["direction"]
    stop_level = record["spot_stop_level"]
    target_level = structure["spot_target_level"]

    future = [(ts, s) for ts, s in spot_history if ts > ts0]
    for ts, spot in future:
        if direction == "BULLISH":
            # target above, stop below
            if spot >= target_level:
                return TARGET, ts
            if spot <= stop_level:
                return STOP, ts
        else:  # BEARISH: target below, stop above
            if spot <= target_level:
                return TARGET, ts
            if spot >= stop_level:
                return STOP, ts
    return OPEN, None


def resolve_all(runs_dir: str | Path, instrument_dirs: dict[str, str]) -> dict:
    """Resolve every shadow record for every instrument.

    instrument_dirs maps instrument name -> subdirectory under runs_dir
    ("" for the top-level NIFTY journals).
    """
    runs = Path(runs_dir)
    results = []
    for inst, sub in instrument_dirs.items():
        base = runs / sub if sub else runs
        shadow_path = base / "shadow" / "shadow_ev.jsonl"
        decisions_path = base / "decisions" / "decisions.jsonl"
        if not shadow_path.exists():
            continue
        history = load_spot_history(decisions_path)
        with open(shadow_path, "r", encoding="utf-8") as f:
            for line in f:
                if not line.strip():
                    continue
                try:
                    rec = json.loads(line)
                except Exception:
                    continue
                if rec.get("instrument") != inst:
                    continue
                for st in rec.get("structures", []):
                    outcome, at = resolve_one(rec, st, history)
                    results.append({
                        "instrument": inst,
                        "timestamp": rec["timestamp"],
                        "strategy": rec.get("strategy"),
                        "direction": rec.get("direction"),
                        "regime": rec.get("regime"),
                        "confidence": rec.get("confidence"),
                        "reward_risk": st["reward_risk"],
                        "p_baseline": st["p_baseline"],
                        "p_breakeven": st["p_breakeven"],
                        "outcome": outcome,
                        "resolved_at": at,
                    })
    return {"resolutions": results}


def summarise(resolutions: list[dict]) -> list[dict]:
    """Hit-rate per (instrument, reward_risk), vs baseline and break-even."""
    buckets = defaultdict(list)
    for r in resolutions:
        buckets[(r["instrument"], r["reward_risk"])].append(r)

    rows = []
    for (inst, rr), items in sorted(buckets.items()):
        decided = [i for i in items if i["outcome"] in (TARGET, STOP)]
        wins = sum(1 for i in decided if i["outcome"] == TARGET)
        n = len(decided)
        hit = (wins / n) if n else None
        p_be = items[0]["p_breakeven"]
        p_base = items[0]["p_baseline"]
        rows.append({
            "instrument": inst,
            "reward_risk": rr,
            "total_predictions": len(items),
            "resolved": n,
            "still_open": len(items) - n,
            "wins": wins,
            "hit_rate": round(hit, 4) if hit is not None else None,
            "p_baseline": p_base,
            "p_breakeven": p_be,
            "beats_breakeven": (hit > p_be) if hit is not None else None,
            "edge_vs_baseline_pp": round((hit - p_base) * 100, 2) if hit is not None else None,
        })
    return rows
