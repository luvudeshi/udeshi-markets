"use client";

import { createContext, use, useMemo, type ReactNode } from "react";

import { createApi, type Api } from "./api";

const ApiContext = createContext<Api | null>(null);

/** Provides the API client. The base URL is read on the server at request time (see
 * layout.tsx), so one build works against any backend. */
export function ApiProvider({ baseUrl, children }: { baseUrl: string; children: ReactNode }) {
  const api = useMemo(() => createApi(baseUrl), [baseUrl]);
  return <ApiContext value={api}>{children}</ApiContext>;
}

export function useApi(): Api {
  const api = use(ApiContext);
  if (api === null) throw new Error("useApi must be used inside ApiProvider");
  return api;
}
