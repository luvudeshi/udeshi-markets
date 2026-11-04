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

/**
 * Fetches with ``fetcher`` whenever ``key`` changes, then every ``intervalMs`` (null: once).
 * A null key fetches nothing. Results for an earlier key are never shown for a later one.
 */
export function usePoll<T>(
  key: string | null,
  fetcher: () => Promise<T>,
  intervalMs: number | null,
): Polled<T> {
  const [state, setState] = useState<{ key: string | null; data?: T; error: Error | null }>({
    key: null,
    error: null,
  });
  const [generation, setGeneration] = useState(0);
  const load = useEffectEvent(fetcher);

  useEffect(() => {
    if (key === null) return;
    let cancelled = false;
    let timer: ReturnType<typeof setTimeout> | undefined;
    const run = async () => {
      try {
        const data = await load();
        if (!cancelled) setState({ key, data, error: null });
      } catch (caught) {
        if (cancelled) return;
        const error = caught instanceof Error ? caught : new Error(String(caught));
        setState((previous) => ({
          key,
          data: previous.key === key ? previous.data : undefined,
          error,
        }));
      } finally {
        if (!cancelled && intervalMs !== null) timer = setTimeout(run, intervalMs);
      }
    };
    void run();
    return () => {
      cancelled = true;
      clearTimeout(timer);
    };
  }, [key, intervalMs, generation]);

  const refresh = useCallback(() => setGeneration((value) => value + 1), []);
  const current = state.key === key;
  return { data: current ? state.data : undefined, error: current ? state.error : null, refresh };
}

/** True while the selected simulation's clock is moving. */
export function useIsRunning(): boolean {
  return useTerminal((state) => state.status?.state === "running");
}
