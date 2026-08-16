"""End-to-end test of the Phase 8 lifecycle notifier.

Simulates a complete trade: ENTRY → POSITION_UPDATE → THESIS_DETERIORATING →
POSITION_ADJUSTED → THESIS_INVALIDATED → EXIT → TRADE_REVIEW.

Sends real Discord alerts so the user can verify the webhook receives them.
"""
from __future__ import annotations

import sys
import os
from datetime import datetime, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv
load_dotenv()

from nifty_engine.notifier import (
    DiscordNotifier, AlertType, Alert, AlertLevel,
    ThesisTracker, ThesisScore, PositionState,
    ProtectionLayer, ProtectionConfig, ProtectionTrigger,
    MAEMFETracker, MAEMFERecord,
)
from nifty_engine.notifier.discord import get_notifier
from nifty_engine.models import (
    IndexQuote, MarketSnapshot, OptionQuote, OptionType, Position,
    MarketRegime, VolatilityRegime, RunMode, StrategyName,
)
from nifty_engine.features.correlation import (
    VIXValuation, OIClassification, FuturesBasis, BankNiftyConfirmation,
    CorrelationRegime, ConfirmationScore,
)


def make_snapshot(spot: float, prev_close: float = None, vix: float = 11.31, **kwargs):
    """Build a test snapshot."""
    now = datetime.utcnow()
    return MarketSnapshot(
        timestamp=now,
        index=IndexQuote(
            ltp=spot,
            prev_close=prev_close or spot,
            open=spot,
            high=spot * 1.005,
            low=spot * 0.995,
            volume=1_000_000,
            vwap=spot * 1.001,
            atr=spot * 0.005,
            adx=kwargs.get("adx", 28.0),
            adx_slope=kwargs.get("adx_slope", 1.5),
            rsi=kwargs.get("rsi", 62.0),
            ema_fast=spot * 1.001,
            ema_mid=spot * 0.999,
            ema_slow=spot * 0.997,
            last_updated=now,
        ),
        india_vix=None,  # We'll inject via confirmation directly
        option_chain=[],
        market_open=True,
        data_valid=True,
        time_bucket=None,
    )


def make_confirmation(direction: str, score: float, **kwargs):
    """Build a test confirmation score."""
    return ConfirmationScore(
        score=score,
        vix_valuation=VIXValuation(
            vix=kwargs.get("vix", 11.31),
            vix_percentile=kwargs.get("vix_pctile", 10.0),
            vix_change=-0.11,
            iv_vix_gap=None,
            valuation=kwargs.get("vix_val", "CHEAP"),
            reasons=[f"VIX {kwargs.get('vix_val', 'CHEAP')}"],
        ),
        oi_classification=OIClassification(
            ce_classification=kwargs.get("ce_oi", "LONG_BUILDUP"),
            pe_classification=kwargs.get("pe_oi", "NEUTRAL"),
            call_wall=kwargs.get("call_wall"),
            put_wall=kwargs.get("put_wall"),
            max_pain=None,
            reasons=[],
        ),
        futures_basis=FuturesBasis(
            spot=25000, futures=25020, basis=20, basis_pct=0.08,
            interpretation="PREMIUM", reasons=[],
        ),
        banknifty_confirmation=BankNiftyConfirmation(
            nifty_change_pct=0.4,
            banknifty_change_pct=0.5,
            correlation_state=kwargs.get("bn_state", "CONFIRMED"),
            reasons=[],
        ),
        correlation_regime=CorrelationRegime(
            short_window_corr=0.85,
            long_window_corr=0.82,
            regime="NORMAL",
            reasons=[],
        ),
        reasons=[],
    )


def make_regime(market_regime, confidence=0.8):
    """Build a minimal regime assessment."""
    from nifty_engine.models import RegimeAssessment, MarketFeatures, MarketRegime, VolatilityRegime
    return RegimeAssessment(
        market_regime=market_regime,
        volatility_regime=VolatilityRegime.NORMAL_VOL,
        confidence=confidence,
        reasons=["test"],
        features_snapshot=MarketFeatures(
            adx=28, adx_slope=1.5, ema_fast=25010, ema_mid=24990, ema_slow=24970,
            vwap=25001, rsi=62, atr=125, atr_pct=0.005,
        ),
    )


def make_option(spot=25000, strike=25000, option_type=OptionType.CE, ltp=180.0):
    """Build a test option quote."""
    return OptionQuote(
        symbol=f"NIFTY24AUG{strike}{option_type.value}",
        exchange="NSE",
        expiry=datetime.utcnow().date() + timedelta(days=7),
        strike=strike,
        option_type=option_type,
        ltp=ltp,
        bid=ltp - 1,
        ask=ltp + 1,
        volume=50000,
        oi=200000,
        iv=0.14,
        delta=0.52,
        gamma=0.001,
        theta=-5.0,
        vega=20.0,
        last_updated=datetime.utcnow(),
    )


def make_position(option, lots=2, entry_price=180.0):
    """Build a test position."""
    return Position(
        strategy=StrategyName.LONG_CALL,
        option=option,
        lots=lots,
        entry_price=entry_price,
        entry_time=datetime.utcnow(),
        side="BUY",
        stop_loss=entry_price * 0.5,
        take_profit=entry_price * 2.0,
        current_price=entry_price,
        unrealised_pnl=0.0,
    )


def main():
    """Run the full lifecycle simulation."""
    notifier = get_notifier()
    print(f"Notifier: webhook={bool(notifier.webhook_url)}, enabled={notifier.enabled}")
    print()

    # ===== 1. REGIME_CHANGE: NIFTY transitions from RANGE → STRONG_BULL =====
    print("[1/9] Sending REGIME_CHANGE alert...")
    notifier.send_alert(
        AlertType.REGIME_CHANGE,
        "Regime Change: RANGE → STRONG_BULL",
        fields={
            "Previous": "RANGE",
            "Current": "STRONG_BULL",
            "Volatility": "NORMAL_VOL",
            "Confidence": "80%",
            "Spot": "₹25,000",
            "ADX": "28.4",
            "RSI": "62.1",
            "Reasons": "ADX rising | EMAs aligned up | above VWAP",
        },
        description="Market regime has shifted from RANGE to STRONG_BULL. Strategy selector will evaluate Long Call candidates.",
    )

    # ===== 2. SETUP_DETECTED =====
    print("[2/9] Sending SETUP_DETECTED alert...")
    notifier.send_alert(
        AlertType.SETUP_DETECTED,
        "Setup Detected: LONG_CALL",
        fields={
            "Strategy": "LONG_CALL",
            "Direction": "BULLISH",
            "NIFTY": "₹25,000",
            "Expected Net": "₹8,200",
            "Confidence": "78%",
            "Risk/Reward": "1.8",
            "Regime": "STRONG_BULL",
            "Status": "WAITING FOR CONFIRMATION",
        },
        description="A directional setup has been detected. The engine will enter if all confirmation + risk gates pass on the next cycle.",
    )

    # ===== 3. ENTRY =====
    print("[3/9] Sending ENTRY alert...")
    spot = 25000.0
    option = make_option(spot=spot, strike=25000, ltp=180.0)
    lots = 2
    fill_price = 179.50
    notifier.send_alert(
        AlertType.ENTRY,
        "Position Entered — LONG_CALL",
        fields={
            "Strategy": "LONG_CALL",
            "Direction": "BULLISH",
            "Option": option.symbol,
            "Strike": "25000",
            "Type": "CE",
            "Lots": "2",
            "Fill Price": f"₹{fill_price:.2f}",
            "Premium Paid": f"₹{fill_price * lots * 75:,.0f}",
            "Stop Loss": "₹90.00",
            "Take Profit": "₹360.00",
            "Expected Net": "₹8,200",
            "Confidence": "78%",
            "Risk/Reward": "1.80",
            "NIFTY Spot": f"₹{spot:,.0f}",
            "Regime": "STRONG_BULL",
            "Volatility": "NORMAL_VOL",
            "IV": "14.0%",
            "Delta": "0.52",
        },
        description=f"Entered {lots} lot(s) of {option.symbol} at ₹{fill_price:.2f}. 3-layer protection + thesis tracker + MAE/MFE are now active.",
    )

    # ===== Initialise trackers =====
    snapshot = make_snapshot(spot=spot, prev_close=24950)
    regime = make_regime(MarketRegime.STRONG_BULL, confidence=0.80)
    confirmation = make_confirmation("BULLISH", score=0.5, vix_val="CHEAP", bn_state="CONFIRMED")
    position = make_position(option, lots=lots, entry_price=fill_price)

    thesis_tracker = ThesisTracker()
    thesis_tracker.init_at_entry(snapshot, regime, confirmation, "BULLISH")
    protection = ProtectionLayer()
    protection.init_at_entry(position, snapshot)
    mae_mfe = MAEMFETracker()
    mae_mfe.init_at_entry(position, trade_id="sim-001")

    print(f"       Initial thesis composite: {thesis_tracker.last_score.composite:.0f}/100 ({thesis_tracker.last_score.state.value})")
    print(f"       Thesis components: trend={thesis_tracker.last_score.trend:.0f} vwap={thesis_tracker.last_score.vwap:.0f} momentum={thesis_tracker.last_score.momentum:.0f} bn={thesis_tracker.last_score.banknifty:.0f} oi={thesis_tracker.last_score.oi:.0f} vix={thesis_tracker.last_score.vix:.0f}")

    # ===== 4. POSITION_UPDATE — price moves favourably =====
    print("[4/9] Sending POSITION_UPDATE alert (price +30)...")
    spot2 = spot + 30
    new_price = fill_price + 15  # option delta ~0.5
    position.current_price = new_price
    position.unrealised_pnl = (new_price - fill_price) * lots * 75
    mae_mfe.update(position, new_price, spot2, datetime.utcnow())
    snapshot2 = make_snapshot(spot=spot2, prev_close=spot, adx=29.0, rsi=65.0)
    protection.update_swing(snapshot2)
    thesis2 = thesis_tracker.update(snapshot2, regime, confirmation)
    notifier.send_alert(
        AlertType.POSITION_UPDATE,
        "Position Update — LONG_CALL (+₹2,250)",
        fields={
            "Position": option.symbol,
            "Entry": f"₹{fill_price:.2f}",
            "Current": f"₹{new_price:.2f}",
            "Unrealised P&L": f"+₹{position.unrealised_pnl:,.0f}",
            "Thesis Score": f"{thesis2.composite:.0f}/100",
            "State": thesis2.state.value,
            "Spot": f"₹{spot2:,.0f}",
            "MAE": f"₹{mae_mfe._mae:+,.0f}",
            "MFE": f"₹{mae_mfe._mfe:+,.0f}",
        },
        description="Position is moving favourably. All 3 protection layers OK.",
    )

    # ===== 5. THESIS_DETERIORATING — Bank Nifty starts diverging =====
    print("[5/9] Sending THESIS_DETERIORATING alert (Bank Nifty diverges)...")
    spot3 = spot2 - 10
    new_price3 = new_price - 5
    position.current_price = new_price3
    position.unrealised_pnl = (new_price3 - fill_price) * lots * 75
    mae_mfe.update(position, new_price3, spot3, datetime.utcnow())
    snapshot3 = make_snapshot(spot=spot3, prev_close=spot2, adx=26.0, adx_slope=-1.0, rsi=58.0)
    # Bank Nifty now diverges
    confirmation3 = make_confirmation("BULLISH", score=-0.2, vix_val="FAIR", bn_state="DIVERGENT")
    protection.update_swing(snapshot3)
    thesis3 = thesis_tracker.update(snapshot3, regime, confirmation3)
    print(f"       Thesis now: {thesis3.composite:.0f}/100 state={thesis3.state.value} (was {thesis2.state.value})")
    notifier.send_alert(
        AlertType.THESIS_DETERIORATING,
        f"Thesis Deteriorating — LONG_CALL",
        fields={
            "Position": option.symbol,
            "Direction": thesis3.direction,
            "Previous State": thesis3.changed_from.value if thesis3.changed_from else "—",
            "Current State": thesis3.state.value,
            "Composite Score": f"{thesis3.composite:.0f}/100",
            "Trend": f"{thesis3.trend:.0f}",
            "VWAP": f"{thesis3.vwap:.0f}",
            "Momentum": f"{thesis3.momentum:.0f}",
            "Bank Nifty": f"{thesis3.banknifty:.0f}",
            "OI": f"{thesis3.oi:.0f}",
            "VIX": f"{thesis3.vix:.0f}",
            "Unrealised P&L": f"₹{position.unrealised_pnl:,.0f}",
            "Spot": f"₹{spot3:,.0f}",
            "Action": "HOLD — monitoring for invalidation",
        },
        description="Thesis score has dropped due to Bank Nifty divergence + ADX falling. Engine will tighten stops and watch for further deterioration.",
    )

    # ===== 6. THESIS_INVALIDATED — protection fires (structure break) =====
    print("[6/9] Sending THESIS_INVALIDATED alert...")
    spot4 = spot3 - 30
    new_price4 = new_price3 - 15
    position.current_price = new_price4
    position.unrealised_pnl = (new_price4 - fill_price) * lots * 75
    mae_mfe.update(position, new_price4, spot4, datetime.utcnow())
    snapshot4 = make_snapshot(spot=spot4, prev_close=spot3, adx=22.0, adx_slope=-2.5, rsi=42.0)
    # Strong bearish confirmation now
    confirmation4 = make_confirmation("BEARISH", score=-0.5, vix_val="EXPENSIVE", bn_state="DIVERGENT")
    protection.update_swing(snapshot4)
    thesis4 = thesis_tracker.update(snapshot4, regime, confirmation4)
    # Evaluate protection
    prot_result = protection.evaluate(position, snapshot4, thesis4.composite, datetime.utcnow())
    print(f"       Protection result: trigger={prot_result.trigger.value} should_exit={prot_result.should_exit}")
    print(f"       Reason: {prot_result.reason}")
    notifier.send_alert(
        AlertType.THESIS_INVALIDATED,
        "Thesis Invalidated — LONG_CALL",
        fields={
            "Position": option.symbol,
            "Trigger": prot_result.trigger.value,
            "Composite Score": f"{thesis4.composite:.0f}/100",
            "Spot": f"₹{spot4:,.0f}",
            "VWAP": f"₹{snapshot4.index.vwap:.0f}",
            "Bank Nifty": "DIVERGENT",
            "Action": "EXIT — thesis no longer valid",
        },
        description="The original bullish thesis has been invalidated. Bank Nifty diverging, VWAP lost, momentum fading. Engine will exit immediately rather than wait for the monetary stop.",
    )

    # ===== 7. EXIT =====
    print("[7/9] Sending EXIT alert...")
    exit_price = new_price4
    qty = lots * 75
    gross = (exit_price - fill_price) * qty
    from nifty_engine.execution import CostModel
    cost = CostModel().cost_for_round_trip(fill_price, exit_price, qty).total
    net = gross - cost
    mae_mfe_record = mae_mfe.finalize(position, exit_price, net)
    notifier.send_alert(
        AlertType.EXIT,
        "Position Exited — LONG_CALL (LOSS)",
        fields={
            "Position": option.symbol,
            "Entry": f"₹{fill_price:.2f}",
            "Exit": f"₹{exit_price:.2f}",
            "Lots": str(lots),
            "Gross P&L": f"₹{gross:+,.0f}",
            "Charges": f"₹{cost:,.0f}",
            "Net P&L": f"₹{net:+,.0f}",
            "Exit Reason": prot_result.trigger.value,
            "Spot at Exit": f"₹{spot4:,.0f}",
        },
        description=f"Position closed via **{prot_result.trigger.value}**. Net P&L: ₹{net:+,.0f} after ₹{cost:,.0f} charges.",
    )

    # ===== 8. TRADE_REVIEW with MAE/MFE =====
    print("[8/9] Sending TRADE_REVIEW alert...")
    notifier.send_alert(
        AlertType.TRADE_REVIEW,
        f"Trade Review — LONG_CALL (LOSS)",
        fields={
            "Position": option.symbol,
            "Net P&L": f"₹{net:+,.0f}",
            "MAE": f"₹{mae_mfe_record.mae_inr:+,.0f} ({mae_mfe_record.mae_pct_of_premium*100:.1f}%)",
            "MFE": f"₹{mae_mfe_record.mfe_inr:+,.0f} ({mae_mfe_record.mfe_pct_of_premium*100:.1f}%)",
            "Capture": f"{mae_mfe_record.capture_rate*100:.0f}%",
            "Exit Reason": prot_result.trigger.value,
            "Thesis at Exit": f"{thesis4.composite:.0f}/100 ({thesis4.state.value})",
        },
        description=(
            f"Strategy: LONG_CALL\n"
            f"Entry: ₹{fill_price:.2f} → Exit: ₹{exit_price:.2f}\n"
            f"Gross P&L: ₹{gross:+,.0f}\n"
            f"Charges: ₹{cost:,.0f}\n"
            f"Net P&L: ₹{net:+,.0f}\n"
            f"MAE (max adverse): ₹{mae_mfe_record.mae_inr:+,.0f} ({mae_mfe_record.mae_pct_of_premium*100:.1f}% of premium)\n"
            f"MFE (max favourable): ₹{mae_mfe_record.mfe_inr:+,.0f} ({mae_mfe_record.mfe_pct_of_premium*100:.1f}% of premium)\n"
            f"Capture rate: {mae_mfe_record.capture_rate*100:.0f}% of MFE\n"
            f"Exit thesis composite: {thesis4.composite:.0f}/100 ({thesis4.state.value})\n"
            f"Exit reason: {prot_result.trigger.value}\n"
            f"Thesis: Correct direction, but Bank Nifty diverged and structure broke"
        ),
    )

    # ===== 9. STRATEGY_PERFORMANCE (daily summary) =====
    print("[9/9] Sending STRATEGY_PERFORMANCE alert...")
    notifier.send_alert(
        AlertType.STRATEGY_PERFORMANCE,
        "Strategy Performance — Daily Summary",
        fields={
            "Date": datetime.utcnow().strftime("%Y-%m-%d"),
            "Total Trades": "1",
            "Winners": "0",
            "Losers": "1",
            "Win Rate": "0.0%",
            "Net P&L": f"₹{net:+,.0f}",
            "Expectancy": f"₹{net:,.0f}",
            "Max Drawdown": f"₹{abs(mae_mfe_record.mae_inr):,.0f}",
            "Charges Paid": f"₹{cost:,.0f}",
            "Engine State": "Paper mode (no live capital)",
        },
        description="End-of-day performance summary. Engine captured the loss via thesis invalidation BEFORE the hard monetary stop was hit — this is the 3-layer protection working as designed.",
    )

    print()
    print(f"Total alerts sent: {len(notifier.recent_alerts())}")
    print("Check your Discord channel — all 9 alert types should have arrived.")


if __name__ == "__main__":
    main()
