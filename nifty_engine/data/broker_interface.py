"""Broker-neutral interface.

Adding another broker (Kite / Angel / Fyers) later means implementing this
interface. The strategy / decision / execution layers never import
broker-specific code directly.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import date, datetime
from typing import Optional

from ..models import OptionQuote, IndexQuote, IndiaVIX, MarketSnapshot, OptionType


class BrokerError(RuntimeError):
    """Generic broker error."""


class NoDataError(BrokerError):
    """Raised when the broker returns no usable market data.

    The engine treats this as 'data_valid=False' and emits NO-TRADE.
    This includes: missing token, holiday, market closed, API failure.
    """


class BrokerInterface(ABC):
    """Contract every broker adapter must satisfy."""

    # ---- connectivity ----
    @abstractmethod
    def is_connected(self) -> bool:
        """True iff the adapter has valid credentials and broker is reachable."""

    @abstractmethod
    def is_market_open(self) -> bool:
        """Return True iff NSE is currently in normal trading session."""

    # ---- market data ----
    @abstractmethod
    def get_index_quote(self, symbol: str = "NIFTY") -> IndexQuote:
        """Fetch live index quote (LTP, OHLC, VWAP, volume)."""

    @abstractmethod
    def get_india_vix(self) -> Optional[IndiaVIX]:
        """Fetch India VIX quote. None if unavailable."""

    @abstractmethod
    def get_option_chain(
        self,
        underlying: str = "NIFTY",
        expiry: Optional[date] = None,
        n_strikes_each_side: int = 5,
    ) -> list[OptionQuote]:
        """Fetch option chain for the given expiry (or nearest weekly).

        Returns at minimum ATM +/- n_strikes_each_side strikes for both
        CE and PE. Each OptionQuote must contain LTP, bid, ask, volume, OI,
        and Greeks where available.
        """

    @abstractmethod
    def get_snapshot(
        self,
        underlying: str = "NIFTY",
        n_strikes_each_side: int = 5,
    ) -> MarketSnapshot:
        """Convenience: build a full MarketSnapshot for the decision engine."""

    # ---- historical (Phase 6 backtest — wired later) ----
    @abstractmethod
    def historical_candles(
        self,
        symbol: str,
        interval: str,             # "1min", "5min", "15min", "day"
        start: datetime,
        end: datetime,
    ):
        """Return historical OHLCV as pandas DataFrame.

        Used by the backtesting engine only. Not in the hot path of paper/live.
        """
