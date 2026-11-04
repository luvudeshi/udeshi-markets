"use client";

import { useMemo, type KeyboardEvent } from "react";

import { Empty, Panel } from "@/components/ui/Panel";
import { ApiError } from "@/lib/api";
import { useApi } from "@/lib/api-context";
import {
  DASH,
  decimalsOf,
  fmtCompact,
  fmtInt,
  fmtNumber,
  fmtPercent,
  signClass,
} from "@/lib/format";
import { usePoll } from "@/lib/hooks";
import { useTerminal } from "@/lib/stream/store";
import type { AnalyticsOut, MarketOut } from "@/lib/types";

// The analytics are recomputed over the whole run on every request (about 35 ms at 30
// simulated minutes, growing with the run), so they are polled less often than the rest.
const ANALYTICS_MS = 5_000;
const SUMMARY_MS = 1_500;

const TH =
  "sticky top-0 z-10 h-6 bg-panel px-1.5 label-caps shadow-[inset_0_-1px_0_var(--color-line)]";

/**
 * The watchlist: every instrument's last price, change, volume and realized volatility, as
 * the backend computes them. Selecting a row points the chart, the book and the ticket at it.
 */
export function MarketOverview({ className = "" }: { className?: string }) {
  const api = useApi();
  const simulationId = useTerminal((state) => state.simulationId);
  const symbols = useTerminal((state) => state.symbols);
  const markets = useTerminal((state) => state.markets);
  const agents = useTerminal((state) => state.agents);
  const selected = useTerminal((state) => state.selectedSymbol);
  const selectSymbol = useTerminal((state) => state.selectSymbol);
  const snapshots = useTerminal((state) => state.snapshots);
  // While running, poll on a timer; otherwise fetch again only when the clock moves (a step).
  const moment = useTerminal((state) =>
    state.status?.state === "running" ? "live" : String(state.status?.time_seconds ?? 0),
  );
  const running = moment === "live";
  const refresh = simulationId === null ? null : `${simulationId}:${snapshots}:${moment}`;
  // Analytics exist only for markets with an environment (it is listed as a participant).
  const hasMarket = agents.some((agent) => agent.strategy === "environment");
  const analytics = usePoll<AnalyticsOut>(
    hasMarket ? refresh : null,
    () => api.analytics(simulationId ?? ""),
    running ? ANALYTICS_MS : null,
  );
  const simulation = usePoll(
    refresh,
    () => api.getSimulation(simulationId ?? ""),
    running ? SUMMARY_MS : null,
  );

  const volatility = useMemo(() => {
    const bySymbol = new Map<string, number | null>();
    for (const metrics of analytics.data?.market ?? []) {
      bySymbol.set(metrics.symbol, metrics.volatility_per_hour);
    }
    return bySymbol;
  }, [analytics.data]);
  // A market without an environment has no analytics (409 no_analytics): shown as dashes.
  const analyticsFailure =
    analytics.error !== null &&
    !(analytics.error instanceof ApiError && analytics.error.code === "no_analytics")
      ? analytics.error.message
      : null;

  const activeAgents = agents.filter(
    (agent) => agent.strategy !== "manual" && agent.strategy !== "environment",
  ).length;
  const summary = simulation.data?.summary;

  const onKey = (event: KeyboardEvent, symbol: string) => {
    if (event.key === "Enter" || event.key === " ") {
      event.preventDefault();
      selectSymbol(symbol);
    }
  };

  return (
    <Panel title="Markets" className={className} testId="markets" bodyClassName="flex flex-col">
      <div className="min-h-0 flex-1 overflow-auto">
        {symbols.length === 0 ? (
          <Empty>No market data yet</Empty>
        ) : (
          <table className="w-full border-collapse text-[11px]" aria-label="Markets">
            <thead>
              <tr>
                <th scope="col" className={`${TH} pl-2.5 text-left`}>
                  Symbol
                </th>
                <th scope="col" className={`${TH} text-right`}>
                  Last
                </th>
                <th
                  scope="col"
                  className={`${TH} text-right`}
                  title="Change from the reference price"
                >
                  Chg
                </th>
                <th scope="col" className={`${TH} text-right`} title="Shares traded">
                  Vol
                </th>
                <th
                  scope="col"
                  className={`${TH} pr-2.5 text-right`}
                  title="Realized volatility of the midprice, per hour"
                >
                  <span className="normal-case">σ</span>/h
                </th>
              </tr>
            </thead>
            <tbody>
              {symbols.map((symbol) => {
                const market = markets[symbol]?.market;
                if (market === undefined) return null;
                const isSelected = symbol === selected;
                const sigma = volatility.get(symbol);
                return (
                  <tr
                    key={symbol}
                    data-testid="market-row"
                    data-symbol={symbol}
                    aria-selected={isSelected}
                    tabIndex={0}
                    onClick={() => selectSymbol(symbol)}
                    onKeyDown={(event) => onKey(event, symbol)}
                    className={`h-[22px] cursor-pointer border-b border-line/60 transition-colors ${
                      isSelected
                        ? "bg-raised shadow-[inset_2px_0_0_var(--color-accent)]"
                        : "hover:bg-hover"
                    }`}
                  >
                    <td
                      className={`pl-2.5 pr-1.5 font-medium tracking-wide ${
                        isSelected ? "text-text" : "text-text/85"
                      }`}
                    >
                      {symbol}
                    </td>
                    <td className="num px-1.5 text-right text-text" data-testid="market-last">
                      {fmtNumber(market.last_price, decimalsOf(market.tick_size))}
                    </td>
                    <td
                      className={`num px-1.5 text-right ${signClass(market.change_percent)}`}
                      data-testid="market-change"
                    >
                      {fmtPercent(market.change_percent)}
                    </td>
                    <td className="num px-1.5 text-right text-muted" data-testid="market-volume">
                      {fmtCompact(market.volume)}
                    </td>
                    <td
                      className="num pr-2.5 pl-1.5 text-right text-muted"
                      data-testid="market-volatility"
                    >
                      {sigma === undefined || sigma === null
                        ? DASH
                        : `${fmtNumber(sigma * 100, 2)}%`}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        )}
        {selected !== null && markets[selected] !== undefined && (
          <Quote market={markets[selected].market} />
        )}
      </div>
      <footer
        className="grid shrink-0 grid-cols-3 border-t border-line text-[11px]"
        aria-label="Market totals"
      >
        <Stat label="Agents" value={fmtInt(activeAgents)} testId="stat-agents" />
        <Stat label="Trades" value={fmtCompact(summary?.trades)} testId="stat-trades" />
        <Stat label="Volume" value={fmtCompact(summary?.volume)} testId="stat-volume" />
      </footer>
      {analyticsFailure !== null && (
        <p
          className="shrink-0 border-t border-line px-2.5 py-1 text-[10.5px] text-ask"
          role="status"
        >
          Analytics unavailable: {analyticsFailure}
        </p>
      )}
    </Panel>
  );
}

/** The selected market's quote and session, as the backend reports them. */
function Quote({ market }: { market: MarketOut }) {
  const places = decimalsOf(market.tick_size);
  // A midprice is at most half a tick off the grid: one more decimal shows it exactly.
  const fields: [label: string, value: string, testId: string, className?: string][] = [
    ["Bid", fmtNumber(market.best_bid, places), "quote-bid", "text-bid"],
    ["Ask", fmtNumber(market.best_ask, places), "quote-ask", "text-ask"],
    ["Mid", fmtNumber(market.midprice, places + 1), "quote-mid"],
    ["Spread", fmtNumber(market.spread, places), "quote-spread"],
    ["Open", fmtNumber(market.open, places), "quote-open"],
    ["High", fmtNumber(market.high, places), "quote-high"],
    ["Low", fmtNumber(market.low, places), "quote-low"],
    ["VWAP", fmtNumber(market.vwap, places + 2), "quote-vwap"],
    ["Reference", fmtNumber(market.reference_price, places), "quote-reference"],
    ["Fundamental", fmtNumber(market.fundamental_value, places + 2), "quote-fundamental"],
  ];
  return (
    <section
      className="border-t border-line"
      aria-label={`${market.symbol} quote`}
      data-testid="market-quote"
    >
      <h3 className="label-caps px-2.5 pt-2 pb-1">
        {market.symbol} <span className="text-faint">· quote and session</span>
      </h3>
      <dl className="px-2.5 pb-2 text-[11px]">
        {fields.map(([label, value, testId, className]) => (
          <div
            key={label}
            className="flex h-5 items-baseline justify-between gap-2 border-b border-line/40"
            title={
              label === "Fundamental"
                ? "The hidden fundamental value: shown to observers, never to agents"
                : undefined
            }
          >
            <dt className="text-muted">{label}</dt>
            <dd className={`num ${className ?? "text-text"}`} data-testid={testId}>
              {value}
            </dd>
          </div>
        ))}
      </dl>
    </section>
  );
}

function Stat({ label, value, testId }: { label: string; value: string; testId: string }) {
  return (
    <div className="flex flex-col gap-0.5 px-2.5 py-1.5 not-first:border-l not-first:border-line">
      <span className="label-caps">{label}</span>
      <span className="num text-[12px] text-text" data-testid={testId}>
        {value}
      </span>
    </div>
  );
}
