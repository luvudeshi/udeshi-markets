"use client";

import { useMemo, useState, type KeyboardEvent, type ReactNode } from "react";

import { Empty, Panel } from "@/components/ui/Panel";
import { Segmented } from "@/components/ui/controls";
import {
  DASH,
  STRATEGY_LABELS,
  fmtInt,
  fmtNumber,
  fmtPercent,
  fmtSigned,
  participantTag,
  signClass,
} from "@/lib/format";
import { useTerminal } from "@/lib/stream/store";
import type { AgentOut, Strategy } from "@/lib/types";

import { AgentDrawer } from "./AgentDrawer";
import { DEFAULT_ORDER, nextOrder, sortAgents, type SortKey, type SortOrder } from "./sort";

const TRADING: readonly Strategy[] = ["market_maker", "random", "momentum", "fundamental", "pairs"];
type Filter = "all" | Strategy;

const TH =
  "sticky top-0 z-10 h-6 bg-panel px-2 label-caps shadow-[inset_0_-1px_0_var(--color-line)]";

/** A strategy's short code, as in the agent tags (MM, RND, …). */
function codeOf(strategy: Strategy): string {
  return participantTag({ id: 0, strategy }).split("-")[0] ?? strategy;
}

/** Non-zero positions in instrument order: "ACME +12 · NOVA −3". */
function inventory(agent: AgentOut, symbols: readonly string[]): string {
  const held = symbols
    .filter((symbol) => (agent.positions[symbol] ?? 0) !== 0)
    .map((symbol) => `${symbol} ${fmtSigned(agent.positions[symbol], 0)}`);
  return held.length === 0 ? DASH : held.join(" · ");
}

/**
 * Every participant's standing, as the backend values it at the current marks, sortable by
 * performance. Selecting a row opens the participant's detail.
 */
export function Leaderboard({ className = "" }: { className?: string }) {
  const simulationId = useTerminal((state) => state.simulationId);
  const agents = useTerminal((state) => state.agents);
  const symbols = useTerminal((state) => state.symbols);
  const [order, setOrder] = useState<SortOrder>(DEFAULT_ORDER);
  const [filter, setFilter] = useState<Filter>("all");
  const [open, setOpen] = useState<{ simulationId: string; agentId: number } | null>(null);

  const present = useMemo(
    () => TRADING.filter((strategy) => agents.some((agent) => agent.strategy === strategy)),
    [agents],
  );
  const activeFilter: Filter = filter !== "all" && present.includes(filter) ? filter : "all";
  const rows = useMemo(
    () =>
      sortAgents(
        activeFilter === "all" ? agents : agents.filter((a) => a.strategy === activeFilter),
        order,
      ),
    [agents, activeFilter, order],
  );

  const header = (key: SortKey, label: ReactNode, align: "left" | "right", title?: string) => {
    const sorted = order.key === key;
    return (
      <th
        scope="col"
        className={`${TH} ${align === "right" ? "text-right" : "text-left"}`}
        aria-sort={sorted ? (order.direction === "asc" ? "ascending" : "descending") : "none"}
        title={title}
      >
        <button
          type="button"
          onClick={() => setOrder((current) => nextOrder(current, key))}
          className={`inline-flex items-center gap-1 uppercase tracking-[inherit] hover:text-text ${
            sorted ? "text-text" : ""
          }`}
          data-testid={`sort-${key}`}
        >
          {label}
          <span aria-hidden="true" className={sorted ? "text-accent" : "text-faint"}>
            {sorted ? (order.direction === "asc" ? "▴" : "▾") : "▿"}
          </span>
        </button>
      </th>
    );
  };

  const onKey = (event: KeyboardEvent, agentId: number) => {
    if (simulationId !== null && (event.key === "Enter" || event.key === " ")) {
      event.preventDefault();
      setOpen({ simulationId, agentId });
    }
  };

  return (
    <Panel
      title="Leaderboard"
      className={className}
      testId="leaderboard"
      actions={
        present.length > 1 ? (
          <Segmented<Filter>
            label="Strategy filter"
            options={["all", ...present]}
            value={activeFilter}
            onChange={setFilter}
            format={(value) => (value === "all" ? "All" : codeOf(value))}
          />
        ) : undefined
      }
    >
      {agents.length === 0 ? (
        <Empty>No participants yet</Empty>
      ) : (
        <table
          className="w-full border-collapse text-[11px] whitespace-nowrap"
          aria-label="Leaderboard"
        >
          <thead>
            <tr>
              <th scope="col" className={`${TH} w-8 pl-2.5 text-right`}>
                #
              </th>
              {header("id", "Agent", "left")}
              <th scope="col" className={`${TH} text-left`}>
                Strategy
              </th>
              {header("pnl", "P&L", "right", "Total P&L at the current marks")}
              {header("return", "Return", "right", "P&L over initial cash")}
              <th scope="col" className={`${TH} text-left`} title="Shares held per symbol">
                Inventory
              </th>
              {header("trades", "Trades", "right")}
              {header("sharpe", "Sharpe", "right", "Sharpe ratio per hour (live analytics)")}
            </tr>
          </thead>
          <tbody>
            {rows.map((agent, index) => {
              const muted = agent.strategy === "environment" || agent.strategy === "manual";
              return (
                <tr
                  key={agent.id}
                  data-testid="leaderboard-row"
                  data-agent-id={agent.id}
                  tabIndex={0}
                  onClick={() =>
                    simulationId !== null && setOpen({ simulationId, agentId: agent.id })
                  }
                  onKeyDown={(event) => onKey(event, agent.id)}
                  className={`h-[19px] cursor-pointer border-b border-line/40 hover:bg-hover ${
                    muted ? "opacity-55" : ""
                  }`}
                >
                  <td className="num pr-2 pl-2.5 text-right text-faint">{index + 1}</td>
                  <td className="num px-2 font-medium text-text">{participantTag(agent)}</td>
                  <td className="px-2 text-muted">{STRATEGY_LABELS[agent.strategy]}</td>
                  <td
                    className={`num px-2 text-right ${signClass(agent.total_pnl)}`}
                    data-testid="leaderboard-pnl"
                  >
                    {fmtSigned(agent.total_pnl, 2)}
                  </td>
                  <td className={`num px-2 text-right ${signClass(agent.return_pct)}`}>
                    {fmtPercent(agent.return_pct, 3)}
                  </td>
                  <td className="num max-w-64 truncate px-2 text-text/80">
                    {inventory(agent, symbols)}
                  </td>
                  <td className="num px-2 text-right text-text/85">{fmtInt(agent.trades)}</td>
                  <td className={`num pr-2.5 pl-2 text-right ${signClass(agent.sharpe_ratio)}`}>
                    {fmtNumber(agent.sharpe_ratio, 2)}
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      )}
      {open !== null && open.simulationId === simulationId && (
        <AgentDrawer
          simulationId={open.simulationId}
          agentId={open.agentId}
          onClose={() => setOpen(null)}
        />
      )}
    </Panel>
  );
}
