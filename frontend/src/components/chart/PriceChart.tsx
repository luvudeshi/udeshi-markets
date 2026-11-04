"use client";

import {
  CandlestickSeries,
  createChart,
  createSeriesMarkers,
  HistogramSeries,
  LineStyle,
  type IChartApi,
  type IPriceLine,
  type ISeriesApi,
  type ISeriesMarkersPluginApi,
  type MouseEventParams,
  type SeriesMarker,
  type Time,
  type UTCTimestamp,
} from "lightweight-charts";
import { useEffect, useMemo, useRef, useState } from "react";

import { Panel } from "@/components/ui/Panel";
import { Segmented } from "@/components/ui/controls";
import { useApi } from "@/lib/api-context";
import {
  DASH,
  decimalsOf,
  fmtInt,
  fmtNumber,
  fmtPercent,
  fmtSigned,
  fmtSimTime,
  signClass,
} from "@/lib/format";
import { usePoll } from "@/lib/hooks";
import { useSelectedMarket, useTerminal } from "@/lib/stream/store";
import type { BarOut, NewsOut } from "@/lib/types";

import { defaultInterval, intervalLabel, intervalOptions } from "./intervals";
import { barStartOf, countPlaced, withNewerBars } from "./placement";
import { chartOptions, PALETTE, VOLUME_DOWN, VOLUME_UP } from "./theme";
import { TradePrints } from "./TradePrints";
import { useHistory } from "./useHistory";

/** When a symbol or interval is first shown, the newest bars are framed: as many as there are,
 * but at least MIN_FRAMED_BARS wide (so a young market's candles are not huge) and at most
 * MAX_FRAMED_BARS. The user can zoom from there. */
const MIN_FRAMED_BARS = 60;
const MAX_FRAMED_BARS = 150;
/** Empty bar slots kept right of the newest bar, where the forming bar's trades are drawn. */
const RIGHT_SLOTS = 5;
/** Most news items fetched for markers (the endpoint's limit). */
const NEWS_LIMIT = 1_000;

interface ChartParts {
  chart: IChartApi;
  candles: ISeriesApi<"Candlestick">;
  volume: ISeriesApi<"Histogram">;
  prints: TradePrints;
  markers: ISeriesMarkersPluginApi<Time>;
  fundamental: IPriceLine | null;
}

interface PlacedNews {
  /** Start of the bar the item is drawn on. */
  time: number;
  item: NewsOut;
}

const SENTIMENT_COLOR: Record<NewsOut["sentiment"], string> = {
  positive: PALETTE.bid,
  negative: PALETTE.ask,
  neutral: PALETTE.info,
};

function newsMarker({ time, item }: PlacedNews, index: number): SeriesMarker<Time> {
  return {
    id: `news-${index}`,
    time: time as UTCTimestamp,
    position: item.sentiment === "negative" ? "aboveBar" : "belowBar",
    shape:
      item.sentiment === "positive"
        ? "arrowUp"
        : item.sentiment === "negative"
          ? "arrowDown"
          : "circle",
    color: SENTIMENT_COLOR[item.sentiment],
    size: 1,
  };
}

/** Candles, volume, executed trades and news for the selected symbol, from the backend's
 * history; it refreshes as bars close. */
export function PriceChart({ className = "" }: { className?: string }) {
  const api = useApi();
  const simulationId = useTerminal((state) => state.simulationId);
  const snapshots = useTerminal((state) => state.snapshots);
  const streamedNews = useTerminal((state) => state.news);
  const market = useSelectedMarket();
  const symbol = market?.symbol ?? null;

  // The simulation's bar interval decides which intervals the backend can serve.
  const simulation = usePoll(
    simulationId,
    () =>
      simulationId === null
        ? Promise.reject(new Error("no simulation selected"))
        : api.getSimulation(simulationId),
    null,
  );
  const base = simulation.data?.bar_interval_seconds ?? null;
  const options = useMemo(() => (base === null ? [] : intervalOptions(base)), [base]);
  const [chosen, setChosen] = useState<number | null>(null);
  const interval = chosen !== null && options.includes(chosen) ? chosen : defaultInterval(options);

  const history = useHistory(
    simulationId,
    symbol,
    interval,
    `${snapshots}:${market?.barsRevision ?? 0}`,
  );
  // At the simulation's own interval the stream's closed bars extend the history until the
  // next (throttled) request, so the newest candles keep up at any speed.
  const streamedBars = interval !== null && interval === base ? market?.bars : undefined;
  const bars = useMemo(
    () =>
      history.bars === null || streamedBars === undefined
        ? history.bars
        : withNewerBars(history.bars, streamedBars),
    [history.bars, streamedBars],
  );
  const starts = useMemo(() => (bars ?? []).map((bar) => bar.start_seconds), [bars]);

  // News for markers: the full list from the API (the stream's snapshot carries only recent
  // items), fetched again whenever a news item arrives. The last list fetched for this run
  // stays in use meanwhile, so markers do not blink.
  const generation = simulationId === null ? null : `${simulationId}|${snapshots}`;
  const lastNews = streamedNews.at(-1);
  const newsPoll = usePoll(
    generation,
    () =>
      simulationId === null
        ? Promise.reject(new Error("no simulation selected"))
        : api.news(simulationId, NEWS_LIMIT),
    null,
    `${streamedNews.length}|${lastNews?.time_seconds ?? ""}`,
  );
  useEffect(() => {
    if (newsPoll.error !== null) console.error("news for the chart failed", newsPoll.error);
  }, [newsPoll.error]);
  const allNews = newsPoll.data ?? streamedNews;
  const placedNews = useMemo<PlacedNews[]>(() => {
    if (interval === null) return [];
    const placed: PlacedNews[] = [];
    for (const item of allNews) {
      if (item.symbol !== symbol) continue;
      const time = barStartOf(starts, interval, item.time_seconds);
      if (time !== null) placed.push({ time, item });
    }
    return placed;
  }, [allNews, symbol, starts, interval]);

  const trades = market?.trades;
  const placedTrades = useMemo(
    () =>
      interval === null || trades === undefined
        ? 0
        : countPlaced(
            starts,
            interval,
            trades.map((trade) => trade.time_seconds),
          ),
    [starts, interval, trades],
  );

  // ---- The chart: created once, fed by the effects below ---------------------------------
  const container = useRef<HTMLDivElement>(null);
  const parts = useRef<ChartParts | null>(null);
  /** The history key the visible range was last framed for. */
  const fittedKey = useRef<string | null>(null);
  const [hovered, setHovered] = useState<number | null>(null);

  useEffect(() => {
    const element = container.current;
    if (element === null) return;
    const chart = createChart(element, chartOptions());
    const candles = chart.addSeries(CandlestickSeries, {
      upColor: PALETTE.bid,
      downColor: PALETTE.ask,
      wickUpColor: PALETTE.bid,
      wickDownColor: PALETTE.ask,
      borderVisible: false,
      priceLineColor: PALETTE.faint,
      priceLineStyle: LineStyle.Dashed,
    });
    const volume = chart.addSeries(
      HistogramSeries,
      { priceFormat: { type: "volume" }, priceLineVisible: false, lastValueVisible: false },
      1,
    );
    const [pricePane, volumePane] = chart.panes();
    pricePane?.setStretchFactor(4);
    volumePane?.setStretchFactor(1);
    const prints = new TradePrints();
    candles.attachPrimitive(prints);
    const markers = createSeriesMarkers(candles, []);
    const onMove = (param: MouseEventParams<Time>) =>
      setHovered(typeof param.time === "number" ? param.time : null);
    chart.subscribeCrosshairMove(onMove);
    parts.current = { chart, candles, volume, prints, markers, fundamental: null };
    fittedKey.current = null;
    return () => {
      chart.unsubscribeCrosshairMove(onMove);
      parts.current = null;
      chart.remove();
    };
  }, []);

  useEffect(() => {
    const current = parts.current;
    if (current === null) return;
    const rows = bars ?? [];
    current.candles.setData(
      rows.map((bar) => ({
        time: bar.start_seconds as UTCTimestamp,
        open: bar.open,
        high: bar.high,
        low: bar.low,
        close: bar.close,
      })),
    );
    current.volume.setData(
      rows.map((bar) => {
        const time = bar.start_seconds as UTCTimestamp;
        // No volume: a gap rather than a one-pixel bar.
        if (bar.volume === 0) return { time };
        return { time, value: bar.volume, color: bar.close >= bar.open ? VOLUME_UP : VOLUME_DOWN };
      }),
    );
    // Frame the newest bars once per symbol and interval; later refreshes keep the user's zoom.
    if (bars !== null && rows.length > 0 && fittedKey.current !== history.key) {
      fittedKey.current = history.key;
      const span = Math.min(Math.max(rows.length, MIN_FRAMED_BARS), MAX_FRAMED_BARS);
      current.chart
        .timeScale()
        .setVisibleLogicalRange({ from: rows.length - span, to: rows.length - 1 + RIGHT_SLOTS });
    }
  }, [bars, history.key]);

  const tickSize = market?.market.tick_size ?? null;
  useEffect(() => {
    const current = parts.current;
    if (current === null || tickSize === null) return;
    current.candles.applyOptions({
      priceFormat: { type: "price", precision: decimalsOf(tickSize), minMove: tickSize },
    });
  }, [tickSize]);

  const fundamental = market?.market.fundamental_value ?? null;
  useEffect(() => {
    const current = parts.current;
    if (current === null) return;
    if (fundamental === null) {
      if (current.fundamental !== null) current.candles.removePriceLine(current.fundamental);
      current.fundamental = null;
    } else if (current.fundamental === null) {
      current.fundamental = current.candles.createPriceLine({
        price: fundamental,
        color: PALETTE.info,
        lineWidth: 1,
        lineStyle: LineStyle.Dashed,
        axisLabelVisible: true,
        title: "FV",
      });
    } else {
      current.fundamental.applyOptions({ price: fundamental });
    }
  }, [fundamental]);

  useEffect(() => {
    parts.current?.markers.setMarkers(placedNews.map(newsMarker));
  }, [placedNews]);

  useEffect(() => {
    if (interval === null) return;
    parts.current?.prints.setData(trades ?? [], starts, interval);
  }, [trades, starts, interval]);

  // ---- Legend: the bar under the crosshair, else the newest ------------------------------
  const byTime = useMemo(
    () => new Map((bars ?? []).map((bar) => [bar.start_seconds, bar])),
    [bars],
  );
  const legendBar: BarOut | undefined =
    (hovered === null ? undefined : byTime.get(hovered)) ?? bars?.at(-1);
  const legendNews =
    legendBar === undefined
      ? []
      : placedNews.filter((placed) => placed.time === legendBar.start_seconds);
  const decimals = tickSize === null ? 2 : decimalsOf(tickSize);
  const summary = market?.market;

  let overlay: string | null = null;
  let failed = false;
  if (simulationId === null) overlay = "No simulation selected";
  else if (market === undefined) overlay = "Waiting for market data";
  else if (interval === null && simulation.error !== null) {
    overlay = `Could not load the simulation: ${simulation.error.message}`;
    failed = true;
  } else if (history.error !== null && bars === null) {
    overlay = `Could not load history: ${history.error.message}`;
    failed = true;
  } else if (bars !== null && bars.length === 0) overlay = "No closed bars yet";

  return (
    <Panel
      className={className}
      title={
        <div className="flex min-w-0 items-baseline gap-2.5">
          <h2 className="label-caps text-text/80">Chart</h2>
          {summary !== undefined && (
            <>
              <span className="num text-[11.5px] font-medium text-text">{summary.symbol}</span>
              <span className="num text-[11.5px] text-text">
                {fmtNumber(summary.last_price, decimals)}
              </span>
              <span className={`num text-[11px] ${signClass(summary.change)}`}>
                {fmtSigned(summary.change, decimals)} ({fmtPercent(summary.change_percent)})
              </span>
            </>
          )}
        </div>
      }
      actions={
        interval !== null && (
          <Segmented
            options={options}
            value={interval}
            onChange={setChosen}
            label="Chart interval"
            format={intervalLabel}
          />
        )
      }
      bodyClassName="flex flex-col overflow-hidden"
    >
      <div
        className="flex min-h-0 flex-1 flex-col"
        data-testid="chart"
        data-symbol={symbol ?? ""}
        data-interval={interval ?? ""}
        data-bars={bars?.length ?? 0}
        data-news={placedNews.length}
        data-prints={placedTrades}
      >
        <div
          className="num flex h-6 shrink-0 items-center gap-3 overflow-hidden border-b border-line/60 px-2.5 text-[10.5px] whitespace-nowrap text-muted"
          data-testid="chart-legend"
        >
          {legendBar === undefined ? (
            <span>{DASH}</span>
          ) : (
            <>
              <span className="text-faint">{fmtSimTime(legendBar.start_seconds)}</span>
              <LegendValue field="open" label="O" value={fmtNumber(legendBar.open, decimals)} />
              <LegendValue field="high" label="H" value={fmtNumber(legendBar.high, decimals)} />
              <LegendValue field="low" label="L" value={fmtNumber(legendBar.low, decimals)} />
              <LegendValue field="close" label="C" value={fmtNumber(legendBar.close, decimals)} />
              <LegendValue field="volume" label="V" value={fmtInt(legendBar.volume)} />
              <LegendValue field="trades" label="N" value={fmtInt(legendBar.trade_count)} />
            </>
          )}
          {fundamental !== null && (
            <span className="text-info">
              FV <span data-field="fundamental">{fmtNumber(fundamental, decimals + 2)}</span>
            </span>
          )}
          {legendNews.map(({ item }, index) => (
            <span
              key={`${item.time_seconds}-${index}`}
              className="truncate"
              style={{ color: SENTIMENT_COLOR[item.sentiment] }}
              title={item.headline}
            >
              {item.headline}
            </span>
          ))}
          {history.error !== null && bars !== null && (
            <span className="truncate text-ask" title={history.error.message}>
              Refresh failed: {history.error.message}
            </span>
          )}
        </div>
        <div className="relative min-h-0 flex-1">
          <div ref={container} className="absolute inset-0" />
          {overlay !== null && (
            <div
              className={`pointer-events-none absolute inset-0 flex items-center justify-center px-4 text-center ${
                failed ? "text-ask" : "text-muted"
              }`}
            >
              {overlay}
            </div>
          )}
        </div>
      </div>
    </Panel>
  );
}

function LegendValue({ field, label, value }: { field: string; label: string; value: string }) {
  return (
    <span>
      {label}{" "}
      <span className="text-text" data-field={field}>
        {value}
      </span>
    </span>
  );
}
