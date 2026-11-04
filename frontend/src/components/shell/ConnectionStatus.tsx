"use client";

import type { ConnectionState } from "@/lib/stream/connection";
import { useTerminal } from "@/lib/stream/store";

const LOOK: Record<ConnectionState, { label: string; dot: string }> = {
  idle: { label: "Offline", dot: "bg-faint" },
  connecting: { label: "Connecting", dot: "bg-accent animate-pulse" },
  live: { label: "Live", dot: "bg-bid" },
  reconnecting: { label: "Reconnecting", dot: "bg-accent animate-pulse" },
  closed: { label: "Closed", dot: "bg-ask" },
};

/** The stream's state: live, (re)connecting, or closed with the server's reason. */
export function ConnectionStatus() {
  const connection = useTerminal((state) => state.connection);
  const detail = useTerminal((state) => state.connectionDetail);
  const look = LOOK[connection];
  return (
    <div
      className="flex items-center gap-1.5 pl-1"
      data-testid="connection"
      data-state={connection}
      title={detail ?? undefined}
    >
      <span className={`size-1.5 rounded-full ${look.dot}`} aria-hidden="true" />
      <span className="label-caps">{look.label}</span>
    </div>
  );
}
