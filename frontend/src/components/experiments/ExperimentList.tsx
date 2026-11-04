"use client";

import { Empty } from "@/components/ui/Panel";
import type { ExperimentOut } from "@/lib/types";

import { fmtClock } from "./format";
import { Progress, StatusBadge } from "./parts";

/** Every experiment, newest first; selecting one shows it on the right. */
export function ExperimentList({
  experiments,
  selectedId,
  onSelect,
}: {
  experiments: ExperimentOut[];
  selectedId: string | null;
  onSelect: (id: string) => void;
}) {
  if (experiments.length === 0) {
    return <Empty>No experiments yet. Configure one above and run it.</Empty>;
  }
  return (
    <ul className="divide-y divide-line" aria-label="Experiments">
      {[...experiments].reverse().map((experiment) => {
        const selected = experiment.id === selectedId;
        return (
          <li key={experiment.id}>
            <button
              type="button"
              onClick={() => onSelect(experiment.id)}
              aria-current={selected ? "true" : undefined}
              data-testid="experiment-row"
              data-id={experiment.id}
              data-status={experiment.status}
              className={`flex w-full flex-col gap-1 border-l-2 px-2.5 py-1.5 text-left transition-colors ${
                selected ? "border-accent bg-raised" : "border-transparent hover:bg-hover"
              }`}
            >
              <div className="flex items-center justify-between gap-2">
                <span className="truncate font-medium text-text" title={experiment.name}>
                  {experiment.name}
                </span>
                <StatusBadge status={experiment.status} />
              </div>
              <Progress
                done={experiment.runs_done}
                total={experiment.runs_total}
                status={experiment.status}
              />
              <div className="num flex justify-between text-[10.5px] text-muted">
                <span>{experiment.id}</span>
                <span>
                  {experiment.points} {experiment.points === 1 ? "point" : "points"} ·{" "}
                  {fmtClock(experiment.created_at)}
                </span>
              </div>
            </button>
          </li>
        );
      })}
    </ul>
  );
}
