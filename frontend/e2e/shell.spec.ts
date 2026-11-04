import { expect, test } from "@playwright/test";

import { steppedMarket } from "./api";

test("the terminal connects to the most recent simulation's stream", async ({ page, request }) => {
  await steppedMarket(request);
  await page.goto("/");
  await expect(page.getByTestId("connection")).toHaveAttribute("data-state", "live");
  await expect(page.getByRole("link", { name: "Terminal" })).toHaveAttribute(
    "aria-current",
    "page",
  );
});

test("the experiments page is one click away", async ({ page }) => {
  await page.goto("/");
  await page.getByRole("link", { name: "Experiments" }).click();
  await expect(page).toHaveURL(/\/experiments$/);
  await expect(page.getByRole("link", { name: "Experiments" })).toHaveAttribute(
    "aria-current",
    "page",
  );
});
