/**
 * Executed trades drawn on the candles as dots, at each trade's own time and price.
 *
 * A separate series cannot do this: lightweight-charts gives every distinct time its own slot,
 * so trades between bar starts would push the candles apart. This primitive instead places
 * each trade inside the bar that contains it (see placement.ts) and converts to pixels with
 * the chart's own scales on every redraw.
 */
import type {
  IPrimitivePaneRenderer,
  IPrimitivePaneView,
  ISeriesPrimitive,
  Logical,
  SeriesAttachedParameter,
  Time,
} from "lightweight-charts";

import type { TradeOut } from "@/lib/types";

import { slotOf } from "./placement";
import { PALETTE } from "./theme";

type Target = Parameters<IPrimitivePaneRenderer["draw"]>[0];

interface Dot {
  x: number;
  y: number;
  radius: number;
  color: string;
}

const MIN_RADIUS = 1.75;
const MAX_RADIUS = 5;

/** Rendering scale only: the dot's area grows with the traded quantity, capped. */
function radiusFor(quantity: number): number {
  return Math.min(MAX_RADIUS, MIN_RADIUS + 0.35 * Math.sqrt(Math.max(quantity, 0)));
}

export class TradePrints implements ISeriesPrimitive<Time> {
  private host: SeriesAttachedParameter<Time> | null = null;
  private trades: readonly TradeOut[] = [];
  private starts: readonly number[] = [];
  private interval = 1;
  private dots: Dot[] = [];
  private readonly views: readonly IPrimitivePaneView[];

  constructor() {
    const renderer: IPrimitivePaneRenderer = { draw: (target) => this.draw(target) };
    const view: IPrimitivePaneView = { zOrder: () => "top", renderer: () => renderer };
    this.views = [view];
  }

  attached(param: SeriesAttachedParameter<Time>): void {
    this.host = param;
  }

  detached(): void {
    this.host = null;
  }

  paneViews(): readonly IPrimitivePaneView[] {
    return this.views;
  }

  /** Called by the chart whenever the viewport changed: recompute the pixel positions. */
  updateAllViews(): void {
    this.dots = this.layout();
  }

  /** The trades to draw, and the loaded bars' start times they are placed against. */
  setData(trades: readonly TradeOut[], starts: readonly number[], interval: number): void {
    this.trades = trades;
    this.starts = starts;
    this.interval = interval;
    this.host?.requestUpdate();
  }

  private layout(): Dot[] {
    const host = this.host;
    if (host === null || this.starts.length === 0) return [];
    const timeScale = host.chart.timeScale();
    const visible = timeScale.getVisibleLogicalRange();
    const dots: Dot[] = [];
    for (const trade of this.trades) {
      const slot = slotOf(this.starts, this.interval, trade.time_seconds);
      if (slot === null) continue;
      if (visible !== null && (slot.index < visible.from - 1 || slot.index > visible.to + 1)) {
        continue;
      }
      // The library only converts whole indices: interpolate between this slot and the next.
      const left = timeScale.logicalToCoordinate(slot.index as Logical);
      const right = timeScale.logicalToCoordinate((slot.index + 1) as Logical);
      const y = host.series.priceToCoordinate(trade.price);
      if (left === null || right === null || y === null) continue;
      dots.push({
        x: left + (slot.fraction - 0.5) * (right - left),
        y,
        radius: radiusFor(trade.quantity),
        color: trade.aggressor_side === "buy" ? PALETTE.bid : PALETTE.ask,
      });
    }
    return dots;
  }

  private draw(target: Target): void {
    const dots = this.dots;
    if (dots.length === 0) return;
    target.useMediaCoordinateSpace(({ context }) => {
      context.save();
      context.lineWidth = 1;
      for (const dot of dots) {
        context.beginPath();
        context.arc(dot.x, dot.y, dot.radius, 0, 2 * Math.PI);
        context.globalAlpha = 0.35;
        context.fillStyle = dot.color;
        context.fill();
        context.globalAlpha = 0.95;
        context.strokeStyle = dot.color;
        context.stroke();
      }
      context.restore();
    });
  }
}
