/**
 * Regenerates the documentation's screenshots (docs/images/). Skipped unless SCREENSHOTS=1:
 *   SCREENSHOTS=1 npx playwright test e2e/screenshots.spec.ts
 */
import { expect, test } from "@playwright/test";

import { SMALL_MARKET, steppedMarket } from "./api";

test.skip(!process.env.SCREENSHOTS, "set SCREENSHOTS=1 to regenerate the screenshots");

test("terminal", async ({ page, request }) => {
  await steppedMarket(request, 120_000, {
    ...SMALL_MARKET,
    agents: {
      random_traders: 10,
      market_makers: 5,
      momentum_traders: 5,
      fundamental_traders: 5,
      pairs_traders: 1,
    },
    market: { news_per_hour: 60 },
  });
  await page.goto("/");
  await expect(page.getByTestId("connection")).toHaveAttribute("data-state", "live");
  await page.waitForTimeout(1_500); // Let the chart and the polled panels settle.
  await page.screenshot({ path: "../docs/images/terminal.png" });
});
