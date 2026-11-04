"use client";

import type { ReactNode } from "react";

import { describeError } from "@/lib/errors";
import type { ExperimentStatus } from "@/lib/types";

const STATUS_LOOK: Record<ExperimentStatus, { text: string; dot: string }> = {
  queued: { text: "text-muted", dot: "bg-muted" },
  running: { text: "text-accent", dot: "bg-accent animate-pulse" },
  completed: { text: "text-bid", dot: "bg-bid" },
  failed: { text: "text-ask", dot: "bg-ask" },
  cancelled: { text: "text-faint", dot: "bg-faint" },
};

export function StatusBadge({ status }: { status: ExperimentStatus }) {
  const look = STATUS_LOOK[status];
  return (
    <span
      className={`inline-flex items-center gap-1.5 ${look.text}`}
      data-testid="experiment-status"
    >
      <span className={`size-1.5 shrink-0 rounded-full ${look.dot}`} aria-hidden="true" />
      <span className="label-caps text-inherit">{status}</span>
    </span>
  );
}

/** Runs done out of runs total, as a bar and as numbers. */
export function Progress({
  done,
  total,
  status,
  className = "",
}: {
  done: number;
  total: number;
  status: ExperimentStatus;
  className?: string;
}) {
  const filled = total > 0 ? `${(100 * Math.min(done, total)) / total}%` : "0%";
  const color = status === "failed" ? "bg-ask" : status === "completed" ? "bg-bid" : "bg-accent";
  return (
    <div className={`flex items-center gap-2 ${className}`}>
      <div
        className="h-1 min-w-10 flex-1 bg-line-strong"
        role="progressbar"
        aria-valuemin={0}
        aria-valuemax={total}
        aria-valuenow={done}
        aria-label="Runs done"
      >
        <div className={`h-full ${color}`} style={{ width: filled }} />
      </div>
      <span className="num shrink-0 text-[10.5px] text-muted">
        {done}/{total}
      </span>
    </div>
  );
}

/** A failure as lines of text: "message (code)", then each validation detail. */
export function errorLines(error: unknown): string[] {
  const { title, lines } = describeError(error);
  return [title, ...lines];
}

export function ErrorNote({ error, testId }: { error: unknown; testId?: string }) {
  if (error === null || error === undefined) return null;
  return (
    <div
      role="alert"
      data-testid={testId}
      className="border-l-2 border-ask bg-ask-soft px-2 py-1.5 text-[11px] leading-4 text-ask"
    >
      {errorLines(error).map((line, index) => (
        <div key={index} className={index === 0 ? "" : "text-ask/80"}>
          {line}
        </div>
      ))}
    </div>
  );
}

/** Classes for a compact native select; each use adds its own height and width (the shared
 * Select primitive is a full-width form field). */
export const COMPACT_SELECT =
  "num min-w-0 rounded-[2px] border border-line-strong bg-bg px-1.5 text-text focus:border-accent/70 focus:outline-none disabled:opacity-50";

/** A small heading inside a panel. */
export function SubHeading({ children }: { children: ReactNode }) {
  return <div className="label-caps border-b border-line pb-1 text-text/70">{children}</div>;
}
