"use client";

import { useCallback, useEffect, useEffectEvent, useState } from "react";

export interface Resource<T> {
  /** The latest result for the current key; kept while a newer version is being fetched. */
  data: T | undefined;
  /** The latest failure for the current key, cleared by the next success. */
  error: Error | null;
  /** Fetches again now. */
  refresh: () => void;
}

const RETRY_MS = 5_000;

/**
 * Fetches with ``fetcher`` when ``key`` changes (dropping the previous key's data), again when
 * ``version`` changes (keeping it until the new result arrives), and then after
 * ``nextDelay(data)`` milliseconds, if that is not null: polling that slows down or stops once
 * the data says nothing more will change. A null key fetches nothing.
 */
export function useResource<T>(
  key: string | null,
  fetcher: () => Promise<T>,
  options: { version?: string | number; nextDelay?: (data: T) => number | null } = {},
): Resource<T> {
  const { version = 0, nextDelay } = options;
  const [state, setState] = useState<{ key: string | null; data?: T; error: Error | null }>({
    key: null,
    error: null,
  });
  const [generation, setGeneration] = useState(0);
  const load = useEffectEvent(fetcher);
  const delayAfter = useEffectEvent((data: T) =>
    nextDelay === undefined ? null : nextDelay(data),
  );
  const polls = nextDelay !== undefined;

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
        delay = polls ? RETRY_MS : null;
      }
      if (!cancelled && delay !== null) timer = setTimeout(run, delay);
    };
    void run();
    return () => {
      cancelled = true;
      clearTimeout(timer);
    };
  }, [key, version, generation, polls]);

  const refresh = useCallback(() => setGeneration((value) => value + 1), []);
  const current = state.key === key;
  return { data: current ? state.data : undefined, error: current ? state.error : null, refresh };
}
