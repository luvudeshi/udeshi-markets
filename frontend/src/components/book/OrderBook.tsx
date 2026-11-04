"use client";

import { Empty, Panel } from "@/components/ui/Panel";
import { decimalsOf, fmtNumber, fmtSimTime } from "@/lib/format";
import { useSelectedMarket } from "@/lib/stream/store";
import type { OrderBookOut } from "@/lib/types";

import { BookLadder } from "./BookLadder";
import { DepthChart } from "./DepthChart";

function Stat({
  label,
  value,
  testId,
  tone = "text-text",
}: {
  label: string;
  value: string;
  testId: string;
  tone?: string;
}) {
  return (
    <div className="flex min-w-0 flex-col gap-0.5 px-2.5 py-1.5">
      <span className="label-caps">{label}</span>
      <span className={`num truncate text-[12px] ${tone}`} data-testid={testId}>
        {value}
      </span>
    </div>
  );
}

function BookSummary({ book, decimals }: { book: OrderBookOut; decimals: number }) {
  return (
    <div className="grid shrink-0 grid-cols-4 divide-x divide-line border-b border-line">
      <Stat
        label="Bid"
        value={fmtNumber(book.best_bid, decimals)}
        testId="best-bid"
        tone="text-bid"
      />
      <Stat
        label="Ask"
        value={fmtNumber(book.best_ask, decimals)}
        testId="best-ask"
        tone="text-ask"
      />
      {/* The mid of two prices on the tick grid needs one more decimal place. */}
      <Stat label="Mid" value={fmtNumber(book.midprice, decimals + 1)} testId="book-mid" />
      <Stat label="Spread" value={fmtNumber(book.spread, decimals)} testId="book-spread" />
    </div>
  );
}

/** The selected market's live order book: best prices, the ladder and a depth chart, all as
 * the backend streamed them. */
export function OrderBook({ className = "" }: { className?: string }) {
  const selected = useSelectedMarket();
  if (selected === undefined) {
    return (
      <Panel title="Order book" className={className} testId="orderbook">
        <Empty>No market selected</Empty>
      </Panel>
    );
  }
  const { book, market } = selected;
  const decimals = decimalsOf(market.tick_size);
  return (
    <Panel
      title={`Order book · ${book.symbol}`}
      className={className}
      testId="orderbook"
      actions={
        <span className="num text-2xs text-faint" title="Simulated time of this book">
          {fmtSimTime(book.time_seconds, true)}
        </span>
      }
    >
      <div className="flex h-full min-h-0 flex-col">
        <BookSummary book={book} decimals={decimals} />
        <BookLadder book={book} decimals={decimals} />
        <DepthChart book={book} decimals={decimals} />
      </div>
    </Panel>
  );
}
