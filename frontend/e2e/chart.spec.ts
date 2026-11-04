import { expect, test, type Page } from "@playwright/test";

import { decimalsOf, fmtInt, fmtNumber } from "../src/lib/format";
import type { BarOut, MarketOut, NewsOut, SimulationOut, TradeOut } from "../src/lib/types";
import { getJson, SMALL_MARKET, steppedMarket } from "./api";

function history(simulation: SimulationOut, symbol: string, interval: number) {
  return `/api/markets/${symbol}/history?simulation_id=${simulation.id}&interval_seconds=${interval}&limit=500`;
}

function isHistoryRequest(url: string, symbol: string, interval: number): boolean {
  const parsed = new URL(url);
  return (
    parsed.pathname === `/api/markets/${symbol}/history` &&
    parsed.searchParams.get("interval_seconds") === String(interval)
  );
}

async function expectLegend(page: Page, bar: BarOut, decimals: number) {
  const legend = page.getByTestId("chart-legend");
  await expect(legend.locator('[data-field="open"]')).toHaveText(fmtNumber(bar.open, decimals));
  await expect(legend.locator('[data-field="high"]')).toHaveText(fmtNumber(bar.high, decimals));
  await expect(legend.locator('[data-field="low"]')).toHaveText(fmtNumber(bar.low, decimals));
  await expect(legend.locator('[data-field="close"]')).toHaveText(fmtNumber(bar.close, decimals));
  await expect(legend.locator('[data-field="volume"]')).toHaveText(fmtInt(bar.volume));
}

test("the chart shows the backend's history, at the interval chosen", async ({ page, request }) => {
  const simulation = await steppedMarket(request);
  const symbol = simulation.symbols[0]!;
  const [market] = await getJson<MarketOut[]>(
    request,
    `/api/markets?simulation_id=${simulation.id}`,
  );
  const decimals = decimalsOf(market!.tick_size);
  const oneSecond = await getJson<BarOut[]>(request, history(simulation, symbol, 1));
  expect(oneSecond.length).toBeGreaterThan(10);

  await page.goto("/");
  const chart = page.getByTestId("chart");
  await expect(chart).toHaveAttribute("data-symbol", symbol);
  await expect(chart).toHaveAttribute("data-interval", "1");
  await expect(chart).toHaveAttribute("data-bars", String(oneSecond.length));
  await expect(chart.locator("canvas").first()).toBeVisible();
  await expectLegend(page, oneSecond.at(-1)!, decimals);

  const fiveSecondRequest = page.waitForRequest((r) => isHistoryRequest(r.url(), symbol, 5));
  await page
    .getByRole("radiogroup", { name: "Chart interval" })
    .getByRole("radio", { name: "5s", exact: true })
    .click();
  await fiveSecondRequest;
  const fiveSeconds = await getJson<BarOut[]>(request, history(simulation, symbol, 5));
  await expect(chart).toHaveAttribute("data-interval", "5");
  await expect(chart).toHaveAttribute("data-bars", String(fiveSeconds.length));
  await expectLegend(page, fiveSeconds.at(-1)!, decimals);
});

test("news and executed trades are placed on the bars that contain them", async ({
  page,
  request,
}) => {
  // Frequent news (about one every 10 simulated seconds per symbol), so there is some to show.
  const simulation = await steppedMarket(request, 20_000, {
    ...SMALL_MARKET,
    market: { news_per_hour: 360 },
  });
  const symbol = simulation.symbols[0]!;
  const bars = await getJson<BarOut[]>(request, history(simulation, symbol, 1));
  const first = bars[0]!.start_seconds;
  const last = bars.at(-1)!.end_seconds;
  const news = await getJson<NewsOut[]>(
    request,
    `/api/news?simulation_id=${simulation.id}&limit=1000`,
  );
  // A news item is drawn on the closed bar that contains it.
  const expectedNews = news.filter(
    (item) => item.symbol === symbol && item.time_seconds >= first && item.time_seconds < last,
  );
  expect(expectedNews.length).toBeGreaterThan(0);
  // The stream's snapshot carries the 100 most recent trades; each is drawn from its bar on.
  const trades = await getJson<TradeOut[]>(
    request,
    `/api/markets/${symbol}/trades?simulation_id=${simulation.id}&limit=100`,
  );
  const expectedPrints = trades.filter((trade) => trade.time_seconds >= first).length;
  expect(expectedPrints).toBeGreaterThan(0);

  await page.goto("/");
  const chart = page.getByTestId("chart");
  await expect(chart).toHaveAttribute("data-bars", String(bars.length));
  await expect(chart).toHaveAttribute("data-news", String(expectedNews.length));
  await expect(chart).toHaveAttribute("data-prints", String(expectedPrints));
});
