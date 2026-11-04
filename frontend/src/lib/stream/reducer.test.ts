import { describe, expect, it } from "vitest";

import type { AgentOut, BarOut, MarketOut, NewsOut, OrderBookOut, TradeOut } from "../types";
import type { SnapshotMessage, StreamMessage } from "./messages";
import { EMPTY_STREAM, NEWS_KEPT, TRADES_KEPT, reduce, reduceAll } from "./reducer";

function book(symbol: string, bid: number | null): OrderBookOut {
  return {
    symbol,
    time_seconds: 1,
    bids:
      bid === null
        ? []
        : [{ price: bid, price_ticks: bid * 100, quantity: 5, orders: 1, cumulative_quantity: 5 }],
    asks: [],
    best_bid: bid,
    best_ask: null,
    midprice: null,
    spread: null,
  };
}

function market(symbol: string, last: number | null): MarketOut {
  return {
    symbol,
    tick_size: 0.01,
    reference_price: 100,
    last_price: last,
    change: null,
    change_percent: null,
    open: null,
    high: null,
    low: null,
    volume: 0,
    trade_count: 0,
    vwap: null,
    best_bid: null,
    best_ask: null,
    midprice: null,
    spread: null,
    fundamental_value: 100,
  };
}

function trade(id: number): TradeOut {
  return {
    trade_id: id,
    time_seconds: id,
    price: 100,
    price_ticks: 10_000,
    quantity: 1,
    aggressor_side: "buy",
    buyer: 1,
    seller: 2,
  };
}

function bar(start: number): BarOut {
  return {
    start_seconds: start,
    end_seconds: start + 1,
    open: 100,
    high: 100,
    low: 100,
    close: 100,
    volume: 0,
    trade_count: 0,
    vwap: null,
  };
}

const news: NewsOut = {
  time_seconds: 3,
  symbol: "ACME",
  impact: 0.01,
  change_percent: 1.005017,
  sentiment: "positive",
  headline: "ACME beats expectations",
};

function snapshot(overrides: Partial<SnapshotMessage> = {}): SnapshotMessage {
  return {
    type: "snapshot",
    simulation: {
      id: "s1",
      state: "running",
      time_seconds: 1,
      progress: 0.1,
      speed: 1,
      error: null,
    },
    markets: [
      {
        symbol: "ACME",
        book: book("ACME", 99),
        market: market("ACME", null),
        trades: [trade(1)],
        bars: [bar(0)],
      },
      {
        symbol: "NOVA",
        book: book("NOVA", null),
        market: market("NOVA", null),
        trades: [],
        bars: [],
      },
    ],
    agents: [],
    news: [],
    ...overrides,
  };
}

describe("reduce", () => {
  it("replaces everything with a snapshot", () => {
    const state = reduce(EMPTY_STREAM, snapshot());
    expect(state.symbols).toEqual(["ACME", "NOVA"]);
    expect(state.status?.state).toBe("running");
    expect(state.markets.ACME?.trades).toEqual([trade(1)]);
    expect(state.snapshots).toBe(1);

    // A later snapshot (after a reset, or a resync) drops what came before it.
    const after = reduceAll(state, [
      { type: "trades", symbol: "ACME", trades: [trade(2)], omitted: 0 },
      snapshot({ markets: [] }),
    ]);
    expect(after.symbols).toEqual([]);
    expect(after.markets).toEqual({});
    expect(after.snapshots).toBe(2);
  });

  it("applies updates to the market they name, and only that one", () => {
    const start = reduce(EMPTY_STREAM, snapshot());
    const messages: StreamMessage[] = [
      { type: "book", ...book("ACME", 98) },
      { type: "market", ...market("ACME", 99.5) },
      { type: "trades", symbol: "ACME", trades: [trade(2), trade(3)], omitted: 0 },
      { type: "bars", symbol: "ACME", bars: [bar(1), bar(2)], omitted: 4 },
    ];
    const state = reduceAll(start, messages);
    const acme = state.markets.ACME;
    expect(acme?.book).toEqual(book("ACME", 98)); // Stored without the message's type.
    expect(acme?.market.last_price).toBe(99.5);
    expect(acme?.trades.map((t) => t.trade_id)).toEqual([1, 2, 3]);
    expect(acme?.bars.map((b) => b.start_seconds)).toEqual([0, 1, 2]);
    expect(acme?.barsOmitted).toBe(4);
    expect(acme?.barsRevision).toBe(1);
    expect(state.markets.NOVA).toBe(start.markets.NOVA); // Untouched, same object.
  });

  it("ignores updates for symbols the snapshot did not include", () => {
    const start = reduce(EMPTY_STREAM, snapshot());
    expect(reduce(start, { type: "book", ...book("ZZZ", 1) })).toBe(start);
    expect(reduce(start, { type: "trades", symbol: "ZZZ", trades: [trade(9)], omitted: 0 })).toBe(
      start,
    );
  });

  it("keeps only the most recent trades and news", () => {
    const start = reduce(EMPTY_STREAM, snapshot());
    const many = Array.from({ length: TRADES_KEPT + 50 }, (_, i) => trade(i + 2));
    const state = reduce(start, { type: "trades", symbol: "ACME", trades: many, omitted: 0 });
    const ids = state.markets.ACME?.trades.map((t) => t.trade_id) ?? [];
    expect(ids).toHaveLength(TRADES_KEPT);
    expect(ids.at(-1)).toBe(TRADES_KEPT + 51);

    let withNews = start;
    for (let i = 0; i < NEWS_KEPT + 3; i++) {
      withNews = reduce(withNews, { type: "news", ...news, time_seconds: i });
    }
    expect(withNews.news).toHaveLength(NEWS_KEPT);
    expect(withNews.news[0]).toEqual({ ...news, time_seconds: 3 });
  });

  it("tracks status, the leaderboard and deletion", () => {
    const agents: AgentOut[] = [
      {
        id: 1,
        name: "market_maker-1",
        strategy: "market_maker",
        symbols: ["ACME"],
        cash: 1,
        equity: 1,
        total_pnl: 0,
        realized_pnl: 0,
        unrealized_pnl: 0,
        return_pct: 0,
        sharpe_ratio: null,
        positions: {},
        trades: 0,
        orders_submitted: 0,
        fills: 0,
        filled_quantity: 0,
      },
    ];
    const state = reduceAll(reduce(EMPTY_STREAM, snapshot()), [
      {
        type: "simulation",
        id: "s1",
        state: "paused",
        time_seconds: 2,
        progress: 0.2,
        speed: 5,
        error: null,
      },
      { type: "agents", agents },
      { type: "closed" },
    ]);
    expect(state.status).toEqual({
      id: "s1",
      state: "paused",
      time_seconds: 2,
      progress: 0.2,
      speed: 5,
      error: null,
    });
    expect(state.agents).toBe(agents);
    expect(state.closed).toBe(true);
  });
});
