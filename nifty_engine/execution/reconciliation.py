"""Reconciliation — paper mode is trivial; live mode (Phase 9) compares
engine's position book with broker's book.

Emergency shutdown triggers are detected here:
  * Stale data
  * Unexpected position mismatch
  * Order rejection
  * Spread anomaly
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Optional

from ..models import MarketSnapshot, OptionQuote


@dataclass
class ReconcileResult:
    ok: bool
    reason: Optional[str] = None
    emergency_action: Optional[str] = None   # "SHUTDOWN" | "LIQUIDATE" | None


class Reconciler:
    """Runs every cycle before the decision pipeline."""

    STALE_DATA_SECONDS = 30
    ABNORMAL_SPREAD_POINTS = 20

    def check_snapshot(self, snapshot: MarketSnapshot) -> ReconcileResult:
        if not snapshot.data_valid:
            return ReconcileResult(
                ok=False,
                reason=snapshot.data_invalid_reason,
                emergency_action=None,           # NO-TRADE — but don't shutdown
            )
        now = datetime.utcnow()
        age = (now - snapshot.timestamp.replace(tzinfo=None)).total_seconds()
        if age > self.STALE_DATA_SECONDS:
            return ReconcileResult(
                ok=False,
                reason=f"stale data ({age:.0f}s old)",
                emergency_action="SHUTDOWN",
            )
        # Spread check on ATM options
        for q in snapshot.option_chain:
            if q.bid and q.ask:
                spread = q.ask - q.bid
                if spread > self.ABNORMAL_SPREAD_POINTS:
                    return ReconcileResult(
                        ok=False,
                        reason=f"abnormal spread {spread:.1f} on {q.symbol}",
                        emergency_action="SHUTDOWN",
                    )
        return ReconcileResult(ok=True)

    def reconcile_positions(
        self,
        engine_positions: list,
        broker_positions: list,
    ) -> ReconcileResult:
        """Phase 9: compare engine book vs broker book.

        In paper mode, broker_positions is empty and engine_positions is
        the source of truth — reconciliation is a no-op.
        """
        return ReconcileResult(ok=True)
