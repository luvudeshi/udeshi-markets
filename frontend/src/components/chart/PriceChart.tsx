"use client";

import { Empty, Panel } from "@/components/ui/Panel";

/** Placeholder: implemented in its own change. */
export function PriceChart({ className = "" }: { className?: string }) {
  return (
    <Panel title="Chart" className={className} testId="chart">
      <Empty>Chart</Empty>
    </Panel>
  );
}
