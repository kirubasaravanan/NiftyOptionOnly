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
    RiskEngine, StrategySelector,
)
from .execution import OrderManager, OrderRequest, Reconciler
from .journal import DecisionLogger, TradeLogger
from .models import (
    Decision, DecisionAction, MarketSnapshot, RunMode, StrategyName,
)
from .utils.time_utils import ist_now, is_trading_allowed_now


class Engine:
    """One instance = one trading session."""

    def __init__(
        self,
        mode: RunMode = RunMode.PAPER,
        capital: Optional[float] = None,
        runs_dir: str = "/home/z/my-project/runs",
    ) -> None:
        self.mode = mode
        cap_env = os.environ.get("ENGINE_CAPITAL")
        self.capital = float(capital if capital is not None else (cap_env or 1_000_000))

        # --- wiring ---
        self.broker = DhanBroker()
        self.cache = MarketCache(ttl_seconds=5.0)
        self.regime = RegimeEngineRunner()
        self.selector = StrategySelector()
        self.option_selector = OptionSelector()
        self.risk = RiskEngine(capital=self.capital)
        self.position_manager = PositionManager()
        self.order_manager = OrderManager(mode=mode, broker=self.broker if mode == RunMode.LIVE else None)
        self.reconciler = Reconciler()

        self.trade_logger = TradeLogger(runs_dir=runs_dir)
        self.decision_logger = DecisionLogger(runs_dir=runs_dir)

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

        # ---- 5. select strategy ----
        chosen_eval, all_evals = self.selector.select(snapshot, regime_assessment)

        # ---- 6. option selection ----
        opt_sel = self.option_selector.select(snapshot, chosen_eval)

        # ---- 7. risk evaluation ----
        if chosen_eval.eligible and opt_sel.selected:
            risk_decision = self.risk.evaluate(
                snapshot, chosen_eval, opt_sel.option,
                open_positions=self.order_manager.open_positions_count(),
                now=now,
            )
        else:
            risk_decision = None

        # ---- 8. compose decision ----
        if (chosen_eval.eligible
                and opt_sel.selected
                and risk_decision is not None
                and risk_decision.allowed):
            action = DecisionAction.ENTER
            option = opt_sel.option
            lots = risk_decision.max_lots
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
                option=option,
                side="BUY",
                lots=lots,
                strategy=chosen_eval.strategy.value,
                stop_loss=stop,
                take_profit=target,
                reason=chosen_eval.strategy.value,
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
        else:
            action = DecisionAction.NO_TRADE
            option = None
            lots = 0
            premium_per_lot = 0.0
            total_premium = 0.0
            stop = None
            target = None
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
            lots=lots,
            premium_per_lot=premium_per_lot,
            total_premium=total_premium,
            expected_net_value=chosen_eval.expected_net_value if chosen_eval else 0.0,
            expected_risk=risk_decision.max_premium_exposure if risk_decision else 0.0,
            confidence=confidence,
            stop_loss=stop,
            take_profit=target,
            reasons=reasons,
            explainability_block=self._explainability(
                snapshot, regime_assessment, chosen_eval, opt_sel, risk_decision, action, lots,
            ),
        )

        # ---- 10. journal ----
        self.decision_logger.log_decision(decision, self._snapshot_summary(snapshot))
        return decision

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
    ) -> str:
        lines = [
            "========== DECISION EXPLAINABILITY ==========",
            f"TIMESTAMP     : {snapshot.timestamp.isoformat()}",
            f"DATA VALID    : {snapshot.data_valid}",
            f"REGIME        : {regime.market_regime.value}",
            f"VOLATILITY    : {regime.volatility_regime.value}",
            f"REGIME CONFID : {regime.confidence:.2f}",
            "",
            f"STRATEGY       : {evaluation.strategy.value if evaluation else 'NO_TRADE'}",
            f"ELIGIBLE       : {evaluation.eligible if evaluation else False}",
            f"EXPECTED NET   : {evaluation.expected_net_value:.0f} INR" if evaluation else "EXPECTED NET   : 0",
            f"CONFIDENCE     : {evaluation.confidence_score:.2f}" if evaluation else "CONFIDENCE     : 0",
            f"RISK/REWARD    : {evaluation.risk_reward:.2f}" if evaluation else "RISK/REWARD    : 0",
            "",
            "REASONS:",
        ]
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
