"use client";

import { useQuery } from "@tanstack/react-query";
import { api, fmtINR, fmtPct, fmtTime, regimeColor, volColor, actionColor } from "@/lib/api";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { ScrollArea } from "@/components/ui/scroll-area";
import { Activity, TrendingUp, TrendingDown, Pause, AlertTriangle, Wifi, WifiOff } from "lucide-react";
import { ConfirmationPanel } from "./ConfirmationPanel";
import { useEffect, useState } from "react";

export function LiveDashboard() {
  // Poll decision every 10s
  const { data: health } = useQuery({
    queryKey: ["health"],
    queryFn: api.health,
    refetchInterval: 60000,
    staleTime: 30000,
  });

  const { data: snapshot } = useQuery({
    queryKey: ["snapshot"],
    queryFn: api.snapshot,
    refetchInterval: 60000,
    staleTime: 30000,
  });

  const { data: decision } = useQuery({
    queryKey: ["decision"],
    queryFn: api.decision,
    refetchInterval: 60000,
    staleTime: 30000,
  });

  const { data: status } = useQuery({
    queryKey: ["status"],
    queryFn: api.status,
    refetchInterval: 60000,
    staleTime: 30000,
  });

  const { data: performance } = useQuery({
    queryKey: ["performance"],
    queryFn: api.performance,
    refetchInterval: 120000,
    staleTime: 60000,
  });

  const spotChange = snapshot?.index.ltp && snapshot?.index.prev_close
    ? snapshot.index.ltp - snapshot.index.prev_close
    : 0;
  const spotChangePct = snapshot?.index.ltp && snapshot?.index.prev_close
    ? (spotChange / snapshot.index.prev_close) * 100
    : 0;
  const isUp = spotChange >= 0;

  return (
    <div className="space-y-4">
      {/* Top metric cards */}
      <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-4 gap-4">
        <Card>
          <CardHeader className="flex flex-row items-center justify-between pb-2">
            <CardTitle className="text-sm font-medium text-muted-foreground">
              NIFTY 50
            </CardTitle>
            {health?.broker_connected ? (
              <Wifi className="h-4 w-4 text-emerald-500" />
            ) : (
              <WifiOff className="h-4 w-4 text-rose-500" />
            )}
          </CardHeader>
          <CardContent>
            <div className="text-2xl font-bold tabular-nums">
              {snapshot?.index.ltp
                ? snapshot.index.ltp.toLocaleString("en-IN", { maximumFractionDigits: 2 })
                : "—"}
            </div>
            <div className={`flex items-center gap-1 text-xs ${isUp ? "text-emerald-600" : "text-rose-600"}`}>
              {isUp ? <TrendingUp className="h-3 w-3" /> : <TrendingDown className="h-3 w-3" />}
              <span className="tabular-nums">
                {isUp ? "+" : ""}{spotChange.toFixed(2)} ({fmtPct(spotChangePct)})
              </span>
            </div>
            <div className="mt-2 text-[10px] text-muted-foreground">
              Prev: {snapshot?.index.prev_close?.toFixed(2) ?? "—"}
              {" · "}VWAP: {snapshot?.index.vwap?.toFixed(2) ?? "—"}
            </div>
          </CardContent>
        </Card>

        <Card>
          <CardHeader className="flex flex-row items-center justify-between pb-2">
            <CardTitle className="text-sm font-medium text-muted-foreground">
              Market Regime
            </CardTitle>
            <Activity className="h-4 w-4 text-muted-foreground" />
          </CardHeader>
          <CardContent>
            <Badge className={`${regimeColor[decision?.regime ?? "NEUTRAL"]} text-xs`}>
              {decision?.regime ?? "—"}
            </Badge>
            <div className="mt-2 text-xs text-muted-foreground">
              ADX: {snapshot?.index.adx?.toFixed(1) ?? "—"}
              {" · "}RSI: {snapshot?.index.rsi?.toFixed(1) ?? "—"}
            </div>
            <div className="text-[10px] text-muted-foreground mt-1">
              Vol: <Badge variant="outline" className={`${volColor[decision?.volatility ?? "NORMAL_VOL"]} text-[10px] ml-1`}>
                {decision?.volatility ?? "—"}
              </Badge>
            </div>
          </CardContent>
        </Card>

        <Card>
          <CardHeader className="flex flex-row items-center justify-between pb-2">
            <CardTitle className="text-sm font-medium text-muted-foreground">
              Last Decision
            </CardTitle>
            {decision?.action === "NO_TRADE"
              ? <Pause className="h-4 w-4 text-amber-500" />
              : decision?.action === "ENTER"
              ? <TrendingUp className="h-4 w-4 text-emerald-500" />
              : <AlertTriangle className="h-4 w-4 text-rose-500" />}
          </CardHeader>
          <CardContent>
            <Badge className={`${actionColor[decision?.action ?? "NO_TRADE"]} text-xs`}>
              {decision?.action ?? "—"}
            </Badge>
            <div className="mt-2 text-xs text-muted-foreground">
              Strategy: <span className="font-medium text-foreground">{decision?.strategy ?? "—"}</span>
            </div>
            <div className="text-[10px] text-muted-foreground mt-1">
              Conf: {decision ? `${(decision.confidence * 100).toFixed(0)}%` : "—"}
              {" · "}Net: {decision ? fmtINR(decision.expected_net_value) : "—"}
            </div>
          </CardContent>
        </Card>

        <Card>
          <CardHeader className="flex flex-row items-center justify-between pb-2">
            <CardTitle className="text-sm font-medium text-muted-foreground">
              Session Status
            </CardTitle>
            {health?.market_open
              ? <Activity className="h-4 w-4 text-emerald-500 animate-pulse" />
              : <Pause className="h-4 w-4 text-slate-400" />}
          </CardHeader>
          <CardContent>
            <Badge variant={health?.market_open ? "default" : "secondary"} className="text-xs">
              {health?.market_open ? "MARKET OPEN" : "MARKET CLOSED"}
            </Badge>
            <div className="mt-2 text-xs text-muted-foreground">
              IST: {health ? new Date(health.ist_time).toLocaleTimeString("en-IN") : "—"}
            </div>
            <div className="text-[10px] text-muted-foreground mt-1">
              Capital: ₹{((health?.capital ?? 0) / 100000).toFixed(1)}L
            </div>
          </CardContent>
        </Card>
      </div>

      {/* Two-column: Decision + Snapshot */}
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
        <Card className="lg:col-span-1">
          <CardHeader>
            <CardTitle className="text-base">Latest Decision</CardTitle>
          </CardHeader>
          <CardContent className="space-y-3">
            {decision ? (
              <>
                <div className="flex items-center gap-2 flex-wrap">
                  <Badge className={actionColor[decision.action]}>{decision.action}</Badge>
                  <Badge variant="outline">{decision.strategy}</Badge>
                  <Badge className={regimeColor[decision.regime]}>{decision.regime}</Badge>
                  <Badge className={volColor[decision.volatility]}>{decision.volatility}</Badge>
                </div>
                <div className="grid grid-cols-2 gap-3 text-sm">
                  <MetricCell label="Expected Net" value={fmtINR(decision.expected_net_value)} />
                  <MetricCell label="Expected Risk" value={fmtINR(decision.expected_risk)} />
                  <MetricCell label="Confidence" value={`${(decision.confidence * 100).toFixed(0)}%`} />
                  <MetricCell label="Lots" value={String(decision.lots)} />
                </div>
                {decision.option && (
                  <div className="rounded-md border p-2 text-xs">
                    <div className="font-semibold">{decision.option.symbol}</div>
                    <div className="text-muted-foreground mt-1">
                      Strike {decision.option.strike} · {decision.option.option_type} · LTP ₹{decision.option.ltp}
                    </div>
                    <div className="text-muted-foreground">
                      IV {decision.option.iv ? `${(decision.option.iv * 100).toFixed(1)}%` : "—"}
                      {" · "}Delta {decision.option.delta?.toFixed(2) ?? "—"}
                      {" · "}Exp {new Date(decision.option.expiry).toLocaleDateString("en-IN", { day: "2-digit", month: "short" })}
                    </div>
                  </div>
                )}
                <div>
                  <div className="text-xs font-medium mb-1">Reasons</div>
                  <ScrollArea className="max-h-48">
                    <ul className="text-xs space-y-1 pr-2">
                      {decision.reasons.map((r, i) => (
                        <li key={i} className="text-muted-foreground">— {r}</li>
                      ))}
                    </ul>
                  </ScrollArea>
                </div>
              </>
            ) : (
              <div className="text-sm text-muted-foreground">Loading decision…</div>
            )}
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <CardTitle className="text-base">Market Snapshot</CardTitle>
          </CardHeader>
          <CardContent className="space-y-3">
            {snapshot ? (
              <>
                <div className={`rounded-md p-2 text-xs ${snapshot.data_valid ? "bg-emerald-50 text-emerald-700 dark:bg-emerald-950/40 dark:text-emerald-300" : "bg-amber-50 text-amber-700 dark:bg-amber-950/40 dark:text-amber-300"}`}>
                  {snapshot.data_valid
                    ? "✓ Data valid — live broker feed"
                    : `⚠ ${snapshot.data_invalid_reason ?? "Data unavailable"}`}
                </div>
                <div className="grid grid-cols-3 gap-3 text-sm">
                  <MetricCell label="Open" value={snapshot.index.open?.toFixed(2)} />
                  <MetricCell label="High" value={snapshot.index.high?.toFixed(2)} />
                  <MetricCell label="Low" value={snapshot.index.low?.toFixed(2)} />
                  <MetricCell label="ATR" value={snapshot.index.atr?.toFixed(2)} />
                  <MetricCell label="VWAP" value={snapshot.index.vwap?.toFixed(2)} />
                  <MetricCell label="Volume" value={snapshot.index.volume.toLocaleString("en-IN")} />
                  <MetricCell label="EMA9" value={snapshot.index.ema_fast?.toFixed(2)} />
                  <MetricCell label="EMA21" value={snapshot.index.ema_mid?.toFixed(2)} />
                  <MetricCell label="EMA50" value={snapshot.index.ema_slow?.toFixed(2)} />
                </div>
                <div>
                  <div className="text-xs text-muted-foreground mb-1">
                    Option chain ({snapshot.option_chain_size} contracts)
                  </div>
                  {snapshot.option_chain_sample.length > 0 ? (
                    <ScrollArea className="h-32 rounded border">
                      <table className="w-full text-xs">
                        <thead className="sticky top-0 bg-muted">
                          <tr>
                            <th className="text-left p-1.5">Symbol</th>
                            <th className="text-right p-1.5">Strike</th>
                            <th className="text-center p-1.5">Type</th>
                            <th className="text-right p-1.5">LTP</th>
                            <th className="text-right p-1.5">IV</th>
                            <th className="text-right p-1.5">Vol</th>
                          </tr>
                        </thead>
                        <tbody>
                          {snapshot.option_chain_sample.map((q, i) => (
                            <tr key={i} className="border-b">
                              <td className="p-1.5 font-mono text-[10px]">{q.symbol}</td>
                              <td className="p-1.5 text-right tabular-nums">{q.strike}</td>
                              <td className="p-1.5 text-center">
                                <Badge variant={q.option_type === "CE" ? "default" : "destructive"} className="text-[10px]">
                                  {q.option_type}
                                </Badge>
                              </td>
                              <td className="p-1.5 text-right tabular-nums">₹{q.ltp.toFixed(2)}</td>
                              <td className="p-1.5 text-right tabular-nums">{q.iv ? `${(q.iv * 100).toFixed(1)}%` : "—"}</td>
                              <td className="p-1.5 text-right tabular-nums">{q.volume.toLocaleString("en-IN")}</td>
                            </tr>
                          ))}
                        </tbody>
                      </table>
                    </ScrollArea>
                  ) : (
                    <div className="text-xs text-muted-foreground">No chain data (market closed)</div>
                  )}
                </div>
              </>
            ) : (
              <div className="text-sm text-muted-foreground">Loading snapshot…</div>
            )}
          </CardContent>
        </Card>
      </div>

      {/* Cross-market confirmation panel */}
      <ConfirmationPanel />

      {/* Open positions + Performance summary */}
      <div className="grid grid-cols-1 lg:grid-cols-3 gap-4">
        <Card className="lg:col-span-1">
          <CardHeader>
            <CardTitle className="text-base">Open Positions</CardTitle>
          </CardHeader>
          <CardContent>
            {status && status.open_positions.length > 0 ? (
              <div className="space-y-2">
                {status.open_positions.map((p, i) => (
                  <div key={i} className="rounded-md border p-2 text-xs">
                    <div className="flex justify-between items-center">
                      <span className="font-semibold">{p.option}</span>
                      <Badge variant="outline" className="text-[10px]">{p.strategy}</Badge>
                    </div>
                    <div className="text-muted-foreground mt-1">
                      {p.lots} lots · ₹{p.entry_price} → ₹{p.current_price ?? "—"}
                    </div>
                    <div className={p.unrealised_pnl >= 0 ? "text-emerald-600 mt-1 font-medium" : "text-rose-600 mt-1 font-medium"}>
                      Unrealised: {fmtINR(p.unrealised_pnl, true)}
                    </div>
                  </div>
                ))}
              </div>
            ) : (
              <div className="text-xs text-muted-foreground">
                No open positions. Engine is in cash or evaluating signals.
              </div>
            )}
          </CardContent>
        </Card>

        <Card className="lg:col-span-2">
          <CardHeader>
            <CardTitle className="text-base">Performance Summary</CardTitle>
          </CardHeader>
          <CardContent>
            {performance && performance.total_trades > 0 ? (
              <div className="grid grid-cols-2 md:grid-cols-3 lg:grid-cols-4 gap-4">
                <MetricCell label="Total Trades" value={String(performance.total_trades)} />
                <MetricCell label="Win Rate" value={`${(performance.win_rate * 100).toFixed(1)}%`} />
                <MetricCell label="Net P&L" value={fmtINR(performance.net_pnl, true)} />
                <MetricCell label="Expectancy" value={fmtINR(performance.expectancy, true)} />
                <MetricCell label="Profit Factor" value={performance.profit_factor.toFixed(2)} />
                <MetricCell label="Max Drawdown" value={fmtINR(performance.max_drawdown)} />
                <MetricCell label="Avg Winner" value={fmtINR(performance.avg_winner)} />
                <MetricCell label="Avg Loser" value={fmtINR(performance.avg_loser)} />
                <MetricCell label="Charges Paid" value={fmtINR(performance.total_charges)} />
                <MetricCell label="Slippage" value={fmtINR(performance.total_slippage)} />
                <MetricCell label="Avg Hold (min)" value={performance.avg_holding_minutes.toFixed(0)} />
                <MetricCell label="Capital" value={fmtINR(health?.capital ?? 1000000)} />
              </div>
            ) : (
              <div className="text-sm text-muted-foreground">
                No closed trades yet — run a backtest from the Backtest tab to populate this section.
              </div>
            )}
          </CardContent>
        </Card>
      </div>
    </div>
  );
}

function MetricCell({ label, value }: { label: string; value: string | undefined | null }) {
  return (
    <div>
      <div className="text-[10px] text-muted-foreground uppercase tracking-wide">{label}</div>
      <div className="text-sm font-semibold tabular-nums">{value ?? "—"}</div>
    </div>
  );
}
