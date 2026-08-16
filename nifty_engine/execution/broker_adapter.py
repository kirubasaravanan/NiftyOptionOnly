"""Execution broker adapter — wraps a BrokerInterface into order-submission
semantics for live mode. Paper mode does not use this.

Phase 9 will fully implement this. Stubbed here so the OrderManager can
reference it without import errors in Phase 1-5.
"""
from __future__ import annotations

from typing import Optional


class BrokerOrderAdapter:
    """Phase 9: translate OrderRequest -> broker-specific order API call."""

    def __init__(self, broker):
        self._broker = broker

    def place_market_order(self, symbol: str, side: str, quantity: int) -> Optional[str]:
        raise NotImplementedError("live order placement is Phase 9")

    def place_limit_order(self, symbol: str, side: str, quantity: int, price: float) -> Optional[str]:
        raise NotImplementedError("live order placement is Phase 9")

    def cancel_order(self, order_id: str) -> bool:
        raise NotImplementedError("live order cancellation is Phase 9")

    def get_order_status(self, order_id: str) -> Optional[str]:
        raise NotImplementedError("live order status polling is Phase 9")
