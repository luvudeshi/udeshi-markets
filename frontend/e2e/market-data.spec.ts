import { expect, test, type Page } from "@playwright/test";

import {
  DASH,
  decimalsOf,
  fmtCompact,
  fmtNumber,
  fmtSigned,
  participantTag,
} from "../src/lib/format";
import type {
  AgentDetailOut,
  AgentOut,
  AnalyticsOut,
  MarketOut,
  SimulationOut,
  TradeOut,
} from "../src/lib/types";
import { getJson, steppedMarket } from "./api";

const SYMBOLS = ["ACME", "NOVA", "QUANT", "ORBIT", "VECTOR"];
const TRADING_AGENTS = 11; // SMALL_MARKET: 4 random, 2 makers, 2 momentum, 2 fundamental, 1 pairs.

function marketRow(page: Page, symbol: string) {
  return page.locator(`[data-testid="market-row"][data-symbol="${symbol}"]`);
}

async function leaderboardIds(page: Page): Promise<number[]> {
  return page
    .getByTestId("leaderboard-row")
    .evaluateAll((rows) => rows.map((row) => Number((row as HTMLElement).dataset.agentId)));
}

test("the overview shows every market as the backend reports it, and selects one", async ({
  page,
  request,
}) => {
  const simulation = await steppedMarket(request);
  await page.goto("/");
  const query = `simulation_id=${simulation.id}`;
  const markets = await getJson<MarketOut[]>(request, `/api/markets?${query}`);
  const analytics = await getJson<AnalyticsOut>(
    request,
    `/api/simulations/${simulation.id}/analytics`,
  );
  const current = await getJson<SimulationOut>(request, `/api/simulations/${simulation.id}`);
  expect(markets.map((market) => market.symbol)).toEqual(SYMBOLS);

  await expect(page.getByTestId("market-row")).toHaveCount(SYMBOLS.length);
  for (const market of markets) {
    const row = marketRow(page, market.symbol);
    await expect(row.getByTestId("market-last")).toHaveText(
      fmtNumber(market.last_price, decimalsOf(market.tick_size)),
    );
    await expect(row.getByTestId("market-volume")).toHaveText(fmtCompact(market.volume));
    const sigma = analytics.market.find((m) => m.symbol === market.symbol)?.volatility_per_hour;
    await expect(row.getByTestId("market-volatility")).toHaveText(
      sigma === null || sigma === undefined ? DASH : `${fmtNumber(sigma * 100, 2)}%`,
    );
  }
  await expect(page.getByTestId("stat-agents")).toHaveText(String(TRADING_AGENTS));
  await expect(page.getByTestId("stat-trades")).toHaveText(fmtCompact(current.summary.trades));
  await expect(page.getByTestId("stat-volume")).toHaveText(fmtCompact(current.summary.volume));

  await expect(marketRow(page, "ACME")).toHaveAttribute("aria-selected", "true");
  await marketRow(page, "NOVA").click();
  await expect(marketRow(page, "NOVA")).toHaveAttribute("aria-selected", "true");
  await expect(marketRow(page, "ACME")).toHaveAttribute("aria-selected", "false");

  // The selected market's quote, including the observer-only fundamental value.
  const nova = markets.find((market) => market.symbol === "NOVA");
  if (nova === undefined) throw new Error("no NOVA market");
  const places = decimalsOf(nova.tick_size);
  const quote = page.getByTestId("market-quote");
  await expect(quote).toContainText("NOVA");
  await expect(quote.getByTestId("quote-bid")).toHaveText(fmtNumber(nova.best_bid, places));
  await expect(quote.getByTestId("quote-ask")).toHaveText(fmtNumber(nova.best_ask, places));
  await expect(quote.getByTestId("quote-spread")).toHaveText(fmtNumber(nova.spread, places));
  await expect(quote.getByTestId("quote-fundamental")).toHaveText(
    fmtNumber(nova.fundamental_value, places + 2),
  );
});

test("the tape names every trade's buyer and seller", async ({ page, request }) => {
  const simulation = await steppedMarket(request);
  await page.goto("/");
  const query = `simulation_id=${simulation.id}`;
  const agents = await getJson<AgentOut[]>(request, `/api/agents?${query}`);
  const byId = new Map(agents.map((agent) => [agent.id, agent]));
  const tag = (id: number | null) => {
    const agent = id === null ? undefined : byId.get(id);
    return agent === undefined ? DASH : participantTag(agent);
  };
  const trades: { symbol: string; trade: TradeOut }[] = [];
  for (const symbol of SYMBOLS) {
    const tape = await getJson<TradeOut[]>(
      request,
      `/api/markets/${symbol}/trades?${query}&limit=100`,
    );
    for (const trade of tape) trades.push({ symbol, trade });
  }
  trades.sort((a, b) => b.trade.trade_id - a.trade.trade_id);
  const expected = trades.slice(0, 100).map(({ symbol, trade }) => ({
    id: trade.trade_id,
    symbol,
    buyer: tag(trade.buyer),
    seller: tag(trade.seller),
  }));
  expect(expected.length).toBeGreaterThan(50);
  expect(expected.every((row) => row.buyer !== DASH && row.seller !== DASH)).toBe(true);

  const rows = page.getByTestId("tape-row");
  await expect(rows).toHaveCount(expected.length);
  const shown = await rows.evaluateAll((elements) =>
    elements.map((element) => {
      const row = element as HTMLElement;
      return {
        id: Number(row.dataset.tradeId),
        symbol: row.dataset.symbol,
        buyer: row.querySelector('[data-col="buyer"]')?.textContent,
        seller: row.querySelector('[data-col="seller"]')?.textContent,
      };
    }),
  );
  expect(shown).toEqual(expected);

  // Only the selected symbol's trades.
  await page.getByRole("radiogroup", { name: "Tape filter" }).getByRole("radio").nth(1).click();
  const acme = trades.filter(({ symbol }) => symbol === "ACME").slice(0, 100);
  await expect(rows).toHaveCount(acme.length);
  const symbols = await rows.evaluateAll((elements) =>
    elements.map((element) => (element as HTMLElement).dataset.symbol),
  );
  expect(new Set(symbols)).toEqual(new Set(["ACME"]));
});

test("the leaderboard sorts by P&L in both directions", async ({ page, request }) => {
  const simulation = await steppedMarket(request);
  await page.goto("/");
  const agents = await getJson<AgentOut[]>(request, `/api/agents?simulation_id=${simulation.id}`);
  const byPnl = (sign: 1 | -1) =>
    [...agents]
      .sort((a, b) => sign * (a.total_pnl - b.total_pnl) || a.id - b.id)
      .map((agent) => agent.id);
  // Some P&L is nonzero, so the order is a real test.
  expect(agents.some((agent) => agent.total_pnl !== 0)).toBe(true);

  await expect(page.getByTestId("leaderboard-row")).toHaveCount(agents.length);
  const header = page.getByRole("columnheader", { name: /P&L/ });
  await expect(header).toHaveAttribute("aria-sort", "descending");
  expect(await leaderboardIds(page)).toEqual(byPnl(-1));
  const top = agents.find((agent) => agent.id === byPnl(-1)[0]);
  await expect(page.getByTestId("leaderboard-pnl").first()).toHaveText(
    fmtSigned(top?.total_pnl, 2),
  );

  await page.getByTestId("sort-pnl").click();
  await expect(header).toHaveAttribute("aria-sort", "ascending");
  expect(await leaderboardIds(page)).toEqual(byPnl(1));

  await page.getByTestId("sort-pnl").click();
  await expect(header).toHaveAttribute("aria-sort", "descending");
  expect(await leaderboardIds(page)).toEqual(byPnl(-1));
});

test("an agent's detail shows its positions as the backend books them", async ({
  page,
  request,
}) => {
  const simulation = await steppedMarket(request);
  await page.goto("/");
  const query = `simulation_id=${simulation.id}`;
  const agents = await getJson<AgentOut[]>(request, `/api/agents?${query}`);
  const trader = agents.find(
    (agent) =>
      agent.strategy !== "environment" &&
      Object.values(agent.positions).some((quantity) => quantity !== 0),
  );
  if (trader === undefined) throw new Error("no agent holds a position");
  const detail = await getJson<AgentDetailOut>(request, `/api/agents/${trader.id}?${query}`);

  await page.locator(`[data-testid="leaderboard-row"][data-agent-id="${trader.id}"]`).click();
  const drawer = page.getByTestId("agent-drawer");
  await expect(drawer).toBeVisible();
  await expect(drawer.getByRole("heading", { name: participantTag(trader) })).toBeVisible();
  await expect(drawer.getByTestId("agent-position")).toHaveCount(detail.position_details.length);
  for (const position of detail.position_details) {
    await expect(
      drawer
        .locator(`[data-testid="agent-position"][data-symbol="${position.symbol}"]`)
        .getByTestId("position-quantity"),
    ).toHaveText(fmtSigned(position.quantity, 0));
  }

  await page.keyboard.press("Escape");
  await expect(drawer).toBeHidden();
});
