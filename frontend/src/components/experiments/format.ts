/**
 * Display helpers for experiment results: names for the metrics the backend reports, and number
 * formats suited to them. Every value is the backend's; nothing here derives a statistic.
 */
import { DASH, STRATEGY_LABELS, fmtNumber, fmtSigned } from "@/lib/format";
import type { ExperimentStatus, Strategy } from "@/lib/types";

const METRIC_LABELS: Record<string, string> = {
  trades: "Trades",
  volume: "Volume",
  volatility_per_hour: "Volatility / h",
  spread_bps: "Spread (bps)",
  depth: "Depth",
  abs_imbalance: "|Imbalance|",
  one_sided_fraction: "One-sided fraction",
  pricing_error_bps: "Pricing error (bps)",
  effective_spread_bps: "Effective spread (bps)",
  realized_spread_bps: "Realized spread (bps)",
  price_impact_bps: "Price impact (bps)",
  fill_rate: "Fill rate",
  average_trade_size: "Avg trade size",
};

const STRATEGY_METRIC_LABELS: Record<string, string> = {
  pnl: "P&L",
  return_pct: "Return %",
  sharpe: "Sharpe",
  max_drawdown_pct: "Max drawdown %",
  win_rate: "Win rate",
  trades: "Trades",
};

/** "spread_bps" → "Spread (bps)"; "market_maker.pnl" → "Market maker · P&L". Unknown names
 * are shown as they are. */
export function metricLabel(metric: string): string {
  const known = METRIC_LABELS[metric];
  if (known !== undefined) return known;
  const dot = metric.indexOf(".");
  if (dot > 0) {
    const strategy = metric.slice(0, dot);
    const measure = metric.slice(dot + 1);
    const who = STRATEGY_LABELS[strategy as Strategy] ?? strategy;
    return `${who} · ${STRATEGY_METRIC_LABELS[measure] ?? measure}`;
  }
  return metric;
}

/** Decimal places that suit a value's magnitude: 12,345 · 123.4 · 12.34 · 0.1234. */
function decimalsFor(value: number): number {
  const magnitude = Math.abs(value);
  if (magnitude >= 1000) return 0;
  if (magnitude >= 100) return 1;
  if (magnitude >= 1 || magnitude === 0) return 2;
  return 4;
}

/** A metric value (or one of its summary statistics). */
export function fmtMetric(value: number | null | undefined): string {
  if (value === null || value === undefined) return DASH;
  if (value !== 0 && Math.abs(value) < 1e-4) return value.toExponential(2);
  return fmtNumber(value, decimalsFor(value));
}

/** A difference between two means, with its sign. */
export function fmtDifference(value: number | null | undefined): string {
  if (value === null || value === undefined) return DASH;
  if (value !== 0 && Math.abs(value) < 1e-4) return value.toExponential(2);
  return fmtSigned(value, decimalsFor(value));
}

/** Welch's t statistic. */
export function fmtT(value: number | null | undefined): string {
  return fmtSigned(value, 2);
}

/** Welch–Satterthwaite degrees of freedom. */
export function fmtDf(value: number | null | undefined): string {
  return fmtNumber(value, 1);
}

/** A two-sided p-value. */
export function fmtP(value: number | null | undefined): string {
  if (value === null || value === undefined) return DASH;
  return value < 1e-4 ? "<0.0001" : fmtNumber(value, 4);
}

/** A sweep point by its parameters: "market_makers=3, volatility=0.04" (the path's last part),
 * or the backend's label for an experiment without a sweep. */
export function pointName(point: { label: string; parameters: Record<string, number> }): string {
  const entries = Object.entries(point.parameters);
  if (entries.length === 0) return "all runs";
  return entries
    .map(([path, value]) => `${path.slice(path.lastIndexOf(".") + 1)}=${value}`)
    .join(", ");
}

export const ACTIVE_STATUSES: ReadonlySet<ExperimentStatus> = new Set(["queued", "running"]);

export function isActive(status: ExperimentStatus): boolean {
  return ACTIVE_STATUSES.has(status);
}

/** A timestamp from the API (ISO 8601) as local wall-clock time, HH:MM:SS. */
export function fmtClock(iso: string | null | undefined): string {
  if (iso === null || iso === undefined) return DASH;
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return DASH;
  return date.toLocaleTimeString("en-GB", { hour12: false });
}
