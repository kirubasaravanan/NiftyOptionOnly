"use client";

import { useState } from "react";
import { useMutation } from "@tanstack/react-query";
import { api, fmtINR, fmtPct, type BacktestResult } from "@/lib/api";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Badge } from "@/components/ui/badge";
import { ScrollArea } from "@/components/ui/scroll-area";
import { LineChart, Line, XAxis, YAxis, Tooltip, ResponsiveContainer, CartesianGrid, BarChart, Bar, ReferenceLine } from "recharts";
import { Play, Loader2, AlertCircle, CheckCircle2 } from "lucide-react";

const DEFAULT_START = "2024-01-01";
const DEFAULT_END = "2024-07-31";

export function BacktestPanel() {
  const [start, setStart] = useState(DEFAULT_START);
  const [end, setEnd] = useState(DEFAULT_END);
  const [capital, setCapital] = useState("1000000");
  const [result, setResult] = useState<BacktestResult | null>(null);

  const mutation = useMutation({
    mutationFn: () => api.backtest({
      start_date: start,
      end_date: end,
      capital: parseFloat(capital),
      interval: "day",
    }),
    onSuccess: (data) => setResult(data),
    onError: (err) => {
      console.error(err);
    },
  });

  return (
    <div className="space-y-4">
      <Card>
        <CardHeader>
          <CardTitle className="text-base">Run Backtest</CardTitle>
        </CardHeader>
        <CardContent>
          <div className="grid grid-cols-1 md:grid-cols-4 gap-4 items-end">
            <div>
              <Label htmlFor="start" className="text-xs">Start Date</Label>
              <Input id="start" type="date" value={start} onChange={(e) => setStart(e.target.value)} />
            </div>
            <div>
              <Label htmlFor="end" className="text-xs">End Date</Label>
              <Input id="end" type="date" value={end} onChange={(e) => setEnd(e.target.value)} />
            </div>
            <div>
              <Label htmlFor="cap" className="text-xs">Capital (₹)</Label>
              <Input id="cap" type="number" value={capital} onChange={(e) => setCapital(e.target.value)} />
            </div>
            <Button
              onClick={() => mutation.mutate()}
              disabled={mutation.isPending}
            >
              {mutation.isPending ? (
                <>
                  <Loader2 className="h-4 w-4 mr-2 animate-spin" />
                  Running…
                </>
              ) : (
                <>
                  <Play className="h-4 w-4 mr-2" />
                  Run Backtest
                </>
              )}
            </Button>
          </div>
          <p className="text-xs text-muted-foreground mt-3">
            Fetches real NIFTY 50 daily candles via DhanHQ historical API and replays them through the
            same decision pipeline used in paper/live mode. Option chain is synthesised with
            Black-Scholes since DhanHQ does not expose historical option Greeks. Every trade applies
            the full cost model (brokerage + STT + GST + slippage + spread).
          </p>
        </CardContent>
      </Card>

      {mutation.isError && (
        <Card className="border-rose-500">
          <CardContent className="pt-6 flex items-center gap-2 text-rose-700 dark:text-rose-400 text-sm">
            <AlertCircle className="h-4 w-4" />
            Backtest failed: {(mutation.error as Error)?.message ?? "unknown error"}
          </CardContent>
        </Card>
      )}

      {result && (
        <>
          {/* Results summary */}
          <Card>
            <CardHeader>
              <CardTitle className="text-base flex items-center gap-2">
                {result.error ? <AlertCircle className="h-4 w-4 text-rose-500" /> : <CheckCircle2 className="h-4 w-4 text-emerald-500" />}
                Backtest Results
                <span className="text-xs text-muted-foreground ml-auto">
                  {result.config.start_date} → {result.config.end_date}
                </span>
              </CardTitle>
            </CardHeader>
            <CardContent>
              {result.error ? (
                <div className="text-sm text-rose-700 dark:text-rose-400">{result.error}</div>
              ) : (
                <div className="grid grid-cols-2 md:grid-cols-3 lg:grid-cols-6 gap-4">
                  <StatCell label="Final Equity" value={fmtINR(result.final_equity)} />
                  <StatCell label="Total Return" value={fmtPct(result.total_return_pct)} tone={result.total_return_pct >= 0 ? "pos" : "neg"} />
                  <StatCell label="Trades" value={String(result.trade_count)} />
                  <StatCell label="Win Rate" value={`${(result.win_rate * 100).toFixed(1)}%`} />
                  <StatCell label="Expectancy" value={fmtINR(result.expectancy, true)} tone={result.expectancy >= 0 ? "pos" : "neg"} />
                  <StatCell label="Profit Factor" value={result.profit_factor.toFixed(2)} />
                  <StatCell label="Max Drawdown" value={`${result.max_drawdown_pct.toFixed(2)}%`} tone="neg" />
                  <StatCell label="Sharpe" value={result.sharpe.toFixed(2)} />
                  <StatCell label="Charges Paid" value={fmtINR(result.total_charges)} />
                  <StatCell label="Slippage" value={fmtINR(result.total_slippage)} />
                  <StatCell label="Decisions" value={String(result.decisions_count)} />
                  <StatCell label="Capital" value={fmtINR(result.config.capital)} />
                </div>
              )}
            </CardContent>
          </Card>

          {/* Equity curve */}
          {!result.error && result.equity_curve.length > 0 && (
            <Card>
              <CardHeader>
                <CardTitle className="text-base">Equity Curve</CardTitle>
              </CardHeader>
              <CardContent>
                <div className="h-72">
                  <ResponsiveContainer width="100%" height="100%">
                    <LineChart data={result.equity_curve.map(p => ({
                      date: new Date(p.timestamp).toLocaleDateString("en-IN", { day: "2-digit", month: "short" }),
                      equity: p.equity,
                      spot: p.spot,
                    }))}>
                      <CartesianGrid strokeDasharray="3 3" opacity={0.3} />
                      <XAxis dataKey="date" tick={{ fontSize: 11 }} interval="preserveStartEnd" />
                      <YAxis yAxisId="left" tick={{ fontSize: 11 }} tickFormatter={(v) => `₹${(v / 100000).toFixed(1)}L`} />
                      <YAxis yAxisId="right" orientation="right" tick={{ fontSize: 11 }} tickFormatter={(v) => v.toFixed(0)} />
                      <Tooltip
                        formatter={(v: number, name: string) => name === "equity" ? fmtINR(v) : v.toFixed(2)}
                        labelStyle={{ fontSize: 11 }}
                        contentStyle={{ fontSize: 12 }}
                      />
                      <ReferenceLine y={result.config.capital} yAxisId="left" stroke="#94a3b8" strokeDasharray="4 4" />
                      <Line yAxisId="left" type="monotone" dataKey="equity" stroke="#10b981" strokeWidth={2} dot={false} name="equity" />
                      <Line yAxisId="right" type="monotone" dataKey="spot" stroke="#6366f1" strokeWidth={1.5} dot={false} strokeOpacity={0.5} name="spot" />
                    </LineChart>
                  </ResponsiveContainer>
                </div>
                <p className="text-xs text-muted-foreground mt-2">
                  Green: equity (left axis, ₹). Indigo: NIFTY spot (right axis). Dashed line: starting capital.
                </p>
              </CardContent>
            </Card>
          )}

          {/* By strategy breakdown */}
          {!result.error && Object.keys(result.by_strategy).length > 0 && (
            <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
              <Card>
                <CardHeader>
                  <CardTitle className="text-base">By Strategy</CardTitle>
                </CardHeader>
                <CardContent>
                  <div className="h-56">
                    <ResponsiveContainer width="100%" height="100%">
                      <BarChart data={Object.entries(result.by_strategy).map(([k, v]) => ({ name: k, pnl: v.net_pnl, count: v.count }))}>
                        <CartesianGrid strokeDasharray="3 3" opacity={0.3} />
                        <XAxis dataKey="name" tick={{ fontSize: 11 }} />
                        <YAxis tick={{ fontSize: 11 }} tickFormatter={(v) => `₹${(v / 1000).toFixed(0)}k`} />
                        <Tooltip formatter={(v: number) => fmtINR(v)} contentStyle={{ fontSize: 12 }} />
                        <ReferenceLine y={0} stroke="#475569" />
                        <Bar dataKey="pnl" name="Net P&L" radius={[4, 4, 0, 0]}>
                          {Object.entries(result.by_strategy).map(([k, v], i) => (
                            <Bar.Cell key={i} fill={v.net_pnl >= 0 ? "#10b981" : "#f43f5e"} />
                          ))}
                        </Bar>
                      </BarChart>
                    </ResponsiveContainer>
                  </div>
                </CardContent>
              </Card>

              <Card>
                <CardHeader>
                  <CardTitle className="text-base">Trade Log ({result.trades.length})</CardTitle>
                </CardHeader>
                <CardContent>
                  <ScrollArea className="h-56 rounded border">
                    <table className="w-full text-xs">
                      <thead className="sticky top-0 bg-muted">
                        <tr>
                          <th className="text-left p-1.5">ID</th>
                          <th className="text-left p-1.5">Strategy</th>
                          <th className="text-right p-1.5">Lots</th>
                          <th className="text-right p-1.5">Entry</th>
                          <th className="text-right p-1.5">Exit</th>
                          <th className="text-right p-1.5">Net P&L</th>
                          <th className="text-left p-1.5">Reason</th>
                        </tr>
                      </thead>
                      <tbody>
                        {result.trades.map((t) => (
                          <tr key={t.trade_id} className="border-b">
                            <td className="p-1.5 font-mono text-[10px]">{t.trade_id}</td>
                            <td className="p-1.5"><Badge variant="outline" className="text-[10px]">{t.strategy}</Badge></td>
                            <td className="p-1.5 text-right tabular-nums">{t.lots}</td>
                            <td className="p-1.5 text-right tabular-nums">{t.entry_price.toFixed(1)}</td>
                            <td className="p-1.5 text-right tabular-nums">{t.exit_price?.toFixed(1) ?? "—"}</td>
                            <td className={`p-1.5 text-right tabular-nums font-medium ${t.net_pnl >= 0 ? "text-emerald-600" : "text-rose-600"}`}>
                              {fmtINR(t.net_pnl, true)}
                            </td>
                            <td className="p-1.5 text-[10px] text-muted-foreground">{t.exit_reason ?? "—"}</td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </ScrollArea>
                </CardContent>
              </Card>
            </div>
          )}
        </>
      )}
    </div>
  );
}

function StatCell({ label, value, tone }: { label: string; value: string; tone?: "pos" | "neg" }) {
  const toneClass = tone === "pos" ? "text-emerald-600 dark:text-emerald-400"
    : tone === "neg" ? "text-rose-600 dark:text-rose-400"
    : "";
  return (
    <div>
      <div className="text-[10px] text-muted-foreground uppercase tracking-wide">{label}</div>
      <div className={`text-sm font-bold tabular-nums ${toneClass}`}>{value}</div>
    </div>
  );
}
