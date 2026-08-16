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

        # Slippage — applied per unit, multiplied by quantity
        slip = 0.0
        if apply_slippage:
            slip = cfg["slippage"]["per_leg_points"] * quantity
        spread = 0.0
        if apply_spread:
            spread = cfg["slippage"]["bid_ask_spread_points"] * quantity

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
