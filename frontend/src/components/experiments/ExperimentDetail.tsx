"use client";

import { useState, type ReactNode } from "react";

import { Empty, Panel } from "@/components/ui/Panel";
import { Button } from "@/components/ui/controls";
import { useApi } from "@/lib/api-context";
import { usePoll } from "@/lib/hooks";
import type { ExperimentOut } from "@/lib/types";

import { DistributionChart } from "./DistributionChart";
import { fmtClock, isActive, metricLabel, pointName } from "./format";
import { COMPACT_SELECT, ErrorNote, Progress, StatusBadge } from "./parts";
import { PointsTable, SummaryTable } from "./tables";

const PREFERRED_METRIC = "spread_bps";
const LINK =
  "inline-flex h-6 items-center rounded-[2px] border border-line-strong bg-raised px-2 text-[11px] font-medium text-text hover:bg-hover";

/** The right-hand side. On wide screens: a header across, then two rows of two panels that
 * fill the window. Narrower: one column of panels that scrolls, so no table is cut short. */
export const DETAIL_GRID =
  "grid min-h-0 auto-rows-max grid-cols-1 content-start gap-px bg-line lg:overflow-y-auto 2xl:auto-rows-auto 2xl:grid-cols-[minmax(0,1.3fr)_minmax(0,1fr)] 2xl:grid-rows-[auto_minmax(0,1fr)_minmax(0,1fr)] 2xl:content-stretch 2xl:overflow-hidden";

/**
 * One experiment: progress and actions, the distribution of a chosen metric across its points,
 * the points, and every metric of the selected point. ``compare`` fills the last cell.
 */
export function ExperimentDetail({
  experiment,
  compare,
  onChanged,
  onDeleted,
}: {
  experiment: ExperimentOut;
  compare: ReactNode;
  onChanged: () => void;
  onDeleted: () => void;
}) {
  const api = useApi();
  const id = experiment.id;
  const detail = usePoll(
    `detail:${id}`,
    () => api.getExperiment(id),
    (data) => (isActive(data.status) ? 1000 : null),
    // The list notices cancellations and completions too; the detail then refreshes at once.
    experiment.status,
  );
  const current = detail.data ?? experiment;
  const runs = usePoll(
    `runs:${id}`,
    () => api.experimentRuns(id),
    null,
    `${current.status}:${current.runs_done}`,
  );

  const [chosenMetric, setChosenMetric] = useState<string | null>(null);
  const [selectedPoint, setSelectedPoint] = useState(0);
  const [confirmDelete, setConfirmDelete] = useState(false);
  const [busy, setBusy] = useState(false);
  const [actionError, setActionError] = useState<unknown>(null);

  const points = detail.data?.results ?? [];
  const metrics = [...new Set(points.flatMap((point) => point.summary.map((s) => s.metric)))];
  const metric =
    chosenMetric !== null && metrics.includes(chosenMetric)
      ? chosenMetric
      : metrics.includes(PREFERRED_METRIC)
        ? PREFERRED_METRIC
        : (metrics[0] ?? PREFERRED_METRIC);
  const point = points.find((p) => p.point === selectedPoint) ?? points[0];

  async function act(action: () => Promise<unknown>, after: () => void) {
    setBusy(true);
    setActionError(null);
    try {
      await action();
      after();
    } catch (caught) {
      setActionError(caught);
    } finally {
      setBusy(false);
    }
  }

  const actions = (
    <>
      <a className={LINK} href={api.runsCsvUrl(id)} download>
        Runs CSV
      </a>
      <a className={LINK} href={api.summaryCsvUrl(id)} download>
        Summary CSV
      </a>
      {isActive(current.status) && (
        <Button
          disabled={busy}
          onClick={() =>
            act(
              () => api.cancelExperiment(id),
              () => {
                detail.refresh();
                onChanged();
              },
            )
          }
        >
          Cancel runs
        </Button>
      )}
      {confirmDelete ? (
        <>
          <Button
            tone="ask"
            disabled={busy}
            onClick={() => act(() => api.deleteExperiment(id), onDeleted)}
          >
            Confirm delete
          </Button>
          <Button tone="ghost" onClick={() => setConfirmDelete(false)}>
            Keep
          </Button>
        </>
      ) : (
        <Button tone="ghost" onClick={() => setConfirmDelete(true)}>
          Delete
        </Button>
      )}
    </>
  );

  return (
    <div className={DETAIL_GRID} data-testid="experiment-detail" data-id={id}>
      <Panel
        title="Experiment"
        actions={actions}
        className="2xl:col-span-2"
        bodyClassName="overflow-visible"
      >
        <div className="flex flex-wrap items-center gap-x-5 gap-y-1.5 px-2.5 py-2">
          <div className="flex min-w-0 items-baseline gap-2">
            <span
              className="truncate text-[13px] font-medium text-text"
              data-testid="experiment-name"
            >
              {current.name}
            </span>
            <span className="num text-[10.5px] text-muted">{id}</span>
          </div>
          <StatusBadge status={current.status} />
          <Progress
            done={current.runs_done}
            total={current.runs_total}
            status={current.status}
            className="w-52"
          />
          <Stat label="Points" value={String(current.points)} />
          <Stat label="Runs / point" value={String(current.request.runs ?? "—")} />
          <Stat label="Duration" value={`${current.request.duration_seconds ?? "—"} s`} />
          <Stat label="Seeds" value={`${current.request.base_seed ?? 1} + k`} />
          <Stat label="Created" value={fmtClock(current.created_at)} />
          <Stat label="Finished" value={fmtClock(current.finished_at)} />
        </div>
        {(current.error !== null || actionError !== null || detail.error !== null) && (
          <div className="flex flex-col gap-1 px-2.5 pb-2">
            {current.error !== null && <ErrorNote error={new Error(current.error)} />}
            <ErrorNote error={actionError} />
            <ErrorNote error={detail.error} />
          </div>
        )}
      </Panel>
      <Panel
        title="Distribution"
        testId="distribution-panel"
        className="h-[400px] 2xl:h-auto"
        actions={
          <select
            aria-label="Metric"
            className={`${COMPACT_SELECT} h-5 w-56 text-[10.5px]`}
            value={metric}
            disabled={metrics.length === 0}
            onChange={(event) => setChosenMetric(event.target.value)}
          >
            {metrics.length === 0 && <option value={metric}>{metricLabel(metric)}</option>}
            {metrics.map((name) => (
              <option key={name} value={name}>
                {metricLabel(name)}
              </option>
            ))}
          </select>
        }
        bodyClassName="overflow-hidden"
      >
        {points.length === 0 ? (
          <Empty>{detail.data === undefined ? "Loading…" : "No runs stored yet"}</Empty>
        ) : (
          <DistributionChart
            points={points}
            runs={runs.data ?? []}
            metric={metric}
            selectedPoint={point?.point ?? 0}
            onSelectPoint={setSelectedPoint}
          />
        )}
      </Panel>
      <Panel title="Points" className="max-2xl:max-h-[320px]">
        <PointsTable
          points={points}
          metric={metric}
          selectedPoint={point?.point ?? 0}
          onSelectPoint={setSelectedPoint}
        />
      </Panel>
      <Panel
        title={point === undefined ? "Summary" : `Summary · ${pointName(point)}`}
        className="max-2xl:max-h-[600px]"
      >
        <SummaryTable point={point} metric={metric} onSelectMetric={setChosenMetric} />
      </Panel>
      {compare}
    </div>
  );
}

function Stat({ label, value }: { label: string; value: string }) {
  return (
    <div className="flex items-baseline gap-1.5">
      <span className="label-caps">{label}</span>
      <span className="num text-[11px] text-text">{value}</span>
    </div>
  );
}
