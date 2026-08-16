"""Phase 8 — Notifier system + Thesis tracker + 3-layer protection.

Discord-driven live trade lifecycle: REGIME_CHANGE → SETUP_DETECTED → ENTRY →
POSITION_UPDATE → THESIS_DETERIORATING → POSITION_ADJUSTED → THESIS_INVALIDATED
→ EXIT / REVERSAL / TIME_STOP / RISK_LIMIT.

Three-layer protection:
  Layer 1 — Hard monetary stop (₹ loss cap)
  Layer 2 — Market-structure invalidation (VWAP lost + swing broken + breadth drop)
  Layer 3 — Time invalidation (no movement within expected horizon)
"""
from __future__ import annotations

from .discord import DiscordNotifier, AlertType, Alert, AlertLevel
from .thesis import ThesisTracker, ThesisScore, PositionState
from .protection import ProtectionLayer, ProtectionResult, ProtectionConfig, ProtectionTrigger
from .mae_mfe import MAEMFETracker, MAEMFERecord

__all__ = [
    "DiscordNotifier", "AlertType", "Alert", "AlertLevel",
    "ThesisTracker", "ThesisScore", "PositionState",
    "ProtectionLayer", "ProtectionResult", "ProtectionConfig", "ProtectionTrigger",
    "MAEMFETracker", "MAEMFERecord",
]
