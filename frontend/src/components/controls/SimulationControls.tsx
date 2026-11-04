"use client";

import { useEffect, useState } from "react";

import { Button, Segmented, Select } from "@/components/ui/controls";
import type { Lifecycle } from "@/lib/api";
import { useApi } from "@/lib/api-context";
import { fmtSimTime } from "@/lib/format";
import { usePoll } from "@/lib/hooks";
import type { SimulationStatus } from "@/lib/stream/messages";
import { useTerminal } from "@/lib/stream/store";
import type { SimulationState } from "@/lib/types";

import { useControls } from "./controls-store";
import { fmtCreatedAt, fmtSpeed } from "./format";
import { NewSimulationDialog } from "./NewSimulationDialog";

/** Events processed by one Step while paused. */
const STEP_EVENTS = 1_000;
const SPEEDS: readonly number[] = [0.5, 1, 5, 10, 50, 100];
const TOAST_MS = 6_000;

type Action = Lifecycle | "step";

/** The actions each state allows (the server checks again: 409 invalid_state otherwise). */
const ACTIONS: Record<SimulationState, readonly Action[]> = {
  created: ["start"],
  running: ["pause", "stop"],
  paused: ["resume", "step", "stop", "reset"],
  stopped: ["reset"],
  completed: ["reset"],
  failed: ["reset"],
};

const ACTION_LOOK: Record<Action, { label: string; tone: "bid" | "ask" | "primary" | "default" }> =
  {
    start: { label: "Start", tone: "bid" },
    resume: { label: "Resume", tone: "bid" },
    pause: { label: "Pause", tone: "primary" },
    step: { label: "Step", tone: "default" },
    stop: { label: "Stop", tone: "ask" },
    reset: { label: "Reset", tone: "default" },
  };

const ACTION_TITLES: Partial<Record<Action, string>> = {
  step: `Process the next ${STEP_EVENTS.toLocaleString("en-US")} events`,
  reset: "Rebuild the simulation from its seed, back to time zero",
  stop: "End the run here (reset to run it again)",
};

const STATE_LOOK: Record<SimulationState, string> = {
  created: "border-info/40 bg-info-soft text-info",
  running: "border-bid/40 bg-bid-soft text-bid",
  paused: "border-accent/40 bg-accent-soft text-accent",
  stopped: "border-line-strong text-muted",
  completed: "border-line-strong text-muted",
  failed: "border-ask/50 bg-ask-soft text-ask",
};

/** Runs one request at a time; failures go to the toast. */
function useAction(): [boolean, (work: () => Promise<unknown>) => Promise<void>] {
  const [busy, setBusy] = useState(false);
  const run = async (work: () => Promise<unknown>) => {
    setBusy(true);
    try {
      await work();
    } catch (error) {
      useControls.getState().notify(error);
    } finally {
      setBusy(false);
    }
  };
  return [busy, run];
}

function SimulationPicker({
  simulationId,
  state,
}: {
  simulationId: string | null;
  /** The selected simulation's state: the list is fetched again when it changes. */
  state: SimulationState | undefined;
}) {
  const api = useApi();
  const version = useControls((store) => store.listVersion);
  const [confirming, setConfirming] = useState(false);
  const [busy, run] = useAction();
  const { data: simulations, refresh } = usePoll(
    "simulations",
    () => api.listSimulations(),
    null,
    `${version}:${simulationId ?? ""}:${state ?? ""}`,
  );

  useEffect(() => {
    if (!confirming) return;
    const timer = setTimeout(() => setConfirming(false), 4_000);
    return () => clearTimeout(timer);
  }, [confirming]);

  const listed = simulations?.some((simulation) => simulation.id === simulationId) ?? false;
  const remove = (id: string) =>
    run(async () => {
      setConfirming(false);
      await api.deleteSimulation(id);
      const remaining = await api.listSimulations();
      const next = remaining.find((simulation) => simulation.active) ?? remaining.at(-1);
      useTerminal.getState().setSimulation(next?.id ?? null);
      useControls.getState().simulationsChanged();
    });

  return (
    <div className="flex shrink-0 items-center gap-1">
      {/* The width sits on a wrapper: the input style's w-full would override it. */}
      <div className="w-36 xl:w-[272px]">
        <Select
          aria-label="Simulation"
          data-testid="sim-picker"
          value={simulationId ?? ""}
          onFocus={refresh}
          onChange={(event) => useTerminal.getState().setSimulation(event.target.value || null)}
          className="h-6 text-[11px]"
        >
          {simulationId === null && <option value="">No simulation</option>}
          {simulationId !== null && !listed && <option value={simulationId}>{simulationId}</option>}
          {simulations?.map((simulation) => (
            <option key={simulation.id} value={simulation.id}>
              {`${simulation.id} · ${simulation.state} · ${fmtCreatedAt(simulation.created_at)}`}
            </option>
          ))}
        </Select>
      </div>
      {simulationId !== null && (
        <span className="hidden xl:inline-flex">
          <Button
            tone={confirming ? "ask" : "ghost"}
            data-testid="sim-delete"
            disabled={busy}
            onClick={() => (confirming ? remove(simulationId) : setConfirming(true))}
            title="Stop this simulation and remove it from the server"
          >
            {confirming ? "Confirm delete" : "Delete"}
          </Button>
        </span>
      )}
    </div>
  );
}

function StateBadge({ state }: { state: SimulationState }) {
  return (
    <span
      data-testid="sim-state"
      className={`inline-flex h-5 items-center gap-1.5 rounded-[2px] border px-1.5 text-[10px] font-medium tracking-[0.08em] uppercase ${STATE_LOOK[state]}`}
    >
      {state === "running" && (
        <span className="size-1.5 animate-pulse rounded-full bg-bid" aria-hidden="true" />
      )}
      {state}
    </span>
  );
}

function Clock({ status, duration }: { status: SimulationStatus; duration: number | undefined }) {
  const progress = Math.min(1, Math.max(0, status.progress));
  return (
    <div
      className="flex w-[60px] shrink-0 flex-col justify-center gap-1 xl:w-[148px]"
      title="Simulated time"
    >
      <div className="num flex items-baseline justify-between text-[11px] leading-none">
        <span data-testid="sim-clock" className="text-text">
          {fmtSimTime(status.time_seconds)}
        </span>
        <span data-testid="sim-duration" className="hidden text-faint xl:inline">
          / {duration === undefined ? "—" : fmtSimTime(duration)}
        </span>
      </div>
      <div
        className="h-[2px] w-full bg-line-strong"
        role="progressbar"
        aria-label="Progress"
        aria-valuemin={0}
        aria-valuemax={100}
        aria-valuenow={Math.round(progress * 100)}
      >
        <div className="h-full bg-accent" style={{ width: `${progress * 100}%` }} />
      </div>
    </div>
  );
}

function ErrorToast() {
  const toast = useControls((state) => state.toast);
  useEffect(() => {
    if (toast === null) return;
    const timer = setTimeout(() => useControls.getState().dismiss(toast.id), TOAST_MS);
    return () => clearTimeout(timer);
  }, [toast]);
  if (toast === null) return null;
  return (
    <div
      role="alert"
      data-testid="sim-error"
      className="fixed top-11 right-3 z-50 w-96 max-w-[calc(100vw-24px)] border border-ask/50 bg-panel px-3 py-2 text-ask"
    >
      <div className="flex items-start justify-between gap-3">
        <p className="font-medium">{toast.title}</p>
        <button
          type="button"
          aria-label="Dismiss"
          onClick={() => useControls.getState().dismiss(toast.id)}
          className="text-muted hover:text-text"
        >
          ✕
        </button>
      </div>
      {toast.lines.length > 0 && (
        <ul className="num mt-1 space-y-0.5 text-[11px]">
          {toast.lines.map((line) => (
            <li key={line}>{line}</li>
          ))}
        </ul>
      )}
    </div>
  );
}

/**
 * The top bar's simulation controls: which simulation, its state and clock, the lifecycle
 * actions its state allows, its speed, and the new-simulation dialog.
 */
export function SimulationControls() {
  const api = useApi();
  const simulationId = useTerminal((state) => state.simulationId);
  const stored = useTerminal((state) => state.status);
  // A status from another simulation can linger until the new stream's snapshot arrives.
  const status = stored !== null && stored.id === simulationId ? stored : null;
  const [busy, run] = useAction();
  const { data: details } = usePoll(
    simulationId === null ? null : `simulation:${simulationId}`,
    () =>
      simulationId === null
        ? Promise.reject(new Error("no simulation selected"))
        : api.getSimulation(simulationId),
    null,
  );

  const act = (id: string, action: Action) =>
    run(() => (action === "step" ? api.step(id, STEP_EVENTS) : api.lifecycle(id, action)));

  return (
    <div
      data-testid="sim-controls"
      className="flex min-w-0 items-center gap-2 overflow-x-auto [scrollbar-width:none] *:shrink-0 xl:gap-2.5"
    >
      <SimulationPicker simulationId={simulationId} state={status?.state} />
      {simulationId !== null && status !== null && (
        <>
          <StateBadge state={status.state} />
          {status.error !== null && (
            <span
              data-testid="sim-runner-error"
              title={status.error}
              className="max-w-64 truncate rounded-[2px] border border-ask/50 bg-ask-soft px-1.5 leading-5 text-ask"
            >
              Runner failed: {status.error}
            </span>
          )}
          <Clock status={status} duration={details?.duration_seconds} />
          <div className="flex items-center gap-1">
            {ACTIONS[status.state].map((action) => (
              <Button
                key={action}
                tone={ACTION_LOOK[action].tone}
                data-testid={`sim-${action}`}
                disabled={busy}
                title={ACTION_TITLES[action]}
                onClick={() => act(simulationId, action)}
              >
                {ACTION_LOOK[action].label}
              </Button>
            ))}
          </div>
          <div
            data-testid="sim-speed"
            className="flex items-center gap-1.5"
            title="Simulated seconds per wall-clock second"
          >
            <span className="label-caps hidden xl:inline">Speed</span>
            <Segmented
              options={SPEEDS}
              value={status.speed}
              onChange={(speed) => run(() => api.setSpeed(simulationId, speed))}
              label="Speed"
              format={fmtSpeed}
              disabled={busy}
            />
          </div>
        </>
      )}
      <Button
        tone="primary"
        data-testid="new-simulation"
        onClick={() => useControls.getState().openDialog()}
      >
        <span className="xl:hidden">New</span>
        <span className="hidden xl:inline">New simulation</span>
      </Button>
      <ErrorToast />
      <NewSimulationDialog />
    </div>
  );
}
