"use client";

import { fmtInt, fmtNumber } from "@/lib/format";
import type { LevelOut, OrderBookOut } from "@/lib/types";

type Side = "bid" | "ask";

const COLUMNS = "grid grid-cols-[1.15fr_1fr_0.55fr_1fr] items-center gap-x-2 px-2.5";

/** The deepest cumulative quantity on either side: the full width of a depth bar. */
export function deepestLevel(book: OrderBookOut): number {
  return Math.max(
    book.bids.at(-1)?.cumulative_quantity ?? 0,
    book.asks.at(-1)?.cumulative_quantity ?? 0,
  );
}

function LevelRow({
  level,
  side,
  decimals,
  deepest,
}: {
  level: LevelOut;
  side: Side;
  decimals: number;
  deepest: number;
}) {
  // A rendering scale: the bar's share of the panel width, nothing more.
  const width = deepest > 0 ? (level.cumulative_quantity / deepest) * 100 : 0;
  return (
    <div
      data-testid={side === "ask" ? "book-ask-row" : "book-bid-row"}
      data-price={level.price}
      className={`num relative h-[18px] shrink-0 text-[11px] leading-[18px] hover:bg-hover ${COLUMNS}`}
    >
      <div
        aria-hidden="true"
        className={`absolute inset-y-px right-0 ${side === "ask" ? "bg-ask-soft" : "bg-bid-soft"}`}
        style={{ width: `${width}%` }}
      />
      <span className={`relative ${side === "ask" ? "text-ask" : "text-bid"}`}>
        {fmtNumber(level.price, decimals)}
      </span>
      <span className="relative text-right text-text">{fmtInt(level.quantity)}</span>
      <span className="relative text-right text-faint">{level.orders}</span>
      <span className="relative text-right text-muted">{fmtInt(level.cumulative_quantity)}</span>
    </div>
  );
}

function NoLevels({ children }: { children: string }) {
  return (
    <div className="flex h-[18px] shrink-0 items-center px-2.5 text-[11px] text-faint">
      {children}
    </div>
  );
}

/**
 * The price ladder: asks above (the best ask next to the middle), bids below (best first).
 * Each side shows as many levels as fit; the ones nearest the spread are always visible.
 */
export function BookLadder({ book, decimals }: { book: OrderBookOut; decimals: number }) {
  const deepest = deepestLevel(book);
  const asks = [...book.asks].reverse(); // Worst first, so the best ask sits by the spread.
  return (
    <div className="flex min-h-0 flex-1 flex-col">
      <div className={`label-caps h-[18px] shrink-0 border-b border-line ${COLUMNS}`}>
        <span>Price</span>
        <span className="text-right">Qty</span>
        <span className="text-right">Ord</span>
        <span className="text-right">Total</span>
      </div>
      <div
        className="flex min-h-0 flex-1 flex-col justify-end overflow-hidden"
        role="group"
        aria-label="Asks"
      >
        {asks.length === 0 ? (
          <NoLevels>No asks</NoLevels>
        ) : (
          asks.map((level) => (
            <LevelRow
              key={level.price_ticks}
              level={level}
              side="ask"
              decimals={decimals}
              deepest={deepest}
            />
          ))
        )}
      </div>
      <div className="flex h-7 shrink-0 items-center justify-between gap-2 border-y border-line bg-raised px-2.5">
        <span className="flex items-baseline gap-2">
          <span className="num text-[13px] text-text">
            {fmtNumber(book.midprice, decimals + 1)}
          </span>
          <span className="label-caps">Mid</span>
        </span>
        <span className="flex items-baseline gap-2">
          <span className="label-caps">Spread</span>
          <span className="num text-[11px] text-text">{fmtNumber(book.spread, decimals)}</span>
        </span>
      </div>
      <div className="flex min-h-0 flex-1 flex-col overflow-hidden" role="group" aria-label="Bids">
        {book.bids.length === 0 ? (
          <NoLevels>No bids</NoLevels>
        ) : (
          book.bids.map((level) => (
            <LevelRow
              key={level.price_ticks}
              level={level}
              side="bid"
              decimals={decimals}
              deepest={deepest}
            />
          ))
        )}
      </div>
    </div>
  );
}
