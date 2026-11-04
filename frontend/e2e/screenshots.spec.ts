/**
 * Regenerates the documentation's screenshots (docs/images/). Skipped unless SCREENSHOTS=1:
 *   SCREENSHOTS=1 npx playwright test e2e/screenshots.spec.ts
 */
import { expect, test } from "@playwright/test";

import type { ExperimentDetailOut, ExperimentOut } from "../src/lib/types";
import { API_URL } from "../playwright.config";
import { SMALL_MARKET, createSimulation, getJson, lifecycle } from "./api";

test.skip(!process.env.SCREENSHOTS, "set SCREENSHOTS=1 to regenerate the screenshots");

test("terminal", async ({ page, request }) => {
  // Run live (not stepped), so the analytics are sampled every simulated second and the
  // volatility and Sharpe columns have values.
  const simulation = await createSimulation(request, {
    ...SMALL_MARKET,
    speed: 60,
    agents: {
      random_traders: 10,
      market_makers: 5,
      momentum_traders: 5,
      fundamental_traders: 5,
      pairs_traders: 1,
    },
    market: { news_per_hour: 60 },
  });
  await lifecycle(request, simulation.id, "start");
  await page.goto("/");
  await expect(page.getByTestId("connection")).toHaveAttribute("data-state", "live");
  await page.waitForTimeout(15_000); // About 15 simulated minutes.
  await lifecycle(request, simulation.id, "pause");
  await page.waitForTimeout(6_000); // Let the polled panels catch up with the pause.
  await page.screenshot({ path: "../docs/images/terminal.png" });
});

test("experiments", async ({ page, request }) => {
  test.setTimeout(300_000);
  const response = await request.post(`${API_URL}/api/experiments`, {
    data: {
      name: "market makers vs spread",
      runs: 8,
      duration_seconds: 120,
      sweep: { "population.market_makers": [1, 3, 6, 10] },
    },
  });
  expect(response.ok()).toBe(true);
  const { id } = (await response.json()) as ExperimentOut;
  await expect
    .poll(
      async () => (await getJson<ExperimentDetailOut>(request, `/api/experiments/${id}`)).status,
      {
        timeout: 240_000,
      },
    )
    .toBe("completed");
  await page.goto("/experiments");
  await page
    .getByTestId("experiment-row")
    .filter({ hasText: "market makers vs spread" })
    .first()
    .click();
  await page.waitForTimeout(1_000);
  await page.screenshot({ path: "../docs/images/experiments.png" });
});
