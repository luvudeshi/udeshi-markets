/** Leaderboard ordering: a display choice over values the backend computed. */
import type { AgentOut } from "@/lib/types";

export type SortKey = "id" | "pnl" | "return" | "trades" | "sharpe";
export type Direction = "asc" | "desc";

export interface SortOrder {
  key: SortKey;
  direction: Direction;
}

export const DEFAULT_ORDER: SortOrder = { key: "pnl", direction: "desc" };

const VALUE: Record<SortKey, (agent: AgentOut) => number | null | undefined> = {
  id: (agent) => agent.id,
  pnl: (agent) => agent.total_pnl,
  return: (agent) => agent.return_pct,
  trades: (agent) => agent.trades,
  sharpe: (agent) => agent.sharpe_ratio,
};

/**
 * A sorted copy. Undefined values (a Sharpe ratio before there are two returns, say) come
 * last in either direction, and ties are broken by id, ascending, so the order is stable.
 */
export function sortAgents(agents: readonly AgentOut[], order: SortOrder): AgentOut[] {
  const value = VALUE[order.key];
  const sign = order.direction === "asc" ? 1 : -1;
  return [...agents].sort((a, b) => {
    const x = value(a);
    const y = value(b);
    const xMissing = x === null || x === undefined;
    const yMissing = y === null || y === undefined;
    if (xMissing !== yMissing) return xMissing ? 1 : -1;
    if (!xMissing && !yMissing && x !== y) return sign * (x < y ? -1 : 1);
    return a.id - b.id;
  });
}

/** Clicking the sorted column reverses it; another column starts descending (the agent
 * column, ascending). */
export function nextOrder(current: SortOrder, key: SortKey): SortOrder {
  if (current.key === key) {
    return { key, direction: current.direction === "asc" ? "desc" : "asc" };
  }
  return { key, direction: key === "id" ? "asc" : "desc" };
}
