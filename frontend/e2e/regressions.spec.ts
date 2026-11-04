/**
 * Regressions found by the Phase 5 review, each reproduced first:
 * - panels that fetch over REST must follow what happens while the clock is stopped (a step,
 *   a manual trade);
 * - a late answer must never replace a newer one;
 * - a failed request is retried and reported, never silently shown as a dash;
 * - the connection status recovers when the API comes back.
 */
import { expect, test, type Page } from "@playwright/test";

import { fmtCompact, fmtPercent } from "../src/lib/format";
import type { NewsOut, OpenOrderOut, OrderBookOut, SimulationOut } from "../src/lib/types";
import { API_URL } from "../playwright.config";
import { getJson, SMALL_MARKET, step, steppedMarket } from "./api";

async function openTerminal(page: Page) {
  await page.goto("/");
  await expect(page.getByTestId("connection")).toHaveAttribute("data-state", "live");
}

test("open orders follow a Step taken while paused", async ({ page, request }) => {
  const simulation = await steppedMarket(request, 8_000);
  const ordersUrl = `/api/markets/ACME/orders?simulation_id=${simulation.id}`;
  const book = await getJson<OrderBookOut>(
    request,
    `/api/markets/ACME/orderbook?simulation_id=${simulation.id}&depth=1`,
  );
  await openTerminal(page);
  await page.getByRole("tab", { name: "Order entry" }).click();
  const ticket = page.getByTestId("order-ticket");
  await ticket.getByRole("radio", { name: "Buy" }).click();
  await ticket.getByRole("radio", { name: "Limit" }).click();
  // Join the best bid: it rests now, and incoming sells will hit it as the clock moves.
  await ticket.getByLabel("Price").fill(String(book.best_bid));
  await ticket.getByLabel("Quantity").fill("200");
  await ticket.getByTestId("ticket-submit").click();
  const rows = ticket.getByTestId("open-order-row");
  await expect(rows).toHaveCount(1);
  await expect(rows.first()).toContainText("200 / 200");

  // Step with the toolbar's Step button until the backend says the order traded.
  let backend: OpenOrderOut[] = [];
  for (let attempt = 0; attempt < 30; attempt += 1) {
    const before = (await page.getByTestId("sim-clock").textContent()) ?? "";
    await page.getByTestId("sim-step").click();
    // The terminal has seen the clock move (the stream's simulation update arrived).
    await expect(page.getByTestId("sim-clock")).not.toHaveText(before);
    backend = await getJson<OpenOrderOut[]>(request, ordersUrl);
    if (backend.length === 0 || backend[0]!.remaining_quantity < 200) break;
  }
  expect(backend.length === 0 || backend[0]!.remaining_quantity < 200).toBe(true);

  // The panel should now show what the backend holds.
  await page.waitForTimeout(3_000);
  if (backend.length === 0) {
    await expect(rows).toHaveCount(0);
  } else {
    const left = backend[0]!.remaining_quantity;
    await expect(rows.first()).toContainText(`${left} / 200`);
  }
});

test("a stale news answer does not replace a newer one on the chart", async ({ page, request }) => {
  const simulation = await steppedMarket(request, 20_000, {
    ...SMALL_MARKET,
    market: { news_per_hour: 300 },
  });

  // Hold the chart's first news request (its answer is taken now, delivered later).
  let requests = 0;
  let release: () => void = () => {};
  const released = new Promise<void>((resolve) => (release = resolve));
  let firstHeld: () => void = () => {};
  const held = new Promise<void>((resolve) => (firstHeld = resolve));
  await page.route(/\/api\/news\?/, async (route) => {
    requests += 1;
    if (requests === 1) {
      const response = await route.fetch();
      firstHeld();
      await released;
      await route.fulfill({ response });
    } else {
      await route.continue();
    }
  });

  await openTerminal(page);
  await held;
  const chart = page.getByTestId("chart");
  await expect(chart).toHaveAttribute("data-symbol", "ACME");

  // News arrives on the stream; the chart fetches the full list again, with the new items.
  await step(request, simulation.id, 2_000);
  await expect.poll(() => requests).toBeGreaterThan(1);
  await page.waitForTimeout(3_000);
  const newer = {
    news: await chart.getAttribute("data-news"),
    bars: await chart.getAttribute("data-bars"),
  };

  // Now the first (older) answer arrives. It belongs to a key that is no longer current.
  release();
  await page.waitForTimeout(3_000);
  const after = {
    news: await chart.getAttribute("data-news"),
    bars: await chart.getAttribute("data-bars"),
  };
  expect(after).toEqual(newer);
});

test("one failed simulation request at load does not break the chart for good", async ({
  page,
  request,
}) => {
  const simulation = await steppedMarket(request, 8_000);
  const path = `/api/simulations/${simulation.id}`;
  let failing = true;
  let failed = 0;
  await page.route(
    (url) => url.pathname === path,
    async (route) => {
      if (failing && route.request().method() === "GET") {
        failed += 1;
        await route.fulfill({
          status: 503,
          contentType: "application/json",
          body: JSON.stringify({ error: { code: "unavailable", message: "try again" } }),
        });
      } else {
        await route.continue();
      }
    },
  );
  const appErrors: string[] = [];
  page.on("console", (message) => {
    // The browser's own network line is not the app reporting anything.
    if (message.type() === "error" && !message.text().startsWith("Failed to load resource")) {
      appErrors.push(message.text());
    }
  });

  await openTerminal(page);
  // The chart, the controls and the market totals each asked once, and each got a 503.
  await expect.poll(() => failed).toBeGreaterThanOrEqual(3);
  failing = false; // From now on the API answers normally.
  await page.waitForTimeout(2_000);
  // The clock moves too (a step), so every panel has had a reason to refresh.
  await step(request, simulation.id, 1_000);
  await page.waitForTimeout(5_000);

  const chart = page.getByTestId("chart");
  await expect(page.getByTestId("connection")).toHaveAttribute("data-state", "live");
  await expect(chart).not.toContainText("Could not load the simulation");
  await expect(chart).not.toHaveAttribute("data-interval", "");
});

test("the market totals follow a manual trade made while paused", async ({ page, request }) => {
  const simulation = await steppedMarket(request, 8_000);
  const summaryOf = async () =>
    (await getJson<SimulationOut>(request, `/api/simulations/${simulation.id}`)).summary;
  await openTerminal(page);
  const before = await summaryOf();
  await expect(page.getByTestId("stat-trades")).toHaveText(fmtCompact(before.trades));

  // A market buy on the ticket: it trades at once, at the current (paused) simulated time.
  await page.getByRole("tab", { name: "Order entry" }).click();
  const ticket = page.getByTestId("order-ticket");
  await ticket.getByRole("radio", { name: "Buy" }).click();
  await ticket.getByRole("radio", { name: "Market" }).click();
  await ticket.getByLabel("Quantity").fill("500");
  await ticket.getByTestId("ticket-submit").click();
  await expect(ticket.getByTestId("ticket-result")).toBeVisible();

  const after = await summaryOf();
  expect(after.trades).toBeGreaterThan(before.trades);
  await page.waitForTimeout(4_000);
  await expect(page.getByTestId("stat-trades")).toHaveText(fmtCompact(after.trades));
  await expect(page.getByTestId("stat-volume")).toHaveText(fmtCompact(after.volume));
});

test("failed requests are reported, not shown as dashes", async ({ page, request }) => {
  const simulation = await steppedMarket(request, 8_000);
  const MESSAGE = "backend overloaded";
  let failList = false;
  const answered: Record<string, number> = {};
  await page.route(
    (url) =>
      url.pathname === `/api/simulations/${simulation.id}` || url.pathname === "/api/simulations",
    async (route) => {
      const url = new URL(route.request().url());
      const fail =
        route.request().method() === "GET" && (url.pathname !== "/api/simulations" || failList);
      if (!fail) return route.continue();
      answered[url.pathname] = (answered[url.pathname] ?? 0) + 1;
      return route.fulfill({
        status: 503,
        contentType: "application/json",
        body: JSON.stringify({ error: { code: "unavailable", message: MESSAGE } }),
      });
    },
  );
  const appErrors: string[] = [];
  page.on("console", (message) => {
    if (message.type() === "error" && !message.text().startsWith("Failed to load resource")) {
      appErrors.push(message.text());
    }
  });
  await openTerminal(page);
  // The picker fetches its list again when focused: make that fail too.
  failList = true;
  await page.getByTestId("sim-picker").focus();
  await expect.poll(() => answered["/api/simulations"] ?? 0).toBeGreaterThan(0);
  await page.waitForTimeout(2_000);

  const reported = await page.getByText(MESSAGE).allTextContents();
  // Four requests failed (chart, controls, market totals, picker); each should say so.
  expect(reported.length + appErrors.length).toBeGreaterThanOrEqual(4);
});

test("the connection status recovers when the API comes back", async ({ page, request }) => {
  // An empty exchange (this test backend only holds this spec's simulations).
  for (const simulation of await getJson<SimulationOut[]>(request, "/api/simulations")) {
    const response = await request.delete(`${API_URL}/api/simulations/${simulation.id}`);
    expect(response.status()).toBe(204);
  }
  // The API is down when the page loads, then up again.
  let down = true;
  await page.route(
    (url) => url.pathname === "/api/simulations",
    async (route) => {
      if (down) await route.abort("connectionrefused");
      else await route.continue();
    },
  );
  await page.goto("/");
  await expect(page.getByTestId("no-simulation")).toContainText("Cannot reach the API");
  await expect(page.getByTestId("connection")).toHaveAttribute("data-state", "closed");
  down = false;
  // NoSimulation polls every 3 s; its next answer is a real, empty list.
  await expect(page.getByTestId("no-simulation")).toContainText("The exchange is idle");
  await page.waitForTimeout(4_000);
  const status = page.getByTestId("connection");
  await expect(status).not.toHaveAttribute("data-state", "closed");
});

test("news shows the backend's percent change of the fundamental, not the log impact", async ({
  page,
  request,
}) => {
  const simulation = await steppedMarket(request, 20_000, {
    ...SMALL_MARKET,
    market: { news_per_hour: 300, news_impact: 0.3 }, // Large: log and percent differ a lot.
  });
  await openTerminal(page);
  const news = await getJson<NewsOut[]>(
    request,
    `/api/news?simulation_id=${simulation.id}&limit=1000`,
  );
  const newest = news.at(-1)!;
  expect(Math.abs(newest.change_percent - newest.impact * 100)).toBeGreaterThan(0.5);
  await expect(page.getByTestId("news-item").first()).toContainText(
    fmtPercent(newest.change_percent),
  );
});

test("sweep points and metrics can be chosen from the keyboard", async ({ page, request }) => {
  const response = await request.post(`${API_URL}/api/experiments`, {
    data: {
      name: "keyboard",
      runs: 2,
      duration_seconds: 10,
      sweep: { "population.market_makers": [1, 3] },
    },
  });
  expect(response.ok()).toBe(true);
  const { id } = (await response.json()) as { id: string };
  await expect
    .poll(async () => (await getJson<{ status: string }>(request, `/api/experiments/${id}`)).status)
    .toBe("completed");
  await page.goto("/experiments");
  await page.getByTestId("experiment-row").filter({ hasText: "keyboard" }).first().click();
  const second = page.locator('[data-testid="points-row"][data-point="1"]');
  await second.focus();
  await page.keyboard.press("Enter");
  await expect(second).toHaveAttribute("aria-selected", "true");
  const row = page.getByTestId("summary-row").nth(1);
  const metric = await row.getAttribute("data-metric");
  await row.focus();
  await page.keyboard.press(" ");
  await expect(
    page.locator(`[data-testid="summary-row"][data-metric="${metric}"]`),
  ).toHaveAttribute("aria-selected", "true");
});
