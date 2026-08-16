"""Engine — orchestrates one decision cycle.

Single entry point: Engine.run_cycle() -> Decision.

This is the heart of the system. It calls each layer in the exact order
specified by the spec (section 28).
"""
from __future__ import annotations

import os
from datetime import datetime
from typing import Optional

from .config import load as load_config
from .data import DhanBroker, MarketCache
from .decision import (
    OptionSelector, PositionManager, RegimeEngineRunner,
    RiskEngine, StrategySelector, SpreadSelector,
)
from .execution import OrderManager, OrderRequest, Reconciler
from .features.correlation import (
    VIXValuationCalculator, OIClassifier, FuturesBasisCalculator,
    BankNiftyCalculator, CorrelationRegimeDetector,
    ConfirmationScoreCalculator, compute_all_confirmations,
)
from .journal import DecisionLogger, TradeLogger
from .models import (
    Decision, DecisionAction, MarketSnapshot, RunMode, StrategyName,
    Position, OptionQuote,
)
from .notifier import (
    DiscordNotifier, AlertType, AlertLevel,
    ThesisTracker, ThesisScore, PositionState,
    ProtectionLayer, ProtectionConfig, ProtectionResult, ProtectionTrigger,
    MAEMFETracker, MAEMFERecord,
)
from .notifier.discord import get_notifier
from .utils.time_utils import ist_now, is_trading_allowed_now


class Engine:
    """One instance = one trading session."""

    def __init__(
        self,
        mode: RunMode = RunMode.PAPER,
        capital: Optional[float] = None,
        runs_dir: str = "/home/z/my-project/runs",
        broker: Optional[DhanBroker] = None,
    ) -> None:
        self.mode = mode
        cap_env = os.environ.get("ENGINE_CAPITAL")
        self.capital = float(capital if capital is not None else (cap_env or 1_000_000))

        # --- wiring ---
        # Reuse the passed-in broker if provided (avoids creating a new DhanContext
        # which loads the 50MB security list CSV).
        self.broker = broker if broker is not None else DhanBroker()
        self.cache = MarketCache(ttl_seconds=5.0)
        self.regime = RegimeEngineRunner()
        self.selector = StrategySelector()
        self.option_selector = OptionSelector()
        self.spread_selector = SpreadSelector()
        self.risk = RiskEngine(capital=self.capital)
        self.position_manager = PositionManager()
        self.order_manager = OrderManager(mode=mode, broker=self.broker if mode == RunMode.LIVE else None)
        self.reconciler = Reconciler()

        self.trade_logger = TradeLogger(runs_dir=runs_dir)
        self.decision_logger = DecisionLogger(runs_dir=runs_dir)

        # --- Phase 8: notifier + thesis + protection + MAE/MFE ---
        self.notifier = get_notifier()
        self.thesis_tracker: Optional[ThesisTracker] = None       # active per-position
        self.protection = ProtectionLayer()
        self.mae_mfe = MAEMFETracker()
        self._last_regime: Optional[str] = None
        self._last_alerted_setup: Optional[str] = None

        self._day_initialised: Optional[datetime] = None

    # ---- public API ----
    def run_cycle(self) -> Decision:
        """Execute one full decision cycle. Returns the Decision."""
        now = ist_now()
        self._maybe_reset_day(now)

        # ---- 1. fetch snapshot ----
        snapshot = self.broker.get_snapshot()

        # ---- 2. reconcile / emergency gate ----
        reconcile = self.reconciler.check_snapshot(snapshot)
        if not reconcile.ok:
            # Emit DATA_API_ERROR alert if emergency
            if reconcile.emergency_action == "SHUTDOWN":
                self.notifier.send_alert(
                    AlertType.DATA_API_ERROR,
                    "Emergency Shutdown Triggered",
                    fields={
                        "Reason": reconcile.reason,
                        "Action": reconcile.emergency_action,
                        "IST Time": now.isoformat(),
                    },
                    description="The engine has detected a critical condition (stale data, abnormal spread, API failure) and is entering emergency shutdown. NO-TRADE will be emitted for the rest of the session.",
                )
            decision = self._no_trade_decision(
                snapshot, now,
                reason=f"reconciliation failed: {reconcile.reason}",
                emergency=reconcile.emergency_action,
            )
            self.decision_logger.log_decision(decision, self._snapshot_summary(snapshot))
            return decision

        # ---- 3. trading-hours gate ----
        allowed, why = is_trading_allowed_now(now)
        if not allowed:
            decision = self._no_trade_decision(
                snapshot, now,
                reason=f"trading not allowed: {why}",
            )
            self.decision_logger.log_decision(decision, self._snapshot_summary(snapshot))
            return decision

        # ---- 4. assess regime ----
        regime_assessment = self.regime.assess(snapshot)

        # Emit REGIME_CHANGE alert when regime transitions
        if self._last_regime is not None and regime_assessment.market_regime.value != self._last_regime:
            self.notifier.send_alert(
                AlertType.REGIME_CHANGE,
                f"Regime Change: {self._last_regime} → {regime_assessment.market_regime.value}",
                fields={
                    "Previous": self._last_regime,
                    "Current": regime_assessment.market_regime.value,
                    "Volatility": regime_assessment.volatility_regime.value,
                    "Confidence": f"{regime_assessment.confidence*100:.0f}%",
                    "Spot": f"₹{snapshot.index.ltp:,.0f}",
                    "ADX": f"{snapshot.index.adx:.1f}" if snapshot.index.adx else "—",
                    "RSI": f"{snapshot.index.rsi:.1f}" if snapshot.index.rsi else "—",
                    "Reasons": " | ".join(regime_assessment.reasons[:3]),
                },
                description=f"Market regime has shifted from **{self._last_regime}** to **{regime_assessment.market_regime.value}**. Strategy selector will now evaluate candidates for this new regime.",
            )
        self._last_regime = regime_assessment.market_regime.value

        # ---- 4b. compute cross-market confirmation (Layer 3 + 4) ----
        # Correlation itself is NEVER a buy signal — it modulates confidence.
        confirmation = self._compute_confirmation(snapshot, regime_assessment)

        # ---- 4c. manage open positions (thesis + protection + MAE/MFE) ----
        # Done BEFORE selecting new strategy so we can exit/reverse first.
        self._manage_open_positions(snapshot, regime_assessment, confirmation, now)

        # ---- 5. select strategy ----
        chosen_eval, all_evals = self.selector.select(snapshot, regime_assessment, confirmation)

        # Emit SETUP_DETECTED alert when an eligible strategy emerges (before entry)
        if (chosen_eval.eligible
                and chosen_eval.strategy != StrategyName.NO_TRADE
                and self.order_manager.open_positions_count() == 0):
            setup_key = f"{chosen_eval.strategy.value}@{snapshot.index.ltp:.0f}"
            if setup_key != self._last_alerted_setup:
                self._last_alerted_setup = setup_key
                self.notifier.send_alert(
                    AlertType.SETUP_DETECTED,
                    f"Setup Detected: {chosen_eval.strategy.value}",
                    fields={
                        "Strategy": chosen_eval.strategy.value,
                        "Direction": chosen_eval.direction or "NEUTRAL",
                        "NIFTY": f"₹{snapshot.index.ltp:,.0f}",
                        "Expected Net": f"₹{chosen_eval.expected_net_value:,.0f}",
                        "Confidence": f"{chosen_eval.confidence_score*100:.0f}%",
                        "Risk/Reward": f"{chosen_eval.risk_reward:.2f}",
                        "Regime": regime_assessment.market_regime.value,
                        "Status": "WAITING FOR CONFIRMATION",
                    },
                    description="A directional setup has been detected. The engine will enter if all confirmation + risk gates pass on the next cycle.",
                )

        # ---- 6. option selection (single-leg OR spread) ----
        spread_sel = None
        opt_sel = None
        if chosen_eval.strategy == StrategyName.DEBIT_SPREAD:
            # Use SpreadSelector for 2-leg debit spreads
            spread_sel = self.spread_selector.select(snapshot, chosen_eval)
            # Build a synthetic opt_sel so downstream code can use the long leg as the primary option
            if spread_sel.selected:
                # For risk engine, we use the long leg + spread's max_loss
                from .models import OptionSelection
                opt_sel = OptionSelection(
                    selected=True,
                    option=spread_sel.long_leg,
                    score=spread_sel.score,
                    reasons=spread_sel.reasons,
                )
        else:
            opt_sel = self.option_selector.select(snapshot, chosen_eval)

        # ---- 7. risk evaluation ----
        if chosen_eval.eligible and opt_sel and opt_sel.selected:
            risk_decision = self.risk.evaluate(
                snapshot, chosen_eval, opt_sel.option,
                open_positions=self.order_manager.open_positions_count(),
                now=now,
            )
        else:
            risk_decision = None

        # Emit RISK_LIMIT alert if risk blocks an otherwise-eligible setup
        if (chosen_eval.eligible and opt_sel and opt_sel.selected
                and risk_decision and not risk_decision.allowed
                and self.risk._consecutive_losses >= self.risk._cfg["cooldown"]["after_consecutive_losses"]):
            self.notifier.send_alert(
                AlertType.RISK_LIMIT,
                "Risk Limit Reached — Cooldown Active",
                fields={
                    "Reason": risk_decision.reason,
                    "Consecutive Losses": self.risk._consecutive_losses,
                    "Day P&L": f"₹{self.risk._day_pnl:,.0f}",
                    "Day Trades": self.risk._day_trades,
                },
                description="Risk engine has blocked a setup that would otherwise be eligible. Engine is in cooldown — no new entries until cooldown expires.",
            )

        # ---- 8. compose decision ----
        if (chosen_eval.eligible
                and opt_sel and opt_sel.selected
                and risk_decision is not None
                and risk_decision.allowed):
            action = DecisionAction.ENTER
            option = opt_sel.option
            lots = risk_decision.max_lots

            # Phase 10: spread-specific fields
            is_spread = (chosen_eval.strategy == StrategyName.DEBIT_SPREAD
                         and spread_sel is not None
                         and spread_sel.selected)
            long_leg = spread_sel.long_leg if is_spread else None
            short_leg = spread_sel.short_leg if is_spread else None
            net_debit = spread_sel.net_debit if is_spread else None
            spread_width = spread_sel.spread_width if is_spread else None
            max_loss = spread_sel.max_loss * lots if is_spread else None
            max_gain = spread_sel.max_gain * lots if is_spread else None
            breakeven = spread_sel.breakeven if is_spread else None

            if is_spread:
                # For spreads, premium = net debit * qty
                premium_per_lot = net_debit * 75
                total_premium = premium_per_lot * lots
            else:
                premium_per_lot = option.ltp * 75
                total_premium = premium_per_lot * lots

            stop = risk_decision.stop_loss
            target = risk_decision.take_profit
            reasons = (
                list(chosen_eval.reasons)
                + list(opt_sel.reasons)
                + [risk_decision.reason]
            )
            confidence = chosen_eval.confidence_score

            # ---- execute (paper mode fills immediately) ----
            order_req = OrderRequest(
                option=option,            # long leg for spreads
                long_leg=long_leg,
                short_leg=short_leg,
                side="BUY",
                lots=lots,
                strategy=chosen_eval.strategy.value,
                stop_loss=stop,
                take_profit=target,
                reason=chosen_eval.strategy.value,
                net_debit=net_debit,
                spread_width=spread_width,
                max_loss=max_loss,
                max_gain=max_gain,
                breakeven=breakeven,
            )
            order_result = self.order_manager.submit(order_req)
            if not order_result.success:
                reasons.append(f"order failed: {order_result.error}")
                action = DecisionAction.NO_TRADE
                lots = 0
                total_premium = 0.0
            else:
                self.risk.add_open_exposure(total_premium)
                reasons.append(
                    f"order filled @ {order_result.fill_price:.2f} "
                    f"(slippage {order_result.slippage_applied:.2f})"
                )
                # Initialise Phase 8 trackers (use long leg as primary option)
                self._init_position_trackers(option, lots, snapshot, regime_assessment, confirmation, chosen_eval.direction or "NEUTRAL")
                # Emit ENTRY alert (with spread details if applicable)
                self._send_entry_alert(option, lots, order_result.fill_price, chosen_eval, risk_decision, snapshot, regime_assessment,
                                        long_leg=long_leg, short_leg=short_leg, net_debit=net_debit,
                                        spread_width=spread_width, max_loss=max_loss, max_gain=max_gain, breakeven=breakeven)
        else:
            action = DecisionAction.NO_TRADE
            option = None
            long_leg = None
            short_leg = None
            lots = 0
            premium_per_lot = 0.0
            total_premium = 0.0
            stop = None
            target = None
            max_loss = None
            max_gain = None
            breakeven = None
            reasons = list(chosen_eval.reasons)
            if not chosen_eval.eligible:
                reasons.append("strategy not eligible")
            if chosen_eval.eligible and not (opt_sel.selected if opt_sel else False):
                reasons.append("option selection failed")
            if (risk_decision and not risk_decision.allowed):
                reasons.append(f"risk blocked: {risk_decision.reason}")
            confidence = chosen_eval.confidence_score

        # ---- 9. compose final Decision ----
        decision = Decision(
            timestamp=now,
            action=action,
            strategy=chosen_eval.strategy if chosen_eval else StrategyName.NO_TRADE,
            regime=regime_assessment.market_regime,
            volatility=regime_assessment.volatility_regime,
            option=option,
            long_leg=long_leg,
            short_leg=short_leg,
            lots=lots,
            premium_per_lot=premium_per_lot,
            total_premium=total_premium,
            expected_net_value=chosen_eval.expected_net_value if chosen_eval else 0.0,
            expected_risk=risk_decision.max_premium_exposure if risk_decision else 0.0,
            max_loss=max_loss,
            max_gain=max_gain,
            breakeven=breakeven,
            confidence=confidence,
            stop_loss=stop,
            take_profit=target,
            reasons=reasons,
            explainability_block=self._explainability(
                snapshot, regime_assessment, chosen_eval, opt_sel, risk_decision, action, lots,
                confirmation,
            ),
        )

        # ---- 10. journal ----
        self.decision_logger.log_decision(decision, self._snapshot_summary(snapshot))
        return decision

    def _compute_confirmation(self, snapshot: MarketSnapshot, regime):
        """Compute cross-market confirmation score.

        Returns None if data is invalid — engine treats that as 'no info'.
        """
        if not snapshot.data_valid:
            return None
        # Direction from regime
        if regime.market_regime.value in ("STRONG_BULL", "BULL", "WEAK_BULL", "BREAKOUT"):
            direction = "BULLISH"
        elif regime.market_regime.value in ("STRONG_BEAR", "BEAR", "WEAK_BEAR"):
            direction = "BEARISH"
        else:
            direction = "NEUTRAL"

        banknifty_q = getattr(snapshot, "_banknifty_quote", None)
        futures_q = getattr(snapshot, "_nifty_futures_quote", None)
        try:
            return compute_all_confirmations(
                snapshot,
                banknifty_quote=banknifty_q,
                futures_quote=futures_q,
                direction=direction,
            )
        except Exception:
            return None

    # ---- Phase 8: position management + alerts ----

    def _init_position_trackers(self, option, lots, snapshot, regime, confirmation, direction):
        """Initialise thesis + protection + MAE/MFE at trade entry."""
        # Find the just-opened position
        pos = None
        for p in self.order_manager.positions:
            if p.status == "OPEN" and p.option.symbol == option.symbol:
                pos = p
                break
        if pos is None:
            pos = self.order_manager.positions[-1] if self.order_manager.positions else None
        if pos is None:
            return

        # Thesis tracker
        self.thesis_tracker = ThesisTracker()
        if confirmation:
            self.thesis_tracker.init_at_entry(snapshot, regime, confirmation, direction)

        # Protection layer
        self.protection = ProtectionLayer()
        self.protection.init_at_entry(pos, snapshot)

        # MAE/MFE
        self.mae_mfe = MAEMFETracker()
        self.mae_mfe.init_at_entry(pos)

    def _manage_open_positions(self, snapshot, regime, confirmation, now):
        """Run thesis + 3-layer protection + MAE/MFE on all open positions."""
        if not self.order_manager.positions:
            return

        for pos in list(self.order_manager.positions):
            if pos.status != "OPEN":
                continue

            # Update swing high/low
            self.protection.update_swing(snapshot)

            # Update mark-to-market
            current_price = pos.current_price or pos.entry_price
            if hasattr(snapshot, "index") and snapshot.index.ltp > 0:
                # Reprice via BS for paper mode (simplified)
                try:
                    from .backtest.engine import bs_option_price
                    t = 7.0 / 365.0
                    new_price = bs_option_price(
                        snapshot.index.ltp, pos.option.strike, t,
                        pos.option.iv or 0.15, 0.07, pos.option.option_type,
                    )
                    current_price = max(0.05, new_price)
                    pos.current_price = current_price
                    pos.unrealised_pnl = (current_price - pos.entry_price) * pos.lots * 75
                except Exception:
                    pass

            # Update MAE/MFE
            self.mae_mfe.update(pos, current_price, snapshot.index.ltp, now)

            # Compute current thesis score
            thesis_score = None
            thesis_composite = None
            if self.thesis_tracker and confirmation:
                thesis = self.thesis_tracker.update(snapshot, regime, confirmation)
                thesis_score = thesis
                thesis_composite = thesis.composite

                # Alert on state transition (THESIS_DETERIORATING, POSITION_ADJUSTED)
                if thesis.changed_from:
                    if thesis.state == PositionState.CAUTIOUS and thesis.changed_from == PositionState.CONFIDENT:
                        self.notifier.send_alert(
                            AlertType.THESIS_DETERIORATING,
                            f"Thesis Deteriorating — {pos.strategy.value}",
                            fields={
                                "Position": pos.option.symbol,
                                "Direction": thesis.direction,
                                "Previous State": thesis.changed_from.value,
                                "Current State": thesis.state.value,
                                "Composite Score": f"{thesis.composite:.0f}/100",
                                **{k.capitalize(): f"{v:.0f}" for k, v in thesis.to_dict().items() if isinstance(v, (int, float)) and k not in ("composite",)},
                                "Unrealised P&L": f"₹{pos.unrealised_pnl:,.0f}",
                                "Spot": f"₹{snapshot.index.ltp:,.0f}",
                                "Action": "HOLD — monitoring for invalidation",
                            },
                            description="Thesis score has dropped from CONFIDENT to CAUTIOUS. Engine will tighten stops and watch for further deterioration.",
                        )
                    elif thesis.state == PositionState.REDUCE:
                        self.notifier.send_alert(
                            AlertType.POSITION_ADJUSTED,
                            f"Position Reduce — {pos.strategy.value}",
                            fields={
                                "Position": pos.option.symbol,
                                "Lots": pos.lots,
                                "Composite Score": f"{thesis.composite:.0f}/100",
                                "Unrealised P&L": f"₹{pos.unrealised_pnl:,.0f}",
                                "Action": "REDUCE — cut exposure",
                            },
                            description="Thesis score has dropped into REDUCE zone. Engine is reducing position size to limit further loss if thesis continues to deteriorate.",
                        )

            # Evaluate 3-layer protection
            protection_result = self.protection.evaluate(
                pos, snapshot, thesis_composite, now,
            )

            # Trigger exit if any layer fires
            if protection_result.should_exit:
                self._exit_position(pos, current_price, snapshot, now,
                                    reason=protection_result.trigger.value,
                                    protection_result=protection_result,
                                    thesis_score=thesis_score)

    def _exit_position(self, pos, exit_price, snapshot, now, reason, protection_result=None, thesis_score=None):
        """Close a position and emit EXIT + TRADE_REVIEW alerts."""
        # Close via order manager
        try:
            self.order_manager.close_position(pos, exit_price)
        except Exception:
            pos.status = "EXITED"
            pos.current_price = exit_price

        # Compute realized P&L
        qty = pos.lots * 75
        gross = (exit_price - pos.entry_price) * qty
        from .execution import CostModel
        cost = CostModel().cost_for_round_trip(pos.entry_price, exit_price, qty).total
        net = gross - cost

        # Finalize MAE/MFE
        mae_mfe_record = self.mae_mfe.finalize(pos, exit_price, net)

        # Emit EXIT alert
        self.notifier.send_alert(
            AlertType.EXIT,
            f"Position Exited — {pos.strategy.value}",
            fields={
                "Position": pos.option.symbol,
                "Entry": f"₹{pos.entry_price:.2f}",
                "Exit": f"₹{exit_price:.2f}",
                "Lots": pos.lots,
                "Gross P&L": f"₹{gross:+,.0f}",
                "Charges": f"₹{cost:,.0f}",
                "Net P&L": f"₹{net:+,.0f}",
                "Exit Reason": reason,
                "Spot at Exit": f"₹{snapshot.index.ltp:,.0f}",
            },
            description=f"Position closed via **{reason}**. Net P&L: ₹{net:+,.0f} after ₹{cost:,.0f} charges.",
        )

        # Emit TRADE_REVIEW alert with MAE/MFE analysis
        review_lines = [
            f"Strategy: {pos.strategy.value}",
            f"Entry: ₹{pos.entry_price:.2f} → Exit: ₹{exit_price:.2f}",
            f"Gross P&L: ₹{gross:+,.0f}",
            f"Charges: ₹{cost:,.0f}",
            f"Net P&L: ₹{net:+,.0f}",
            f"MAE (max adverse): ₹{mae_mfe_record.mae_inr:+,.0f} ({mae_mfe_record.mae_pct_of_premium*100:.1f}% of premium)",
            f"MFE (max favourable): ₹{mae_mfe_record.mfe_inr:+,.0f} ({mae_mfe_record.mfe_pct_of_premium*100:.1f}% of premium)",
            f"Capture rate: {mae_mfe_record.capture_rate*100:.0f}% of MFE",
            f"Exit reason: {reason}",
        ]
        if thesis_score:
            review_lines.append(f"Initial thesis composite: —")
            review_lines.append(f"Exit thesis composite: {thesis_score.composite:.0f}/100 ({thesis_score.state.value})")
            review_lines.append(f"Direction: {thesis_score.direction}")

        self.notifier.send_alert(
            AlertType.TRADE_REVIEW,
            f"Trade Review — {pos.strategy.value} ({'WIN' if net > 0 else 'LOSS'})",
            fields={
                "Position": pos.option.symbol,
                "Net P&L": f"₹{net:+,.0f}",
                "MAE": f"₹{mae_mfe_record.mae_inr:+,.0f}",
                "MFE": f"₹{mae_mfe_record.mfe_inr:+,.0f}",
                "Capture": f"{mae_mfe_record.capture_rate*100:.0f}%",
                "Exit Reason": reason,
            },
            description="\n".join(review_lines),
        )

        # Reset trackers
        self.thesis_tracker = None
        self._last_alerted_setup = None

    def _send_entry_alert(self, option, lots, fill_price, eval_, risk_decision, snapshot, regime,
                          long_leg=None, short_leg=None, net_debit=None,
                          spread_width=None, max_loss=None, max_gain=None, breakeven=None):
        """Send the ENTRY alert to Discord (supports both single-leg and spread)."""
        is_spread = (long_leg is not None and short_leg is not None)
        fields = {
            "Strategy": eval_.strategy.value,
            "Direction": eval_.direction or "NEUTRAL",
            "Lots": str(lots),
            "Fill Price": f"₹{fill_price:.2f}",
            "Premium Paid": f"₹{fill_price * lots * 75:,.0f}",
            "Stop Loss": f"₹{risk_decision.stop_loss:.2f}" if risk_decision.stop_loss else "—",
            "Take Profit": f"₹{risk_decision.take_profit:.2f}" if risk_decision.take_profit else "—",
            "Expected Net": f"₹{eval_.expected_net_value:,.0f}",
            "Confidence": f"{eval_.confidence_score*100:.0f}%",
            "Risk/Reward": f"{eval_.risk_reward:.2f}",
            "NIFTY Spot": f"₹{snapshot.index.ltp:,.0f}",
            "Regime": regime.market_regime.value,
            "Volatility": regime.volatility_regime.value,
        }
        if is_spread:
            fields.update({
                "Structure": "Bull Call Spread" if eval_.direction == "BULLISH" else "Bear Put Spread",
                "Long Leg": f"{long_leg.symbol} @ ₹{long_leg.ltp:.2f} (strike {long_leg.strike})",
                "Short Leg": f"{short_leg.symbol} @ ₹{short_leg.ltp:.2f} (strike {short_leg.strike})",
                "Net Debit": f"₹{net_debit:.2f}/unit",
                "Spread Width": f"{spread_width}pt",
                "Max Loss": f"₹{max_loss:,.0f}" if max_loss else "—",
                "Max Gain": f"₹{max_gain:,.0f}" if max_gain else "—",
                "Breakeven": f"₹{breakeven:,.0f}" if breakeven else "—",
            })
            description = (
                f"Entered {lots} lot(s) of "
                f"{'Bull Call Spread' if eval_.direction == 'BULLISH' else 'Bear Put Spread'} "
                f"at ₹{net_debit:.2f}/unit net debit. "
                f"Max loss ₹{max_loss:,.0f}, max gain ₹{max_gain:,.0f}, breakeven ₹{breakeven:,.0f}. "
                f"3-layer protection + thesis tracker + MAE/MFE are now active."
            )
        else:
            fields.update({
                "Option": option.symbol,
                "Strike": str(option.strike),
                "Type": option.option_type.value,
                "IV": f"{option.iv*100:.1f}%" if option.iv else "—",
                "Delta": f"{option.delta:.2f}" if option.delta else "—",
            })
            description = (
                f"Entered {lots} lot(s) of {option.symbol} at ₹{fill_price:.2f}. "
                f"3-layer protection + thesis tracker + MAE/MFE are now active."
            )

        self.notifier.send_alert(
            AlertType.ENTRY,
            f"Position Entered — {eval_.strategy.value}",
            fields=fields,
            description=description,
        )

    # ---- helpers ----
    def _maybe_reset_day(self, now: datetime) -> None:
        if self._day_initialised is None or self._day_initialised.date() != now.date():
            self.risk.reset_day(now)
            self._day_initialised = now

    def _no_trade_decision(
        self,
        snapshot: MarketSnapshot,
        now: datetime,
        reason: str,
        emergency: Optional[str] = None,
    ) -> Decision:
        regime_assessment = self.regime.assess(snapshot)
        reasons = [reason]
        if emergency:
            reasons.append(f"EMERGENCY: {emergency}")
        return Decision(
            timestamp=now,
            action=DecisionAction.NO_TRADE,
            strategy=StrategyName.NO_TRADE,
            regime=regime_assessment.market_regime,
            volatility=regime_assessment.volatility_regime,
            option=None,
            lots=0,
            premium_per_lot=0.0,
            total_premium=0.0,
            expected_net_value=0.0,
            expected_risk=0.0,
            confidence=1.0,
            reasons=reasons,
            explainability_block=f"NO TRADE: {reason}",
        )

    @staticmethod
    def _snapshot_summary(snapshot: MarketSnapshot) -> dict:
        return {
            "timestamp": snapshot.timestamp.isoformat(),
            "spot": snapshot.index.ltp,
            "data_valid": snapshot.data_valid,
            "data_invalid_reason": snapshot.data_invalid_reason,
            "time_bucket": snapshot.time_bucket.value if snapshot.time_bucket else None,
            "vix": snapshot.india_vix.ltp if snapshot.india_vix else None,
            "chain_size": len(snapshot.option_chain),
        }

    @staticmethod
    def _explainability(
        snapshot, regime, evaluation, opt_sel, risk_decision, action, lots,
        confirmation=None,
    ) -> str:
        lines = [
            "========== DECISION EXPLAINABILITY ==========",
            f"TIMESTAMP     : {snapshot.timestamp.isoformat()}",
            f"DATA VALID    : {snapshot.data_valid}",
            f"REGIME        : {regime.market_regime.value}",
            f"VOLATILITY    : {regime.volatility_regime.value}",
            f"REGIME CONFID : {regime.confidence:.2f}",
        ]
        if confirmation is not None:
            lines.append("")
            lines.append("----- CROSS-MARKET CONFIRMATION -----")
            lines.append(f"SCORE         : {confirmation.score:+.2f}  (range -1 to +1)")
            lines.append(f"VIX           : {confirmation.vix_valuation.vix:.2f} ({confirmation.vix_valuation.valuation})")
            if confirmation.vix_valuation.iv_vix_gap is not None:
                lines.append(f"IV-VIX GAP    : {confirmation.vix_valuation.iv_vix_gap*100:+.2f}%")
            lines.append(f"CE OI CLASS   : {confirmation.oi_classification.ce_classification}")
            lines.append(f"PE OI CLASS   : {confirmation.oi_classification.pe_classification}")
            if confirmation.oi_classification.call_wall:
                lines.append(f"CALL WALL     : {confirmation.oi_classification.call_wall} (resistance)")
            if confirmation.oi_classification.put_wall:
                lines.append(f"PUT WALL     : {confirmation.oi_classification.put_wall} (support)")
            lines.append(f"FUTURES BASIS : {confirmation.futures_basis.interpretation}")
            if confirmation.futures_basis.basis_pct is not None:
                lines.append(f"  basis%      : {confirmation.futures_basis.basis_pct:+.3f}%")
            lines.append(f"BANK NIFTY    : {confirmation.banknifty_confirmation.correlation_state}")
            if confirmation.banknifty_confirmation.banknifty_change_pct is not None:
                lines.append(f"  BN change%  : {confirmation.banknifty_confirmation.banknifty_change_pct:+.2f}%")
            lines.append(f"CORR REGIME   : {confirmation.correlation_regime.regime}")
            lines.append("CONFIRMATION REASONS:")
            for r in confirmation.reasons:
                lines.append(f"  - {r}")
        lines.append("")
        lines.append(f"STRATEGY       : {evaluation.strategy.value if evaluation else 'NO_TRADE'}")
        lines.append(f"ELIGIBLE       : {evaluation.eligible if evaluation else False}")
        lines.append(f"EXPECTED NET   : {evaluation.expected_net_value:.0f} INR" if evaluation else "EXPECTED NET   : 0")
        lines.append(f"CONFIDENCE     : {evaluation.confidence_score:.2f}" if evaluation else "CONFIDENCE     : 0")
        lines.append(f"RISK/REWARD    : {evaluation.risk_reward:.2f}" if evaluation else "RISK/REWARD    : 0")
        lines.append("")
        lines.append("REASONS:")
        if evaluation:
            for r in evaluation.reasons:
                lines.append(f"  - {r}")
        if opt_sel and opt_sel.selected and opt_sel.option:
            lines.append("")
            lines.append(f"OPTION         : {opt_sel.option.symbol}")
            lines.append(f"STRIKE         : {opt_sel.option.strike}")
            lines.append(f"LTP            : {opt_sel.option.ltp}")
            lines.append(f"IV             : {(opt_sel.option.iv or 0)*100:.1f}%")
            lines.append(f"DELTA          : {opt_sel.option.delta}")
            lines.append(f"OPTION SCORE   : {opt_sel.score:.2f}")
        if risk_decision:
            lines.append("")
            lines.append(f"RISK ALLOWED   : {risk_decision.allowed}")
            lines.append(f"MAX LOTS       : {risk_decision.max_lots}")
            lines.append(f"STOP LOSS      : {risk_decision.stop_loss}")
            lines.append(f"TAKE PROFIT    : {risk_decision.take_profit}")
        lines.append("")
        lines.append(f"FINAL ACTION   : {action.value}")
        lines.append(f"LOTS           : {lots}")
        lines.append("============================================")
        return "\n".join(lines)
