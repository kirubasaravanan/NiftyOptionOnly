"""CLI entry point for the Adaptive NIFTY Options Engine.

Usage:
    python -m nifty_engine.cli run --mode paper          # one decision cycle
    python -m nifty_engine.cli watch --interval 30       # loop every 30s
    python -m nifty_engine.cli status                    # show risk/journal status
    python -m nifty_engine.cli report                    # performance summary
"""
from __future__ import annotations

import argparse
import os
import sys
import time
from pathlib import Path

from dotenv import load_dotenv

# Load .env if present — never hard-code credentials
load_dotenv()

from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.json import JSON

from .engine import Engine
from .models import RunMode
from .utils.time_utils import ist_now


console = Console()


def cmd_run(args) -> int:
    mode = RunMode(args.mode)
    engine = Engine(mode=mode)
    decision = engine.run_cycle()

    _print_decision(decision)
    return 0


def cmd_watch(args) -> int:
    mode = RunMode(args.mode)
    engine = Engine(mode=mode)
    interval = max(5, args.interval)

    console.print(Panel.fit(
        f"[bold cyan]NIFTY Engine — WATCH mode[/bold cyan]\n"
        f"Mode: {mode.value} | Interval: {interval}s | Ctrl+C to stop",
        border_style="cyan",
    ))

    try:
        while True:
            decision = engine.run_cycle()
            _print_decision(decision, compact=True)
            console.print(f"[dim]--- next cycle in {interval}s ---[/dim]")
            time.sleep(interval)
    except KeyboardInterrupt:
        console.print("\n[yellow]Stopping — flush & exit.[/yellow]")
        return 0


def cmd_status(args) -> int:
    engine = Engine(mode=RunMode.PAPER)
    console.print(Panel.fit("[bold]Engine Status[/bold]", border_style="cyan"))
    console.print(JSON.from_data({"risk": engine.risk.status()}))
    console.print(f"\nOpen positions: {engine.order_manager.open_positions_count()}")
    for p in engine.order_manager.positions:
        if p.status == "OPEN":
            console.print(f"  - {p.option.symbol} x{p.lots} lots @ {p.entry_price} | stop={p.stop_loss} | target={p.take_profit}")
    return 0


def cmd_report(args) -> int:
    from .journal import PerformanceAnalytics, TradeLogger
    pa = PerformanceAnalytics(TradeLogger())
    report = pa.report()

    table = Table(title="Performance Report")
    table.add_column("Metric", style="cyan")
    table.add_column("Value", style="white")
    table.add_row("Total Trades", str(report.total_trades))
    table.add_row("Win Rate", f"{report.win_rate*100:.1f}%")
    table.add_row("Net P&L", f"₹{report.net_pnl:,.0f}")
    table.add_row("Gross P&L", f"₹{report.gross_pnl:,.0f}")
    table.add_row("Total Charges", f"₹{report.total_charges:,.0f}")
    table.add_row("Total Slippage", f"₹{report.total_slippage:,.0f}")
    table.add_row("Avg Winner", f"₹{report.avg_winner:,.0f}")
    table.add_row("Avg Loser", f"₹{report.avg_loser:,.0f}")
    table.add_row("Expectancy", f"₹{report.expectancy:,.0f}")
    table.add_row("Profit Factor", f"{report.profit_factor:.2f}")
    table.add_row("Max Drawdown", f"₹{report.max_drawdown:,.0f}")
    table.add_row("Avg Holding (min)", f"{report.avg_holding_minutes:.1f}")
    console.print(table)
    return 0


def _print_decision(decision, compact: bool = False) -> None:
    color = {
        "ENTER": "bold green",
        "NO_TRADE": "dim yellow",
        "EXIT": "red",
        "TAKE_PROFIT": "magenta",
        "HOLD": "cyan",
        "MOVE_STOP": "blue",
    }.get(decision.action.value, "white")

    if compact:
        ts = decision.timestamp.strftime("%H:%M:%S")
        console.print(
            f"[{ts}] [{color}]{decision.action.value:10s}[/] | "
            f"{decision.strategy.value:12s} | "
            f"regime={decision.regime.value:12s} | "
            f"vol={decision.volatility.value:14s} | "
            f"net≈₹{decision.expected_net_value:.0f} | "
            f"conf={decision.confidence:.2f}"
        )
        return

    console.print(Panel.fit(
        f"[bold]NIFTY Decision @ {decision.timestamp.strftime('%Y-%m-%d %H:%M:%S')}[/bold]",
        border_style="cyan",
    ))

    summary = Table(show_header=False, box=None)
    summary.add_column("k", style="cyan")
    summary.add_column("v", style="white")
    summary.add_row("ACTION", f"[{color}]{decision.action.value}[/]")
    summary.add_row("STRATEGY", decision.strategy.value)
    summary.add_row("REGIME", decision.regime.value)
    summary.add_row("VOLATILITY", decision.volatility.value)
    summary.add_row("LOTS", str(decision.lots))
    summary.add_row("EXPECTED NET", f"₹{decision.expected_net_value:.0f}")
    summary.add_row("EXPECTED RISK", f"₹{decision.expected_risk:.0f}")
    summary.add_row("CONFIDENCE", f"{decision.confidence:.2f}")
    if decision.option:
        summary.add_row("OPTION", decision.option.symbol)
        summary.add_row("STRIKE", str(decision.option.strike))
        summary.add_row("LTP", str(decision.option.ltp))
    if decision.stop_loss:
        summary.add_row("STOP LOSS", str(decision.stop_loss))
    if decision.take_profit:
        summary.add_row("TAKE PROFIT", str(decision.take_profit))
    console.print(summary)

    if decision.reasons:
        console.print("\n[bold]Reasons:[/bold]")
        for r in decision.reasons:
            console.print(f"  - {r}")

    if decision.explainability_block:
        console.print("\n[dim]" + decision.explainability_block + "[/dim]")


def main() -> int:
    parser = argparse.ArgumentParser(prog="nifty_engine")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_run = sub.add_parser("run", help="Run one decision cycle")
    p_run.add_argument("--mode", choices=["backtest", "paper", "live"], default="paper")
    p_run.set_defaults(func=cmd_run)

    p_watch = sub.add_parser("watch", help="Loop continuously")
    p_watch.add_argument("--mode", choices=["backtest", "paper", "live"], default="paper")
    p_watch.add_argument("--interval", type=int, default=30, help="Seconds between cycles")
    p_watch.set_defaults(func=cmd_watch)

    p_status = sub.add_parser("status", help="Show risk + positions status")
    p_status.set_defaults(func=cmd_status)

    p_report = sub.add_parser("report", help="Show performance analytics")
    p_report.set_defaults(func=cmd_report)

    args = parser.parse_args()
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
