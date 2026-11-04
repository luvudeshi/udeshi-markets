"use client";

import { Empty, Panel } from "@/components/ui/Panel";

/** Placeholder: implemented in its own change. */
export function Leaderboard({ className = "" }: { className?: string }) {
  return (
    <Panel title="Leaderboard" className={className} testId="leaderboard">
      <Empty>Leaderboard</Empty>
    </Panel>
  );
}
