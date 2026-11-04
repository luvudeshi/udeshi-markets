/**
 * Display formatting only: every value is computed by the backend and only turned into text
 * here (fixed decimals, separators, signs, units).
 */
import type { AgentOut, Strategy } from "./types";

export const DASH = "—";

const grouped = new Map<number, Intl.NumberFormat>();
function formatter(decimals: number): Intl.NumberFormat {
  let found = grouped.get(decimals);
  if (found === undefined) {
    found = new Intl.NumberFormat("en-US", {
      minimumFractionDigits: decimals,
      maximumFractionDigits: decimals,
    });
    grouped.set(decimals, found);
  }
  return found;
}

/** Decimal places of a tick size: 0.01 → 2, 0.5 → 1, 1 → 0, 1e-7 → 7. */
export function decimalsOf(tickSize: number): number {
  const text = tickSize.toString();
  const exponent = /e-(\d+)$/.exec(text);
  if (exponent?.[1] !== undefined) {
    const mantissa = text.slice(0, text.indexOf("e"));
    const fraction = mantissa.split(".")[1]?.length ?? 0;
    return Number(exponent[1]) + fraction;
  }
  return text.split(".")[1]?.length ?? 0;
}

export function fmtNumber(value: number | null | undefined, decimals = 2): string {
  return value === null || value === undefined ? DASH : formatter(decimals).format(value);
}

export function fmtInt(value: number | null | undefined): string {
  return fmtNumber(value, 0);
}

/** With an explicit sign: +1.25, −0.40 (a true minus sign), 0.00. */
export function fmtSigned(value: number | null | undefined, decimals = 2): string {
  if (value === null || value === undefined) return DASH;
  const text = formatter(decimals).format(Math.abs(value));
  if (Number(text.replace(/,/g, "")) === 0) return text;
  return `${value > 0 ? "+" : "−"}${text}`;
}

/** For values already in percent (the API's `change_percent`, `return_pct`). */
export function fmtPercent(value: number | null | undefined, decimals = 2): string {
  return value === null || value === undefined ? DASH : `${fmtSigned(value, decimals)}%`;
}

/** 950, 12.4K, 3.21M. */
export function fmtCompact(value: number | null | undefined): string {
  if (value === null || value === undefined) return DASH;
  const magnitude = Math.abs(value);
  if (magnitude >= 1e9) return `${fmtNumber(value / 1e9, 2)}B`;
  if (magnitude >= 1e6) return `${fmtNumber(value / 1e6, 2)}M`;
  if (magnitude >= 1e4) return `${fmtNumber(value / 1e3, 1)}K`;
  return fmtNumber(value, 0);
}

function pad(value: number, width = 2): string {
  return String(value).padStart(width, "0");
}

/** Simulated time since the start: 01:02:03, or 01:02:03.456 with milliseconds. */
export function fmtSimTime(seconds: number, withMillis = false): string {
  const totalMillis = Math.round(seconds * 1000);
  const millis = totalMillis % 1000;
  const whole = Math.floor(totalMillis / 1000);
  const text = `${pad(Math.floor(whole / 3600))}:${pad(Math.floor(whole / 60) % 60)}:${pad(whole % 60)}`;
  return withMillis ? `${text}.${pad(millis, 3)}` : text;
}

/** A duration for labels: 45s, 5m, 1h 30m. */
export function fmtDuration(seconds: number): string {
  if (seconds < 60) return `${fmtNumber(seconds, seconds % 1 === 0 ? 0 : 1)}s`;
  if (seconds < 3600) return `${fmtNumber(seconds / 60, seconds % 60 === 0 ? 0 : 1)}m`;
  const hours = Math.floor(seconds / 3600);
  const minutes = Math.round((seconds % 3600) / 60);
  return minutes === 0 ? `${hours}h` : `${hours}h ${minutes}m`;
}

/** Tailwind text colour for a signed value. */
export function signClass(value: number | null | undefined): string {
  if (value === null || value === undefined || value === 0) return "text-muted";
  return value > 0 ? "text-bid" : "text-ask";
}

export const STRATEGY_LABELS: Record<Strategy, string> = {
  market_maker: "Market maker",
  random: "Random",
  momentum: "Momentum",
  fundamental: "Fundamental",
  pairs: "Pairs",
  environment: "Environment",
  manual: "You",
};

const STRATEGY_CODES: Record<Strategy, string> = {
  market_maker: "MM",
  random: "RND",
  momentum: "MOM",
  fundamental: "FUN",
  pairs: "PRS",
  environment: "ENV",
  manual: "YOU",
};

/** A short tag for a participant: MM-1, RND-7, ENV, YOU. */
export function participantTag(agent: Pick<AgentOut, "id" | "strategy">): string {
  const code = STRATEGY_CODES[agent.strategy];
  return agent.strategy === "environment" || agent.strategy === "manual"
    ? code
    : `${code}-${agent.id}`;
}
