"""Instrument registry — DhanHQ identifiers per underlying.

Added 2026-08-18 to support BANKNIFTY/SENSEX alongside NIFTY. All three
verified live to work through the identical DhanHQ API shape (IDX_I
exchange segment, same option_chain()/expiry_list()/intraday_minute_data()
calls) — this is a config table, not per-instrument integration code.

Lot sizes verified same day against DhanHQ's live instrument master
(Security.fetch_security_list(), SEM_LOT_UNITS for current OPTIDX
contracts) — NIFTY's original 75 here was stale (real current lot size is
65, likely an NSE revision broker.yaml hadn't caught up to); BANKNIFTY/
SENSEX's placeholder guesses (30/20) turned out correct. NSE/BSE revise
lot sizes periodically — re-verify against the instrument master if
strategy economics ever look off, don't assume these stay correct forever.
Note: these `lot_size` fields are for reference only — the actual runtime
source of truth is still get_lot_size() in utils/config_helpers.py, reading
broker_*.yaml.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class InstrumentConfig:
    name: str
    index_security_id: str
    exchange_segment: str
    instrument_type: str
    lot_size: int


NIFTY = InstrumentConfig(
    name="NIFTY",
    index_security_id="13",
    exchange_segment="IDX_I",
    instrument_type="INDEX",
    lot_size=65,  # corrected 2026-08-18 — was 75, stale
)

BANKNIFTY = InstrumentConfig(
    name="BANKNIFTY",
    index_security_id="25",
    exchange_segment="IDX_I",
    instrument_type="INDEX",
    lot_size=30,  # verified 2026-08-18 against live Dhan instrument master
)

SENSEX = InstrumentConfig(
    name="SENSEX",
    index_security_id="51",
    exchange_segment="IDX_I",
    instrument_type="INDEX",
    lot_size=20,  # verified 2026-08-18 against live Dhan instrument master
)

ALL_INSTRUMENTS = {"NIFTY": NIFTY, "BANKNIFTY": BANKNIFTY, "SENSEX": SENSEX}
