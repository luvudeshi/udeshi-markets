"use client";

import { useEffect } from "react";

import { useApi } from "@/lib/api-context";
import { StreamConnection } from "@/lib/stream/connection";
import { useTerminal } from "@/lib/stream/store";

const RETRY_MS = 3_000;

/**
 * Keeps the store connected to the selected simulation's stream. On first load it selects the
 * server's active simulation (the most recently created), if there is one, retrying until the
 * API answers.
 */
export function TerminalStream() {
  const api = useApi();
  const simulationId = useTerminal((state) => state.simulationId);

  useEffect(() => {
    let cancelled = false;
    let timer: ReturnType<typeof setTimeout> | undefined;
    const attempt = async () => {
      if (useTerminal.getState().simulationId !== null) return;
      try {
        const simulations = await api.listSimulations();
        if (cancelled) return;
        const store = useTerminal.getState();
        // The API answers (again): whatever said it was unreachable no longer applies.
        if (store.connection === "closed" && store.simulationId === null) {
          store.setConnection("idle", null);
        }
        const active = simulations.find((simulation) => simulation.active);
        if (active !== undefined && store.simulationId === null) store.setSimulation(active.id);
      } catch (error) {
        if (cancelled) return;
        console.error("could not list simulations", error);
        useTerminal.getState().setConnection("closed", `API unreachable at ${api.baseUrl}`);
        timer = setTimeout(attempt, RETRY_MS); // Until it answers.
      }
    };
    void attempt();
    return () => {
      cancelled = true;
      clearTimeout(timer);
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
