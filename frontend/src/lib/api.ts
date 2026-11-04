/**
 * A typed client for the REST API (docs/api.md). It only moves data: every number it returns
 * was computed by the backend.
 */
import type {
  AgentDetailOut,
  AgentOut,
  AnalyticsOut,
  BarOut,
  CancelOut,
  ComparisonResultOut,
  ExperimentDetailOut,
  ExperimentIn,
  ExperimentOut,
  Health,
  MarketDetailOut,
  MarketOut,
  NewsOut,
  OpenOrderOut,
  OrderBookOut,
  OrderIn,
  OrderOut,
  RunOut,
  SimulationCreate,
  SimulationOut,
  TradeOut,
} from "./types";

/** An error response, in the API's one error shape: `{"error": {"code", "message"}}`. */
export class ApiError extends Error {
  constructor(
    readonly status: number,
    readonly code: string,
    message: string,
    readonly details?: unknown,
  ) {
    super(message);
    this.name = "ApiError";
  }
}

export type Lifecycle = "start" | "pause" | "resume" | "stop" | "reset";

type Query = Record<string, string | number | undefined | null>;

function queryString(query: Query | undefined): string {
  if (query === undefined) return "";
  const params = new URLSearchParams();
  for (const [key, value] of Object.entries(query)) {
    if (value !== undefined && value !== null) params.set(key, String(value));
  }
  const text = params.toString();
  return text === "" ? "" : `?${text}`;
}

async function failure(response: Response): Promise<ApiError> {
  let code = "http_error";
  let message = `${response.status} ${response.statusText}`;
  let details: unknown;
  try {
    const body: unknown = await response.json();
    if (typeof body === "object" && body !== null && "error" in body) {
      const error = (body as { error: { code?: unknown; message?: unknown; details?: unknown } })
        .error;
      if (typeof error.code === "string") code = error.code;
      if (typeof error.message === "string") message = error.message;
      details = error.details;
    }
  } catch {
    // Not JSON (a proxy's error page, say): keep the status line as the message.
  }
  return new ApiError(response.status, code, message, details);
}

export interface Api {
  readonly baseUrl: string;
  health(): Promise<Health>;

  listSimulations(): Promise<SimulationOut[]>;
  createSimulation(body: SimulationCreate): Promise<SimulationOut>;
  getSimulation(id: string): Promise<SimulationOut>;
  deleteSimulation(id: string): Promise<void>;
  lifecycle(id: string, action: Lifecycle): Promise<SimulationOut>;
  setSpeed(id: string, speed: number): Promise<SimulationOut>;
  step(id: string, events: number): Promise<SimulationOut>;
  analytics(id: string): Promise<AnalyticsOut>;

  markets(simulationId: string): Promise<MarketOut[]>;
  market(simulationId: string, symbol: string): Promise<MarketDetailOut>;
  orderBook(simulationId: string, symbol: string, depth: number): Promise<OrderBookOut>;
  trades(simulationId: string, symbol: string, limit: number): Promise<TradeOut[]>;
  history(
    simulationId: string,
    symbol: string,
    intervalSeconds: number,
    limit: number,
  ): Promise<BarOut[]>;
  myOrders(simulationId: string, symbol: string): Promise<OpenOrderOut[]>;
  submitOrder(simulationId: string, symbol: string, order: OrderIn): Promise<OrderOut>;
  cancelOrder(simulationId: string, symbol: string, orderId: number): Promise<CancelOut>;

  agents(simulationId: string): Promise<AgentOut[]>;
  agent(simulationId: string, agentId: number): Promise<AgentDetailOut>;
  news(simulationId: string, limit: number): Promise<NewsOut[]>;

  listExperiments(): Promise<ExperimentOut[]>;
  createExperiment(body: ExperimentIn): Promise<ExperimentOut>;
  getExperiment(id: string): Promise<ExperimentDetailOut>;
  experimentRuns(id: string): Promise<RunOut[]>;
  cancelExperiment(id: string): Promise<ExperimentOut>;
  deleteExperiment(id: string): Promise<void>;
  compare(a: string, pointA: number, b: string, pointB: number): Promise<ComparisonResultOut>;

  /** Download links (CSV), for plain anchors. */
  runsCsvUrl(id: string): string;
  summaryCsvUrl(id: string): string;
  compareCsvUrl(a: string, pointA: number, b: string, pointB: number): string;
  /** The simulation's WebSocket stream (all symbols). */
  streamUrl(simulationId: string): string;
}

export function createApi(baseUrl: string): Api {
  const root = baseUrl.replace(/\/+$/, "");
  const url = (path: string, query?: Query) => `${root}${path}${queryString(query)}`;

  async function request<T>(method: string, path: string, query?: Query, body?: unknown) {
    const response = await fetch(url(path, query), {
      method,
      headers: body === undefined ? undefined : { "content-type": "application/json" },
      body: body === undefined ? undefined : JSON.stringify(body),
      cache: "no-store",
    });
    if (!response.ok) throw await failure(response);
    if (response.status === 204) return undefined as T;
    return (await response.json()) as T;
  }
  const get = <T>(path: string, query?: Query) => request<T>("GET", path, query);
  const post = <T>(path: string, body?: unknown, query?: Query) =>
    request<T>("POST", path, query, body);
  const del = <T>(path: string, query?: Query) => request<T>("DELETE", path, query);
  const sim = (simulationId: string) => ({ simulation_id: simulationId });
  const symbolPath = (symbol: string) => `/api/markets/${encodeURIComponent(symbol)}`;
  const compareQuery = (a: string, pointA: number, b: string, pointB: number) => ({
    a,
    b,
    point_a: pointA,
    point_b: pointB,
  });

  return {
    baseUrl: root,
    health: () => get("/api/health"),

    listSimulations: () => get("/api/simulations"),
    createSimulation: (body) => post("/api/simulations", body),
    getSimulation: (id) => get(`/api/simulations/${id}`),
    deleteSimulation: (id) => del(`/api/simulations/${id}`),
    lifecycle: (id, action) => post(`/api/simulations/${id}/${action}`),
    setSpeed: (id, speed) => post(`/api/simulations/${id}/speed`, { speed }),
    step: (id, events) => post(`/api/simulations/${id}/step`, { events }),
    analytics: (id) => get(`/api/simulations/${id}/analytics`),

    markets: (simulationId) => get("/api/markets", sim(simulationId)),
    market: (simulationId, symbol) => get(symbolPath(symbol), sim(simulationId)),
    orderBook: (simulationId, symbol, depth) =>
      get(`${symbolPath(symbol)}/orderbook`, { ...sim(simulationId), depth }),
    trades: (simulationId, symbol, limit) =>
      get(`${symbolPath(symbol)}/trades`, { ...sim(simulationId), limit }),
    history: (simulationId, symbol, intervalSeconds, limit) =>
      get(`${symbolPath(symbol)}/history`, {
        ...sim(simulationId),
        interval_seconds: intervalSeconds,
        limit,
      }),
    myOrders: (simulationId, symbol) => get(`${symbolPath(symbol)}/orders`, sim(simulationId)),
    submitOrder: (simulationId, symbol, order) =>
      post(`${symbolPath(symbol)}/orders`, order, sim(simulationId)),
    cancelOrder: (simulationId, symbol, orderId) =>
      del(`${symbolPath(symbol)}/orders/${orderId}`, sim(simulationId)),

    agents: (simulationId) => get("/api/agents", sim(simulationId)),
    agent: (simulationId, agentId) => get(`/api/agents/${agentId}`, sim(simulationId)),
    news: (simulationId, limit) => get("/api/news", { ...sim(simulationId), limit }),

    listExperiments: () => get("/api/experiments"),
    createExperiment: (body) => post("/api/experiments", body),
    getExperiment: (id) => get(`/api/experiments/${id}`),
    experimentRuns: (id) => get(`/api/experiments/${id}/runs`),
    cancelExperiment: (id) => post(`/api/experiments/${id}/cancel`),
    deleteExperiment: (id) => del(`/api/experiments/${id}`),
    compare: (a, pointA, b, pointB) =>
      get("/api/experiments/compare", compareQuery(a, pointA, b, pointB)),

    runsCsvUrl: (id) => url(`/api/experiments/${id}/runs.csv`),
    summaryCsvUrl: (id) => url(`/api/experiments/${id}/summary.csv`),
    compareCsvUrl: (a, pointA, b, pointB) =>
      url("/api/experiments/compare.csv", compareQuery(a, pointA, b, pointB)),
    streamUrl: (simulationId) =>
      `${root.replace(/^http/, "ws")}/api/simulations/${simulationId}/stream`,
  };
}
