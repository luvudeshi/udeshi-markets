"use client";

import { fmtInt, fmtNumber } from "@/lib/format";
import type { OrderOut } from "@/lib/types";

import type { ErrorDescription } from "@/lib/errors";

export type Outcome =
  { kind: "order"; order: OrderOut } | { kind: "error"; problem: ErrorDescription };

const STATUS: Record<OrderOut["status"], { label: string; tone: string }> = {
  filled: { label: "Filled", tone: "text-bid border-bid/50 bg-bid-soft" },
  resting: { label: "Resting", tone: "text-accent border-accent/50 bg-accent-soft" },
  cancelled: { label: "Cancelled", tone: "text-muted border-line-strong bg-raised" },
  rejected: { label: "Rejected", tone: "text-ask border-ask/50 bg-ask-soft" },
};

function Quantity({ label, value }: { label: string; value: number }) {
  return (
    <span className="flex items-baseline gap-1">
      <span className="label-caps">{label}</span>
      <span className="num text-text">{fmtInt(value)}</span>
    </span>
  );
}

/** What the exchange did with the last order: its status, quantities and fills, exactly as the
 * API reported them; or why the API refused it. */
export function TicketResult({ outcome, decimals }: { outcome: Outcome; decimals: number }) {
  if (outcome.kind === "error") {
    return (
      <div
        data-testid="ticket-result"
        data-status="error"
        role="alert"
        className="border border-ask/40 bg-ask-soft px-2 py-1.5 text-[11px]"
      >
        <span className="num text-ask">{outcome.problem.code}</span>
        <span className="text-text"> · {outcome.problem.message}</span>
        {outcome.problem.lines.map((line) => (
          <div key={line} className="text-text/80">
            {line}
          </div>
        ))}
      </div>
    );
  }
  const { order } = outcome;
  const status = STATUS[order.status];
  const reason = order.reject_reason ?? order.cancel_reason;
  return (
    <div
      data-testid="ticket-result"
      data-status={order.status}
      role="status"
      className="flex flex-col gap-1 border border-line bg-raised px-2 py-1.5 text-[11px]"
    >
      <div className="flex items-center justify-between gap-2">
        <span className={`label-caps border px-1 ${status.tone}`}>{status.label}</span>
        {order.order_id !== null && (
          <span className="num text-2xs text-faint">#{order.order_id}</span>
        )}
      </div>
      <div className="flex flex-wrap gap-x-3 gap-y-0.5">
        <Quantity label="Filled" value={order.filled_quantity} />
        <Quantity label="Resting" value={order.resting_quantity} />
        <Quantity label="Cancelled" value={order.cancelled_quantity} />
      </div>
      {reason !== null && (
        <div className="text-muted">
          Reason: <span className="num text-text">{reason.replaceAll("_", " ")}</span>
        </div>
      )}
      {order.fills.length > 0 && (
        <ul className="flex flex-col border-t border-line pt-1" aria-label="Fills">
          {order.fills.map((fill) => (
            <li
              key={fill.trade_id}
              data-testid="ticket-fill"
              data-trade-id={fill.trade_id}
              className="num flex justify-between gap-2"
            >
              <span>
                {fmtInt(fill.quantity)} @ {fmtNumber(fill.price, decimals)}
              </span>
              <span className="text-faint">
                {fill.is_aggressor ? "taker" : "maker"} · trade {fill.trade_id}
              </span>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
