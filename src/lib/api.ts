// API client + TypeScript types for the NIFTY Options Trading Engine
//
// The Caddy gateway runs on port 81 and routes /api/*?XTransformPort=N to localhost:N.
// The Next.js dev server runs on port 3000. So frontend requests must go to
// http://localhost:81/api/...?XTransformPort=8000 to reach the FastAPI backend.

const GATEWAY_PORT = 81;
const API_PORT = 8000;

function apiUrl(path: string): string {
  // Always target the Caddy gateway with XTransformPort so it proxies to the FastAPI backend.
  // Relative paths would hit the Next.js dev server (port 3000) which returns 404 for /api/*.
  const sep = path.includes("?") ? "&" : "?";
  return `http://localhost:${GATEWAY_PORT}${path}${sep}XTransformPort=${API_PORT}`;
}

async function fetchJson<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(apiUrl(path), init);
  if (!res.ok) {
    const txt = await res.text();
    throw new Error(`API ${res.status}: ${txt}`);
  }
  return (await res.json()) as T;
}

// ---------- types ----------

export type MarketRegime =
  | "STRONG_BULL" | "BULL" | "WEAK_BULL" | "NEUTRAL"
  | "WEAK_BEAR" | "BEAR" | "STRONG_BEAR" | "RANGE"
  | "BREAKOUT" | "REVERSAL";

export type VolatilityRegime =
  | "LOW_VOL" | "NORMAL_VOL" | "HIGH_VOL"
  | "VOL_EXPANSION" | "VOL_CONTRACTION";

export type StrategyName =
  | "LONG_CALL" | "LONG_PUT" | "LONG_STRADDLE" | "LONG_STRANGLE"
  | "DEBIT_SPREAD" | "IRON_CONDOR" | "BUTTERFLY" | "NO_TRADE";

export type DecisionAction =
  | "ENTER" | "HOLD" | "ADD" | "REDUCE" | "MOVE_STOP"
  | "TAKE_PROFIT" | "EXIT" | "REVERSE" | "NO_TRADE";

export interface Snapshot {
  timestamp: string;
  data_valid: boolean;
  data_invalid_reason: string | null;
  market_open: boolean;
  time_bucket: string | null;
  index: {
    ltp: number;
    prev_close: number | null;
    open: number | null;
    high: number | null;
    low: number | null;
    volume: number;
    vwap: number | null;
    atr: number | null;
    adx: number | null;
    adx_slope: number | null;
    rsi: number | null;
    ema_fast: number | null;
    ema_mid: number | null;
    ema_slow: number | null;
  };
  india_vix: { ltp: number; iv_percentile: number | null } | null;
  option_chain_size: number;
  option_chain_sample: Array<{
    symbol: string;
    strike: number;
    option_type: "CE" | "PE";
    ltp: number;
    iv: number | null;
    delta: number | null;
    theta: number | null;
    volume: number;
    oi: number;
  }>;
  confirmation: ConfirmationData | null;
  aux: AuxMarketData;
}

export interface Decision {
  timestamp: string;
  action: DecisionAction;
  strategy: StrategyName;
  regime: MarketRegime;
  volatility: VolatilityRegime;
  lots: number;
  premium_per_lot: number;
  total_premium: number;
  expected_net_value: number;
  expected_risk: number;
  confidence: number;
  stop_loss: number | null;
  take_profit: number | null;
  reasons: string[];
  option: {
    symbol: string;
    strike: number;
    option_type: "CE" | "PE";
    ltp: number;
    iv: number | null;
    delta: number | null;
    expiry: string;
  } | null;
  explainability: string;
}

export interface DecisionRecord {
  timestamp: string;
  action: DecisionAction;
  strategy: StrategyName;
  regime: MarketRegime;
  volatility: VolatilityRegime;
  confidence: number;
  expected_net_value: number;
  reasons: string[];
  snapshot_summary: {
    timestamp: string;
    spot: number;
    data_valid: boolean;
    data_invalid_reason: string | null;
    time_bucket: string | null;
    vix: number | null;
    chain_size: number;
  };
}

export interface TradeRecord {
  trade_id: string;
  entry_time: string;
  exit_time: string | null;
  strategy: StrategyName;
  regime_at_entry: MarketRegime;
  vol_regime_at_entry: VolatilityRegime;
  option_symbol: string;
  strike: number;
  option_type: "CE" | "PE";
  expiry: string;
  lots: number;
  entry_price: number;
  exit_price: number | null;
  gross_pnl: number;
  charges: number;
  net_pnl: number;
  slippage: number;
  exit_reason: string | null;
  holding_minutes: number | null;
}

export interface PerformanceReport {
  total_trades: number;
  winners: number;
  losers: number;
  win_rate: number;
  gross_pnl: number;
  net_pnl: number;
  avg_winner: number;
  avg_loser: number;
  expectancy: number;
  profit_factor: number;
  max_drawdown: number;
  total_charges: number;
  total_slippage: number;
  avg_holding_minutes: number;
  by_strategy: Record<string, { count: number; net_pnl: number }>;
  by_regime: Record<string, { count: number; net_pnl: number }>;
  by_volatility: Record<string, { count: number; net_pnl: number }>;
}

export interface BacktestResult {
  config: {
    start_date: string;
    end_date: string;
    interval: string;
    capital: number;
  };
  final_equity: number;
  total_return_pct: number;
  max_drawdown_pct: number;
  total_charges: number;
  total_slippage: number;
  trade_count: number;
  win_rate: number;
  expectancy: number;
  profit_factor: number;
  sharpe: number;
  by_strategy: Record<string, { count: number; net_pnl: number }>;
  equity_curve: Array<{
    timestamp: string;
    equity: number;
    spot: number;
    open_positions: number;
  }>;
  trades: TradeRecord[];
  decisions_count: number;
  error: string | null;
}

export interface AblationVariant {
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

export interface AblationResult {
  baseline: AblationVariant;
  variants: AblationVariant[];
  summary: string;
  recommendation: string[];
}

export interface ConfirmationData {
  vix_valuation: {
    vix: number | null;
    vix_percentile: number | null;
    vix_change: number | null;
    iv_vix_gap: number | null;
    valuation: string;
    reasons: string[];
  };
  oi_classification: {
    ce: string;
    pe: string;
    call_wall: number | null;
    put_wall: number | null;
    max_pain: number | null;
    reasons: string[];
  };
  futures_basis: {
    spot: number;
    futures: number | null;
    basis: number | null;
    basis_pct: number | null;
    interpretation: string;
    reasons: string[];
  };
  banknifty_confirmation: {
    nifty_change_pct: number;
    banknifty_change_pct: number | null;
    correlation_state: string;
    reasons: string[];
  };
  error?: string;
}

export interface AuxMarketData {
  banknifty: { ltp: number; prev_close: number | null; open?: number; high?: number; low?: number } | null;
  nifty_futures: { ltp: number; prev_close: number | null; open?: number; high?: number; low?: number } | null;
}

export interface AlertItem {
  type: string;
  title: string;
  fields: Record<string, string>;
  description: string | null;
  timestamp: string;
  footer: string | null;
  emoji: string;
  level: string;
}

export interface HealthStatus {
  ok: boolean;
  broker_connected: boolean;
  market_open: boolean;
  ist_time: string;
  capital: number;
}

// ---------- API calls ----------

export const api = {
  health: () => fetchJson<HealthStatus>("/api/health"),
  snapshot: () => fetchJson<Snapshot>("/api/snapshot"),
  decision: () => fetchJson<Decision>("/api/decision"),
  status: () => fetchJson<{
    risk: Record<string, unknown>;
    open_positions: Array<{
      strategy: string;
      option: string;
      strike: number;
      option_type: string;
      lots: number;
      entry_price: number;
      current_price: number | null;
      stop_loss: number | null;
      take_profit: number | null;
      unrealised_pnl: number;
      entry_time: string;
    }>;
  }>("/api/status"),
  decisions: (limit = 100) =>
    fetchJson<{ count: number; items: DecisionRecord[] }>(`/api/journal/decisions?limit=${limit}`),
  trades: (limit = 100) =>
    fetchJson<{ count: number; items: TradeRecord[] }>(`/api/journal/trades?limit=${limit}`),
  performance: () => fetchJson<PerformanceReport>("/api/performance"),
  backtest: (req: { start_date: string; end_date: string; capital: number; interval: string }) =>
    fetchJson<BacktestResult>("/api/backtest/run", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(req),
    }),
  ablation: (req: { start_date: string; end_date: string; capital: number }) =>
    fetchJson<AblationResult>("/api/ablation/run", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(req),
    }),
  configs: () => fetchJson<Record<string, string>>("/api/config"),
  updateConfig: (name: string, content: string) =>
    fetchJson<{ ok: boolean; name: string; size: number }>(`/api/config/${name}`, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ name, content }),
    }),
  alerts: (limit = 50) =>
    fetchJson<{ count: number; items: AlertItem[] }>(`/api/alerts?limit=${limit}`),
  sendTestAlert: () =>
    fetchJson<{ ok: boolean; sent: boolean; webhook_configured: boolean; enabled: boolean }>("/api/alerts/test", {
      method: "POST",
    }),
};

// ---------- helpers ----------

export const regimeColor: Record<MarketRegime, string> = {
  STRONG_BULL: "bg-emerald-600 text-white",
  BULL: "bg-emerald-500 text-white",
  WEAK_BULL: "bg-emerald-400 text-emerald-950",
  NEUTRAL: "bg-slate-400 text-white",
  WEAK_BEAR: "bg-rose-400 text-rose-950",
  BEAR: "bg-rose-500 text-white",
  STRONG_BEAR: "bg-rose-600 text-white",
  RANGE: "bg-amber-500 text-white",
  BREAKOUT: "bg-violet-500 text-white",
  REVERSAL: "bg-orange-500 text-white",
};

export const volColor: Record<VolatilityRegime, string> = {
  LOW_VOL: "bg-sky-400 text-sky-950",
  NORMAL_VOL: "bg-slate-400 text-white",
  HIGH_VOL: "bg-amber-500 text-white",
  VOL_EXPANSION: "bg-rose-500 text-white",
  VOL_CONTRACTION: "bg-emerald-400 text-emerald-950",
};

export const actionColor: Record<DecisionAction, string> = {
  ENTER: "bg-emerald-500 text-white",
  HOLD: "bg-slate-400 text-white",
  ADD: "bg-emerald-400 text-emerald-950",
  REDUCE: "bg-amber-400 text-amber-950",
  MOVE_STOP: "bg-sky-400 text-sky-950",
  TAKE_PROFIT: "bg-emerald-600 text-white",
  EXIT: "bg-rose-500 text-white",
  REVERSE: "bg-violet-500 text-white",
  NO_TRADE: "bg-amber-500 text-white",
};

export function fmtINR(n: number, withSign = false): string {
  const sign = withSign && n > 0 ? "+" : "";
  return `${sign}₹${n.toLocaleString("en-IN", { maximumFractionDigits: 0 })}`;
}

export function fmtPct(n: number, withSign = true): string {
  const sign = withSign && n > 0 ? "+" : "";
  return `${sign}${n.toFixed(2)}%`;
}

export function fmtTime(iso: string): string {
  const d = new Date(iso);
  return d.toLocaleString("en-IN", {
    hour: "2-digit", minute: "2-digit", second: "2-digit",
    day: "2-digit", month: "short",
  });
}
