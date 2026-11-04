"use client";

import { useState } from "react";

import { Empty, Panel } from "@/components/ui/Panel";
import { Button } from "@/components/ui/controls";
import { useApi } from "@/lib/api-context";
import type { ComparisonResultOut, ExperimentOut } from "@/lib/types";

import { fmtDf, fmtDifference, fmtMetric, fmtP, fmtT, metricLabel, pointName } from "./format";
import { COMPACT_SELECT, ErrorNote } from "./parts";
import { Th } from "./tables";
import { useResource } from "./useResource";

/** Two-sided p-values below this are marked significant (a display threshold). */
const SIGNIFICANCE = 0.05;
const TD = "num px-1.5 py-[3px] text-right whitespace-nowrap";
const LINK =
  "inline-flex h-6 items-center rounded-[2px] border border-line-strong bg-raised px-2 text-[11px] font-medium text-text hover:bg-hover";

interface Side {
  id: string;
  point: number;
}

/** One side of the comparison: an experiment and one of its points. */
function SidePicker({
  name,
  side,
  experiments,
  onChange,
}: {
  name: "A" | "B";
  side: Side | null;
  experiments: ExperimentOut[];
  onChange: (side: Side) => void;
}) {
  const api = useApi();
  const experiment = experiments.find((e) => e.id === side?.id);
  const detail = useResource(side === null ? null : `compare-points:${side.id}`, () =>
    api.getExperiment(side?.id ?? ""),
  );
  const labels = new Map((detail.data?.results ?? []).map((p) => [p.point, pointName(p)]));
  const count = experiment?.points ?? 0;
  return (
    <div className="flex min-w-0 items-center gap-1.5">
      <span className="label-caps w-3 shrink-0 text-text">{name}</span>
      <select
        aria-label={`Experiment ${name}`}
        className={`${COMPACT_SELECT} h-6 flex-1 text-[11px]`}
        value={side?.id ?? ""}
        onChange={(event) => onChange({ id: event.target.value, point: 0 })}
      >
        {side === null && <option value="">Choose an experiment</option>}
        {[...experiments].reverse().map((e) => (
          <option key={e.id} value={e.id}>
            {e.name} · {e.id}
          </option>
        ))}
      </select>
      <select
        aria-label={`Point ${name}`}
        className={`${COMPACT_SELECT} h-6 w-44 shrink-0 text-[11px]`}
        value={side?.point ?? 0}
        disabled={side === null}
        onChange={(event) =>
          side !== null && onChange({ id: side.id, point: Number(event.target.value) })
        }
      >
        {Array.from({ length: Math.max(count, 1) }, (_, point) => (
          <option key={point} value={point}>
            {point} · {labels.get(point) ?? "…"}
          </option>
        ))}
      </select>
    </div>
  );
}

/**
 * Welch's t-test, metric by metric, between two experiment points (any two experiments). The
 * statistics are the backend's (`GET /api/experiments/compare`).
 */
export function ComparePanel({
  experiments,
  selected,
}: {
  experiments: ExperimentOut[];
  selected: ExperimentOut | undefined;
}) {
  const api = useApi();
  const [chosenA, setChosenA] = useState<Side | null>(null);
  const [chosenB, setChosenB] = useState<Side | null>(null);
  const [result, setResult] = useState<{ key: string; data: ComparisonResultOut } | null>(null);
  const [error, setError] = useState<unknown>(null);
  const [busy, setBusy] = useState(false);

  // Until the user chooses, compare the selected experiment's first two points.
  const a = chosenA ?? (selected === undefined ? null : { id: selected.id, point: 0 });
  const b =
    chosenB ??
    (selected === undefined ? null : { id: selected.id, point: Math.min(1, selected.points - 1) });
  const key = a === null || b === null ? null : `${a.id}:${a.point}|${b.id}:${b.point}`;
  const shown = result !== null && result.key === key ? result.data : null;

  async function run() {
    if (a === null || b === null || key === null) return;
    setBusy(true);
    setError(null);
    try {
      setResult({ key, data: await api.compare(a.id, a.point, b.id, b.point) });
    } catch (caught) {
      setError(caught);
    } finally {
      setBusy(false);
    }
  }

  return (
    <Panel
      title="Compare"
      testId="compare-panel"
      className="max-2xl:max-h-[600px]"
      bodyClassName="flex flex-col"
    >
      <div className="flex flex-col gap-1.5 border-b border-line p-2">
        <SidePicker name="A" side={a} experiments={experiments} onChange={setChosenA} />
        <SidePicker name="B" side={b} experiments={experiments} onChange={setChosenB} />
        <div className="flex items-center justify-between gap-2">
          <span className="text-[10.5px] text-muted">
            Welch&apos;s t-test of B against A; p &lt; {SIGNIFICANCE} highlighted.
          </span>
          <div className="flex items-center gap-1">
            {a !== null && b !== null && (
              <a className={LINK} href={api.compareCsvUrl(a.id, a.point, b.id, b.point)} download>
                CSV
              </a>
            )}
            <Button
              tone="primary"
              disabled={key === null || busy}
              onClick={run}
              data-testid="compare-run"
            >
              {busy ? "Comparing…" : "Compare"}
            </Button>
          </div>
        </div>
        <ErrorNote error={error} testId="compare-error" />
      </div>
      <div className="min-h-0 flex-1 overflow-auto">
        {shown === null ? (
          <Empty>Choose two points and compare them</Empty>
        ) : (
          <table className="w-full border-collapse text-[11px]" data-testid="compare-table">
            <thead>
              <tr className="border-b border-line">
                <Th>Metric</Th>
                <Th title={shown.a.label}>Mean A</Th>
                <Th title={shown.b.label}>Mean B</Th>
                <Th title="Mean B minus mean A">B − A</Th>
                <Th title="Welch's t statistic">t</Th>
                <Th title="Welch–Satterthwaite degrees of freedom">df</Th>
                <Th title="Two-sided p-value">p</Th>
              </tr>
            </thead>
            <tbody>
              {shown.metrics.map((row) => {
                const significant = row.p_value !== null && row.p_value < SIGNIFICANCE;
                return (
                  <tr
                    key={row.metric}
                    data-testid="compare-row"
                    data-metric={row.metric}
                    data-significant={significant}
                    className={`border-b border-line/60 ${significant ? "bg-accent-soft" : ""}`}
                  >
                    <td className="min-w-28 px-1.5 py-[3px] leading-tight" title={row.metric}>
                      {metricLabel(row.metric)}
                    </td>
                    <td className={TD} data-column="mean_a">
                      {fmtMetric(row.mean_a)}
                    </td>
                    <td className={TD} data-column="mean_b">
                      {fmtMetric(row.mean_b)}
                    </td>
                    <td className={TD} data-column="difference">
                      {fmtDifference(row.difference)}
                    </td>
                    <td className={`${TD} text-muted`} data-column="t">
                      {fmtT(row.t)}
                    </td>
                    <td className={`${TD} text-muted`} data-column="df">
                      {fmtDf(row.df)}
                    </td>
                    <td className={`${TD} ${significant ? "text-accent" : ""}`} data-column="p">
                      {fmtP(row.p_value)}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        )}
      </div>
    </Panel>
  );
}
