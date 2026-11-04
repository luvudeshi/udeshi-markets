"use client";

import { Empty, Panel } from "@/components/ui/Panel";

/** Placeholder: implemented in its own change. */
export function MarketOverview({ className = "" }: { className?: string }) {
  return (
    <Panel title="Markets" className={className} testId="markets">
      <Empty>Markets</Empty>
    </Panel>
  );
}
