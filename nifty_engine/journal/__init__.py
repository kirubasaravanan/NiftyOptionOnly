"""Journal — trade & decision logging + performance analytics.

Every cycle (including NO-TRADE) is journaled. This is critical for
strategy improvement — knowing WHY the system didn't trade is as
important as knowing why it did.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Optional

from ..models import Decision, DecisionRecord, Position, TradeRecord
from .performance import PerformanceAnalytics, PerformanceReport


class TradeLogger:
    """Appends trade records to a JSONL file under runs/journal/."""

    def __init__(self, runs_dir: str | Path = "/home/z/my-project/runs") -> None:
        self._dir = Path(runs_dir) / "journal"
        self._dir.mkdir(parents=True, exist_ok=True)
        self._path = self._dir / "trades.jsonl"

    def log_trade(self, trade: TradeRecord) -> None:
        with open(self._path, "a", encoding="utf-8") as f:
            f.write(trade.model_dump_json() + "\n")

    def all_trades(self) -> list[TradeRecord]:
        if not self._path.exists():
            return []
        out = []
        with open(self._path, "r", encoding="utf-8") as f:
            for line in f:
                try:
                    out.append(TradeRecord.model_validate_json(line))
                except Exception:
                    continue
        return out


class MarketSnapshotLogger:
    """Appends a compact option-chain slice to runs/snapshots/market_snapshots.jsonl.

    Purely observational — feeds later strategy research (e.g. reconstructing
    what a credit spread would have looked like at a given moment). Never read
    by any live trading logic; saving here can never change a decision.
    """

    def __init__(self, runs_dir: str | Path = "/home/z/my-project/runs") -> None:
        self._dir = Path(runs_dir) / "snapshots"
        self._dir.mkdir(parents=True, exist_ok=True)
        self._path = self._dir / "market_snapshots.jsonl"

    def log_snapshot(self, record: dict) -> None:
        with open(self._path, "a", encoding="utf-8") as f:
            f.write(json.dumps(record, default=str) + "\n")


class DecisionLogger:
    """Appends every cycle's decision to runs/decisions/decisions.jsonl."""

    def __init__(self, runs_dir: str | Path = "/home/z/my-project/runs") -> None:
        self._dir = Path(runs_dir) / "decisions"
        self._dir.mkdir(parents=True, exist_ok=True)
        self._path = self._dir / "decisions.jsonl"

    def log_decision(
        self,
        decision: Decision,
        snapshot_summary: dict,
        all_evaluations: Optional[list[dict]] = None,
        confirmation_summary: Optional[dict] = None,
    ) -> None:
        record = DecisionRecord(
            timestamp=decision.timestamp,
            action=decision.action,
            strategy=decision.strategy,
            regime=decision.regime,
            volatility=decision.volatility,
            confidence=decision.confidence,
            expected_net_value=decision.expected_net_value,
            reasons=decision.reasons,
            snapshot_summary=snapshot_summary,
            all_evaluations=all_evaluations or [],
            confirmation_summary=confirmation_summary,
        )
        with open(self._path, "a", encoding="utf-8") as f:
            f.write(record.model_dump_json() + "\n")

    def all_decisions(self) -> list[DecisionRecord]:
        if not self._path.exists():
            return []
        out = []
        with open(self._path, "r", encoding="utf-8") as f:
            for line in f:
                try:
                    out.append(DecisionRecord.model_validate_json(line))
                except Exception:
                    continue
        return out


__all__ = [
    "TradeLogger", "DecisionLogger", "MarketSnapshotLogger",
    "PerformanceAnalytics", "PerformanceReport",
]
