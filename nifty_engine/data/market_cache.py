"""In-memory market cache.

Reduces redundant broker calls within a single decision cycle. TTL-based
expiry so stale data is never used in the hot loop.
"""
from __future__ import annotations

import threading
import time
from datetime import date
from typing import Optional

from ..models import IndexQuote, IndiaVIX, MarketSnapshot, OptionQuote


class MarketCache:
    """Thread-safe TTL cache for snapshots & derived pieces."""

    def __init__(self, ttl_seconds: float = 5.0) -> None:
        self._ttl = ttl_seconds
        self._lock = threading.Lock()
        self._index: Optional[IndexQuote] = None
        self._index_ts: float = 0.0
        self._vix: Optional[IndiaVIX] = None
        self._vix_ts: float = 0.0
        self._chain: list[OptionQuote] = []
        self._chain_key: Optional[tuple] = None  # (underlying, expiry, n)
        self._chain_ts: float = 0.0

    # ---- public API ----
    def get_index(self) -> Optional[IndexQuote]:
        with self._lock:
            if self._index is None:
                return None
            if time.time() - self._index_ts > self._ttl:
                return None
            return self._index

    def put_index(self, q: IndexQuote) -> None:
        with self._lock:
            self._index = q
            self._index_ts = time.time()

    def get_vix(self) -> Optional[IndiaVIX]:
        with self._lock:
            if self._vix is None:
                return None
            if time.time() - self._vix_ts > self._ttl:
                return None
            return self._vix

    def put_vix(self, v: IndiaVIX) -> None:
        with self._lock:
            self._vix = v
            self._vix_ts = time.time()

    def get_chain(
        self, underlying: str, expiry: Optional[date], n: int,
    ) -> Optional[list[OptionQuote]]:
        key = (underlying, expiry, n)
        with self._lock:
            if self._chain_key != key:
                return None
            if time.time() - self._chain_ts > self._ttl:
                return None
            return list(self._chain)

    def put_chain(
        self, underlying: str, expiry: Optional[date], n: int,
        chain: list[OptionQuote],
    ) -> None:
        key = (underlying, expiry, n)
        with self._lock:
            self._chain_key = key
            self._chain = list(chain)
            self._chain_ts = time.time()

    def invalidate(self) -> None:
        with self._lock:
            self._index = None
            self._vix = None
            self._chain = []
            self._chain_key = None
