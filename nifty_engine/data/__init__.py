"""Data layer — broker-neutral interface + DhanHQ adapter + market cache.

CRITICAL: this layer NEVER fabricates data. If the broker API returns nothing
(token missing, holiday, network error), it returns a snapshot with
`data_valid=False` and the engine upstream will emit NO-TRADE.
"""
from __future__ import annotations

from .broker_interface import BrokerInterface, BrokerError, NoDataError
from .dhan_client import DhanBroker
from .market_cache import MarketCache
from .option_chain import OptionChainBuilder

__all__ = [
    "BrokerInterface", "BrokerError", "NoDataError",
    "DhanBroker", "MarketCache", "OptionChainBuilder",
]
