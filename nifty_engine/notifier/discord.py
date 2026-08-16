"""Discord webhook notifier — 15 alert types covering the full trade lifecycle.

Per spec:
  Prediction != position management.
  The system must distinguish:
    A. Prediction ("NIFTY will rise")
    B. Thesis ("because VWAP+ADX+breadth+BankNifty confirm")
    C. Invalidation ("if NIFTY breaks X + VWAP lost + structure breaks -> exit")

Discord channels (logical; physical channel depends on webhook config):
  #market-regime, #trade-setups, #live-paper-trades, #risk-alerts,
  #trade-reviews, #daily-performance, #system-health
"""
from __future__ import annotations

import os
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Optional

import requests


class AlertType(str, Enum):
    REGIME_CHANGE = "REGIME_CHANGE"                       # 🧭
    SETUP_DETECTED = "SETUP_DETECTED"                     # 🎯
    ENTRY = "ENTRY"                                       # 🟢
    POSITION_UPDATE = "POSITION_UPDATE"                   # 📈
    THESIS_DETERIORATING = "THESIS_DETERIORATING"         # ⚠️
    POSITION_ADJUSTED = "POSITION_ADJUSTED"               # 🔄
    THESIS_INVALIDATED = "THESIS_INVALIDATED"             # 🚨
    EXIT = "EXIT"                                         # 🔴
    REVERSAL = "REVERSAL"                                 # 🔄
    RISK_LIMIT = "RISK_LIMIT"                             # 🛑
    TIME_STOP = "TIME_STOP"                               # ⏱️
    DATA_API_ERROR = "DATA_API_ERROR"                     # 💥
    DAILY_SUMMARY = "DAILY_SUMMARY"                       # 📊
    TRADE_REVIEW = "TRADE_REVIEW"                         # 📝
    STRATEGY_PERFORMANCE = "STRATEGY_PERFORMANCE"         # 📈


class AlertLevel(str, Enum):
    DEBUG = "DEBUG"
    INFO = "INFO"
    WARN = "WARN"
    ERROR = "ERROR"
    CRITICAL = "CRITICAL"


# Emoji + colour per alert type
_ALERT_META = {
    AlertType.REGIME_CHANGE:            {"emoji": "🧭", "color": 0x3498DB, "level": AlertLevel.INFO},      # blue
    AlertType.SETUP_DETECTED:           {"emoji": "🎯", "color": 0xF1C40F, "level": AlertLevel.INFO},     # yellow
    AlertType.ENTRY:                    {"emoji": "🟢", "color": 0x2ECC71, "level": AlertLevel.INFO},     # green
    AlertType.POSITION_UPDATE:          {"emoji": "📈", "color": 0x2ECC71, "level": AlertLevel.DEBUG},    # green
    AlertType.THESIS_DETERIORATING:     {"emoji": "⚠️", "color": 0xE67E22, "level": AlertLevel.WARN},     # orange
    AlertType.POSITION_ADJUSTED:        {"emoji": "🔄", "color": 0x9B59B6, "level": AlertLevel.INFO},     # purple
    AlertType.THESIS_INVALIDATED:       {"emoji": "🚨", "color": 0xE74C3C, "level": AlertLevel.CRITICAL},# red
    AlertType.EXIT:                     {"emoji": "🔴", "color": 0xE74C3C, "level": AlertLevel.WARN},     # red
    AlertType.REVERSAL:                 {"emoji": "🔄", "color": 0x9B59B6, "level": AlertLevel.WARN},    # purple
    AlertType.RISK_LIMIT:               {"emoji": "🛑", "color": 0xE74C3C, "level": AlertLevel.CRITICAL},# red
    AlertType.TIME_STOP:                {"emoji": "⏱️", "color": 0xE67E22, "level": AlertLevel.WARN},     # orange
    AlertType.DATA_API_ERROR:           {"emoji": "💥", "color": 0xE74C3C, "level": AlertLevel.ERROR},   # red
    AlertType.DAILY_SUMMARY:            {"emoji": "📊", "color": 0x3498DB, "level": AlertLevel.INFO},     # blue
    AlertType.TRADE_REVIEW:             {"emoji": "📝", "color": 0x95A5A6, "level": AlertLevel.INFO},    # grey
    AlertType.STRATEGY_PERFORMANCE:     {"emoji": "📈", "color": 0x3498DB, "level": AlertLevel.INFO},    # blue
}


_LEVEL_PRIORITY = {
    AlertLevel.DEBUG: 10,
    AlertLevel.INFO: 20,
    AlertLevel.WARN: 30,
    AlertLevel.ERROR: 40,
    AlertLevel.CRITICAL: 50,
}


@dataclass
class Alert:
    """One Discord alert payload."""
    type: AlertType
    title: str
    fields: dict = field(default_factory=dict)            # key-value pairs shown as fields
    description: Optional[str] = None
    timestamp: datetime = field(default_factory=datetime.utcnow)
    footer: Optional[str] = None

    def to_discord_payload(self) -> dict:
        meta = _ALERT_META[self.type]
        emoji = meta["emoji"]
        color = meta["color"]

        # Build embed
        embed: dict = {
            "title": f"{emoji} {self.title}",
            "color": color,
            "timestamp": self.timestamp.isoformat(),
            "fields": [],
        }
        if self.description:
            embed["description"] = self.description

        for k, v in self.fields.items():
            value = str(v) if v is not None else "—"
            # Discord field value max 1024 chars
            if len(value) > 1024:
                value = value[:1020] + "..."
            embed["fields"].append({
                "name": k,
                "value": value,
                "inline": len(str(value)) < 40,  # short values render inline
            })

        if self.footer:
            embed["footer"] = {"text": self.footer}
        else:
            embed["footer"] = {"text": f"NIFTY Engine · {self.type.value}"}

        return {"embeds": [embed], "username": "NIFTY Engine"}


class DiscordNotifier:
    """Async Discord webhook notifier — non-blocking, fail-safe.

    Features:
      * Sends alerts to Discord webhook in a background thread
      * Filters by minimum alert level
      * Logs all alerts locally (in-memory ring buffer + JSONL file)
      * Never raises — Discord failures are logged but never crash the engine
    """

    MAX_RING_BUFFER = 500

    def __init__(
        self,
        webhook_url: Optional[str] = None,
        enabled: bool = True,
        min_level: AlertLevel = AlertLevel.INFO,
        log_file: Optional[str] = "/home/z/my-project/runs/alerts/alerts.jsonl",
    ) -> None:
        self.webhook_url = webhook_url or os.environ.get("DISCORD_WEBHOOK_URL", "")
        self.enabled = enabled and os.environ.get("DISCORD_ENABLED", "true").lower() == "true"
        self.min_level = min_level
        self.log_file = log_file
        self._lock = threading.Lock()
        self._ring_buffer: list[Alert] = []

        if self.log_file:
            from pathlib import Path
            Path(self.log_file).parent.mkdir(parents=True, exist_ok=True)

    # ---- public API ----
    def send(self, alert: Alert) -> bool:
        """Send an alert. Returns True if sent (or queued), False if filtered out."""
        meta_level = _ALERT_META[alert.type]["level"]
        if _LEVEL_PRIORITY[meta_level] < _LEVEL_PRIORITY[self.min_level]:
            # Still log locally even if not sent to Discord
            self._log_locally(alert, sent=False, reason="filtered")
            return False

        # Always log locally
        self._log_locally(alert, sent=True, reason="sent")

        if not self.enabled or not self.webhook_url:
            return False

        # Send in background thread — never block the engine
        payload = alert.to_discord_payload()
        thread = threading.Thread(
            target=self._send_payload, args=(payload,), daemon=True,
        )
        thread.start()
        return True

    def send_alert(
        self,
        type: AlertType,
        title: str,
        fields: Optional[dict] = None,
        description: Optional[str] = None,
        footer: Optional[str] = None,
    ) -> bool:
        """Convenience method."""
        return self.send(Alert(
            type=type, title=title, fields=fields or {},
            description=description, footer=footer,
        ))

    def recent_alerts(self, limit: int = 50) -> list[dict]:
        """Return recent alerts from the in-memory ring buffer (newest first)."""
        with self._lock:
            out = []
            for a in reversed(self._ring_buffer[-limit:]):
                out.append({
                    "type": a.type.value,
                    "title": a.title,
                    "fields": a.fields,
                    "description": a.description,
                    "timestamp": a.timestamp.isoformat(),
                    "footer": a.footer,
                    "emoji": _ALERT_META[a.type]["emoji"],
                    "level": _ALERT_META[a.type]["level"].value,
                })
            return out

    # ---- internal ----
    def _log_locally(self, alert: Alert, sent: bool, reason: str) -> None:
        with self._lock:
            self._ring_buffer.append(alert)
            if len(self._ring_buffer) > self.MAX_RING_BUFFER:
                self._ring_buffer = self._ring_buffer[-self.MAX_RING_BUFFER:]
        if self.log_file:
            try:
                import json
                with open(self.log_file, "a", encoding="utf-8") as f:
                    payload = alert.to_discord_payload()
                    record = {
                        "timestamp": alert.timestamp.isoformat(),
                        "type": alert.type.value,
                        "title": alert.title,
                        "sent_to_discord": sent,
                        "reason": reason,
                        "embed": payload["embeds"][0] if payload.get("embeds") else None,
                    }
                    f.write(json.dumps(record) + "\n")
            except Exception:
                pass  # never crash the engine on log file write

    def _send_payload(self, payload: dict) -> None:
        try:
            resp = requests.post(
                self.webhook_url, json=payload, timeout=10,
                headers={"Content-Type": "application/json"},
            )
            if resp.status_code not in (200, 204):
                # Discord rate limited or webhook issue — log silently
                pass
        except Exception:
            pass  # never crash the engine on Discord failure


# Singleton notifier
_notifier_singleton: Optional[DiscordNotifier] = None


def get_notifier() -> DiscordNotifier:
    global _notifier_singleton
    if _notifier_singleton is None:
        _notifier_singleton = DiscordNotifier()
    return _notifier_singleton
