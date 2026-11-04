"use client";

import { useCallback, useEffect, useEffectEvent, useState } from "react";

import { useTerminal } from "./stream/store";

export interface Polled<T> {
  /** The latest result for the current key (undefined until the first one arrives). */
  data: T | undefined;
  /** The latest failure for the current key; cleared by the next success. */
  error: Error | null;
  /** Fetches again now. */
  refresh: () => void;
}

/** Milliseconds until the next fetch: fixed, decided by the latest result (polling that slows
 * down or stops once nothing more will change), or null for no polling. */
export type PollInterval<T> = number | null | ((data: T) => number | null);

/** How long a fetch waits after a failure before trying again, unless it polls faster. */
const RETRY_MS = 5_000;

/**
 * Fetches with ``fetcher`` and polls at ``interval``.
 * - A new ``key`` fetches at once and drops the previous key's result: it was about something
 *   else (another simulation, agent or experiment). A null key fetches nothing.
 * - A new ``version`` fetches at once but keeps showing the current result until the new one
 *   arrives: the same thing, changed (a step, a reset, a new list entry), so nothing blinks.
 * - A response that arrives after the key or version changed is discarded.
 * - A failure is reported to the console and retried (at the polling interval, or after
 *   RETRY_MS for a fetch that does not poll): one dropped request must not leave a panel
 *   empty until the page is reloaded.
 */
export function usePoll<T>(
  key: string | null,
  fetcher: () => Promise<T>,
  interval: PollInterval<T>,
  version: string | number = 0,
): Polled<T> {
  const [state, setState] = useState<{ key: string | null; data?: T; error: Error | null }>({
    key: null,
    error: null,
  });
  const [generation, setGeneration] = useState(0);
  const load = useEffectEvent(fetcher);
  const delayAfter = useEffectEvent((data: T) =>
    typeof interval === "function" ? interval(data) : interval,
  );
  const retryAfter = typeof interval === "number" ? Math.min(interval, RETRY_MS) : RETRY_MS;

  useEffect(() => {
    if (key === null) return;
    let cancelled = false;
    let timer: ReturnType<typeof setTimeout> | undefined;
    const run = async () => {
      let delay: number | null;
      try {
        const data = await load();
        if (cancelled) return;
        setState({ key, data, error: null });
        delay = delayAfter(data);
      } catch (caught) {
        if (cancelled) return;
        const error = caught instanceof Error ? caught : new Error(String(caught));
        console.error(`request ${key} failed`, error);
        setState((previous) => ({
          key,
          data: previous.key === key ? previous.data : undefined,
          error,
        }));
        delay = retryAfter;
      }
      if (!cancelled && delay !== null) timer = setTimeout(run, delay);
    };
    void run();
    return () => {
      cancelled = true;
      clearTimeout(timer);
    };
  }, [key, version, generation, retryAfter]);

  const refresh = useCallback(() => setGeneration((value) => value + 1), []);
  const current = state.key === key;
  return { data: current ? state.data : undefined, error: current ? state.error : null, refresh };
}

/**
 * "live" while the selected simulation runs (panels then poll on a timer). Otherwise a value
 * that changes whenever what the REST endpoints report can have changed: the simulated time
 * moved (a step), or a trade happened without it moving (a manual order while paused). Use it
 * as a usePoll version. Selected as one string, so a running clock causes no re-render.
 */
export function useMoment(): string {
  return useTerminal((state) => {
    if (state.status?.state === "running") return "live";
    let newest = 0; // Trade ids are exchange-wide and increasing.
    for (const symbol of state.symbols) {
      newest = Math.max(newest, state.markets[symbol]?.trades.at(-1)?.trade_id ?? 0);
    }
    return `${state.snapshots}:${state.status?.time_seconds ?? 0}:${newest}`;
  });
}

/** True while the selected simulation's clock is moving. */
export function useIsRunning(): boolean {
  return useTerminal((state) => state.status?.state === "running");
}
