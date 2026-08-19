"""Transaction cost engine — applied identically in backtest, paper, and live.

The strategy selector's expected_net_value calculation MUST use this model.
A strategy is profitable ONLY if Expected Gross - ThisModel > 0.
"""
from __future__ import annotations

from dataclasses import dataclass

from ..config import load as load_config


@dataclass
class TradeCostBreakdown:
    """Per-leg cost breakdown — journal this for every fill."""
    brokerage: float
    stt: float
    exchange_charges: float
    gst: float
    sebi: float
    stamp_duty: float
    slippage: float
    bid_ask_spread_impact: float
    total: float

    def as_dict(self) -> dict:
        return {
            "brokerage": round(self.brokerage, 4),
            "stt": round(self.stt, 4),
            "exchange_charges": round(self.exchange_charges, 4),
            "gst": round(self.gst, 4),
            "sebi": round(self.sebi, 4),
            "stamp_duty": round(self.stamp_duty, 4),
            "slippage": round(self.slippage, 4),
            "bid_ask_spread_impact": round(self.bid_ask_spread_impact, 4),
            "total": round(self.total, 4),
        }


class CostModel:
    """NIFTY options cost model — single source of truth."""

    def __init__(self) -> None:
        self._cfg = load_config("costs")

    # ---- public API ----
    def cost_for_leg(
        self,
        premium_per_unit: float,
        quantity: int,
        side: str,                       # "BUY" | "SELL"
        apply_slippage: bool = True,
        apply_spread: bool = True,
    ) -> TradeCostBreakdown:
        """Compute all-in cost for a single option leg.

        quantity = number of shares (NOT lots). For NIFTY 1 lot = 75 shares.
        premium_per_unit = price of one share.
        """
        notional = premium_per_unit * quantity
        cfg = self._cfg

        brokerage = min(
            cfg["brokerage"]["per_order"] * 1,    # 1 order per leg
            cfg["brokerage"]["cap_per_order"],
        )

        # STT — buy side: 0.0625% of premium; sell side: 0.125% of strike (exercised)
        # For normal square-off sell (most of our trades) the 0.0625% premium rate applies.
        if side.upper() == "BUY":
            stt = notional * cfg["stt"]["options_buy_pct_of_premium"]
        else:
            stt = notional * cfg["stt"]["options_sell_pct_of_premium"]

        exchange_charges = notional * cfg["exchange_transaction_charges"]["options_pct_of_premium"]

        sebi_turnover = max(notional, 0.0)
        sebi = sebi_turnover * (cfg["sebi_charges"]["per_crore"] / 1e7)

        # GST on (brokerage + exchange + sebi)
        gst = (brokerage + exchange_charges + sebi) * cfg["gst"]["rate"]

        # Stamp duty — buyer side only
        stamp = (notional * cfg["stamp_duty"]["options_buy_pct_of_premium"]) if side.upper() == "BUY" else 0.0

        # Slippage / bid-ask — percentage of premium, not fixed points.
        # Calibrated 2026-08-19 against real measured spreads; see costs.yaml
        # for the measurement and why percentage is the right dimension
        # (spreads vary 3.6x in points across instruments but are ~constant
        # as a % of premium). Falls back to the legacy fixed-points keys if
        # an older costs.yaml is in use, so this can't silently zero out.
        slip = 0.0
        if apply_slippage:
            slip = self._per_unit_cost(
                premium_per_unit, "slippage_pct_of_premium", "per_leg_points",
            ) * quantity
        spread = 0.0
        if apply_spread:
            spread = self._per_unit_cost(
                premium_per_unit, "bid_ask_pct_of_premium", "bid_ask_spread_points",
            ) * quantity

        total = brokerage + stt + exchange_charges + gst + sebi + stamp + slip + spread
        return TradeCostBreakdown(
            brokerage=brokerage,
            stt=stt,
            exchange_charges=exchange_charges,
            gst=gst,
            sebi=sebi,
            stamp_duty=stamp,
            slippage=slip,
            bid_ask_spread_impact=spread,
            total=total,
        )

    def _per_unit_cost(self, premium_per_unit: float, pct_key: str, legacy_points_key: str) -> float:
        """Per-unit slippage/spread cost, percentage-based with a tick floor.

        Reads the percentage key; if absent (older costs.yaml), falls back to
        the legacy fixed-points key so an out-of-date config degrades to the
        old behaviour rather than silently costing nothing.
        """
        slip_cfg = self._cfg["slippage"]
        pct = slip_cfg.get(pct_key)
        if pct is None:
            return float(slip_cfg.get(legacy_points_key, 0.0))
        floor = float(slip_cfg.get("min_points_per_leg", 0.0))
        return max(premium_per_unit * float(pct), floor)

    def cost_for_round_trip(
        self,
        entry_premium_per_unit: float,
        exit_premium_per_unit: float,
        quantity: int,
        lot_size: int = 75,
    ) -> TradeCostBreakdown:
        """Sum of buy + sell leg costs for a complete trade.

        Round-trip is what matters for expectancy — every trade is a buy
        followed by a sell (or short followed by buy, but our v1 strategies
        are long-only).
        """
        buy = self.cost_for_leg(entry_premium_per_unit, quantity, "BUY")
        sell = self.cost_for_leg(exit_premium_per_unit, quantity, "SELL")
        return TradeCostBreakdown(
            brokerage=buy.brokerage + sell.brokerage,
            stt=buy.stt + sell.stt,
            exchange_charges=buy.exchange_charges + sell.exchange_charges,
            gst=buy.gst + sell.gst,
            sebi=buy.sebi + sell.sebi,
            stamp_duty=buy.stamp_duty + sell.stamp_duty,
            slippage=buy.slippage + sell.slippage,
            bid_ask_spread_impact=buy.bid_ask_spread_impact + sell.bid_ask_spread_impact,
            total=buy.total + sell.total,
        )

    # ---- expected cost estimate for strategy evaluation ----
    def estimate_round_trip_cost(
        self,
        entry_premium_per_unit: float,
        quantity: int,
    ) -> float:
        """Quick estimate assuming exit premium ≈ entry premium.

        Used by the strategy selector for EXPECTED_NET_VALUE calculation.
        The real exit cost is computed at fill time using actual exit price.
        """
        return self.cost_for_round_trip(
            entry_premium_per_unit, entry_premium_per_unit, quantity,
        ).total

    # ---- Phase 10: multi-leg (spread) cost helpers ----
    def cost_for_spread_round_trip(
        self,
        long_entry: float,
        long_exit: float,
        short_entry: float,
        short_exit: float,
        quantity: int,
    ) -> TradeCostBreakdown:
        """Round-trip cost for a 2-leg spread.

        A spread = Buy long leg + Sell short leg (entry) +
                    Sell long leg + Buy short leg (exit).
        So 4 individual legs = 2 round-trips of single-leg cost.

        For NIFTY options this matters because:
          - Brokerage doubles (₹20 × 4 = ₹80 vs ₹40 for single-leg round-trip)
          - STT applies on each sell side
          - Slippage applies to each fill
        """
        long_rt = self.cost_for_round_trip(long_entry, long_exit, quantity)
        short_rt = self.cost_for_round_trip(short_entry, short_exit, quantity)
        return TradeCostBreakdown(
            brokerage=long_rt.brokerage + short_rt.brokerage,
            stt=long_rt.stt + short_rt.stt,
            exchange_charges=long_rt.exchange_charges + short_rt.exchange_charges,
            gst=long_rt.gst + short_rt.gst,
            sebi=long_rt.sebi + short_rt.sebi,
            stamp_duty=long_rt.stamp_duty + short_rt.stamp_duty,
            slippage=long_rt.slippage + short_rt.slippage,
            bid_ask_spread_impact=long_rt.bid_ask_spread_impact + short_rt.bid_ask_spread_impact,
            total=long_rt.total + short_rt.total,
        )

    def estimate_spread_round_trip_cost(
        self,
        long_premium: float,
        short_premium: float,
        quantity: int,
    ) -> float:
        """Quick estimate for spread: assume exit premiums ≈ entry premiums.

        Used by DebitSpreadStrategy.evaluate() for expected_net_value calc.
        """
        return self.cost_for_spread_round_trip(
            long_premium, long_premium,
            short_premium, short_premium,
            quantity,
        ).total
