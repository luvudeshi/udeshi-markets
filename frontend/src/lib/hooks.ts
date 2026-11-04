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

/** How long a poll that adapts to its data waits after a failure before trying again. */
const RETRY_MS = 5_000;

/**
 * Fetches with ``fetcher`` and polls at ``interval``.
 * - A new ``key`` fetches at once and drops the previous key's result: it was about something
 *   else (another simulation, agent or experiment). A null key fetches nothing.
 * - A new ``version`` fetches at once but keeps showing the current result until the new one
 *   arrives: the same thing, changed (a step, a reset, a new list entry), so nothing blinks.
 * - A response that arrives after the key or version changed is discarded.
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
  const retryAfter = typeof interval === "function" ? RETRY_MS : interval;

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

/** True while the selected simulation's clock is moving. */
export function useIsRunning(): boolean {
  return useTerminal((state) => state.status?.state === "running");
}
