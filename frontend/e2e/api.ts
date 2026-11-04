/** Direct calls to the backend, to set up the state a test needs quickly and exactly. */
import type { APIRequestContext } from "@playwright/test";

import type { SimulationCreate, SimulationOut } from "../src/lib/types";
import { API_URL } from "../playwright.config";

/** A speed at which the live runner never moves the clock: the market changes only when a
 * test steps it, so what the page shows is reproducible. */
export const FROZEN = 1e-9;

export const SMALL_MARKET: SimulationCreate = {
  seed: 7,
  speed: FROZEN,
  duration_seconds: 600,
  agents: {
    random_traders: 4,
    market_makers: 2,
    momentum_traders: 2,
    fundamental_traders: 2,
    pairs_traders: 1,
  },
};

async function ok<T>(response: Awaited<ReturnType<APIRequestContext["get"]>>): Promise<T> {
  if (!response.ok()) {
    throw new Error(`${response.status()} ${response.url()}: ${await response.text()}`);
  }
  return (await response.json()) as T;
}

export async function createSimulation(
  request: APIRequestContext,
  body: SimulationCreate = SMALL_MARKET,
): Promise<SimulationOut> {
  return ok(await request.post(`${API_URL}/api/simulations`, { data: body }));
}

export async function lifecycle(
  request: APIRequestContext,
  id: string,
  action: "start" | "pause" | "resume" | "stop" | "reset",
): Promise<SimulationOut> {
  return ok(await request.post(`${API_URL}/api/simulations/${id}/${action}`));
}

export async function step(
  request: APIRequestContext,
  id: string,
  events: number,
): Promise<SimulationOut> {
  return ok(await request.post(`${API_URL}/api/simulations/${id}/step`, { data: { events } }));
}

/** A small market, started, paused and advanced by ``events`` events: deterministic. */
export async function steppedMarket(
  request: APIRequestContext,
  events = 20_000,
  body: SimulationCreate = SMALL_MARKET,
): Promise<SimulationOut> {
  const simulation = await createSimulation(request, body);
  await lifecycle(request, simulation.id, "start");
  await lifecycle(request, simulation.id, "pause");
  return step(request, simulation.id, events);
}

export async function getJson<T>(request: APIRequestContext, path: string): Promise<T> {
  return ok(await request.get(`${API_URL}${path}`));
}
