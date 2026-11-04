"use client";

import { Empty, Panel } from "@/components/ui/Panel";

/** Placeholder: implemented in its own change. */
export function OrderBook({ className = "" }: { className?: string }) {
  return (
    <Panel title="Order book" className={className} testId="orderbook">
      <Empty>Order book</Empty>
    </Panel>
  );
}
