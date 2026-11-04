/**
 * The new-simulation form: its fields, the backend's defaults and limits (docs/api.md,
 * backend/udeshi_api/schemas.py), and the conversion of what was typed into a request.
 *
 * The limits only shape the inputs (spinner bounds, invalid styling). The server is the
 * authority: out-of-range values are sent and its 422 explains them. Locally, only text that is
 * not a number at all is refused, and seeds a browser cannot send exactly.
 */
import type { SimulationCreate } from "@/lib/types";

export type RunKey =
  | "seed"
  | "duration_minutes"
  | "speed"
  | "initial_cash"
  | "order_latency_ms"
  | "bar_interval_seconds";
export type AgentKey =
  | "random_traders"
  | "market_makers"
  | "momentum_traders"
  | "fundamental_traders"
  | "pairs_traders"
  | "order_frequency";
export type MarketKey =
  | "volatility"
  | "volatile_multiplier"
  | "news_per_hour"
  | "news_impact"
  | "shocks_per_hour"
  | "shock_impact"
  | "liquidity_shocks_per_hour"
  | "liquidity_shock_quantity";
export type FieldKey = RunKey | AgentKey | MarketKey;

export interface FieldSpec<K extends FieldKey = FieldKey> {
  key: K;
  label: string;
  min: number;
  max: number;
  /** Whole numbers only (step 1); otherwise any value. */
  integer?: boolean;
  hint?: string;
}

const HOUR_RATE = 3_600;
const MAX_AGENTS = 500;
/** The largest seed a browser can send exactly (the API accepts up to 2^64 - 1). */
export const MAX_BROWSER_SEED = Number.MAX_SAFE_INTEGER;

export const RUN_FIELDS: readonly FieldSpec<RunKey>[] = [
  { key: "seed", label: "Seed", min: 0, max: MAX_BROWSER_SEED, integer: true },
  {
    key: "duration_minutes",
    label: "Duration (min)",
    min: 0,
    max: 7 * 24 * 60,
    hint: "Simulated time; more than zero, at most a week",
  },
  {
    key: "speed",
    label: "Speed",
    min: 0,
    max: 10_000,
    hint: "Simulated seconds per wall-clock second",
  },
  { key: "initial_cash", label: "Initial cash", min: 0, max: 1e15, hint: "Every participant's" },
  { key: "order_latency_ms", label: "Order latency (ms)", min: 0, max: 60_000 },
  { key: "bar_interval_seconds", label: "Bar interval (s)", min: 0.001, max: 7 * 24 * 3_600 },
];

export const AGENT_FIELDS: readonly FieldSpec<AgentKey>[] = [
  { key: "random_traders", label: "Random traders", min: 0, max: MAX_AGENTS, integer: true },
  { key: "market_makers", label: "Market makers", min: 0, max: MAX_AGENTS, integer: true },
  { key: "momentum_traders", label: "Momentum traders", min: 0, max: MAX_AGENTS, integer: true },
  {
    key: "fundamental_traders",
    label: "Fundamental traders",
    min: 0,
    max: MAX_AGENTS,
    integer: true,
  },
  { key: "pairs_traders", label: "Pairs traders", min: 0, max: MAX_AGENTS, integer: true },
  {
    key: "order_frequency",
    label: "Order frequency",
    min: 0,
    max: 100,
    hint: "Scales how often every agent acts (2: twice as often)",
  },
];

export const MARKET_FIELDS: readonly FieldSpec<MarketKey>[] = [
  {
    key: "volatility",
    label: "Volatility",
    min: 0,
    max: 10,
    hint: "Log-volatility of fundamental values per √hour",
  },
  {
    key: "volatile_multiplier",
    label: "Volatile multiplier",
    min: 1,
    max: 100,
    hint: "Volatility in the volatile regime, relative to calm",
  },
  { key: "news_per_hour", label: "News per hour", min: 0, max: HOUR_RATE, hint: "Per instrument" },
  { key: "news_impact", label: "News impact", min: 0, max: 1, hint: "Mean log impact" },
  {
    key: "shocks_per_hour",
    label: "Shocks per hour",
    min: 0,
    max: HOUR_RATE,
    hint: "Unannounced jumps in fundamental value",
  },
  { key: "shock_impact", label: "Shock impact", min: 0, max: 1, hint: "Mean log impact" },
  {
    key: "liquidity_shocks_per_hour",
    label: "Liquidity shocks per hour",
    min: 0,
    max: HOUR_RATE,
    hint: "Forced market orders",
  },
  {
    key: "liquidity_shock_quantity",
    label: "Liquidity shock quantity",
    min: 1,
    max: 1_000_000_000,
    integer: true,
  },
];

export const AGENT_COUNT_KEYS = [
  "random_traders",
  "market_makers",
  "momentum_traders",
  "fundamental_traders",
  "pairs_traders",
] as const satisfies readonly AgentKey[];

export type FormValues = Record<FieldKey, string> & {
  /** Trade the cointegrated ACME/NOVA pair (off: no pairs). */
  pair: boolean;
  startNow: boolean;
};

/** The backend's defaults (SimulationCreate, AgentsIn and MarketIn). */
export const DEFAULT_FORM: FormValues = {
  seed: "1",
  duration_minutes: "30",
  speed: "1",
  initial_cash: "1000000",
  order_latency_ms: "0",
  bar_interval_seconds: "1",
  random_traders: "10",
  market_makers: "5",
  momentum_traders: "5",
  fundamental_traders: "5",
  pairs_traders: "1",
  order_frequency: "1",
  volatility: "0.02",
  volatile_multiplier: "3",
  news_per_hour: "6",
  news_impact: "0.01",
  shocks_per_hour: "2",
  shock_impact: "0.005",
  liquidity_shocks_per_hour: "2",
  liquidity_shock_quantity: "200",
  pair: true,
  startNow: true,
};

export const DEFAULT_PAIR = { leader: "ACME", follower: "NOVA" } as const;

const LABELS: Record<FieldKey, string> = Object.fromEntries(
  [...RUN_FIELDS, ...AGENT_FIELDS, ...MARKET_FIELDS].map((field) => [field.key, field.label]),
) as Record<FieldKey, string>;

/** The number typed in a field, or null when it is not one. */
export function parseField(text: string): number | null {
  const trimmed = text.trim();
  if (trimmed === "") return null;
  const value = Number(trimmed);
  return Number.isFinite(value) ? value : null;
}

/** How many agents the form asks for (null while a count is not a number). */
export function agentTotal(values: FormValues): number | null {
  let total = 0;
  for (const key of AGENT_COUNT_KEYS) {
    const count = parseField(values[key]);
    if (count === null) return null;
    total += count;
  }
  return total;
}

export type FormResult = { request: SimulationCreate } | { problems: string[] };

/** The request for ``values``, or the fields that are not numbers. */
export function toRequest(values: FormValues): FormResult {
  const problems: string[] = [];
  const numbers = {} as Record<FieldKey, number>;
  for (const key of Object.keys(LABELS) as FieldKey[]) {
    const value = parseField(values[key]);
    if (value === null) problems.push(`${LABELS[key]}: enter a number`);
    else numbers[key] = value;
  }
  if (problems.length === 0 && !Number.isSafeInteger(numbers.seed)) {
    problems.push(
      `Seed: a whole number from 0 to ${MAX_BROWSER_SEED} (larger seeds cannot be sent exactly)`,
    );
  }
  if (problems.length > 0) return { problems };
  return {
    request: {
      seed: numbers.seed,
      duration_seconds: numbers.duration_minutes * 60,
      speed: numbers.speed,
      initial_cash: numbers.initial_cash,
      order_latency_ms: numbers.order_latency_ms,
      bar_interval_seconds: numbers.bar_interval_seconds,
      agents: {
        random_traders: numbers.random_traders,
        market_makers: numbers.market_makers,
        momentum_traders: numbers.momentum_traders,
        fundamental_traders: numbers.fundamental_traders,
        pairs_traders: numbers.pairs_traders,
        order_frequency: numbers.order_frequency,
      },
      market: {
        volatility: numbers.volatility,
        volatile_multiplier: numbers.volatile_multiplier,
        news_per_hour: numbers.news_per_hour,
        news_impact: numbers.news_impact,
        shocks_per_hour: numbers.shocks_per_hour,
        shock_impact: numbers.shock_impact,
        liquidity_shocks_per_hour: numbers.liquidity_shocks_per_hour,
        liquidity_shock_quantity: numbers.liquidity_shock_quantity,
        pairs: values.pair ? [{ ...DEFAULT_PAIR }] : [],
      },
    },
  };
}
