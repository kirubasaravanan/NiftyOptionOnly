"""DhanHQ broker adapter (dhanhq 2.x API).

Token + client ID from env vars. Never hard-coded.
Today is a market holiday — option chain / live quotes are unavailable.
Historical candle API still works (used by backtester).

DESIGN RULES:
  * If token missing -> raise NoDataError
  * If broker returns failure/empty -> raise NoDataError
  * NEVER fabricate data. Engine treats NoDataError as data_valid=False
    and emits NO-TRADE.
"""
from __future__ import annotations

import os
from datetime import date, datetime, timedelta
from typing import Optional

from ..models import (
    IndiaVIX, IndexQuote, MarketSnapshot, OptionQuote, OptionType, TimeBucket,
)
from ..config import load as load_config
from ..utils.time_utils import ist_now, is_market_open, current_time_bucket
from .broker_interface import BrokerError, BrokerInterface, NoDataError
from ..features.technical import attach_features_to_index


# Lazy import — engine should run even if dhanhq is not installed
try:
    from dhanhq import DhanContext, HistoricalData, OptionChain, MarketFeed
    _DHAN_AVAILABLE = True
except ImportError:
    _DHAN_AVAILABLE = False


# NIFTY instrument identifiers on Dhan
NIFTY_INDEX_SECURITY_ID = "13"
NIFTY_INDEX_EXCHANGE = "IDX_I"
NIFTY_INDEX_INSTRUMENT_TYPE = "INDEX"

# Bank Nifty — used as confirmation factor (security_id 25)
BANKNIFTY_INDEX_SECURITY_ID = "25"
# India VIX — used for IV valuation (security_id 21)
INDIA_VIX_SECURITY_ID = "21"
# NIFTY current-month futures (Aug 2026 contract — security_id 58072)
# NOTE: this must be updated monthly when futures roll over
NIFTY_FUT_SECURITY_ID = "58072"
NIFTY_FUT_EXCHANGE = "NSE_FNO"
NIFTY_FUT_INSTRUMENT_TYPE = "FUTIDX"


class DhanBroker(BrokerInterface):
    """DhanHQ implementation of BrokerInterface (dhanhq 2.x API)."""

    def __init__(self) -> None:
        self._token = os.environ.get("DHAN_ACCESS_TOKEN", "").strip()
        self._client_id = os.environ.get("DHAN_CLIENT_ID", "").strip()
        self._cfg = load_config("broker")["dhan"]
        self._context = None
        self._hd = None                  # HistoricalData
        self._oc = None                  # OptionChain
        self._mf = None                  # MarketFeed (lazy)
        self._connected = False
        self._last_chain_spot: Optional[float] = None  # live spot from last option-chain fetch
        self._tech_features_cache: Optional[dict] = None
        self._tech_features_cache_ts: float = 0.0
        self.TECH_FEATURES_TTL_SECONDS = 120.0

        if not self._token or not self._client_id:
            return
        if not _DHAN_AVAILABLE:
            self._token = ""
            return

        try:
            self._context = DhanContext(self._client_id, self._token)
            self._hd = HistoricalData(self._context)
            self._oc = OptionChain(self._context)
            self._connected = True
        except Exception as exc:
            raise BrokerError(f"Dhan init failed: {type(exc).__name__}") from exc

    # ---- connectivity ----
    def is_connected(self) -> bool:
        return bool(self._connected and self._context is not None)

    def is_market_open(self) -> bool:
        return is_market_open()

    def _require(self) -> None:
        if not self.is_connected():
            raise NoDataError(
                "Dhan not connected (token/client_id missing or dhanhq not installed)"
            )

    # ---- market data ----
    def get_index_quote(self, symbol: str = "NIFTY") -> IndexQuote:
        """Get live index quote. On holiday/no data, raises NoDataError."""
        self._require()
        # MarketFeed uses websocket — heavier. On holiday it returns nothing.
        # Fall back to fetching today's daily candle (which works on holidays too
        # — it returns the most recent trading day's close).
        try:
            today = ist_now().date()
            resp = self._hd.historical_daily_data(
                NIFTY_INDEX_SECURITY_ID,
                NIFTY_INDEX_EXCHANGE,
                NIFTY_INDEX_INSTRUMENT_TYPE,
                today.isoformat(),
                today.isoformat(),
            )
        except Exception as exc:
            raise NoDataError(f"index quote fetch failed: {type(exc).__name__}") from exc

        data = resp.get("data") if isinstance(resp, dict) else None
        if not data or not data.get("close"):
            # Try recent date range instead
            return self._fallback_recent_index_quote()

        closes = data["close"]
        opens = data["open"]
        highs = data["high"]
        lows = data["low"]
        vols = data["volume"]
        idx = -1
        ltp = float(closes[idx])
        if ltp <= 0:
            return self._fallback_recent_index_quote()

        return IndexQuote(
            symbol=symbol,
            ltp=ltp,
            prev_close=float(closes[-2]) if len(closes) > 1 else None,
            open=float(opens[idx]),
            high=float(highs[idx]),
            low=float(lows[idx]),
            volume=int(vols[idx]),
            last_updated=ist_now(),
        )

    def _fallback_recent_index_quote(self) -> IndexQuote:
        """Fetch the most recent N days of daily candles and use the last one."""
        from datetime import timedelta
        end = ist_now().date()
        start = end - timedelta(days=14)
        try:
            resp = self._hd.historical_daily_data(
                NIFTY_INDEX_SECURITY_ID,
                NIFTY_INDEX_EXCHANGE,
                NIFTY_INDEX_INSTRUMENT_TYPE,
                start.isoformat(),
                end.isoformat(),
            )
        except Exception as exc:
            raise NoDataError(f"recent quote fetch failed: {type(exc).__name__}") from exc
        data = resp.get("data") if isinstance(resp, dict) else None
        if not data or not data.get("close"):
            raise NoDataError("recent daily candles returned empty")
        closes = data["close"]
        opens = data["open"]
        highs = data["high"]
        lows = data["low"]
        vols = data["volume"]
        idx = -1
        if not closes:
            raise NoDataError("daily candles close list empty")
        return IndexQuote(
            symbol="NIFTY",
            ltp=float(closes[idx]),
            prev_close=float(closes[-2]) if len(closes) > 1 else None,
            open=float(opens[idx]),
            high=float(highs[idx]),
            low=float(lows[idx]),
            volume=int(vols[idx]),
            last_updated=ist_now(),
        )

    def get_india_vix(self) -> Optional[IndiaVIX]:
        """India VIX — fetched from DhanHQ historical API (security_id '15').
        Works even on holidays because it returns the most recent trading day."""
        if not self.is_connected():
            return None
        try:
            from datetime import timedelta
            end = ist_now().date()
            start = end - timedelta(days=14)
            resp = self._hd.historical_daily_data(
                INDIA_VIX_SECURITY_ID, "IDX_I", "INDEX",
                start.isoformat(), end.isoformat(),
            )
            data = resp.get("data") if isinstance(resp, dict) else None
            if not data or not data.get("close"):
                return None
            closes = data["close"]
            if not closes:
                return None
            ltp = float(closes[-1])
            prev_close = float(closes[-2]) if len(closes) > 1 else None
            # VIX percentile: rank of current vs last 60 closes
            vix_pct = None
            if len(closes) >= 10:
                sorted_v = sorted(closes)
                rank = sum(1 for x in sorted_v if x <= ltp)
                vix_pct = 100.0 * rank / len(sorted_v)
            return IndiaVIX(
                ltp=ltp,
                prev_close=prev_close,
                iv_percentile=vix_pct,
                last_updated=ist_now(),
            )
        except Exception:
            return None

    def get_banknifty_quote(self) -> Optional[dict]:
        """Fetch Bank Nifty recent daily quote. Returns dict with ltp + prev_close.
        Works on holidays (returns last trading day's close)."""
        if not self.is_connected():
            return None
        try:
            from datetime import timedelta
            end = ist_now().date()
            start = end - timedelta(days=14)
            resp = self._hd.historical_daily_data(
                BANKNIFTY_INDEX_SECURITY_ID, "IDX_I", "INDEX",
                start.isoformat(), end.isoformat(),
            )
            data = resp.get("data") if isinstance(resp, dict) else None
            if not data or not data.get("close"):
                return None
            closes = data["close"]
            if not closes:
                return None
            return {
                "ltp": float(closes[-1]),
                "prev_close": float(closes[-2]) if len(closes) > 1 else None,
                "open": float(data["open"][-1]),
                "high": float(data["high"][-1]),
                "low": float(data["low"][-1]),
            }
        except Exception:
            return None

    def get_nifty_futures_quote(self) -> Optional[dict]:
        """Fetch NIFTY futures quote (current month contract). Returns dict with ltp + prev_close.
        Works on holidays (returns last trading day's close)."""
        if not self.is_connected():
            return None
        try:
            from datetime import timedelta
            end = ist_now().date()
            start = end - timedelta(days=14)
            resp = self._hd.historical_daily_data(
                NIFTY_FUT_SECURITY_ID, NIFTY_FUT_EXCHANGE, NIFTY_FUT_INSTRUMENT_TYPE,
                start.isoformat(), end.isoformat(),
            )
            data = resp.get("data") if isinstance(resp, dict) else None
            if not data or not data.get("close"):
                return None
            closes = data["close"]
            if not closes:
                return None
            return {
                "ltp": float(closes[-1]),
                "prev_close": float(closes[-2]) if len(closes) > 1 else None,
                "open": float(data["open"][-1]),
                "high": float(data["high"][-1]),
                "low": float(data["low"][-1]),
            }
        except Exception:
            return None

    def get_option_chain(
        self,
        underlying: str = "NIFTY",
        expiry: Optional[date] = None,
        n_strikes_each_side: int = 5,
    ) -> list[OptionQuote]:
        """Fetch option chain. On holiday, DhanHQ returns empty -> NoDataError."""
        self._require()
        # Get list of expiries first
        try:
            exp_resp = self._oc.expiry_list(int(NIFTY_INDEX_SECURITY_ID), NIFTY_INDEX_EXCHANGE)
        except Exception as exc:
            raise NoDataError(f"expiry_list fetch failed: {type(exc).__name__}") from exc
        if exp_resp.get("status") != "success":
            raise NoDataError(f"expiry_list failed: {exp_resp.get('remarks')}")

        exp_data = exp_resp.get("data", "")
        # The SDK's response wrapper double-nests: exp_resp["data"] is itself
        # {"data": [...], "status": "success"} from DhanHQ's raw body, not the
        # flat expiry list directly. Unwrap one more level when present.
        if isinstance(exp_data, dict) and "data" in exp_data:
            exp_data = exp_data["data"]
        if not exp_data:
            raise NoDataError("no expiries returned (market holiday?)")

        # Parse expiry list — Dhan returns list of {expiry: "...", expiryCode: ...}
        expiries = self._parse_expiries(exp_data)
        if not expiries:
            raise NoDataError("could not parse expiry list")

        # Pick requested expiry or nearest
        target_expiry = expiry or expiries[0]["date"]
        target_code = None
        for e in expiries:
            if e["date"] == target_expiry:
                target_code = e["code"]
                break
        if target_code is None:
            target_code = expiries[0]["code"]
            target_expiry = expiries[0]["date"]

        # Fetch option chain for that expiry
        try:
            oc_resp = self._oc.option_chain(
                int(NIFTY_INDEX_SECURITY_ID), NIFTY_INDEX_EXCHANGE, target_code,
            )
        except Exception as exc:
            raise NoDataError(f"option_chain fetch failed: {type(exc).__name__}") from exc
        if oc_resp.get("status") != "success":
            raise NoDataError(f"option_chain failed: {oc_resp.get('remarks')}")

        raw = oc_resp.get("data", "")
        # Same double-nesting as expiry_list — unwrap the inner "data" key.
        if isinstance(raw, dict) and "data" in raw and "oc" not in raw:
            raw = raw["data"]
        if not raw:
            raise NoDataError("option chain returned empty data")

        # The option chain payload carries the underlying's genuinely live
        # price (raw["last_price"]) — capture it so get_snapshot() can use it
        # in place of the index quote's stale daily-candle value (see
        # get_index_quote: it reads today's historical daily candle "close",
        # which does not track intraday movement). No extra API call needed —
        # this chain fetch already happens every cycle.
        if isinstance(raw, dict):
            live_spot = raw.get("last_price")
            if live_spot:
                try:
                    self._last_chain_spot = float(live_spot)
                except (TypeError, ValueError):
                    pass

        return self._parse_option_chain(raw, underlying, target_expiry)

    def _apply_cached_technicals(self, index: IndexQuote, underlying: str, now: datetime) -> None:
        """Attach ADX/RSI/EMA/ATR/VWAP to `index`, using a cached feature set
        when fresh enough. Never raises — a failed/rate-limited fetch just
        means the fields stay whatever the cache (or None) already has."""
        import time
        age = time.monotonic() - self._tech_features_cache_ts
        if self._tech_features_cache is None or age > self.TECH_FEATURES_TTL_SECONDS:
            # This lands after several other DhanHQ calls already made this
            # cycle (index, VIX, Bank Nifty, futures, option chain) and can
            # trip a short burst-rate limit even though the same call succeeds
            # in isolation. It only needs to succeed once per TTL window, so
            # one short-delay retry is cheap insurance against that.
            for attempt in range(2):
                try:
                    bar_start = now - timedelta(days=5)
                    df = self.historical_candles(underlying, "5min", bar_start, now)
                    attach_features_to_index(index, df)
                    self._tech_features_cache = {
                        "atr": index.atr, "adx": index.adx, "adx_slope": index.adx_slope,
                        "rsi": index.rsi, "ema_fast": index.ema_fast,
                        "ema_mid": index.ema_mid, "ema_slow": index.ema_slow,
                        "vwap": index.vwap,
                    }
                    self._tech_features_cache_ts = time.monotonic()
                    return
                except Exception:
                    if attempt == 0:
                        time.sleep(1.5)
                    # else fall through to serving the stale cache, if any
        if self._tech_features_cache:
            for field, value in self._tech_features_cache.items():
                setattr(index, field, value)

    def get_snapshot(
        self,
        underlying: str = "NIFTY",
        n_strikes_each_side: int = 5,
    ) -> MarketSnapshot:
        """Build full snapshot. On ANY failure -> data_valid=False."""
        now = ist_now()

        if not self.is_connected():
            return MarketSnapshot(
                timestamp=now,
                index=IndexQuote(ltp=0.0, last_updated=now),
                option_chain=[],
                market_open=False,
                data_valid=False,
                data_invalid_reason="DHAN_ACCESS_TOKEN / DHAN_CLIENT_ID not set or dhanhq not installed",
                time_bucket=current_time_bucket(),
            )

        if not self.is_market_open():
            # On holiday we can still fetch the most recent daily candle
            # for index but option chain is unavailable. We DO fetch VIX,
            # Bank Nifty and futures so the dashboard has something to display.
            try:
                index = self._fallback_recent_index_quote()
                vix = self.get_india_vix()
                bn = self.get_banknifty_quote()
                nf = self.get_nifty_futures_quote()
                # Stash the auxiliary data on the snapshot so callers can read it
                # (the snapshot model doesn't have a dedicated field for these,
                # but we attach them via _aux for the API serializer)
                snap = MarketSnapshot(
                    timestamp=now,
                    index=index,
                    india_vix=vix,
                    option_chain=[],
                    market_open=False,
                    data_valid=False,
                    data_invalid_reason="market closed (holiday / outside trading hours)",
                    time_bucket=current_time_bucket(),
                )
                # Attach auxiliary market data via private attributes
                snap._banknifty_quote = bn
                snap._nifty_futures_quote = nf
                return snap
            except NoDataError as exc:
                return MarketSnapshot(
                    timestamp=now,
                    index=IndexQuote(ltp=0.0, last_updated=now),
                    option_chain=[],
                    data_valid=False,
                    data_invalid_reason=str(exc),
                    time_bucket=current_time_bucket(),
                )

        # Market open — fetch everything
        try:
            index = self.get_index_quote(underlying)
        except NoDataError as exc:
            return MarketSnapshot(
                timestamp=now,
                index=IndexQuote(ltp=0.0, last_updated=now),
                option_chain=[],
                data_valid=False,
                data_invalid_reason=f"index: {exc}",
                time_bucket=current_time_bucket(),
            )

        vix = self.get_india_vix()
        bn = self.get_banknifty_quote()
        nf = self.get_nifty_futures_quote()

        try:
            chain = self.get_option_chain(
                underlying=underlying, n_strikes_each_side=n_strikes_each_side,
            )
            # FIX: get_index_quote() reads today's historical daily candle
            # "close", which does not update through the trading day — it was
            # measured staying frozen at the market-open value for hours.
            # The option chain response's own last_price is genuinely live
            # (DhanHQ refreshes it every request), so use it as the real spot
            # whenever we have it, rather than the stale daily-candle value.
            if self._last_chain_spot and self._last_chain_spot > 0:
                index.ltp = self._last_chain_spot
                index.last_updated = now

            # FIX: ADX/RSI/EMA/ATR/VWAP were only ever computed in the backtest
            # engine — the live snapshot path never called TechnicalCalculator
            # at all, so these fields stayed None on every live cycle forever.
            # The regime engine then read the missing ADX as a literal 0.0 and
            # EMA/VWAP as literally equal to spot, which structurally prevents
            # it from ever classifying a genuine trending (BULL/BEAR) regime —
            # only the OI-wall-based BREAKOUT check (a separate signal) could
            # ever fire.
            #
            # Cached with its own TTL rather than fetched every cycle: 5-minute
            # bar features don't change meaningfully faster than that anyway,
            # and fetching a 5-day intraday window on every single decision
            # cycle — on top of the option chain + VIX + Bank Nifty + futures
            # calls already made above — was tripping DhanHQ's rate limiting
            # (confirmed live: identical call succeeds alone, fails when
            # bursted right after those other calls).
            self._apply_cached_technicals(index, underlying, now)
        except NoDataError as exc:
            snap = MarketSnapshot(
                timestamp=now,
                index=index,
                india_vix=vix,
                option_chain=[],
                data_valid=False,
                data_invalid_reason=f"option chain: {exc}",
                time_bucket=current_time_bucket(),
            )
            snap._banknifty_quote = bn
            snap._nifty_futures_quote = nf
            return snap

        # ---- final validity check ----
        if index.ltp <= 0 or not chain:
            snap = MarketSnapshot(
                timestamp=now,
                index=index,
                india_vix=vix,
                option_chain=chain,
                data_valid=False,
                data_invalid_reason="index LTP zero or chain empty",
                time_bucket=current_time_bucket(),
            )
            snap._banknifty_quote = bn
            snap._nifty_futures_quote = nf
            return snap

        snap = MarketSnapshot(
            timestamp=now,
            index=index,
            india_vix=vix,
            option_chain=chain,
            market_open=True,
            data_valid=True,
            data_invalid_reason=None,
            time_bucket=current_time_bucket(),
        )
        snap._banknifty_quote = bn
        snap._nifty_futures_quote = nf
        return snap

    # ---- historical (Phase 6) ----
    def historical_candles(
        self,
        symbol: str,
        interval: str,        # "1min", "5min", "15min", "day"
        start: datetime,
        end: datetime,
    ):
        """Return historical OHLCV as pandas DataFrame.

        For NIFTY index daily candles: symbol="NIFTY", interval="day".
        For intraday: interval="1min" | "5min" | "15min".
        """
        import pandas as pd
        self._require()

        # Map interval to dhan intraday minute value
        interval_map = {"1min": 1, "5min": 5, "15min": 15, "30min": 30, "60min": 60}
        if interval == "day":
            try:
                resp = self._hd.historical_daily_data(
                    NIFTY_INDEX_SECURITY_ID,
                    NIFTY_INDEX_EXCHANGE,
                    NIFTY_INDEX_INSTRUMENT_TYPE,
                    start.date().isoformat(),
                    end.date().isoformat(),
                )
            except Exception as exc:
                raise NoDataError(f"historical daily fetch failed: {type(exc).__name__}") from exc
            data = resp.get("data") if isinstance(resp, dict) else None
            if not data or not data.get("close"):
                raise NoDataError("historical daily returned empty")
            # Build dates list — Dhan returns OHLCV lists but no explicit timestamps;
            # infer from start..end trading days.
            n = len(data["close"])
            dates = pd.bdate_range(start=start.date(), end=end.date())[:n]
            df = pd.DataFrame({
                "open": data["open"],
                "high": data["high"],
                "low": data["low"],
                "close": data["close"],
                "volume": data.get("volume", [0] * n),
            }, index=dates[:n])
            return df

        if interval not in interval_map:
            raise ValueError(f"unsupported interval: {interval}")

        try:
            resp = self._hd.intraday_minute_data(
                NIFTY_INDEX_SECURITY_ID,
                NIFTY_INDEX_EXCHANGE,
                NIFTY_INDEX_INSTRUMENT_TYPE,
                start.date().isoformat(),
                end.date().isoformat(),
                interval=interval_map[interval],
            )
        except Exception as exc:
            raise NoDataError(f"intraday fetch failed: {type(exc).__name__}") from exc
        data = resp.get("data") if isinstance(resp, dict) else None
        if not data or not data.get("close"):
            raise NoDataError("intraday returned empty")
        n = len(data["close"])
        # Intraday timestamps are typically in start.date() trading day
        # We approximate with even spacing across trading hours
        timestamps = pd.date_range(
            start=start, periods=n, freq=f"{interval_map[interval]}min"
        )
        df = pd.DataFrame({
            "open": data["open"],
            "high": data["high"],
            "low": data["low"],
            "close": data["close"],
            "volume": data.get("volume", [0] * n),
        }, index=timestamps)
        return df

    # ---- option chain parsing ----
    @staticmethod
    def _parse_expiries(raw) -> list[dict]:
        """Dhan returns either a list of dicts or JSON string."""
        if isinstance(raw, str):
            import json
            try:
                raw = json.loads(raw)
            except Exception:
                return []
        if not isinstance(raw, list):
            return []
        out = []
        for item in raw:
            try:
                if isinstance(item, dict):
                    d = item.get("expiry") or item.get("date")
                    c = item.get("expiryCode") or item.get("code")
                    if d and c is not None:
                        if isinstance(d, str):
                            d = datetime.fromisoformat(d.replace("Z", "+00:00")).date() \
                                if "T" in d else datetime.strptime(d, "%Y-%m-%d").date()
                        out.append({"date": d, "code": c})
                elif isinstance(item, str):
                    # Current DhanHQ v2 API returns a flat list of expiry date
                    # strings (no separate "code") — the string itself is what
                    # /optionchain expects back as the Expiry parameter.
                    d = datetime.strptime(item, "%Y-%m-%d").date()
                    out.append({"date": d, "code": item})
            except Exception:
                continue
        # Sort by date ascending
        out.sort(key=lambda x: x["date"])
        return out

    @staticmethod
    def _parse_option_chain(raw, underlying: str, expiry: date) -> list[OptionQuote]:
        """Convert DhanHQ option-chain payload to OptionQuote list."""
        import json
        if isinstance(raw, str):
            try:
                raw = json.loads(raw)
            except Exception:
                return []
        now = ist_now()
        out: list[OptionQuote] = []
        # Dhan returns either list of records or dict {strike: {ce, pe}}
        if isinstance(raw, list):
            for rec in raw:
                q = DhanBroker._record_to_quote(rec, underlying, expiry, now)
                if q:
                    out.append(q)
        elif isinstance(raw, dict):
            oc = raw.get("oc", raw)
            if isinstance(oc, dict):
                for strike_str, legs in oc.items():
                    try:
                        strike = float(strike_str)
                    except (TypeError, ValueError):
                        continue
                    if not isinstance(legs, dict):
                        continue
                    for side in ("ce", "pe"):
                        rec = legs.get(side)
                        if not rec:
                            continue
                        q = DhanBroker._record_to_quote(rec, underlying, expiry, now, strike=strike, side=side)
                        if q:
                            out.append(q)
        return out

    @staticmethod
    def _record_to_quote(rec, underlying, expiry, now, strike=None, side=None) -> Optional[OptionQuote]:
        try:
            if strike is None:
                strike = float(rec.get("strike_price", rec.get("strike", 0)) or 0)
            if side is None:
                side = rec.get("option_type", "CE")
            ot = OptionType.CE if str(side).upper() in ("CE", "CALL") else OptionType.PE
            # DhanHQ's live option-chain response has no "symbol"/"trading_symbol"
            # field — only a numeric security_id, which isn't a valid string for
            # OptionQuote.symbol (Pydantic ValidationError, silently swallowed
            # below as a ValueError subclass) and isn't human-readable anyway.
            # Always build a readable synthetic symbol instead.
            sym = (rec.get("symbol") or rec.get("trading_symbol")
                   or f"{underlying}{expiry.strftime('%y%b').upper()}{int(strike)}{side.upper()}")
            # The actual DhanHQ instrument ID — required to place/track/reconcile
            # a real order against this specific contract (Phase 9). None for
            # synthetic/backtest quotes that don't have one.
            sec_id_raw = rec.get("security_id")
            security_id = int(sec_id_raw) if sec_id_raw is not None else None
            ltp = float(rec.get("last_price", rec.get("ltp", 0)) or 0)
            if ltp <= 0:
                return None
            iv_raw = float(rec.get("implied_volatility", rec.get("iv", 0)) or 0)
            # DhanHQ's live response nests Greeks under "greeks" and uses
            # top_bid_price/top_ask_price rather than flat bid/ask — fall back
            # to the flat names too, for compatibility with other chain shapes
            # (e.g. the backtest engine's Black-Scholes-synthesised chain).
            greeks = rec.get("greeks") or {}
            return OptionQuote(
                symbol=sym,
                security_id=security_id,
                exchange="NSE",
                expiry=expiry,
                strike=strike,
                option_type=ot,
                ltp=ltp,
                bid=float(rec.get("top_bid_price", rec.get("bid", 0)) or 0) or None,
                ask=float(rec.get("top_ask_price", rec.get("ask", 0)) or 0) or None,
                volume=int(rec.get("volume", rec.get("traded_volume", 0)) or 0),
                oi=int(rec.get("oi", rec.get("open_interest", 0)) or 0),
                oi_change=int(rec.get("oi_change", 0) or 0),
                iv=(iv_raw / 100.0) if iv_raw > 0 else None,
                delta=float(greeks.get("delta", rec.get("delta", 0)) or 0) or None,
                gamma=float(greeks.get("gamma", rec.get("gamma", 0)) or 0) or None,
                theta=float(greeks.get("theta", rec.get("theta", 0)) or 0) or None,
                vega=float(greeks.get("vega", rec.get("vega", 0)) or 0) or None,
                last_updated=now,
            )
        except (TypeError, ValueError, KeyError):
            return None
