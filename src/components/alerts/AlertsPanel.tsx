"use client";

import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { api } from "@/lib/api";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { ScrollArea } from "@/components/ui/scroll-area";
import { Bell, Send, Loader2, Activity, AlertTriangle, CheckCircle2, XCircle } from "lucide-react";
import { toast } from "sonner";

interface AlertItem {
  type: string;
  title: string;
  fields: Record<string, string>;
  description: string | null;
  timestamp: string;
  footer: string | null;
  emoji: string;
  level: string;
}

const ALERT_COLORS: Record<string, string> = {
  REGIME_CHANGE: "border-l-blue-500 bg-blue-50/40 dark:bg-blue-950/20",
  SETUP_DETECTED: "border-l-yellow-500 bg-yellow-50/40 dark:bg-yellow-950/20",
  ENTRY: "border-l-emerald-500 bg-emerald-50/40 dark:bg-emerald-950/20",
  POSITION_UPDATE: "border-l-emerald-400 bg-emerald-50/30 dark:bg-emerald-950/10",
  THESIS_DETERIORATING: "border-l-orange-500 bg-orange-50/40 dark:bg-orange-950/20",
  POSITION_ADJUSTED: "border-l-purple-500 bg-purple-50/40 dark:bg-purple-950/20",
  THESIS_INVALIDATED: "border-l-rose-500 bg-rose-50/40 dark:bg-rose-950/20",
  EXIT: "border-l-rose-500 bg-rose-50/40 dark:bg-rose-950/20",
  REVERSAL: "border-l-purple-500 bg-purple-50/40 dark:bg-purple-950/20",
  RISK_LIMIT: "border-l-rose-600 bg-rose-50/40 dark:bg-rose-950/30",
  TIME_STOP: "border-l-orange-500 bg-orange-50/40 dark:bg-orange-950/20",
  DATA_API_ERROR: "border-l-rose-600 bg-rose-50/40 dark:bg-rose-950/30",
  DAILY_SUMMARY: "border-l-blue-500 bg-blue-50/40 dark:bg-blue-950/20",
  TRADE_REVIEW: "border-l-slate-500 bg-slate-50/40 dark:bg-slate-950/20",
  STRATEGY_PERFORMANCE: "border-l-blue-500 bg-blue-50/40 dark:bg-blue-950/20",
};

const LEVEL_ICONS: Record<string, React.ReactNode> = {
  DEBUG: <Activity className="h-3 w-3" />,
  INFO: <CheckCircle2 className="h-3 w-3 text-emerald-500" />,
  WARN: <AlertTriangle className="h-3 w-3 text-orange-500" />,
  ERROR: <XCircle className="h-3 w-3 text-rose-500" />,
  CRITICAL: <XCircle className="h-3 w-3 text-rose-600 animate-pulse" />,
};

export function AlertsPanel() {
  const qc = useQueryClient();

  const { data: alertsData } = useQuery({
    queryKey: ["alerts"],
    queryFn: () => api.alerts(100),
    refetchInterval: 30000,  // 30s — alerts don't change that often
  });

  const testMutation = useMutation({
    mutationFn: () =>
      fetch(`http://localhost:81/api/alerts/test?XTransformPort=8000`, {
        method: "POST",
      }).then((r) => r.json()),
    onSuccess: (data: { ok: boolean; sent: boolean }) => {
      if (data.ok && data.sent) {
        toast.success("Test alert sent to Discord — check your channel");
      } else {
        toast.error("Test alert NOT sent — webhook not configured or disabled");
      }
      qc.invalidateQueries({ queryKey: ["alerts"] });
    },
    onError: (err) => {
      toast.error(`Failed: ${(err as Error)?.message ?? "unknown error"}`);
    },
  });

  const alerts = alertsData?.items ?? [];

  // Count by type
  const byType: Record<string, number> = {};
  for (const a of alerts) {
    byType[a.type] = (byType[a.type] ?? 0) + 1;
  }

  return (
    <div className="space-y-4">
      <Card>
        <CardHeader>
          <CardTitle className="text-base flex items-center justify-between">
            <span className="flex items-center gap-2">
              <Bell className="h-4 w-4" />
              Live Alert Feed
            </span>
            <div className="flex items-center gap-2">
              <Badge variant="outline" className="text-[10px]">
                {alertsData?.count ?? 0} alerts
              </Badge>
              <Button
                size="sm"
                variant="outline"
                onClick={() => testMutation.mutate()}
                disabled={testMutation.isPending}
              >
                {testMutation.isPending ? (
                  <Loader2 className="h-3 w-3 mr-1 animate-spin" />
                ) : (
                  <Send className="h-3 w-3 mr-1" />
                )}
                Send Test Alert
              </Button>
            </div>
          </CardTitle>
        </CardHeader>
        <CardContent>
          <p className="text-xs text-muted-foreground mb-3">
            15 alert types covering the full trade lifecycle: REGIME_CHANGE → SETUP_DETECTED →
            ENTRY → POSITION_UPDATE → THESIS_DETERIORATING → POSITION_ADJUSTED → THESIS_INVALIDATED →
            EXIT → REVERSAL → RISK_LIMIT → TIME_STOP → DATA_API_ERROR → DAILY_SUMMARY → TRADE_REVIEW →
            STRATEGY_PERFORMANCE. All alerts are also persisted to <code className="bg-muted px-1 rounded">runs/alerts/alerts.jsonl</code>.
          </p>

          {Object.keys(byType).length > 0 && (
            <div className="flex flex-wrap gap-1.5">
              {Object.entries(byType).map(([type, count]) => (
                <Badge key={type} variant="outline" className="text-[10px]">
                  {type} ×{count}
                </Badge>
              ))}
            </div>
          )}
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle className="text-base">Recent Alerts</CardTitle>
        </CardHeader>
        <CardContent>
          <ScrollArea className="h-[600px]">
            {alerts.length === 0 ? (
              <div className="p-8 text-center text-sm text-muted-foreground">
                <Bell className="h-8 w-8 mx-auto mb-2 opacity-40" />
                No alerts yet. Run a decision cycle, execute a trade, or send a test alert to see alerts appear here.
              </div>
            ) : (
              <div className="space-y-2 pr-2">
                {alerts.map((alert, i) => (
                  <AlertCard key={i} alert={alert} />
                ))}
              </div>
            )}
          </ScrollArea>
        </CardContent>
      </Card>
    </div>
  );
}

function AlertCard({ alert }: { alert: AlertItem }) {
  const colorClass = ALERT_COLORS[alert.type] ?? "border-l-slate-400 bg-slate-50/40";
  const levelIcon = LEVEL_ICONS[alert.level] ?? <Activity className="h-3 w-3" />;

  return (
    <div className={`rounded-md border-l-4 ${colorClass} p-3`}>
      <div className="flex items-start justify-between gap-2 mb-1.5">
        <div className="flex items-center gap-2 flex-1 min-w-0">
          <span className="text-base flex-shrink-0">{alert.emoji}</span>
          <span className="font-medium text-sm truncate">{alert.title}</span>
          <Badge variant="outline" className="text-[10px] flex-shrink-0">
            {levelIcon}
            <span className="ml-1">{alert.type}</span>
          </Badge>
        </div>
        <span className="text-[10px] text-muted-foreground whitespace-nowrap">
          {new Date(alert.timestamp).toLocaleTimeString("en-IN", {
            hour: "2-digit", minute: "2-digit", second: "2-digit",
          })}
        </span>
      </div>

      {alert.description && (
        <div className="text-xs text-muted-foreground whitespace-pre-wrap mb-2 mt-1">
          {alert.description}
        </div>
      )}

      {Object.keys(alert.fields).length > 0 && (
        <div className="grid grid-cols-2 md:grid-cols-3 gap-x-3 gap-y-1 text-[11px]">
          {Object.entries(alert.fields).map(([k, v]) => (
            <div key={k} className="flex flex-col">
              <span className="text-muted-foreground uppercase tracking-wide text-[9px]">{k}</span>
              <span className="font-medium tabular-nums truncate" title={v}>{v}</span>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
