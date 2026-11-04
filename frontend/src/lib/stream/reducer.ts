/**
 * The terminal's market state, built from the stream: a snapshot replaces everything, then each
 * update replaces or appends what it carries. Pure functions, so they are unit-tested directly.
 *
 * Nothing is computed here. Books, summaries and the leaderboard are taken as the server sent
 * them; trades, bars and news are appended and capped to what the panels show.
 */
import type { AgentOut, BarOut, MarketOut, NewsOut, OrderBookOut, TradeOut } from "../types";
import type { SimulationStatus, SnapshotMessage, StreamMessage } from "./messages";

export const TRADES_KEPT = 200;
export const BARS_KEPT = 2_000;
export const NEWS_KEPT = 200;

export interface MarketState {
  symbol: string;
  market: MarketOut;
  book: OrderBookOut;
  /** Most recent trades, oldest first. */
  trades: TradeOut[];
  /** Most recent closed bars at the simulation's bar interval, oldest first. */
  bars: BarOut[];
  /** Bars the stream skipped after a jump in time (the history endpoint has them). */
  barsOmitted: number;
  /** Incremented whenever bars close, so a chart knows to refresh its history. */
  barsRevision: number;
}

export interface StreamState {
  status: SimulationStatus | null;
  /** Instrument order, as in the snapshot. */
  symbols: string[];
  markets: Record<string, MarketState>;
  agents: AgentOut[];
  /** Most recent news, oldest first. */
  news: NewsOut[];
  /** Snapshots received: one per connection, plus one after every reset or resync. */
  snapshots: number;
  /** The simulation was deleted on the server. */
  closed: boolean;
}

export const EMPTY_STREAM: StreamState = {
  status: null,
  symbols: [],
  markets: {},
  agents: [],
  news: [],
  snapshots: 0,
  closed: false,
};

function tail<T>(items: T[], limit: number): T[] {
  return items.length > limit ? items.slice(items.length - limit) : items;
}

function fromSnapshot(message: SnapshotMessage, snapshots: number): StreamState {
  const markets: Record<string, MarketState> = {};
  for (const entry of message.markets) {
    markets[entry.symbol] = {
      symbol: entry.symbol,
      market: entry.market,
      book: entry.book,
      trades: tail(entry.trades, TRADES_KEPT),
      bars: tail(entry.bars, BARS_KEPT),
      barsOmitted: 0,
      barsRevision: 0,
    };
  }
  return {
    status: message.simulation,
    symbols: message.markets.map((entry) => entry.symbol),
    markets,
    agents: message.agents,
    news: tail(message.news, NEWS_KEPT),
    snapshots,
    closed: false,
  };
}

/** Applies ``update`` to one market; a symbol the snapshot did not include is ignored. */
function withMarket(
  state: StreamState,
  symbol: string,
  update: (market: MarketState) => MarketState,
): StreamState {
  const current = state.markets[symbol];
  if (current === undefined) return state;
  return { ...state, markets: { ...state.markets, [symbol]: update(current) } };
}

export function reduce(state: StreamState, message: StreamMessage): StreamState {
  switch (message.type) {
    case "snapshot":
      return fromSnapshot(message, state.snapshots + 1);
    case "simulation": {
      const { type, ...status } = message;
      return { ...state, status };
    }
    case "book": {
      const { type, ...book } = message;
      return withMarket(state, book.symbol, (market) => ({ ...market, book }));
    }
    case "market": {
      const { type, ...summary } = message;
      return withMarket(state, summary.symbol, (market) => ({ ...market, market: summary }));
    }
    case "trades":
      return withMarket(state, message.symbol, (market) => ({
        ...market,
        trades: tail([...market.trades, ...message.trades], TRADES_KEPT),
      }));
    case "bars":
      return withMarket(state, message.symbol, (market) => ({
        ...market,
        bars: tail([...market.bars, ...message.bars], BARS_KEPT),
        barsOmitted: market.barsOmitted + message.omitted,
        barsRevision: market.barsRevision + 1,
      }));
    case "news": {
      const { type, ...item } = message;
      return { ...state, news: tail([...state.news, item], NEWS_KEPT) };
    }
    case "agents":
      return { ...state, agents: message.agents };
    case "closed":
      return { ...state, closed: true };
  }
}

export function reduceAll(state: StreamState, messages: readonly StreamMessage[]): StreamState {
  return messages.reduce(reduce, state);
}
