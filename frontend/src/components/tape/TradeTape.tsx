"use client";

import { useMemo, useState } from "react";

import { Empty } from "@/components/ui/Panel";
import { Segmented } from "@/components/ui/controls";
import { DASH, decimalsOf, fmtInt, fmtNumber, fmtSimTime, participantTag } from "@/lib/format";
import { useTerminal } from "@/lib/stream/store";
import { MANUAL_TRADER_ID, type AgentOut, type TradeOut } from "@/lib/types";

/** Rows shown: the most recent executions across the markets in view. */
export const TAPE_ROWS = 100;

type Scope = "all" | "selected";

const TH =
  "sticky top-0 z-10 h-6 bg-panel px-1 label-caps shadow-[inset_0_-1px_0_var(--color-line)]";

interface Execution {
  symbol: string;
  decimals: number;
  trade: TradeOut;
}

/**
 * Recent executions, newest first, with both counterparties: the observer's view of the tape
 * (agents themselves only ever see an anonymous one).
 */
export function TradeTape() {
  const symbols = useTerminal((state) => state.symbols);
  const markets = useTerminal((state) => state.markets);
  const agents = useTerminal((state) => state.agents);
  const selected = useTerminal((state) => state.selectedSymbol);
  const [scope, setScope] = useState<Scope>("all");

  const participants = useMemo(
    () => new Map<number, AgentOut>(agents.map((agent) => [agent.id, agent])),
    [agents],
  );

  const executions = useMemo(() => {
    const shown = scope === "selected" && selected !== null ? [selected] : symbols;
    const rows: Execution[] = [];
    for (const symbol of shown) {
      const market = markets[symbol];
      if (market === undefined) continue;
      const decimals = decimalsOf(market.market.tick_size);
      // Each market keeps its most recent trades, so their union holds the overall most recent.
      for (const trade of market.trades.slice(-TAPE_ROWS)) rows.push({ symbol, decimals, trade });
    }
    // Trade ids are exchange-wide and increase with time: newest first.
    rows.sort((a, b) => b.trade.trade_id - a.trade.trade_id);
    return rows.slice(0, TAPE_ROWS);
  }, [markets, symbols, scope, selected]);

  const party = (id: number | null) => {
    if (id === null) return { tag: DASH, you: false };
    const agent = participants.get(id);
    return {
      tag: agent === undefined ? `#${id}` : participantTag(agent),
      you: id === MANUAL_TRADER_ID,
    };
  };

  return (
    <div className="flex h-full flex-col" data-testid="trade-tape">
      <div className="flex h-7 shrink-0 items-center justify-between gap-2 border-b border-line px-2.5">
        <Segmented<Scope>
          label="Tape filter"
          options={["all", "selected"]}
          value={scope}
          onChange={setScope}
          format={(value) => (value === "all" ? "All" : (selected ?? "Selected"))}
          disabled={selected === null}
        />
        <span className="label-caps" data-testid="tape-count">
          {executions.length === 0 ? "No trades" : `Last ${executions.length}`}
        </span>
      </div>
      <div className="min-h-0 flex-1 overflow-auto">
        {executions.length === 0 ? (
          <Empty>No executions yet</Empty>
        ) : (
          <table
            className="w-full border-collapse text-[11px] whitespace-nowrap"
            aria-label="Trade tape"
          >
            <thead>
              <tr>
                <th scope="col" className={`${TH} pl-2.5 text-left`}>
                  Time
                </th>
                <th scope="col" className={`${TH} text-left`}>
                  Asset
                </th>
                <th scope="col" className={`${TH} text-right`}>
                  Price
                </th>
                <th scope="col" className={`${TH} text-right`}>
                  Qty
                </th>
                <th scope="col" className={`${TH} text-left`}>
                  Buyer
                </th>
                <th scope="col" className={`${TH} pr-2.5 text-left`}>
                  Seller
                </th>
              </tr>
            </thead>
            <tbody>
              {executions.map(({ symbol, decimals, trade }) => {
                const buyer = party(trade.buyer);
                const seller = party(trade.seller);
                const aggressorBuys = trade.aggressor_side === "buy";
                return (
                  <tr
                    key={trade.trade_id}
                    data-testid="tape-row"
                    data-trade-id={trade.trade_id}
                    data-symbol={symbol}
                    className={`h-[18px] border-b border-line/40 ${
                      buyer.you || seller.you ? "bg-accent-soft" : "hover:bg-hover"
                    }`}
                  >
                    <td className="num pr-1 pl-2.5 text-muted">
                      {fmtSimTime(trade.time_seconds, true)}
                    </td>
                    <td className="px-1 text-text/85">{symbol}</td>
                    <td
                      className={`num px-1 text-right ${aggressorBuys ? "text-bid" : "text-ask"}`}
                      title={aggressorBuys ? "Buyer initiated" : "Seller initiated"}
                    >
                      {fmtNumber(trade.price, decimals)}
                    </td>
                    <td className="num px-1 text-right text-text">{fmtInt(trade.quantity)}</td>
                    <td
                      className={`num px-1 ${buyer.you ? "font-medium text-accent" : "text-text/80"}`}
                      data-col="buyer"
                    >
                      {buyer.tag}
                    </td>
                    <td
                      className={`num pr-2.5 pl-1 ${seller.you ? "font-medium text-accent" : "text-text/80"}`}
                      data-col="seller"
                    >
                      {seller.tag}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        )}
      </div>
    </div>
  );
}
