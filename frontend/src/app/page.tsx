"use client";

import { OrderBook } from "@/components/book/OrderBook";
import { PriceChart } from "@/components/chart/PriceChart";
import { NoSimulation } from "@/components/controls/NoSimulation";
import { SimulationControls } from "@/components/controls/SimulationControls";
import { Leaderboard } from "@/components/leaderboard/Leaderboard";
import { NewsFeed } from "@/components/news/NewsFeed";
import { MarketOverview } from "@/components/overview/MarketOverview";
import { ConnectionStatus } from "@/components/shell/ConnectionStatus";
import { ExecutionPanel } from "@/components/shell/ExecutionPanel";
import { TerminalStream } from "@/components/shell/TerminalStream";
import { TopBar } from "@/components/shell/TopBar";

// Below the lg breakpoint the panels stack and the page scrolls; from lg up they tile the
// viewport in three columns: markets and news | chart and leaderboard | book and executions.
const STACKED = "max-lg:h-[420px]";

export default function TerminalPage() {
  return (
    <div className="flex h-dvh flex-col">
      <TopBar>
        <SimulationControls />
        <ConnectionStatus />
      </TopBar>
      <TerminalStream />
      <main className="relative grid min-h-0 flex-1 grid-cols-1 gap-px overflow-y-auto bg-line lg:grid-cols-[248px_minmax(0,1fr)_320px] lg:grid-rows-[minmax(0,1fr)_minmax(0,38%)] lg:overflow-hidden 2xl:grid-cols-[288px_minmax(0,1fr)_380px]">
        <MarketOverview className={`${STACKED} lg:col-start-1 lg:row-start-1`} />
        <PriceChart className={`max-lg:h-[460px] lg:col-start-2 lg:row-start-1`} />
        <OrderBook className={`max-lg:h-[520px] lg:col-start-3 lg:row-start-1`} />
        <NewsFeed className={`${STACKED} lg:col-start-1 lg:row-start-2`} />
        <Leaderboard className={`${STACKED} lg:col-start-2 lg:row-start-2`} />
        <ExecutionPanel className={`${STACKED} lg:col-start-3 lg:row-start-2`} />
        <NoSimulation />
      </main>
    </div>
  );
}
