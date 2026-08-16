"use client";

import { useState } from "react";
import { useMutation } from "@tanstack/react-query";
import { api, fmtINR, fmtPct } from "@/lib/api";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Badge } from "@/components/ui/badge";
import { ScrollArea } from "@/components/ui/scroll-area";
import { BarChart, Bar, XAxis, YAxis, Tooltip, ResponsiveContainer, CartesianGrid, ReferenceLine, Cell } from "recharts";
import { Play, Loader2, AlertCircle, CheckCircle2, FlaskConical } from "lucide-react";
import { toast } from "sonner";

interface AblationVariant {
  feature_name: string;
  description: string;
  oos_return_pct: number;
  oos_expectancy: number;
  oos_win_rate: number;
  oos_trades: number;
  oos_max_dd_pct: number;
  oos_sharpe: number;
  incremental_expectancy: number;
  incremental_return: number;
  keeps_feature: boolean;
  error: string | null;
}

interface AblationResult {
  baseline: AblationVariant;
  variants: AblationVariant[];
  summary: string;
  recommendation: string[];
}

const DEFAULT_START = "2024-01-01";
const DEFAULT_END = "2024-07-31";

export function AblationPanel() {
  const [start, setStart] = useState(DEFAULT_START);
  const [end, setEnd] = useState(DEFAULT_END);
  const [capital, setCapital] = useState("1000000");
  const [result, setResult] = useState<AblationResult | null>(null);

  const mutation = useMutation({
    mutationFn: () => api.ablation({
      start_date: start, end_date: end,
      capital: parseFloat(capital),
    }),
    onSuccess: (data: AblationResult) => {
      setResult(data);
      toast.success(`Ablation complete — ${data.variants.length} variants tested`);
    },
    onError: (err) => {
      toast.error(`Ablation failed: ${(err as Error)?.message ?? "unknown error"}`);
    },
  });

  const chartData = result ? [
    { name: "baseline", expectancy: result.baseline.oos_expectancy, fill: "#10b981" },
    ...result.variants.map((v) => ({
      name: v.feature_name.replace("without_", "−"),
      expectancy: v.oos_expectancy,
      fill: v.keeps_feature ? "#10b981" : "#f43f5e",
    })),
  ] : [];

  return (
    <div className="space-y-4">
      <Card>
        <CardHeader>
          <CardTitle className="text-base flex items-center gap-2">
            <FlaskConical className="h-4 w-4" />
            Ablation Testing Framework
          </CardTitle>
        </CardHeader>
        <CardContent>
          <p className="text-xs text-muted-foreground mb-3">
            Per spec section 26: every new factor must pass an incremental-value ablation test
            before being allowed into the live decision engine. This runs the backtest 6 times
            (1 baseline + 5 variants, each with one Layer 3 feature disabled) and reports
            which features actually improve out-of-sample expectancy after costs.
          </p>

          <div className="grid grid-cols-1 md:grid-cols-4 gap-4 items-end">
            <div>
              <Label htmlFor="ab-start" className="text-xs">Start Date</Label>
              <Input id="ab-start" type="date" value={start} onChange={(e) => setStart(e.target.value)} />
            </div>
            <div>
              <Label htmlFor="ab-end" className="text-xs">End Date</Label>
              <Input id="ab-end" type="date" value={end} onChange={(e) => setEnd(e.target.value)} />
            </div>
            <div>
              <Label htmlFor="ab-cap" className="text-xs">Capital (₹)</Label>
              <Input id="ab-cap" type="number" value={capital} onChange={(e) => setCapital(e.target.value)} />
            </div>
            <Button onClick={() => mutation.mutate()} disabled={mutation.isPending}>
              {mutation.isPending ? (
                <>
                  <Loader2 className="h-4 w-4 mr-2 animate-spin" />
                  Running 6 backtests…
                </>
              ) : (
                <>
                  <Play className="h-4 w-4 mr-2" />
                  Run Ablation Test
                </>
              )}
            </Button>
          </div>
        </CardContent>
      </Card>

      {mutation.isError && (
        <Card className="border-rose-500">
          <CardContent className="pt-6 flex items-center gap-2 text-rose-700 dark:text-rose-400 text-sm">
            <AlertCircle className="h-4 w-4" />
            Ablation failed: {(mutation.error as Error)?.message ?? "unknown error"}
          </CardContent>
        </Card>
      )}

      {result && (
        <>
          <Card>
            <CardHeader>
              <CardTitle className="text-base flex items-center gap-2">
                <CheckCircle2 className="h-4 w-4 text-emerald-500" />
                Baseline (All Features On)
                <span className="text-xs text-muted-foreground ml-auto">
                  {result.baseline.oos_trades} trades · {(result.baseline.oos_win_rate * 100).toFixed(1)}% win rate
                </span>
              </CardTitle>
            </CardHeader>
            <CardContent>
              <div className="grid grid-cols-2 md:grid-cols-3 lg:grid-cols-6 gap-4">
                <Metric label="OOS Return" value={fmtPct(result.baseline.oos_return_pct)} tone={result.baseline.oos_return_pct >= 0 ? "pos" : "neg"} />
                <Metric label="OOS Expectancy" value={fmtINR(result.baseline.oos_expectancy, true)} tone={result.baseline.oos_expectancy >= 0 ? "pos" : "neg"} />
                <Metric label="Win Rate" value={`${(result.baseline.oos_win_rate * 100).toFixed(1)}%`} />
                <Metric label="Max Drawdown" value={`${result.baseline.oos_max_dd_pct.toFixed(2)}%`} tone="neg" />
                <Metric label="Sharpe" value={result.baseline.oos_sharpe.toFixed(2)} />
                <Metric label="Trades" value={String(result.baseline.oos_trades)} />
              </div>
            </CardContent>
          </Card>

          <Card>
            <CardHeader>
              <CardTitle className="text-base">OOS Expectancy by Variant</CardTitle>
            </CardHeader>
            <CardContent>
              <div className="h-72">
                <ResponsiveContainer width="100%" height="100%">
                  <BarChart data={chartData} layout="vertical">
                    <CartesianGrid strokeDasharray="3 3" opacity={0.3} />
                    <XAxis type="number" tick={{ fontSize: 11 }} tickFormatter={(v) => `₹${(v / 1000).toFixed(0)}k`} />
                    <YAxis type="category" dataKey="name" tick={{ fontSize: 11 }} width={120} />
                    <Tooltip
                      formatter={(v: number) => fmtINR(v)}
                      contentStyle={{ fontSize: 12 }}
                    />
                    <ReferenceLine x={result.baseline.oos_expectancy} stroke="#94a3b8" strokeDasharray="4 4" />
                    <Bar dataKey="expectancy" name="OOS Expectancy" radius={[0, 4, 4, 0]}>
                      {chartData.map((entry, i) => (
                        <Cell key={`cell-${i}`} fill={entry.fill} />
                      ))}
                    </Bar>
                  </BarChart>
                </ResponsiveContainer>
              </div>
              <p className="text-xs text-muted-foreground mt-2">
                Green bars: removing the feature HURTS performance (KEEP it). Red bars: removing it doesn't hurt (consider DROP). Dashed line: baseline.
              </p>
            </CardContent>
          </Card>

          <Card>
            <CardHeader>
              <CardTitle className="text-base">Per-Feature Incremental Value</CardTitle>
            </CardHeader>
            <CardContent>
              <ScrollArea className="rounded border">
                <table className="w-full text-xs">
                  <thead className="sticky top-0 bg-muted">
                    <tr>
                      <th className="text-left p-2">Feature</th>
                      <th className="text-left p-2">Description</th>
                      <th className="text-right p-2">OOS Expectancy</th>
                      <th className="text-right p-2">Δ Expectancy</th>
                      <th className="text-right p-2">OOS Return</th>
                      <th className="text-right p-2">Δ Return</th>
                      <th className="text-right p-2">Win Rate</th>
                      <th className="text-right p-2">Sharpe</th>
                      <th className="text-center p-2">Verdict</th>
                    </tr>
                  </thead>
                  <tbody>
                    <tr className="border-b bg-emerald-50 dark:bg-emerald-950/30 font-medium">
                      <td className="p-2">{result.baseline.feature_name}</td>
                      <td className="p-2 text-muted-foreground">{result.baseline.description}</td>
                      <td className="p-2 text-right tabular-nums">{fmtINR(result.baseline.oos_expectancy)}</td>
                      <td className="p-2 text-right tabular-nums">—</td>
                      <td className="p-2 text-right tabular-nums">{fmtPct(result.baseline.oos_return_pct)}</td>
                      <td className="p-2 text-right tabular-nums">—</td>
                      <td className="p-2 text-right tabular-nums">{(result.baseline.oos_win_rate * 100).toFixed(1)}%</td>
                      <td className="p-2 text-right tabular-nums">{result.baseline.oos_sharpe.toFixed(2)}</td>
                      <td className="p-2 text-center"><Badge className="bg-emerald-500 text-white text-[10px]">BASELINE</Badge></td>
                    </tr>
                    {result.variants.map((v) => (
                      <tr key={v.feature_name} className="border-b hover:bg-muted/50">
                        <td className="p-2 font-mono">{v.feature_name}</td>
                        <td className="p-2 text-muted-foreground">{v.description}</td>
                        <td className="p-2 text-right tabular-nums">{fmtINR(v.oos_expectancy)}</td>
                        <td className={`p-2 text-right tabular-nums font-medium ${v.incremental_expectancy > 0 ? "text-emerald-600" : v.incremental_expectancy < 0 ? "text-rose-600" : "text-muted-foreground"}`}>
                          {v.incremental_expectancy > 0 ? "+" : ""}{fmtINR(v.incremental_expectancy)}
                        </td>
                        <td className="p-2 text-right tabular-nums">{fmtPct(v.oos_return_pct)}</td>
                        <td className={`p-2 text-right tabular-nums ${v.incremental_return > 0 ? "text-emerald-600" : v.incremental_return < 0 ? "text-rose-600" : "text-muted-foreground"}`}>
                          {fmtPct(v.incremental_return)}
                        </td>
                        <td className="p-2 text-right tabular-nums">{(v.oos_win_rate * 100).toFixed(1)}%</td>
                        <td className="p-2 text-right tabular-nums">{v.oos_sharpe.toFixed(2)}</td>
                        <td className="p-2 text-center">
                          {v.keeps_feature ? (
                            <Badge className="bg-emerald-500 text-white text-[10px]">KEEP</Badge>
                          ) : (
                            <Badge variant="outline" className="text-[10px]">DROP</Badge>
                          )}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </ScrollArea>
            </CardContent>
          </Card>

          <Card>
            <CardHeader>
              <CardTitle className="text-base">Recommendation</CardTitle>
            </CardHeader>
            <CardContent>
              <ScrollArea className="max-h-72">
                <pre className="text-xs font-mono whitespace-pre-wrap">{result.recommendation.join("\n")}</pre>
              </ScrollArea>
            </CardContent>
          </Card>
        </>
      )}
    </div>
  );
}

function Metric({ label, value, tone }: { label: string; value: string; tone?: "pos" | "neg" }) {
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
