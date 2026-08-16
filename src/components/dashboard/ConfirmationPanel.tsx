"use client";

import { api, type ConfirmationData, type AuxMarketData, fmtPct } from "@/lib/api";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Activity, TrendingUp, TrendingDown, Minus, Gauge, Layers, DollarSign, Building2, PieChart } from "lucide-react";
import { useQuery } from "@tanstack/react-query";

export function ConfirmationPanel() {
  const { data: snapshot } = useQuery({
    queryKey: ["snapshot"],
    queryFn: api.snapshot,
    refetchInterval: 30000,  // 30s — server caches for 30s
  });

  const confirmation = snapshot?.confirmation;
  const aux = snapshot?.aux;

  if (!confirmation) {
    return (
      <Card>
        <CardHeader>
          <CardTitle className="text-base flex items-center gap-2">
            <Layers className="h-4 w-4" />
            Cross-Market Confirmation
          </CardTitle>
        </CardHeader>
        <CardContent>
          <div className="text-xs text-muted-foreground">
            {snapshot?.data_invalid_reason
              ? `⚠ ${snapshot.data_invalid_reason}`
              : "Loading confirmation data…"}
          </div>
        </CardContent>
      </Card>
    );
  }

  // Handle error response
  if ("error" in confirmation && confirmation.error) {
    return (
      <Card>
        <CardHeader>
          <CardTitle className="text-base flex items-center gap-2">
            <Layers className="h-4 w-4" />
            Cross-Market Confirmation
          </CardTitle>
        </CardHeader>
        <CardContent>
          <div className="text-xs text-rose-600 dark:text-rose-400">
            ⚠ Error computing confirmation: {confirmation.error}
          </div>
        </CardContent>
      </Card>
    );
  }

  return (
    <Card>
      <CardHeader>
        <CardTitle className="text-base flex items-center gap-2">
          <Layers className="h-4 w-4" />
          Cross-Market Confirmation
          <span className="text-xs text-muted-foreground ml-auto">
            Layer 3 + 4 features · NEVER a buy signal — modulates confidence only
          </span>
        </CardTitle>
      </CardHeader>
      <CardContent>
        <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-4 gap-3">
          {/* VIX valuation */}
          <ConfirmationCard
            icon={<Gauge className="h-4 w-4" />}
            title="VIX Valuation"
            value={confirmation.vix_valuation.vix ? confirmation.vix_valuation.vix.toFixed(2) : "—"}
            badge={confirmation.vix_valuation.valuation}
            badgeClass={valuationColor(confirmation.vix_valuation.valuation)}
            subValue={confirmation.vix_valuation.vix_percentile != null ? `pctile ${confirmation.vix_valuation.vix_percentile.toFixed(0)}` : ""}
            subSubValue={confirmation.vix_valuation.iv_vix_gap != null ? `IV-VIX ${confirmation.vix_valuation.iv_vix_gap >= 0 ? "+" : ""}${(confirmation.vix_valuation.iv_vix_gap * 100).toFixed(2)}%` : ""}
            reasons={confirmation.vix_valuation.reasons}
          />

          {/* OI classification */}
          <ConfirmationCard
            icon={<PieChart className="h-4 w-4" />}
            title="OI Classification"
            value={`CE ${confirmation.oi_classification.ce.replace("_", " ")}`}
            badge={`PE ${confirmation.oi_classification.pe.replace("_", " ")}`}
            badgeClass="bg-slate-500 text-white"
            subValue={confirmation.oi_classification.call_wall ? `Call wall ${confirmation.oi_classification.call_wall}` : ""}
            subSubValue={confirmation.oi_classification.put_wall ? `Put wall ${confirmation.oi_classification.put_wall}` : ""}
            reasons={confirmation.oi_classification.reasons}
          />

          {/* Futures basis */}
          <ConfirmationCard
            icon={<DollarSign className="h-4 w-4" />}
            title="Futures Basis"
            value={confirmation.futures_basis.basis_pct != null ? `${confirmation.futures_basis.basis_pct >= 0 ? "+" : ""}${confirmation.futures_basis.basis_pct.toFixed(3)}%` : "—"}
            badge={confirmation.futures_basis.interpretation}
            badgeClass={basisColor(confirmation.futures_basis.interpretation)}
            subValue={confirmation.futures_basis.futures ? `Fut ₹${confirmation.futures_basis.futures.toFixed(0)}` : ""}
            subSubValue={`Spot ₹${confirmation.futures_basis.spot.toFixed(0)}`}
            reasons={confirmation.futures_basis.reasons}
          />

          {/* Bank Nifty */}
          <ConfirmationCard
            icon={<Building2 className="h-4 w-4" />}
            title="Bank Nifty"
            value={aux?.banknifty?.ltp ? `₹${aux.banknifty.ltp.toFixed(0)}` : "—"}
            badge={confirmation.banknifty_confirmation.correlation_state}
            badgeClass={bnColor(confirmation.banknifty_confirmation.correlation_state)}
            subValue={confirmation.banknifty_confirmation.banknifty_change_pct != null ? `BN ${confirmation.banknifty_confirmation.banknifty_change_pct >= 0 ? "+" : ""}${confirmation.banknifty_confirmation.banknifty_change_pct.toFixed(2)}%` : ""}
            subSubValue={`NIFTY ${confirmation.banknifty_confirmation.nifty_change_pct >= 0 ? "+" : ""}${confirmation.banknifty_confirmation.nifty_change_pct.toFixed(2)}%`}
            reasons={confirmation.banknifty_confirmation.reasons}
          />
        </div>

        {/* Composite guidance banner */}
        <CompositeBanner confirmation={confirmation} />
      </CardContent>
    </Card>
  );
}

function ConfirmationCard({
  icon, title, value, badge, badgeClass, subValue, subSubValue, reasons,
}: {
  icon: React.ReactNode;
  title: string;
  value: string;
  badge: string;
  badgeClass: string;
  subValue: string;
  subSubValue: string;
  reasons: string[];
}) {
  return (
    <div className="rounded-md border p-3">
      <div className="flex items-center justify-between mb-1">
        <div className="flex items-center gap-1.5 text-xs text-muted-foreground">
          {icon}
          {title}
        </div>
        <Badge className={`${badgeClass} text-[10px]`}>{badge}</Badge>
      </div>
      <div className="text-base font-bold tabular-nums">{value}</div>
      <div className="text-[10px] text-muted-foreground mt-1">{subValue}</div>
      <div className="text-[10px] text-muted-foreground">{subSubValue}</div>
      {reasons.length > 0 && (
        <ul className="mt-2 space-y-0.5">
          {reasons.slice(0, 2).map((r, i) => (
            <li key={i} className="text-[10px] text-muted-foreground leading-tight">
              · {r.length > 80 ? r.substring(0, 80) + "…" : r}
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

function CompositeBanner({ confirmation }: { confirmation: ConfirmationData }) {
  // Derive a composite view of the market
  const vix = confirmation.vix_valuation.valuation;
  const bn = confirmation.banknifty_confirmation.correlation_state;
  const fb = confirmation.futures_basis.interpretation;
  const ce = confirmation.oi_classification.ce;
  const pe = confirmation.oi_classification.pe;

  // Count confirmations vs divergences
  let positives = 0;
  let negatives = 0;
  if (vix === "CHEAP") positives++;
  if (vix === "EXPENSIVE") negatives++;
  if (bn === "CONFIRMED") positives++;
  if (bn === "DIVERGENT") negatives++;
  if (fb === "PREMIUM") positives++;
  if (fb === "DISCOUNT") negatives++;
  if (ce === "LONG_BUILDUP") positives++;
  if (ce === "LONG_UNWINDING") negatives++;
  if (pe === "LONG_BUILDUP") negatives++;  // PE long buildup = bearish
  if (pe === "LONG_UNWINDING") positives++;

  let verdict = "NEUTRAL";
  let verdictClass = "bg-slate-500 text-white";
  let icon = <Minus className="h-4 w-4" />;
  if (positives - negatives >= 2) {
    verdict = "STRONG CONFIRMATION";
    verdictClass = "bg-emerald-500 text-white";
    icon = <TrendingUp className="h-4 w-4" />;
  } else if (positives - negatives >= 1) {
    verdict = "MILD CONFIRMATION";
    verdictClass = "bg-emerald-400 text-emerald-950";
    icon = <TrendingUp className="h-4 w-4" />;
  } else if (negatives - positives >= 2) {
    verdict = "DIVERGENCE — CAUTION";
    verdictClass = "bg-rose-500 text-white";
    icon = <TrendingDown className="h-4 w-4" />;
  } else if (negatives - positives >= 1) {
    verdict = "MILD DIVERGENCE";
    verdictClass = "bg-amber-500 text-white";
    icon = <Activity className="h-4 w-4" />;
  }

  return (
    <div className={`mt-4 rounded-md p-3 ${verdictClass} flex items-center gap-2`}>
      {icon}
      <span className="font-semibold">{verdict}</span>
      <span className="text-xs opacity-90 ml-auto">
        {positives} confirmations · {negatives} divergences · {5 - positives - negatives} neutral
      </span>
    </div>
  );
}

function valuationColor(v: string): string {
  if (v === "CHEAP") return "bg-emerald-500 text-white";
  if (v === "EXPENSIVE") return "bg-rose-500 text-white";
  if (v === "FAIR") return "bg-amber-400 text-amber-950";
  return "bg-slate-400 text-white";
}

function basisColor(v: string): string {
  if (v === "PREMIUM") return "bg-emerald-400 text-emerald-950";
  if (v === "DISCOUNT") return "bg-rose-400 text-rose-950";
  if (v === "FLAT") return "bg-slate-400 text-white";
  return "bg-slate-400 text-white";
}

function bnColor(v: string): string {
  if (v === "CONFIRMED") return "bg-emerald-500 text-white";
  if (v === "DIVERGENT") return "bg-rose-500 text-white";
  if (v === "NEUTRAL") return "bg-slate-400 text-white";
  return "bg-slate-400 text-white";
}
