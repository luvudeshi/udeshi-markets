"use client";

import { useEffect } from "react";

import { useApi } from "@/lib/api-context";
import { StreamConnection } from "@/lib/stream/connection";
import { useTerminal } from "@/lib/stream/store";

/**
 * Keeps the store connected to the selected simulation's stream. On first load it selects the
 * server's active simulation (the most recently created), if there is one.
 */
export function TerminalStream() {
  const api = useApi();
  const simulationId = useTerminal((state) => state.simulationId);

  useEffect(() => {
    if (useTerminal.getState().simulationId !== null) return;
    let cancelled = false;
    api
      .listSimulations()
      .then((simulations) => {
        const active = simulations.find((simulation) => simulation.active);
        const store = useTerminal.getState();
        if (!cancelled && active !== undefined && store.simulationId === null) {
          store.setSimulation(active.id);
        }
      })
      .catch((error: unknown) => {
        console.error("could not list simulations", error);
        if (!cancelled) {
          useTerminal.getState().setConnection("closed", `API unreachable at ${api.baseUrl}`);
        }
      });
    return () => {
      cancelled = true;
    };
  }, [api]);

  useEffect(() => {
    if (simulationId === null) return;
    const { apply, setConnection } = useTerminal.getState();
    const connection = new StreamConnection(api.streamUrl(simulationId), {
      onMessages: apply,
      onState: setConnection,
      onInvalid: (data) => console.error("unrecognised stream message", data),
    });
    connection.start();
    return () => {
      connection.stop();
      setConnection("idle", null);
    };
  }, [api, simulationId]);

  return null;
}
