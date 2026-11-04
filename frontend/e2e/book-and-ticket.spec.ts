import { expect, test, type Page } from "@playwright/test";

import { fmtInt, fmtNumber } from "../src/lib/format";
import type { OpenOrderOut, OrderBookOut, TradeOut } from "../src/lib/types";
import { MANUAL_TRADER_ID } from "../src/lib/types";
import { getJson, steppedMarket } from "./api";

// ACME's tick is 0.01: prices show 2 decimals, the mid (half a tick finer) 3.
const PRICE_DECIMALS = 2;
// Enough events for a book on both sides, but not so many that the 600 s run completes (a
// finished simulation refuses orders): this leaves it paused at about 422 s.
const EVENTS = 8_000;

async function openTerminal(page: Page) {
  await page.goto("/");
  await expect(page.getByTestId("connection")).toHaveAttribute("data-state", "live");
}

function bookUrl(simulationId: string) {
  // The stream sends 15 levels per side (UDESHI_STREAM_BOOK_DEPTH).
  return `/api/markets/ACME/orderbook?simulation_id=${simulationId}&depth=15`;
}

test("the order book shows the backend's book, level by level", async ({ page, request }) => {
  const simulation = await steppedMarket(request, EVENTS);
  const book = await getJson<OrderBookOut>(request, bookUrl(simulation.id));
  expect(book.bids.length).toBeGreaterThan(0);
  expect(book.asks.length).toBeGreaterThan(0);

  await openTerminal(page);
  const panel = page.getByTestId("orderbook");
  await expect(panel).toContainText("Order book · ACME");
  await expect(panel.getByTestId("best-bid")).toHaveText(fmtNumber(book.best_bid, PRICE_DECIMALS));
  await expect(panel.getByTestId("best-ask")).toHaveText(fmtNumber(book.best_ask, PRICE_DECIMALS));
  await expect(panel.getByTestId("book-mid")).toHaveText(
    fmtNumber(book.midprice, PRICE_DECIMALS + 1),
  );
  await expect(panel.getByTestId("book-spread")).toHaveText(fmtNumber(book.spread, PRICE_DECIMALS));

  // Every level, in the backend's order: bids best first; asks listed worst first so that the
  // best ask sits next to the spread.
  const bids = panel.getByTestId("book-bid-row");
  const asks = panel.getByTestId("book-ask-row");
  await expect(bids).toHaveCount(book.bids.length);
  await expect(asks).toHaveCount(book.asks.length);
  const bidPrices = await bids.evaluateAll((rows) => rows.map((row) => row.dataset.price));
  const askPrices = await asks.evaluateAll((rows) => rows.map((row) => row.dataset.price));
  expect(bidPrices).toEqual(book.bids.map((level) => String(level.price)));
  expect(askPrices).toEqual(book.asks.map((level) => String(level.price)).reverse());
  const [bestBid] = book.bids;
  await expect(bids.first()).toContainText(fmtInt(bestBid!.quantity));
  await expect(bids.first()).toContainText(fmtInt(bestBid!.cumulative_quantity));
  await expect(panel.getByTestId("depth-chart")).toBeVisible();
});

test("an order entered on the ticket rests, is listed, and can be cancelled", async ({
  page,
  request,
}) => {
  const simulation = await steppedMarket(request, EVENTS);
  await openTerminal(page);
  await page.getByRole("tab", { name: "Order entry" }).click();
  const ticket = page.getByTestId("order-ticket");

  // A bid far below the market: it cannot trade, so it rests.
  await ticket.getByRole("radio", { name: "Buy" }).click();
  await ticket.getByRole("radio", { name: "Limit" }).click();
  await ticket.getByLabel("Price").fill("50.00");
  await ticket.getByLabel("Quantity").fill("7");
  await ticket.getByTestId("ticket-submit").click();
  const result = ticket.getByTestId("ticket-result");
  await expect(result).toHaveAttribute("data-status", "resting");
  await expect(result).toContainText("Resting");

  const rows = ticket.getByTestId("open-order-row");
  await expect(rows).toHaveCount(1);
  const [resting] = await getJson<OpenOrderOut[]>(
    request,
    `/api/markets/ACME/orders?simulation_id=${simulation.id}`,
  );
  expect(resting).toMatchObject({ side: "buy", price: 50, remaining_quantity: 7 });
  await expect(rows.first()).toHaveAttribute("data-order-id", String(resting!.order_id));
  await expect(rows.first()).toContainText("50.00");

  await rows.first().getByRole("button", { name: "Cancel" }).click();
  await expect(rows).toHaveCount(0);
  await expect(ticket.getByTestId("open-orders")).toContainText("No resting orders");
  expect(
    await getJson<OpenOrderOut[]>(
      request,
      `/api/markets/ACME/orders?simulation_id=${simulation.id}`,
    ),
  ).toEqual([]);
});

test("a marketable order shows its fills, and the book follows", async ({ page, request }) => {
  const simulation = await steppedMarket(request, EVENTS);
  await openTerminal(page);
  await page.getByRole("tab", { name: "Order entry" }).click();
  const ticket = page.getByTestId("order-ticket");

  await ticket.getByRole("radio", { name: "Buy" }).click();
  await ticket.getByRole("radio", { name: "Market" }).click();
  await expect(ticket.getByLabel("Price")).toBeDisabled();
  await ticket.getByLabel("Quantity").fill("3");
  await ticket.getByTestId("ticket-submit").click();
  const result = ticket.getByTestId("ticket-result");
  await expect(result).toHaveAttribute("data-status", "filled");

  // The fills shown are the trades the tape records with the API user as buyer.
  const tape = await getJson<TradeOut[]>(
    request,
    `/api/markets/ACME/trades?simulation_id=${simulation.id}&limit=50`,
  );
  const mine = tape.filter((trade) => trade.buyer === MANUAL_TRADER_ID);
  expect(mine.reduce((total, trade) => total + trade.quantity, 0)).toBe(3);
  const fills = result.getByTestId("ticket-fill");
  await expect(fills).toHaveCount(mine.length);
  for (const [index, trade] of mine.entries()) {
    await expect(fills.nth(index)).toHaveAttribute("data-trade-id", String(trade.trade_id));
    await expect(fills.nth(index)).toContainText(
      `${fmtInt(trade.quantity)} @ ${fmtNumber(trade.price, PRICE_DECIMALS)}`,
    );
  }

  // The order was published at once, so the streamed book already shows its effect.
  const book = await getJson<OrderBookOut>(
    request,
    `/api/markets/ACME/orderbook?simulation_id=${simulation.id}`,
  );
  await expect(page.getByTestId("best-ask")).toHaveText(fmtNumber(book.best_ask, PRICE_DECIMALS));
});

test("an order the API rejects shows the API's error", async ({ page, request }) => {
  await steppedMarket(request, EVENTS);
  await openTerminal(page);
  await page.getByRole("tab", { name: "Order entry" }).click();
  const ticket = page.getByTestId("order-ticket");
  await ticket.getByLabel("Price").fill("100.005"); // Not on ACME's 0.01 tick grid.
  await ticket.getByLabel("Quantity").fill("1");
  await ticket.getByTestId("ticket-submit").click();
  const result = ticket.getByTestId("ticket-result");
  await expect(result).toHaveAttribute("data-status", "error");
  await expect(result).toContainText("invalid_price");
});
