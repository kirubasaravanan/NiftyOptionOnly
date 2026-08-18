"""Instrument registry — DhanHQ identifiers per underlying.

Added 2026-08-18 to support BANKNIFTY/SENSEX alongside NIFTY. All three
verified live to work through the identical DhanHQ API shape (IDX_I
exchange segment, same option_chain()/expiry_list()/intraday_minute_data()
calls) — this is a config table, not per-instrument integration code.

Lot sizes for BANKNIFTY/SENSEX are placeholders pending verification against
Dhan's live funds/margin or instrument-master data before either goes live —
do not trust these numbers for real position sizing without checking first.
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
    lot_size=75,
)

BANKNIFTY = InstrumentConfig(
    name="BANKNIFTY",
    index_security_id="25",
    exchange_segment="IDX_I",
    instrument_type="INDEX",
    lot_size=30,  # PLACEHOLDER — verify against live Dhan data before trading
)

SENSEX = InstrumentConfig(
    name="SENSEX",
    index_security_id="51",
    exchange_segment="IDX_I",
    instrument_type="INDEX",
    lot_size=20,  # PLACEHOLDER — verify against live Dhan data before trading
)

ALL_INSTRUMENTS = {"NIFTY": NIFTY, "BANKNIFTY": BANKNIFTY, "SENSEX": SENSEX}
