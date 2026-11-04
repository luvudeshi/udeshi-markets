"use client";

import { fmtInt, fmtNumber } from "@/lib/format";
import type { LevelOut, OrderBookOut } from "@/lib/types";

import { deepestLevel } from "./BookLadder";

// The SVG is drawn in a fixed coordinate box and stretched to the panel; strokes keep their
// width (non-scaling-stroke) and labels are HTML, so nothing is distorted.
const WIDTH = 300;
const HEIGHT = 100;
const TOP = 6; // Headroom above the deepest level.

/**
 * A step path tracing one side's cumulative quantity (from the backend's
 * `cumulative_quantity`), from its best level outwards to the edge of the chart.
 */
function sidePath(
  levels: LevelOut[],
  edge: number,
  x: (price: number) => number,
  y: (quantity: number) => number,
): string {
  const first = levels[0];
  if (first === undefined) return "";
  let path = `M ${x(first.price)} ${HEIGHT}`;
  levels.forEach((level, index) => {
    const next = levels[index + 1];
    path += ` V ${y(level.cumulative_quantity)} H ${next === undefined ? x(edge) : x(next.price)}`;
  });
  return `${path} V ${HEIGHT} Z`;
}

/** Cumulative depth around the mid: bids to the left, asks to the right. */
export function DepthChart({ book, decimals }: { book: OrderBookOut; decimals: number }) {
  const deepest = deepestLevel(book);
  const lowest = book.bids.at(-1)?.price ?? book.asks[0]?.price;
  const highest = book.asks.at(-1)?.price ?? book.bids[0]?.price;

  if (lowest === undefined || highest === undefined || deepest === 0) {
    return (
      <div
        data-testid="depth-chart"
        className="flex h-[104px] shrink-0 items-center justify-center border-t border-line text-[11px] text-faint"
      >
        No depth
      </div>
    );
  }

  // Rendering scales: prices and quantities to coordinates, with a margin so the deepest
  // level on each side keeps a visible step.
  const margin = highest > lowest ? (highest - lowest) * 0.05 : Math.max(lowest * 0.001, 1e-9);
  const left = lowest - margin;
  const right = highest + margin;
  const x = (price: number) => ((price - left) / (right - left)) * WIDTH;
  const y = (quantity: number) => HEIGHT - (quantity / deepest) * (HEIGHT - TOP);
  const mid = book.midprice;

  return (
    <div
      data-testid="depth-chart"
      className="relative h-[104px] shrink-0 border-t border-line"
      role="img"
      aria-label={`Market depth: up to ${fmtInt(deepest)} shares`}
    >
      <svg
        viewBox={`0 0 ${WIDTH} ${HEIGHT}`}
        preserveAspectRatio="none"
        className="absolute inset-x-0 top-3.5 bottom-4 h-[calc(100%-2rem)] w-full"
        aria-hidden="true"
      >
        <path
          d={sidePath(book.bids, left, x, y)}
          fill="var(--color-bid-soft)"
          stroke="var(--color-bid)"
          strokeWidth={1}
          vectorEffect="non-scaling-stroke"
        />
        <path
          d={sidePath(book.asks, right, x, y)}
          fill="var(--color-ask-soft)"
          stroke="var(--color-ask)"
          strokeWidth={1}
          vectorEffect="non-scaling-stroke"
        />
        {mid !== null && (
          <line
            x1={x(mid)}
            x2={x(mid)}
            y1={0}
            y2={HEIGHT}
            stroke="var(--color-faint)"
            strokeDasharray="3 3"
            strokeWidth={1}
            vectorEffect="non-scaling-stroke"
          />
        )}
      </svg>
      <span className="label-caps absolute top-0.5 left-2.5">Depth</span>
      <span className="num absolute top-0.5 right-2.5 text-2xs text-faint">{fmtInt(deepest)}</span>
      <div className="num absolute inset-x-2.5 bottom-0.5 flex justify-between text-2xs text-faint">
        <span>{fmtNumber(lowest, decimals)}</span>
        <span>{fmtNumber(mid, decimals + 1)}</span>
        <span>{fmtNumber(highest, decimals)}</span>
      </div>
    </div>
  );
}
