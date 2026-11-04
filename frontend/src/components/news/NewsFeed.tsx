"use client";

import { Empty, Panel } from "@/components/ui/Panel";

/** Placeholder: implemented in its own change. */
export function NewsFeed({ className = "" }: { className?: string }) {
  return (
    <Panel title="News" className={className} testId="news">
      <Empty>News</Empty>
    </Panel>
  );
}
