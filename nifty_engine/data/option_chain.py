"""Option chain helper — filter to ATM +/- N strikes, build OI structure."""
from __future__ import annotations

from typing import Optional

from ..models import OptionQuote, OptionType


class OptionChainBuilder:
    """Pure-function helper for working with raw option chains.

    No external IO — operates on lists of OptionQuote.
    """

    @staticmethod
    def find_atm_strike(chain: list[OptionQuote], spot: float) -> Optional[float]:
        if not chain or spot <= 0:
            return None
        strikes = sorted({q.strike for q in chain})
        return min(strikes, key=lambda s: abs(s - spot))

    @staticmethod
    def filter_atm_window(
        chain: list[OptionQuote],
        spot: float,
        n_each_side: int = 5,
    ) -> list[OptionQuote]:
        atm = OptionChainBuilder.find_atm_strike(chain, spot)
        if atm is None:
            return []
        strikes = sorted({q.strike for q in chain})
        idx = strikes.index(atm)
        lo = max(0, idx - n_each_side)
        hi = min(len(strikes), idx + n_each_side + 1)
        wanted = set(strikes[lo:hi])
        return [q for q in chain if q.strike in wanted]

    @staticmethod
    def pick_quote_by_type(
        chain: list[OptionQuote],
        strike: float,
        option_type: OptionType,
    ) -> Optional[OptionQuote]:
        candidates = [q for q in chain
                      if q.strike == strike and q.option_type == option_type]
        if not candidates:
            return None
        # Prefer the one with the highest volume (most liquid)
        return max(candidates, key=lambda q: q.volume)

    @staticmethod
    def liquidity_score(q: OptionQuote) -> float:
        """0..1 score. Penalises wide spreads and zero volume."""
        if q.volume <= 0:
            return 0.0
        spread = (q.ask or 0.0) - (q.bid or 0.0)
        mid = (q.ask or 0.0 + q.bid or 0.0) / 2 if (q.bid and q.ask) else q.ltp
        spread_pct = spread / mid if mid > 0 else 1.0
        # Liquid if spread < 2% of mid; illiquid if > 10%
        spread_score = max(0.0, 1.0 - (spread_pct / 0.10))
        volume_score = min(1.0, q.volume / 5_000)  # 5000+ = perfect
        return 0.5 * spread_score + 0.5 * volume_score

    @staticmethod
    def oi_structure_summary(
        chain: list[OptionQuote],
        spot: float,
    ) -> dict:
        """High-level OI structure — used by regime engine.

        Returns:
          {
            "max_pain": float,
            "ce_oi_above_spot": int,
            "ce_oi_below_spot": int,
            "pe_oi_above_spot": int,
            "pe_oi_below_spot": int,
            "call_wall": float,      # strike with highest CE OI (resistance)
            "put_wall": float,       # strike with highest PE OI (support)
          }
        """
        if not chain:
            return {}
        atm = OptionChainBuilder.find_atm_strike(chain, spot)
        if atm is None:
            return {}

        ce_above = sum(q.oi for q in chain if q.option_type == OptionType.CE and q.strike > spot)
        ce_below = sum(q.oi for q in chain if q.option_type == OptionType.CE and q.strike < spot)
        pe_above = sum(q.oi for q in chain if q.option_type == OptionType.PE and q.strike > spot)
        pe_below = sum(q.oi for q in chain if q.option_type == OptionType.PE and q.strike < spot)

        ces = [(q.strike, q.oi) for q in chain if q.option_type == OptionType.CE and q.oi > 0]
        pes = [(q.strike, q.oi) for q in chain if q.option_type == OptionType.PE and q.oi > 0]
        call_wall = max(ces, key=lambda x: x[1])[0] if ces else None
        put_wall = max(pes, key=lambda x: x[1])[0] if pes else None

        max_pain = OptionChainBuilder._max_pain(chain)
        return {
            "max_pain": max_pain,
            "ce_oi_above_spot": ce_above,
            "ce_oi_below_spot": ce_below,
            "pe_oi_above_spot": pe_above,
            "pe_oi_below_spot": pe_below,
            "call_wall": call_wall,
            "put_wall": put_wall,
        }

    @staticmethod
    def _max_pain(chain: list[OptionQuote]) -> Optional[float]:
        """Compute max-pain strike — strike at which total option holder loss is max."""
        if not chain:
            return None
        strikes = sorted({q.strike for q in chain})
        if not strikes:
            return None
        pains: dict[float, float] = {}
        for s in strikes:
            total = 0.0
            for q in chain:
                if q.option_type == OptionType.CE:
                    total += max(0.0, s - q.strike) * q.oi
                else:
                    total += max(0.0, q.strike - s) * q.oi
            pains[s] = total
        return min(pains, key=pains.get)
