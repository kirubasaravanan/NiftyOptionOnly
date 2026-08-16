"use client";

import { useQuery } from "@tanstack/react-query";
import { api, fmtINR, fmtTime, regimeColor, volColor, actionColor } from "@/lib/api";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { ScrollArea } from "@/components/ui/scroll-area";
import { Input } from "@/components/ui/input";
import { useState } from "react";
import { Search } from "lucide-react";

export function JournalPanel() {
  const [filter, setFilter] = useState("");
  const [tab, setTab] = useState<"decisions" | "trades">("decisions");

  const { data: decisions } = useQuery({
    queryKey: ["journal-decisions"],
    queryFn: () => api.decisions(500),
    refetchInterval: 30000,
  });

  const { data: trades } = useQuery({
    queryKey: ["journal-trades"],
    queryFn: () => api.trades(500),
    refetchInterval: 30000,
  });

  const filteredDecisions = (decisions?.items ?? []).filter((d) =>
    filter === "" ||
    d.strategy.toLowerCase().includes(filter.toLowerCase()) ||
    d.action.toLowerCase().includes(filter.toLowerCase()) ||
    d.regime.toLowerCase().includes(filter.toLowerCase()) ||
    d.reasons.some((r) => r.toLowerCase().includes(filter.toLowerCase()))
  );

  const filteredTrades = (trades?.items ?? []).filter((t) =>
    filter === "" ||
    t.strategy.toLowerCase().includes(filter.toLowerCase()) ||
    t.option_symbol.toLowerCase().includes(filter.toLowerCase()) ||
    t.exit_reason?.toLowerCase().includes(filter.toLowerCase())
  );

  return (
    <div className="space-y-4">
      <Card>
        <CardHeader>
          <CardTitle className="text-base flex items-center justify-between">
            <span>Trade Journal</span>
            <Badge variant="outline">
              {decisions?.count ?? 0} decisions · {trades?.count ?? 0} trades
            </Badge>
          </CardTitle>
        </CardHeader>
        <CardContent>
          <div className="flex items-center gap-2 mb-4">
            <div className="flex gap-1 rounded-md bg-muted p-1">
              <button
                className={`px-3 py-1 text-xs rounded ${tab === "decisions" ? "bg-background shadow-sm font-medium" : "text-muted-foreground"}`}
                onClick={() => setTab("decisions")}
              >
                Decisions ({decisions?.count ?? 0})
              </button>
              <button
                className={`px-3 py-1 text-xs rounded ${tab === "trades" ? "bg-background shadow-sm font-medium" : "text-muted-foreground"}`}
                onClick={() => setTab("trades")}
              >
                Trades ({trades?.count ?? 0})
              </button>
            </div>
            <div className="relative flex-1 max-w-xs">
              <Search className="absolute left-2 top-2.5 h-3 w-3 text-muted-foreground" />
              <Input
                placeholder="Filter…"
                value={filter}
                onChange={(e) => setFilter(e.target.value)}
                className="pl-7 h-8 text-xs"
              />
            </div>
          </div>

          {tab === "decisions" ? (
            <ScrollArea className="h-[600px] rounded border">
              <div className="divide-y">
                {filteredDecisions.length === 0 ? (
                  <div className="p-4 text-sm text-muted-foreground">No decisions match the filter.</div>
                ) : filteredDecisions.map((d, i) => (
                  <div key={i} className="p-3 hover:bg-muted/50">
                    <div className="flex flex-wrap items-center gap-1.5 mb-1.5">
                      <Badge className={`${actionColor[d.action]} text-[10px]`}>{d.action}</Badge>
                      <Badge variant="outline" className="text-[10px]">{d.strategy}</Badge>
                      <Badge className={`${regimeColor[d.regime]} text-[10px]`}>{d.regime}</Badge>
                      <Badge className={`${volColor[d.volatility]} text-[10px]`}>{d.volatility}</Badge>
                      <span className="text-[10px] text-muted-foreground ml-auto tabular-nums">
                        {fmtTime(d.timestamp)}
                      </span>
                    </div>
                    <div className="grid grid-cols-2 md:grid-cols-4 gap-2 text-[10px] mb-1">
                      <span className="text-muted-foreground">Spot: <span className="font-medium text-foreground tabular-nums">{d.snapshot_summary.spot.toFixed(2)}</span></span>
                      <span className="text-muted-foreground">Conf: <span className="font-medium text-foreground">{(d.confidence * 100).toFixed(0)}%</span></span>
                      <span className="text-muted-foreground">Net: <span className="font-medium text-foreground tabular-nums">{fmtINR(d.expected_net_value)}</span></span>
                      <span className="text-muted-foreground">Bucket: <span className="font-medium text-foreground">{d.snapshot_summary.time_bucket ?? "—"}</span></span>
                    </div>
                    <div className="text-[10px] text-muted-foreground">
                      {d.reasons.join(" · ")}
                    </div>
                  </div>
                ))}
              </div>
            </ScrollArea>
          ) : (
            <ScrollArea className="h-[600px] rounded border">
              {filteredTrades.length === 0 ? (
                <div className="p-4 text-sm text-muted-foreground">
                  No trades yet — they appear here after positions close in paper or backtest mode.
                </div>
              ) : (
                <table className="w-full text-xs">
                  <thead className="sticky top-0 bg-muted">
                    <tr>
                      <th className="text-left p-2">ID</th>
                      <th className="text-left p-2">Entry</th>
                      <th className="text-left p-2">Strategy</th>
                      <th className="text-left p-2">Symbol</th>
                      <th className="text-right p-2">Lots</th>
                      <th className="text-right p-2">Entry</th>
                      <th className="text-right p-2">Exit</th>
                      <th className="text-right p-2">Gross</th>
                      <th className="text-right p-2">Charges</th>
                      <th className="text-right p-2">Net P&L</th>
                      <th className="text-right p-2">Hold (min)</th>
                      <th className="text-left p-2">Exit Reason</th>
                    </tr>
                  </thead>
                  <tbody>
                    {filteredTrades.map((t) => (
                      <tr key={t.trade_id} className="border-b hover:bg-muted/50">
                        <td className="p-2 font-mono text-[10px]">{t.trade_id}</td>
                        <td className="p-2 text-[10px]">{fmtTime(t.entry_time)}</td>
                        <td className="p-2"><Badge variant="outline" className="text-[10px]">{t.strategy}</Badge></td>
                        <td className="p-2 font-mono text-[10px]">{t.option_symbol}</td>
                        <td className="p-2 text-right tabular-nums">{t.lots}</td>
                        <td className="p-2 text-right tabular-nums">{t.entry_price.toFixed(1)}</td>
                        <td className="p-2 text-right tabular-nums">{t.exit_price?.toFixed(1) ?? "—"}</td>
                        <td className="p-2 text-right tabular-nums">{fmtINR(t.gross_pnl, true)}</td>
                        <td className="p-2 text-right tabular-nums text-rose-600">-{fmtINR(t.charges).replace("+", "").replace("₹", "₹")}</td>
                        <td className={`p-2 text-right tabular-nums font-semibold ${t.net_pnl >= 0 ? "text-emerald-600" : "text-rose-600"}`}>
                          {fmtINR(t.net_pnl, true)}
                        </td>
                        <td className="p-2 text-right tabular-nums">{t.holding_minutes ?? "—"}</td>
                        <td className="p-2 text-[10px] text-muted-foreground">{t.exit_reason ?? "—"}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              )}
            </ScrollArea>
          )}
        </CardContent>
      </Card>
    </div>
  );
}
