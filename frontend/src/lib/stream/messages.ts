/**
 * The WebSocket stream's messages (docs/api.md, "WebSocket stream"). Their payloads are the
 * REST models, so these types are built from the generated ones.
 */
import type {
  AgentOut,
  BarOut,
  MarketOut,
  NewsOut,
  OrderBookOut,
  SimulationState,
  TradeOut,
} from "../types";

export interface SimulationStatus {
  id: string;
  state: SimulationState;
  time_seconds: number;
  progress: number;
  speed: number;
  error: string | null;
}

export interface MarketSnapshot {
  symbol: string;
  book: OrderBookOut;
  market: MarketOut;
  trades: TradeOut[];
  bars: BarOut[];
}

export type SnapshotMessage = {
  type: "snapshot";
  simulation: SimulationStatus;
  markets: MarketSnapshot[];
  agents: AgentOut[];
  news: NewsOut[];
};
export type SimulationMessage = { type: "simulation" } & SimulationStatus;
export type BookMessage = { type: "book" } & OrderBookOut;
export type MarketMessage = { type: "market" } & MarketOut;
/** At most the newest trades the tape keeps; ``omitted`` counts the batch's older ones. */
export type TradesMessage = {
  type: "trades";
  symbol: string;
  trades: TradeOut[];
  omitted: number;
};
export type BarsMessage = { type: "bars"; symbol: string; bars: BarOut[]; omitted: number };
export type NewsMessage = { type: "news" } & NewsOut;
export type AgentsMessage = { type: "agents"; agents: AgentOut[] };
export type ClosedMessage = { type: "closed" };

export type StreamMessage =
  | SnapshotMessage
  | SimulationMessage
  | BookMessage
  | MarketMessage
  | TradesMessage
  | BarsMessage
  | NewsMessage
  | AgentsMessage
  | ClosedMessage;

const TYPES: ReadonlySet<string> = new Set<StreamMessage["type"]>([
  "snapshot",
  "simulation",
  "book",
  "market",
  "trades",
  "bars",
  "news",
  "agents",
  "closed",
]);

/** Parses one frame. Returns null (and the caller reports it) for anything that is not a
 * known message: the server's types are checked, not trusted blindly. */
export function parseMessage(data: unknown): StreamMessage | null {
  if (typeof data !== "string") return null;
  let value: unknown;
  try {
    value = JSON.parse(data);
  } catch {
    return null;
  }
  if (typeof value !== "object" || value === null) return null;
  const type = (value as { type?: unknown }).type;
  return typeof type === "string" && TYPES.has(type) ? (value as StreamMessage) : null;
}
