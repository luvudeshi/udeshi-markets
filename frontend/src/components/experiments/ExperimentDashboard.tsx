"use client";

import { useState } from "react";

import { Empty, Panel } from "@/components/ui/Panel";
import { useApi } from "@/lib/api-context";
import { usePoll } from "@/lib/hooks";

import { ComparePanel } from "./ComparePanel";
import { DETAIL_GRID, ExperimentDetail } from "./ExperimentDetail";
import { ExperimentForm } from "./ExperimentForm";
import { ExperimentList } from "./ExperimentList";
import { isActive } from "./format";
import { ErrorNote } from "./parts";

/**
 * The research view: configure and queue experiments on the left, study the selected one on the
 * right (distributions per sweep point, summaries) and compare any two points. Experiments run
 * in the backend's worker processes; this page polls their progress.
 */
export function ExperimentDashboard() {
  const api = useApi();
  const list = usePoll(
    "experiments",
    () => api.listExperiments(),
    (experiments) => (experiments.some((e) => isActive(e.status)) ? 1000 : 5000),
  );
  const experiments = list.data ?? [];
  // Until one is chosen, the most recent experiment is shown.
  const [chosen, setChosen] = useState<string | null>(null);
  const selected =
    chosen === null
      ? experiments.at(-1)
      : experiments.find((experiment) => experiment.id === chosen);

  const compare = <ComparePanel experiments={experiments} selected={selected} />;

  return (
    <div className="grid min-h-full grid-cols-1 gap-px bg-line lg:h-full lg:grid-cols-[340px_minmax(0,1fr)] 2xl:grid-cols-[380px_minmax(0,1fr)]">
      {/* The form keeps its full height; the list takes the rest, and the column scrolls when
          the window is too short for both. */}
      <div className="flex min-h-0 flex-col gap-px bg-line lg:overflow-y-auto">
        <Panel title="New experiment" className="shrink-0">
          <ExperimentForm
            onCreated={(experiment) => {
              setChosen(experiment.id);
              list.refresh();
            }}
          />
        </Panel>
        <div className="flex h-[360px] shrink-0 flex-col lg:h-auto lg:min-h-[240px] lg:flex-1">
          <Panel
            title="Experiments"
            testId="experiment-list"
            className="flex-1"
            actions={<span className="num text-[10.5px] text-muted">{experiments.length}</span>}
          >
            {list.error !== null && (
              <div className="p-2">
                <ErrorNote error={list.error} />
              </div>
            )}
            {list.data === undefined && list.error === null ? (
              <Empty>Loading…</Empty>
            ) : (
              <ExperimentList
                experiments={experiments}
                selectedId={selected?.id ?? null}
                onSelect={setChosen}
              />
            )}
          </Panel>
        </div>
      </div>
      {selected === undefined ? (
        <div className={DETAIL_GRID}>
          <Panel title="Experiment" className="h-[240px] 2xl:col-span-2 2xl:row-span-2 2xl:h-auto">
            <Empty>
              {experiments.length === 0
                ? "Run an experiment to see its results here."
                : "Select an experiment."}
            </Empty>
          </Panel>
          <Panel title="Summary">
            <Empty>No experiment selected</Empty>
          </Panel>
          {compare}
        </div>
      ) : (
        <ExperimentDetail
          key={selected.id}
          experiment={selected}
          compare={compare}
          onChanged={list.refresh}
          onDeleted={() => {
            setChosen(null);
            list.refresh();
          }}
        />
      )}
    </div>
  );
}
