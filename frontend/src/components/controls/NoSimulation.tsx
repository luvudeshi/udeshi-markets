"use client";

import { useEffect, useState } from "react";

import { Button } from "@/components/ui/controls";
import { useApi } from "@/lib/api-context";
import { usePoll } from "@/lib/hooks";
import { useTerminal } from "@/lib/stream/store";

import { useControls } from "./controls-store";
import { describeError, type ErrorDescription } from "./errors";

const POLL_MS = 3_000;

/**
 * Shown over the terminal while the server has no simulation at all, with a quick start (the
 * default market, started) and a way to configure one. The server's list decides, not only the
 * store, which fills in asynchronously; a simulation created elsewhere is picked up.
 */
export function NoSimulation() {
  const api = useApi();
  const simulationId = useTerminal((state) => state.simulationId);
  const version = useControls((state) => state.listVersion);
  const [busy, setBusy] = useState(false);
  const [failure, setFailure] = useState<ErrorDescription | null>(null);
  const {
    data: simulations,
    error,
    refresh,
  } = usePoll(
    simulationId === null ? `no-simulation:${version}` : null,
    () => api.listSimulations(),
    POLL_MS,
  );

  useEffect(() => {
    if (simulationId !== null || simulations === undefined || simulations.length === 0) return;
    const active = simulations.find((simulation) => simulation.active) ?? simulations.at(-1);
    if (active !== undefined) useTerminal.getState().setSimulation(active.id);
  }, [simulationId, simulations]);

  if (simulationId !== null) return null;
  if (error === null && (simulations === undefined || simulations.length > 0)) return null;

  async function quickStart() {
    setBusy(true);
    setFailure(null);
    let created: string | null = null;
    try {
      created = (await api.createSimulation({ agents: {} })).id;
    } catch (createError) {
      setFailure(describeError(createError));
      setBusy(false);
      return;
    }
    const controls = useControls.getState();
    useTerminal.getState().setSimulation(created);
    controls.simulationsChanged();
    try {
      await api.lifecycle(created, "start");
    } catch (startError) {
      controls.notify(startError);
    } finally {
      setBusy(false);
    }
  }

  return (
    <div
      data-testid="no-simulation"
      className="absolute inset-0 z-20 flex items-center justify-center bg-bg/85 p-4"
    >
      <div className="w-full max-w-[440px] border border-line-strong bg-panel">
        <header className="flex h-8 items-center border-b border-line px-4">
          <h2 className="label-caps text-text/80">No simulation</h2>
        </header>
        <div className="space-y-3 px-4 py-4 leading-relaxed">
          {error !== null ? (
            <p className="text-ask" role="alert">
              Cannot reach the API at <span className="num">{api.baseUrl}</span>:{" "}
              {describeError(error).title}
            </p>
          ) : (
            <p className="text-muted">
              The exchange is idle. Start the default market (26 agents trading five instruments
              with hidden fundamental values and news) or configure your own: the agent mix, the
              market environment and the speed.
            </p>
          )}
          {failure !== null && (
            <div role="alert" className="text-ask">
              <p>{failure.title}</p>
              {failure.lines.map((line) => (
                <p key={line} className="num text-[11px]">
                  {line}
                </p>
              ))}
            </div>
          )}
        </div>
        <footer className="flex items-center justify-end gap-2 border-t border-line px-4 py-2.5">
          {error !== null ? (
            <Button onClick={refresh}>Retry</Button>
          ) : (
            <>
              <Button
                data-testid="configure-simulation"
                onClick={() => useControls.getState().openDialog()}
                disabled={busy}
              >
                Configure simulation
              </Button>
              <Button tone="primary" data-testid="quick-start" onClick={quickStart} disabled={busy}>
                {busy ? "Starting…" : "Quick start"}
              </Button>
            </>
          )}
        </footer>
      </div>
    </div>
  );
}
