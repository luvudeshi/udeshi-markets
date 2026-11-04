"use client";

import { useState, type FormEvent } from "react";

import { Button, Field, Select, TextInput } from "@/components/ui/controls";
import { useApi } from "@/lib/api-context";
import type { ExperimentIn, ExperimentOut } from "@/lib/types";

import { ErrorNote, SubHeading } from "./parts";

/** Settings a sweep can vary (ExperimentIn.sweep paths), with how the form labels them. */
export const SWEEP_PATHS = [
  "population.market_makers",
  "population.random_traders",
  "population.momentum_traders",
  "population.fundamental_traders",
  "population.pairs_traders",
  "population.order_frequency",
  "environment.volatility",
  "environment.news_per_hour",
  "environment.news_impact",
  "environment.shocks_per_hour",
  "environment.liquidity_shocks_per_hour",
] as const;
type SweepPath = (typeof SWEEP_PATHS)[number];
const MAX_SWEEPS = 5; // ExperimentIn.sweep allows five settings.

type NumberKey =
  | "runs"
  | "duration"
  | "baseSeed"
  | "latency"
  | "random"
  | "makers"
  | "momentum"
  | "fundamental"
  | "pairs"
  | "frequency"
  | "volatility"
  | "newsPerHour"
  | "newsImpact"
  | "shocksPerHour"
  | "liquidityShocksPerHour";

interface SweepRow {
  id: number;
  path: SweepPath;
  values: string;
}

// The backend's defaults (ExperimentIn, AgentsIn, MarketIn), as the form's starting values.
const DEFAULTS: Record<NumberKey, string> = {
  runs: "10",
  duration: "300",
  baseSeed: "1",
  latency: "0",
  random: "10",
  makers: "5",
  momentum: "5",
  fundamental: "5",
  pairs: "1",
  frequency: "1",
  volatility: "0.02",
  newsPerHour: "6",
  newsImpact: "0.01",
  shocksPerHour: "2",
  liquidityShocksPerHour: "2",
};

interface FieldSpec {
  key: NumberKey;
  label: string;
  hint?: string;
}

const RUN_FIELDS: FieldSpec[] = [
  { key: "runs", label: "Runs", hint: "Seeds per point" },
  { key: "duration", label: "Duration (s)", hint: "Simulated seconds per run" },
  { key: "baseSeed", label: "Base seed", hint: "Run k uses seed base + k" },
  { key: "latency", label: "Latency (ms)", hint: "Order latency" },
];
const AGENT_FIELDS: FieldSpec[] = [
  { key: "random", label: "Random" },
  { key: "makers", label: "Market makers" },
  { key: "momentum", label: "Momentum" },
  { key: "fundamental", label: "Fundamental" },
  { key: "pairs", label: "Pairs" },
  {
    key: "frequency",
    label: "Frequency",
    hint: "Order frequency: 2 makes every agent act twice as often",
  },
];
const MARKET_FIELDS: FieldSpec[] = [
  { key: "volatility", label: "Volatility", hint: "Log-volatility of fundamentals per sqrt(hour)" },
  { key: "newsPerHour", label: "News / h", hint: "Per instrument" },
  { key: "newsImpact", label: "News impact", hint: "Mean log impact" },
  { key: "shocksPerHour", label: "Shocks / h", hint: "Unannounced jumps" },
  { key: "liquidityShocksPerHour", label: "Liq. shocks / h", hint: "Forced market orders" },
];
const LABELS = Object.fromEntries(
  [...RUN_FIELDS, ...AGENT_FIELDS, ...MARKET_FIELDS].map((f) => [f.key, f.label]),
) as Record<NumberKey, string>;

/** The request, or what is wrong with the form (checked before anything is sent). */
function buildRequest(
  name: string,
  values: Record<NumberKey, string>,
  sweeps: SweepRow[],
): { body: ExperimentIn } | { problem: string } {
  const numbers = {} as Record<NumberKey, number>;
  for (const key of Object.keys(values) as NumberKey[]) {
    const text = values[key].trim();
    const value = Number(text);
    if (text === "" || !Number.isFinite(value))
      return { problem: `${LABELS[key]} must be a number` };
    numbers[key] = value;
  }
  if (numbers.baseSeed > Number.MAX_SAFE_INTEGER) {
    return { problem: `Base seed must be at most ${Number.MAX_SAFE_INTEGER} here` };
  }
  const sweep: Record<string, number[]> = {};
  for (const row of sweeps) {
    const parts = row.values.split(/[\s,;]+/).filter((part) => part !== "");
    const parsed = parts.map(Number);
    if (parsed.length === 0) return { problem: `Sweep ${row.path}: give at least one value` };
    if (parsed.some((value) => !Number.isFinite(value))) {
      return { problem: `Sweep ${row.path}: values must be numbers, separated by commas` };
    }
    sweep[row.path] = parsed;
  }
  return {
    body: {
      name: name.trim() === "" ? "experiment" : name.trim(),
      runs: numbers.runs,
      duration_seconds: numbers.duration,
      base_seed: numbers.baseSeed,
      order_latency_ms: numbers.latency,
      agents: {
        random_traders: numbers.random,
        market_makers: numbers.makers,
        momentum_traders: numbers.momentum,
        fundamental_traders: numbers.fundamental,
        pairs_traders: numbers.pairs,
        order_frequency: numbers.frequency,
      },
      market: {
        volatility: numbers.volatility,
        news_per_hour: numbers.newsPerHour,
        news_impact: numbers.newsImpact,
        shocks_per_hour: numbers.shocksPerHour,
        liquidity_shocks_per_hour: numbers.liquidityShocksPerHour,
      },
      sweep: sweeps.length === 0 ? null : sweep,
    },
  };
}

/** Creates an experiment: a market run with many seeds, optionally across a parameter sweep. */
export function ExperimentForm({ onCreated }: { onCreated: (experiment: ExperimentOut) => void }) {
  const api = useApi();
  const [name, setName] = useState("experiment");
  const [values, setValues] = useState<Record<NumberKey, string>>(DEFAULTS);
  const [sweeps, setSweeps] = useState<SweepRow[]>([]);
  const [nextRow, setNextRow] = useState(1);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<unknown>(null);

  const used = new Set(sweeps.map((row) => row.path));
  const free = SWEEP_PATHS.filter((path) => !used.has(path));

  function field(spec: FieldSpec) {
    return (
      <Field key={spec.key} label={spec.label} hint={spec.hint}>
        <TextInput
          inputMode="decimal"
          value={values[spec.key]}
          onChange={(event) =>
            setValues((current) => ({ ...current, [spec.key]: event.target.value }))
          }
        />
      </Field>
    );
  }

  async function submit(event: FormEvent) {
    event.preventDefault();
    const request = buildRequest(name, values, sweeps);
    if ("problem" in request) {
      setError(new Error(request.problem));
      return;
    }
    setSubmitting(true);
    setError(null);
    try {
      onCreated(await api.createExperiment(request.body));
    } catch (caught) {
      setError(caught);
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <form
      className="flex flex-col gap-3 p-2.5"
      onSubmit={submit}
      data-testid="experiment-form"
      noValidate
    >
      <div className="flex flex-col gap-2">
        <SubHeading>Run</SubHeading>
        <Field label="Name">
          <TextInput
            value={name}
            maxLength={100}
            onChange={(event) => setName(event.target.value)}
          />
        </Field>
        <div className="grid grid-cols-2 items-end gap-2 2xl:grid-cols-4">
          {RUN_FIELDS.map(field)}
        </div>
      </div>
      <div className="flex flex-col gap-2">
        <SubHeading>Agents</SubHeading>
        <div className="grid grid-cols-3 items-end gap-2">{AGENT_FIELDS.map(field)}</div>
      </div>
      <div className="flex flex-col gap-2">
        <SubHeading>Market</SubHeading>
        <div className="grid grid-cols-3 items-end gap-2">{MARKET_FIELDS.map(field)}</div>
      </div>
      <div className="flex flex-col gap-2">
        <SubHeading>Sweep</SubHeading>
        {sweeps.length === 0 && (
          <p className="text-[11px] leading-4 text-muted">
            No sweep: one point. Add settings to vary; every combination of their values becomes a
            point, each run with the same seeds.
          </p>
        )}
        {sweeps.map((row, index) => (
          <div
            key={row.id}
            className="grid grid-cols-[minmax(0,1fr)_96px_auto] items-center gap-1.5"
          >
            <Select
              aria-label={`Sweep setting ${index + 1}`}
              value={row.path}
              onChange={(event) => {
                const path = event.target.value as SweepPath;
                setSweeps((current) => current.map((r) => (r.id === row.id ? { ...r, path } : r)));
              }}
            >
              {SWEEP_PATHS.map((path) => (
                <option key={path} value={path} disabled={path !== row.path && used.has(path)}>
                  {path}
                </option>
              ))}
            </Select>
            <TextInput
              aria-label={`Sweep values ${index + 1}`}
              placeholder="1, 3, 5"
              value={row.values}
              onChange={(event) => {
                const text = event.target.value;
                setSweeps((current) =>
                  current.map((r) => (r.id === row.id ? { ...r, values: text } : r)),
                );
              }}
            />
            <Button
              tone="ghost"
              aria-label={`Remove sweep ${index + 1}`}
              onClick={() => setSweeps((current) => current.filter((r) => r.id !== row.id))}
            >
              ×
            </Button>
          </div>
        ))}
        <div>
          <Button
            disabled={free.length === 0 || sweeps.length >= MAX_SWEEPS}
            onClick={() => {
              const path = free[0];
              if (path === undefined) return;
              setSweeps((current) => [...current, { id: nextRow, path, values: "" }]);
              setNextRow((value) => value + 1);
            }}
          >
            + Add sweep
          </Button>
        </div>
      </div>
      <ErrorNote error={error} testId="experiment-form-error" />
      <Button
        type="submit"
        tone="primary"
        disabled={submitting}
        data-testid="experiment-create"
        className="h-7"
      >
        {submitting ? "Queuing…" : "Run experiment"}
      </Button>
    </form>
  );
}
