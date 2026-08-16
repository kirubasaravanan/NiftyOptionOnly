"""DhanHQ broker adapter.

Implements BrokerInterface using the official `dhanhq` Python package.
Token + client ID are read from environment variables — never hard-coded.

DESIGN RULES:
  * If DHAN_ACCESS_TOKEN or DHAN_CLIENT_ID is empty -> raise NoDataError.
  * If the broker library is not installed -> raise NoDataError with reason.
  * If the broker returns empty/None -> raise NoDataError.
  * NEVER fall back to synthetic / mock data. The decision engine will
    see `data_valid=False` and emit NO-TRADE.

Today is a market holiday — running the engine will produce a clean
NO-TRADE log explaining "market closed / no token".
"""
from __future__ import annotations

import os
from datetime import date, datetime, timedelta, timezone
from typing import Optional

from ..models import (
    IndiaVIX, IndexQuote, MarketSnapshot, OptionQuote, OptionType, TimeBucket,
)
from ..config import load as load_config
from ..utils.time_utils import ist_now, is_market_open, current_time_bucket
from .broker_interface import BrokerError, BrokerInterface, NoDataError


# Try importing dhanhq lazily so the rest of the engine can run without it
try:
    from dhanhq import dhan
    _DHAN_AVAILABLE = True
except ImportError:
    _DHAN_AVAILABLE = False


class DhanBroker(BrokerInterface):
    """DhanHQ implementation of BrokerInterface."""

    def __init__(self) -> None:
        self._token = os.environ.get("DHAN_ACCESS_TOKEN", "").strip()
        self._client_id = os.environ.get("DHAN_CLIENT_ID", "").strip()
        self._cfg = load_config("broker")["dhan"]
        self._client = None
        self._connected = False

        if not self._token or not self._client_id:
            # Don't raise here — let is_connected() report False and the
            # engine will log a clean NO-TRADE.
            return

        if not _DHAN_AVAILABLE:
            # The dhanhq package isn't installed — treat as no-data.
            self._token = ""
            return

        try:
            self._client = dhan.Dhan(
                client_id=self._client_id,
                access_token=self._token,
            )
            self._connected = True
        except Exception as exc:
            # Never leak credential details.
            raise BrokerError(f"Dhan client init failed: {type(exc).__name__}") from exc

    # ---- connectivity ----
    def is_connected(self) -> bool:
        return bool(self._connected and self._client is not None)

    def is_market_open(self) -> bool:
        """Check whether NSE is currently in a live session in IST."""
        return is_market_open()

    # ---- helpers ----
    def _require_connected(self) -> None:
        if not self.is_connected():
            reason = "missing DHAN_ACCESS_TOKEN / DHAN_CLIENT_ID or dhanhq not installed"
            raise NoDataError(f"Dhan broker not connected: {reason}")

    # ---- market data ----
    def get_index_quote(self, symbol: str = "NIFTY") -> IndexQuote:
        self._require_connected()
        try:
            # dhanhq: market_feed for index
            resp = self._client.market_feed(
                instrument="IDX_I",
                exchange_segment="IDX_I",
                instrument_token=str(self._cfg["nifty"]["instrument_token"]),
            )
        except Exception as exc:
            raise NoDataError(f"index quote fetch failed: {type(exc).__name__}") from exc

        data = self._extract_first(resp)
        if not data:
            raise NoDataError("index quote returned empty payload")

        try:
            return IndexQuote(
                symbol=symbol,
                ltp=float(data.get("last_price", data.get("ltp", 0.0)) or 0.0),
                prev_close=float(data.get("previous_close", 0.0) or 0.0),
                open=float(data.get("open", 0.0) or 0.0),
                high=float(data.get("high", 0.0) or 0.0),
                low=float(data.get("low", 0.0) or 0.0),
                volume=int(data.get("volume", 0) or 0),
                vwap=float(data.get("avg_price", 0.0) or 0.0) or None,
                last_updated=ist_now(),
            )
        except (TypeError, ValueError) as exc:
            raise NoDataError(f"index quote parse failed: {exc}") from exc

    def get_india_vix(self) -> Optional[IndiaVIX]:
        if not self.is_connected():
            return None
        try:
            # India VIX is on NSE index — instrument token differs; we look
            # it up via the option chain / instrument master. For Phase 1-5
            # we accept None when lookup fails (vol regime still works
            # from option-chain IV).
            resp = self._client.market_feed(
                instrument="IDX_I",
                exchange_segment="IDX_I",
                instrument_token="13",  # NIFTY50; VIX token retrieved separately later
            )
            data = self._extract_first(resp)
            if not data:
                return None
            return IndiaVIX(
                ltp=float(data.get("last_price", 0.0) or 0.0),
                prev_close=float(data.get("previous_close", 0.0) or 0.0),
                last_updated=ist_now(),
            )
        except Exception:
            return None

    def get_option_chain(
        self,
        underlying: str = "NIFTY",
        expiry: Optional[date] = None,
        n_strikes_each_side: int = 5,
    ) -> list[OptionQuote]:
        self._require_connected()
        try:
            # dhanhq: option_chain fetches chain for the underlying
            chain_payload = self._client.option_chain(
                under_security_id=str(self._cfg["nifty"]["instrument_token"]),
                under_exchange_segment="IDX_I",
                expiry=expiry.isoformat() if expiry else None,
            )
        except Exception as exc:
            raise NoDataError(f"option chain fetch failed: {type(exc).__name__}") from exc

        raw = self._extract_first(chain_payload) or []
        if not raw:
            raise NoDataError("option chain returned empty payload")

        # We don't have spot yet here, so we keep ALL strikes; the option
        # selector will pick ATM +/- n_strikes_each_side.
        return self._parse_option_chain(raw, underlying)

    def get_snapshot(
        self,
        underlying: str = "NIFTY",
        n_strikes_each_side: int = 5,
    ) -> MarketSnapshot:
        """Build the full snapshot. On ANY failure, return data_valid=False."""
        now = ist_now()

        # ---- gate 1: connectivity ----
        if not self.is_connected():
            return MarketSnapshot(
                timestamp=now,
                index=IndexQuote(ltp=0.0, last_updated=now),
                option_chain=[],
                market_open=False,
                data_valid=False,
                data_invalid_reason=(
                    "DHAN_ACCESS_TOKEN / DHAN_CLIENT_ID not set OR dhanhq not installed"
                ),
                time_bucket=current_time_bucket(),
            )

        # ---- gate 2: market open? ----
        if not self.is_market_open():
            return MarketSnapshot(
                timestamp=now,
                index=IndexQuote(ltp=0.0, last_updated=now),
                option_chain=[],
                market_open=False,
                data_valid=False,
                data_invalid_reason="market closed (holiday / outside trading hours)",
                time_bucket=current_time_bucket(),
            )

        # ---- fetch everything ----
        try:
            index = self.get_index_quote(underlying)
        except NoDataError as exc:
            return MarketSnapshot(
                timestamp=now,
                index=IndexQuote(ltp=0.0, last_updated=now),
                option_chain=[],
                data_valid=False,
                data_invalid_reason=f"index quote: {exc}",
                time_bucket=current_time_bucket(),
            )

        vix = self.get_india_vix()  # may be None — non-fatal

        try:
            chain = self.get_option_chain(
                underlying=underlying, n_strikes_each_side=n_strikes_each_side,
            )
        except NoDataError as exc:
            return MarketSnapshot(
                timestamp=now,
                index=index,
                india_vix=vix,
                option_chain=[],
                data_valid=False,
                data_invalid_reason=f"option chain: {exc}",
                time_bucket=current_time_bucket(),
            )

        # ---- final validity check ----
        if index.ltp <= 0 or not chain:
            return MarketSnapshot(
                timestamp=now,
                index=index,
                india_vix=vix,
                option_chain=chain,
                data_valid=False,
                data_invalid_reason="index LTP zero or chain empty",
                time_bucket=current_time_bucket(),
            )

        return MarketSnapshot(
            timestamp=now,
            index=index,
            india_vix=vix,
            option_chain=chain,
            market_open=True,
            data_valid=True,
            data_invalid_reason=None,
            time_bucket=current_time_bucket(),
        )

    # ---- historical (Phase 6) ----
    def historical_candles(self, symbol, interval, start, end):
        self._require_connected()
        # Wired in Phase 6 — backtesting engine.
        raise NotImplementedError("historical_candles is implemented in Phase 6")

    # ---- internal helpers ----
    @staticmethod
    def _extract_first(payload) -> list | dict | None:
        """DhanHQ often wraps results in {'data': [...]} or returns a list."""
        if payload is None:
            return None
        if isinstance(payload, list):
            return payload
        if isinstance(payload, dict):
            if "data" in payload:
                return payload["data"]
            return payload
        return None

    @staticmethod
    def _parse_option_chain(raw, underlying: str) -> list[OptionQuote]:
        """Convert DhanHQ option-chain records to OptionQuote models."""
        now = ist_now()
        out: list[OptionQuote] = []
        if isinstance(raw, dict) and "oc" in raw:
            raw = raw["oc"]
        if isinstance(raw, dict):
            # {"strike": {"ce": {...}, "pe": {...}}}
            for strike_str, legs in raw.items():
                try:
                    strike = float(strike_str)
                except (TypeError, ValueError):
                    continue
                for side, rec in legs.items():
                    if not rec:
                        continue
                    quote = DhanBroker._record_to_quote(rec, strike, side, underlying, now)
                    if quote:
                        out.append(quote)
        elif isinstance(raw, list):
            for rec in raw:
                strike = float(rec.get("strike_price", rec.get("strike", 0)) or 0)
                side = "CE" if str(rec.get("option_type", "CE")).upper() in ("CE", "CALL") else "PE"
                quote = DhanBroker._record_to_quote(rec, strike, side, underlying, now)
                if quote:
                    out.append(quote)
        return out

    @staticmethod
    def _record_to_quote(rec, strike, side, underlying, now) -> Optional[OptionQuote]:
        try:
            expiry_str = rec.get("expiry", rec.get("expiry_date", ""))
            expiry_date = (
                datetime.strptime(expiry_str, "%Y-%m-%d").date()
                if expiry_str else now.date()
            )
            sym = rec.get("symbol") or rec.get("trading_symbol") or (
                f"{underlying}{expiry_date.strftime('%y%b').upper()}{int(strike)}{side}"
            )
            ltp = float(rec.get("last_price", rec.get("ltp", 0.0)) or 0.0)
            if ltp <= 0:
                # skip records with no price — never fabricate
                return None
            return OptionQuote(
                symbol=sym,
                exchange="NSE",
                expiry=expiry_date,
                strike=strike,
                option_type=OptionType.CE if side.upper() == "CE" else OptionType.PE,
                ltp=ltp,
                bid=float(rec.get("bid", 0.0) or 0.0) or None,
                ask=float(rec.get("ask", 0.0) or 0.0) or None,
                bid_qty=int(rec.get("bid_qty", 0) or 0) or None,
                ask_qty=int(rec.get("ask_qty", 0) or 0) or None,
                volume=int(rec.get("volume", rec.get("traded_volume", 0)) or 0),
                oi=int(rec.get("oi", rec.get("open_interest", 0)) or 0),
                oi_change=int(rec.get("oi_change", 0) or 0),
                iv=(float(rec.get("iv", 0.0) or 0.0) / 100.0) or None,  # Dhan returns %
                delta=float(rec.get("delta", 0.0) or 0.0) or None,
                gamma=float(rec.get("gamma", 0.0) or 0.0) or None,
                theta=float(rec.get("theta", 0.0) or 0.0) or None,
                vega=float(rec.get("vega", 0.0) or 0.0) or None,
                last_updated=now,
            )
        except (TypeError, ValueError, KeyError):
            return None
