"use client";

import { LiveDashboard } from "@/components/dashboard/LiveDashboard";
import { BacktestPanel } from "@/components/backtest/BacktestPanel";
import { JournalPanel } from "@/components/journal/JournalPanel";
import { ConfigPanel } from "@/components/config/ConfigPanel";
import { AblationPanel } from "@/components/ablation/AblationPanel";
import { Card, CardContent } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Activity, BarChart3, BookOpen, Settings, TrendingUp, FlaskConical } from "lucide-react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { useState } from "react";

const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      staleTime: 5000,
      retry: 1,
    },
  },
});

type Tab = "dashboard" | "backtest" | "ablation" | "journal" | "config";

const TABS: Array<{ id: Tab; label: string; icon: React.ReactNode }> = [
  { id: "dashboard", label: "Live Dashboard", icon: <Activity className="h-4 w-4" /> },
  { id: "backtest", label: "Backtest", icon: <BarChart3 className="h-4 w-4" /> },
  { id: "ablation", label: "Ablation", icon: <FlaskConical className="h-4 w-4" /> },
  { id: "journal", label: "Journal", icon: <BookOpen className="h-4 w-4" /> },
  { id: "config", label: "Configuration", icon: <Settings className="h-4 w-4" /> },
];

export default function Home() {
  const [tab, setTab] = useState<Tab>("dashboard");

  return (
    <QueryClientProvider client={queryClient}>
      <div className="min-h-screen bg-background flex flex-col">
        {/* Header */}
        <header className="border-b bg-background/95 backdrop-blur supports-[backdrop-filter]:bg-background/60 sticky top-0 z-50">
          <div className="container mx-auto px-4 py-3 flex items-center justify-between">
            <div className="flex items-center gap-3">
              <div className="flex items-center gap-2">
                <div className="h-8 w-8 rounded-md bg-gradient-to-br from-emerald-500 to-teal-600 flex items-center justify-center text-white">
                  <TrendingUp className="h-4 w-4" />
                </div>
                <div>
                  <h1 className="text-sm font-bold leading-tight">Adaptive NIFTY Options Engine</h1>
                  <p className="text-[10px] text-muted-foreground leading-tight">
                    Phase 7 · Paper + Backtest + Walk-forward + Ablation · DhanHQ
                  </p>
                </div>
              </div>
            </div>
            <div className="flex items-center gap-2">
              <Badge variant="outline" className="text-[10px]">
                <span className="h-1.5 w-1.5 rounded-full bg-emerald-500 mr-1.5 animate-pulse" />
                Engine Online
              </Badge>
              <Badge variant="outline" className="text-[10px]">v0.3.0</Badge>
            </div>
          </div>

          {/* Tabs */}
          <div className="container mx-auto px-4 pb-0">
            <div className="flex gap-1 overflow-x-auto">
              {TABS.map((t) => (
                <button
                  key={t.id}
                  onClick={() => setTab(t.id)}
                  className={`flex items-center gap-1.5 px-3 py-2 text-xs font-medium border-b-2 transition-colors whitespace-nowrap ${
                    tab === t.id
                      ? "border-primary text-primary"
                      : "border-transparent text-muted-foreground hover:text-foreground"
                  }`}
                >
                  {t.icon}
                  {t.label}
                </button>
              ))}
            </div>
          </div>
        </header>

        {/* Main content */}
        <main className="container mx-auto px-4 py-6 flex-1">
          {tab === "dashboard" && <LiveDashboard />}
          {tab === "backtest" && <BacktestPanel />}
          {tab === "ablation" && <AblationPanel />}
          {tab === "journal" && <JournalPanel />}
          {tab === "config" && <ConfigPanel />}
        </main>

        {/* Footer */}
        <footer className="border-t mt-auto bg-muted/30">
          <div className="container mx-auto px-4 py-4 text-xs text-muted-foreground flex flex-col md:flex-row md:items-center md:justify-between gap-2">
            <div>
              Phase 1-7: data · features · regime · Long CE/PE + NO-TRADE · paper · backtest · walk-forward · ablation
            </div>
            <div className="flex items-center gap-3 flex-wrap">
              <span><Badge variant="outline" className="text-[10px]">DhanHQ adapter</Badge></span>
              <span><Badge variant="outline" className="text-[10px]">Real data only — no synthetic</Badge></span>
              <span><Badge variant="outline" className="text-[10px]">NO-TRADE is a valid strategy</Badge></span>
              <span><Badge variant="outline" className="text-[10px]">Every feature ablation-tested</Badge></span>
            </div>
          </div>
        </footer>
      </div>
    </QueryClientProvider>
  );
}
