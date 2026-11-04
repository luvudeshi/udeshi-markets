import { describe, expect, it } from "vitest";

import type { AgentOut } from "@/lib/types";

import { DEFAULT_ORDER, nextOrder, sortAgents } from "./sort";

function agent(id: number, pnl: number, sharpe: number | null, trades = 0): AgentOut {
  return {
    id,
    name: `random-${id}`,
    strategy: "random",
    symbols: ["ACME"],
    cash: 0,
    equity: 0,
    total_pnl: pnl,
    realized_pnl: 0,
    unrealized_pnl: 0,
    return_pct: pnl / 100,
    sharpe_ratio: sharpe,
    positions: {},
    trades,
    orders_submitted: 0,
    fills: 0,
    filled_quantity: 0,
  };
}

const AGENTS = [agent(3, 5, null), agent(1, -2, 0.5), agent(2, 5, 1.5), agent(4, 0, -1)];
const ids = (agents: AgentOut[]) => agents.map((a) => a.id);

describe("sortAgents", () => {
  it("orders by P&L, breaking ties by id", () => {
    expect(ids(sortAgents(AGENTS, DEFAULT_ORDER))).toEqual([2, 3, 4, 1]);
    expect(ids(sortAgents(AGENTS, { key: "pnl", direction: "asc" }))).toEqual([1, 4, 2, 3]);
  });

  it("puts undefined values last in both directions", () => {
    expect(ids(sortAgents(AGENTS, { key: "sharpe", direction: "desc" }))).toEqual([2, 1, 4, 3]);
    expect(ids(sortAgents(AGENTS, { key: "sharpe", direction: "asc" }))).toEqual([4, 1, 2, 3]);
  });

  it("does not modify its input", () => {
    const input = [...AGENTS];
    sortAgents(input, DEFAULT_ORDER);
    expect(input).toEqual(AGENTS);
  });
});

describe("nextOrder", () => {
  it("reverses the sorted column and starts others descending", () => {
    expect(nextOrder(DEFAULT_ORDER, "pnl")).toEqual({ key: "pnl", direction: "asc" });
    expect(nextOrder({ key: "pnl", direction: "asc" }, "pnl")).toEqual(DEFAULT_ORDER);
    expect(nextOrder(DEFAULT_ORDER, "trades")).toEqual({ key: "trades", direction: "desc" });
    expect(nextOrder(DEFAULT_ORDER, "id")).toEqual({ key: "id", direction: "asc" });
  });
});
