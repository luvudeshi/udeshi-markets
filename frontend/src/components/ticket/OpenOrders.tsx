"use client";

import { useState } from "react";

import { Button } from "@/components/ui/controls";
import { useApi } from "@/lib/api-context";
import { fmtInt, fmtNumber, fmtSimTime } from "@/lib/format";
import type { Polled } from "@/lib/hooks";
import type { OpenOrderOut } from "@/lib/types";

import { describeError, type Problem } from "./errors";

const COLUMNS = "grid grid-cols-[2.4rem_1fr_1fr_1fr_3.4rem] items-center gap-x-2";

/** The API user's resting orders in the selected market, each with a Cancel button. */
export function OpenOrders({
  simulationId,
  symbol,
  decimals,
  orders,
}: {
  simulationId: string;
  symbol: string;
  decimals: number;
  orders: Polled<OpenOrderOut[]>;
}) {
  const api = useApi();
  const [cancelling, setCancelling] = useState<number | null>(null);
  const [problem, setProblem] = useState<Problem | null>(null);

  async function cancel(orderId: number) {
    setCancelling(orderId);
    setProblem(null);
    try {
      await api.cancelOrder(simulationId, symbol, orderId);
    } catch (error) {
      // An order that filled meanwhile is reported as order_not_found: say so, then refresh.
      setProblem(describeError(error));
    } finally {
      setCancelling(null);
      orders.refresh();
    }
  }

  const list = orders.data;
  return (
    <section data-testid="open-orders" aria-label="Your open orders" className="flex flex-col">
      <header className="flex h-6 items-center justify-between border-y border-line bg-raised px-2.5">
        <h3 className="label-caps">Your open orders · {symbol}</h3>
        <span className="num text-2xs text-faint">{list === undefined ? "" : list.length}</span>
      </header>
      {problem !== null && (
        <div role="alert" className="px-2.5 py-1 text-[11px] text-ask">
          {problem.code} · {problem.message}
        </div>
      )}
      {orders.error !== null && (
        <div role="alert" className="px-2.5 py-1 text-[11px] text-ask">
          Could not load your orders: {describeError(orders.error).message}
        </div>
      )}
      {list === undefined ? (
        orders.error === null && <div className="px-2.5 py-2 text-[11px] text-faint">Loading…</div>
      ) : list.length === 0 ? (
        <div className="px-2.5 py-2 text-[11px] text-faint">No resting orders</div>
      ) : (
        <div role="table" aria-label="Open orders" className="flex flex-col">
          <div role="row" className={`label-caps h-[18px] px-2.5 ${COLUMNS}`}>
            <span role="columnheader">Side</span>
            <span role="columnheader" className="text-right">
              Price
            </span>
            <span role="columnheader" className="text-right" title="Remaining of the original">
              Left / Qty
            </span>
            <span role="columnheader" className="text-right">
              Time
            </span>
            <span role="columnheader" className="sr-only">
              Action
            </span>
          </div>
          {list.map((order) => (
            <div
              role="row"
              key={order.order_id}
              data-testid="open-order-row"
              data-order-id={order.order_id}
              className={`num h-[22px] px-2.5 text-[11px] hover:bg-hover ${COLUMNS}`}
            >
              <span role="cell" className={order.side === "buy" ? "text-bid" : "text-ask"}>
                {order.side === "buy" ? "BUY" : "SELL"}
              </span>
              <span role="cell" className="text-right">
                {fmtNumber(order.price, decimals)}
              </span>
              <span role="cell" className="text-right">
                {fmtInt(order.remaining_quantity)}
                <span className="text-faint"> / {fmtInt(order.original_quantity)}</span>
              </span>
              <span role="cell" className="text-right text-muted">
                {fmtSimTime(order.time_seconds)}
              </span>
              <span role="cell" className="flex justify-end">
                <Button
                  tone="ghost"
                  className="h-[18px] px-1.5"
                  disabled={cancelling !== null}
                  onClick={() => void cancel(order.order_id)}
                  aria-label={`Cancel order ${order.order_id}`}
                >
                  {cancelling === order.order_id ? "…" : "Cancel"}
                </Button>
              </span>
            </div>
          ))}
        </div>
      )}
    </section>
  );
}
