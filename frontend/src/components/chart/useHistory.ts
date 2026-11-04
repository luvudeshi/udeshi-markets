"use client";

import { useEffect, useRef, useState } from "react";

import { useApi } from "@/lib/api-context";
import type { BarOut } from "@/lib/types";

import { ThrottledLoader } from "./ThrottledLoader";

/** Bars per request: the endpoint aggregates only the tail it returns. */
export const HISTORY_LIMIT = 500;
/** At most one history request per second, however fast bars close. */
export const MIN_REFRESH_MS = 1_000;

export interface History {
  /** Which simulation, symbol and interval the bars belong to. */
  key: string | null;
  /** Closed bars, oldest first; null until the first response for this key. */
  bars: BarOut[] | null;
  error: Error | null;
}

/**
 * The backend's OHLCV history for one symbol at one interval. Loaded when the key changes, and
 * again (throttled) whenever ``revision`` changes, which the caller bumps when bars close or a
 * new snapshot arrives. A response for an earlier key is never shown for a later one.
 */
export function useHistory(
  simulationId: string | null,
  symbol: string | null,
  interval: number | null,
  revision: string,
): History {
  const api = useApi();
  const [state, setState] = useState<History>({ key: null, bars: null, error: null });
  const loader = useRef<ThrottledLoader<BarOut[]> | null>(null);
  const key =
    simulationId === null || symbol === null || interval === null
      ? null
      : `${simulationId}|${symbol}|${interval}`;

  useEffect(() => {
    if (simulationId === null || symbol === null || interval === null) return;
    const current = `${simulationId}|${symbol}|${interval}`;
    const created = new ThrottledLoader(
      () => api.history(simulationId, symbol, interval, HISTORY_LIMIT),
      MIN_REFRESH_MS,
      (bars) => setState({ key: current, bars, error: null }),
      (error) => {
        console.error(`history of ${symbol} at ${interval}s failed`, error);
        setState((previous) => ({
          key: current,
          bars: previous.key === current ? previous.bars : null,
          error,
        }));
      },
    );
    loader.current = created;
    created.request();
    return () => {
      created.dispose();
      if (loader.current === created) loader.current = null;
    };
  }, [api, simulationId, symbol, interval]);

  const seen = useRef(revision);
  useEffect(() => {
    if (seen.current === revision) return;
    seen.current = revision;
    loader.current?.request();
  }, [revision]);

  return state.key === key ? state : { key, bars: null, error: null };
}
