"use client";

import { useEffect, useEffectEvent, useRef, type ReactNode } from "react";
import { createPortal } from "react-dom";
import { useShallow } from "zustand/react/shallow";

import { Button } from "@/components/ui/controls";
import { useApi } from "@/lib/api-context";
import {
  DASH,
  STRATEGY_LABELS,
  decimalsOf,
  fmtInt,
  fmtNumber,
  fmtPercent,
  fmtSigned,
  fmtSimTime,
  participantTag,
  signClass,
} from "@/lib/format";
import { usePoll } from "@/lib/hooks";
import { useTerminal } from "@/lib/stream/store";
import type { AgentDetailOut } from "@/lib/types";

type AgentStats = NonNullable<AgentDetailOut["stats"]>;

const DETAIL_MS = 1_500;

const TH = "h-6 px-2 label-caps text-left font-medium shadow-[inset_0_-1px_0_var(--color-line)]";
const TD = "h-[18px] px-2 whitespace-nowrap";

const STATS: readonly [keyof AgentStats, string][] = [
  ["orders_submitted", "Orders"],
  ["orders_rejected", "Rejected"],
  ["orders_filled", "Filled"],
  ["cancels_sent", "Cancels"],
  ["fills", "Fills"],
  ["filled_quantity", "Filled qty"],
  ["aggressive_fills", "Aggressive"],
  ["passive_fills", "Passive"],
];

function Section({ title, children }: { title: string; children: ReactNode }) {
  return (
    <section className="border-b border-line">
      <h3 className="label-caps px-3 pt-2.5 pb-1.5 text-text/80">{title}</h3>
      {children}
    </section>
  );
}

function Figure({
  label,
  value,
  className = "",
}: {
  label: string;
  value: string;
  className?: string;
}) {
  return (
    <div className="flex flex-col gap-0.5 px-3 py-1.5">
      <span className="label-caps">{label}</span>
      <span className={`num text-[12px] ${className || "text-text"}`}>{value}</span>
    </div>
  );
}

/** "spread_earned" → "Spread earned". */
function metricLabel(key: string): string {
  const words = key.replace(/_/g, " ");
  return words.charAt(0).toUpperCase() + words.slice(1);
}

/**
 * One participant in detail, from `GET /api/agents/{id}`: positions, orders, activity and its
 * strategy's own measures. A sheet over the terminal, closed with Esc or the close button.
 */
export function AgentDrawer({
  simulationId,
  agentId,
  onClose,
}: {
  simulationId: string;
  agentId: number;
  onClose: () => void;
}) {
  const api = useApi();
  // While running, poll on a timer; otherwise fetch again only when the clock moves (a step).
  // Selected as one value so that a running clock does not re-render the sheet every tick.
  const moment = useTerminal((state) =>
    state.status?.state === "running" ? "live" : String(state.status?.time_seconds ?? 0),
  );
  const snapshots = useTerminal((state) => state.snapshots);
  const tickSizes = useTerminal(
    useShallow((state) =>
      Object.fromEntries(
        state.symbols.map((symbol) => [symbol, state.markets[symbol]?.market.tick_size]),
      ),
    ),
  );
  const dialog = useRef<HTMLElement>(null);

  const detail = usePoll<AgentDetailOut>(
    `${simulationId}:${agentId}:${snapshots}:${moment}`,
    () => api.agent(simulationId, agentId),
    moment === "live" ? DETAIL_MS : null,
  );

  const close = useEffectEvent(onClose);
  useEffect(() => {
    dialog.current?.focus(); // Keyboard users land in the sheet; Esc closes it.
    const onKey = (event: globalThis.KeyboardEvent) => {
      if (event.key === "Escape") close();
    };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, []);

  const decimals = (symbol: string) => {
    const tick = tickSizes[symbol];
    return tick === undefined ? 2 : decimalsOf(tick);
  };
  const agent = detail.data;

  return createPortal(
    <div className="fixed inset-0 z-50">
      <div className="absolute inset-0 bg-black/50" onClick={onClose} aria-hidden="true" />
      <aside
        ref={dialog}
        tabIndex={-1}
        role="dialog"
        aria-modal="true"
        aria-labelledby="agent-drawer-title"
        data-testid="agent-drawer"
        className="absolute inset-y-0 right-0 flex w-[min(560px,100vw)] flex-col border-l border-line-strong bg-panel text-[11px] focus:outline-none"
      >
        <header className="flex h-10 shrink-0 items-center gap-3 border-b border-line px-3">
          <h2 id="agent-drawer-title" className="flex min-w-0 items-baseline gap-2">
            <span className="num text-[13px] font-medium text-text">
              {agent === undefined ? `#${agentId}` : participantTag(agent)}
            </span>
            {agent !== undefined && (
              <span className="truncate text-muted">
                {STRATEGY_LABELS[agent.strategy]} · {agent.name}
              </span>
            )}
          </h2>
          <Button
            tone="ghost"
            className="ml-auto"
            onClick={onClose}
            aria-label="Close agent detail"
          >
            Close <span className="text-faint">Esc</span>
          </Button>
        </header>

        <div className="min-h-0 flex-1 overflow-auto">
          {detail.error !== null && (
            <p className="border-b border-line px-3 py-2 text-ask" role="alert">
              {detail.error.message}
            </p>
          )}
          {agent === undefined ? (
            detail.error === null && <p className="px-3 py-3 text-muted">Loading…</p>
          ) : (
            <>
              <div className="grid grid-cols-4 border-b border-line">
                <Figure label="Cash" value={fmtNumber(agent.cash, 2)} />
                <Figure label="Equity" value={fmtNumber(agent.equity, 2)} />
                <Figure
                  label="P&L"
                  value={fmtSigned(agent.total_pnl, 2)}
                  className={signClass(agent.total_pnl)}
                />
                <Figure
                  label="Return"
                  value={fmtPercent(agent.return_pct, 3)}
                  className={signClass(agent.return_pct)}
                />
                <Figure
                  label="Realized"
                  value={fmtSigned(agent.realized_pnl, 2)}
                  className={signClass(agent.realized_pnl)}
                />
                <Figure
                  label="Unrealized"
                  value={fmtSigned(agent.unrealized_pnl, 2)}
                  className={signClass(agent.unrealized_pnl)}
                />
                <Figure label="Sharpe /h" value={fmtNumber(agent.sharpe_ratio, 2)} />
                <Figure label="Trades" value={fmtInt(agent.trades)} />
              </div>

              <Section title="Positions">
                {agent.position_details.length === 0 ? (
                  <p className="px-3 pb-2.5 text-muted">No positions</p>
                ) : (
                  <table className="w-full border-collapse" aria-label="Positions">
                    <thead>
                      <tr>
                        <th scope="col" className={`${TH} pl-3`}>
                          Symbol
                        </th>
                        <th scope="col" className={`${TH} text-right`}>
                          Qty
                        </th>
                        <th scope="col" className={`${TH} text-right`}>
                          Avg price
                        </th>
                        <th scope="col" className={`${TH} text-right`}>
                          Realized
                        </th>
                        <th scope="col" className={`${TH} text-right`}>
                          Unrealized
                        </th>
                        <th scope="col" className={`${TH} text-right`}>
                          Bought
                        </th>
                        <th scope="col" className={`${TH} pr-3 text-right`}>
                          Sold
                        </th>
                      </tr>
                    </thead>
                    <tbody>
                      {agent.position_details.map((position) => (
                        <tr
                          key={position.symbol}
                          data-testid="agent-position"
                          data-symbol={position.symbol}
                          className="border-b border-line/40"
                        >
                          <td className={`${TD} pl-3 font-medium`}>{position.symbol}</td>
                          <td
                            className={`${TD} num text-right ${signClass(position.quantity)}`}
                            data-testid="position-quantity"
                          >
                            {fmtSigned(position.quantity, 0)}
                          </td>
                          <td className={`${TD} num text-right`}>
                            {fmtNumber(position.average_price, decimals(position.symbol) + 2)}
                          </td>
                          <td
                            className={`${TD} num text-right ${signClass(position.realized_pnl)}`}
                          >
                            {fmtSigned(position.realized_pnl, 2)}
                          </td>
                          <td
                            className={`${TD} num text-right ${signClass(position.unrealized_pnl)}`}
                          >
                            {fmtSigned(position.unrealized_pnl, 2)}
                          </td>
                          <td className={`${TD} num text-right text-muted`}>
                            {fmtInt(position.bought)}
                          </td>
                          <td className={`${TD} num pr-3 text-right text-muted`}>
                            {fmtInt(position.sold)}
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                )}
              </Section>

              <Section title={`Open orders (${agent.open_orders.length})`}>
                {agent.open_orders.length === 0 ? (
                  <p className="px-3 pb-2.5 text-muted">None resting</p>
                ) : (
                  <table className="w-full border-collapse" aria-label="Open orders">
                    <thead>
                      <tr>
                        <th scope="col" className={`${TH} pl-3`}>
                          Id
                        </th>
                        <th scope="col" className={TH}>
                          Symbol
                        </th>
                        <th scope="col" className={TH}>
                          Side
                        </th>
                        <th scope="col" className={`${TH} text-right`}>
                          Price
                        </th>
                        <th scope="col" className={`${TH} text-right`}>
                          Left / qty
                        </th>
                        <th scope="col" className={`${TH} pr-3 text-right`}>
                          Since
                        </th>
                      </tr>
                    </thead>
                    <tbody>
                      {agent.open_orders.map((order) => (
                        <tr key={order.order_id} className="border-b border-line/40">
                          <td className={`${TD} num pl-3 text-muted`}>{order.order_id}</td>
                          <td className={TD}>{order.symbol}</td>
                          <td className={`${TD} ${order.side === "buy" ? "text-bid" : "text-ask"}`}>
                            {order.side.toUpperCase()}
                          </td>
                          <td className={`${TD} num text-right`}>
                            {fmtNumber(order.price, decimals(order.symbol))}
                          </td>
                          <td className={`${TD} num text-right`}>
                            {fmtInt(order.remaining_quantity)} / {fmtInt(order.original_quantity)}
                          </td>
                          <td className={`${TD} num pr-3 text-right text-muted`}>
                            {fmtSimTime(order.time_seconds, true)}
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                )}
              </Section>

              <Section title="Recent orders">
                {agent.recent_orders.length === 0 ? (
                  <p className="px-3 pb-2.5 text-muted">No orders yet</p>
                ) : (
                  <table className="w-full border-collapse" aria-label="Recent orders">
                    <thead>
                      <tr>
                        <th scope="col" className={`${TH} pl-3`}>
                          Time
                        </th>
                        <th scope="col" className={TH}>
                          Symbol
                        </th>
                        <th scope="col" className={TH}>
                          Side
                        </th>
                        <th scope="col" className={`${TH} text-right`}>
                          Price
                        </th>
                        <th scope="col" className={`${TH} text-right`}>
                          Qty
                        </th>
                        <th scope="col" className={`${TH} text-right`}>
                          Filled
                        </th>
                        <th scope="col" className={`${TH} pr-3`}>
                          Status
                        </th>
                      </tr>
                    </thead>
                    <tbody>
                      {[...agent.recent_orders].reverse().map((order, index) => {
                        const reason = (order.reject_reason ?? order.cancel_reason)?.replace(
                          /_/g,
                          " ",
                        );
                        return (
                          <tr
                            key={`${order.order_id}-${index}`}
                            className="border-b border-line/40"
                          >
                            <td className={`${TD} num pl-3 text-muted`}>
                              {fmtSimTime(order.time_seconds, true)}
                            </td>
                            <td className={TD}>{order.symbol}</td>
                            <td
                              className={`${TD} ${order.side === "buy" ? "text-bid" : "text-ask"}`}
                            >
                              {order.side.toUpperCase()}
                              <span className="text-faint">
                                {" "}
                                {order.type === "market"
                                  ? "MKT"
                                  : order.time_in_force.toUpperCase()}
                              </span>
                            </td>
                            <td className={`${TD} num text-right`}>
                              {order.price === null || order.price === undefined
                                ? DASH
                                : fmtNumber(order.price, decimals(order.symbol))}
                            </td>
                            <td className={`${TD} num text-right`}>{fmtInt(order.quantity)}</td>
                            <td className={`${TD} num text-right`}>
                              {fmtInt(order.filled_quantity)}
                            </td>
                            <td
                              className={`${TD} pr-3 ${
                                order.status === "rejected" ? "text-ask" : "text-muted"
                              }`}
                              title={reason ?? undefined}
                            >
                              {order.status}
                              {reason ? <span className="text-faint"> · {reason}</span> : null}
                            </td>
                          </tr>
                        );
                      })}
                    </tbody>
                  </table>
                )}
              </Section>

              {agent.stats !== null && agent.stats !== undefined && (
                <Section title="Activity">
                  <div className="grid grid-cols-4 pb-1">
                    {STATS.map(([key, label]) => (
                      <Figure key={key} label={label} value={fmtInt(agent.stats?.[key])} />
                    ))}
                  </div>
                </Section>
              )}

              {Object.keys(agent.metrics).length > 0 && (
                <Section title="Strategy measures">
                  <dl className="grid grid-cols-2 pb-1">
                    {Object.entries(agent.metrics).map(([key, value]) => (
                      <div
                        key={key}
                        className="flex items-baseline justify-between gap-2 px-3 py-1"
                      >
                        <dt className="text-muted">{metricLabel(key)}</dt>
                        <dd className="num text-text">
                          {Number.isInteger(value) ? fmtInt(value) : fmtNumber(value, 4)}
                        </dd>
                      </div>
                    ))}
                  </dl>
                </Section>
              )}
            </>
          )}
        </div>
      </aside>
    </div>,
    document.body,
  );
}
