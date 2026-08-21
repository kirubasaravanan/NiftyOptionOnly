"""Time utilities — IST handling, market open check, time-bucket resolution."""
from __future__ import annotations

from datetime import datetime, time
from zoneinfo import ZoneInfo

from ..config import load as load_config
from ..models import TimeBucket

_IST = ZoneInfo("Asia/Kolkata")


def ist_now() -> datetime:
    return datetime.now(_IST)


def to_ist(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        return dt.replace(tzinfo=_IST)
    return dt.astimezone(_IST)


def _parse_hhmm(s: str) -> time:
    parts = s.split(":")
    return time(hour=int(parts[0]), minute=int(parts[1]))


def is_market_open(now: datetime | None = None) -> bool:
    """True iff NSE is currently in regular trading session in IST."""
    cfg = load_config("trading_hours")["market"]
    now = to_ist(now) if now else ist_now()

    # holiday check
    holidays = cfg.get("holidays", [])
    today_str = now.date().isoformat()
    if today_str in holidays:
        return False

    # weekend
    if now.weekday() >= 5:  # 5=Sat, 6=Sun
        return False

    open_t = _parse_hhmm(cfg["morning_open"])
    close_t = _parse_hhmm(cfg["morning_close"])
    return open_t <= now.time() <= close_t


def is_eod_force_close(now: datetime | None = None) -> bool:
    """True once the configured end-of-day forced-flatten time has passed.

    Independent of is_trading_allowed_now()'s new-entry cutoff -- this
    governs whether EXISTING open positions get force-closed, not whether
    new ones can be opened.
    """
    now = to_ist(now) if now else ist_now()
    cfg = load_config("trading_hours")["market"]
    cutoff_str = cfg.get("eod_force_close")
    if not cutoff_str:
        return False
    return now.time() >= _parse_hhmm(cutoff_str)


def is_trading_allowed_now(now: datetime | None = None) -> tuple[bool, str]:
    """Returns (allowed, reason). Used to gate new entries."""
    now = to_ist(now) if now else ist_now()
    cfg = load_config("trading_hours")
    market_cfg = cfg["market"]

    today_str = now.date().isoformat()
    if today_str in market_cfg.get("holidays", []):
        return False, "holiday"
    if now.weekday() >= 5:
        return False, "weekend"

    bucket = current_time_bucket(now)
    if bucket is None:
        return False, "outside session"

    # find bucket spec
    for b in cfg["time_buckets"]:
        if b["name"] == bucket.value:
            if not b.get("trade_allowed", True):
                return False, f"bucket {bucket.value} disallows trades"
            if "no_new_entries_after" in b:
                cutoff = _parse_hhmm(b["no_new_entries_after"])
                if now.time() > cutoff:
                    return False, "no new entries near close"
            return True, "ok"
    return False, "no matching bucket"


def current_time_bucket(now: datetime | None = None) -> TimeBucket | None:
    now = to_ist(now) if now else ist_now()
    cfg = load_config("trading_hours")
    for b in cfg["time_buckets"]:
        start = _parse_hhmm(b["start"])
        end = _parse_hhmm(b["end"])
        if start <= now.time() <= end:
            return TimeBucket(b["name"])
    return None


def bucket_threshold_multiplier(now: datetime | None = None) -> float:
    """Multiplier to apply to strategy thresholds in choppy buckets (e.g. midday)."""
    now = to_ist(now) if now else ist_now()
    bucket = current_time_bucket(now)
    if bucket is None:
        return 1.0
    cfg = load_config("trading_hours")
    for b in cfg["time_buckets"]:
        if b["name"] == bucket.value:
            return float(b.get("threshold_multiplier", 1.0))
    return 1.0
