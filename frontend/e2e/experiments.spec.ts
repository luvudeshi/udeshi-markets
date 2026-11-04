import { expect, test } from "@playwright/test";

import { API_URL } from "../playwright.config";
import { fmtDf, fmtDifference, fmtMetric, fmtP, fmtT } from "../src/components/experiments/format";
import type { ComparisonResultOut, ExperimentDetailOut, ExperimentOut } from "../src/lib/types";
import { getJson } from "./api";

test("a sweep runs in the background, then is summarised, plotted and compared", async ({
  page,
  request,
}) => {
  test.setTimeout(180_000);
  const name = `e2e sweep ${Date.now()}`;
  await page.goto("/experiments");

  const form = page.getByTestId("experiment-form");
  const fill = (label: string, value: string) =>
    form.getByLabel(label, { exact: true }).fill(value);
  await fill("Name", name);
  await fill("Runs", "2");
  await fill("Duration (s)", "20");
  await fill("Random", "2");
  await fill("Market makers", "1");
  await fill("Momentum", "1");
  await fill("Fundamental", "1");
  await fill("Pairs", "0");
  await form.getByRole("button", { name: "+ Add sweep" }).click();
  await expect(form.getByLabel("Sweep setting 1")).toHaveValue("population.market_makers");
  await form.getByLabel("Sweep values 1").fill("1, 3");
  await page.getByTestId("experiment-create").click();

  // The new experiment is listed and selected, and its status follows the backend's.
  const row = page.getByTestId("experiment-row").filter({ hasText: name });
  await expect(row).toHaveCount(1);
  await expect(row).toHaveAttribute("data-status", "completed", { timeout: 150_000 });
  const id = await row.getAttribute("data-id");
  if (id === null) throw new Error("the experiment row has no id");

  const detail = page.getByTestId("experiment-detail");
  await expect(detail).toHaveAttribute("data-id", id);
  await expect(detail.getByTestId("experiment-status")).toHaveText("completed");
  await expect(detail.getByTestId("points-row")).toHaveCount(2);
  await expect(detail.getByTestId("distribution-point")).toHaveCount(2);

  // Everything shown is the backend's summary, formatted.
  const rest = await getJson<ExperimentDetailOut>(request, `/api/experiments/${id}`);
  expect(rest.status).toBe("completed");
  expect(rest.results.map((point) => point.parameters)).toEqual([
    { "population.market_makers": 1 },
    { "population.market_makers": 3 },
  ]);
  const spread = (point: number) => {
    const found = rest.results[point]?.summary.find((s) => s.metric === "spread_bps");
    if (found === undefined) throw new Error(`no spread_bps summary for point ${point}`);
    return found;
  };
  await expect(page.getByTestId("distribution-chart")).toHaveAttribute("data-metric", "spread_bps");
  for (const point of [0, 1]) {
    await expect(
      detail.locator(`[data-testid="distribution-point"][data-point="${point}"]`),
    ).toHaveAttribute("data-count", String(spread(point).count));
  }
  const summaryRow = detail.getByTestId("summary-table").locator('[data-metric="spread_bps"]');
  for (const point of [0, 1]) {
    await detail.getByTestId("points-row").nth(point).click();
    await expect(summaryRow.locator('[data-column="mean"]')).toHaveText(
      fmtMetric(spread(point).mean),
    );
    await expect(summaryRow.locator('[data-column="median"]')).toHaveText(
      fmtMetric(spread(point).median),
    );
    await expect(summaryRow.locator('[data-column="stdev"]')).toHaveText(
      fmtMetric(spread(point).stdev),
    );
  }

  await expect(detail.getByRole("link", { name: "Runs CSV" })).toHaveAttribute(
    "href",
    `${API_URL}/api/experiments/${id}/runs.csv`,
  );
  await expect(detail.getByRole("link", { name: "Summary CSV" })).toHaveAttribute(
    "href",
    `${API_URL}/api/experiments/${id}/summary.csv`,
  );

  // Point 0 against point 1: the backend's Welch tests, row by row.
  const compare = page.getByTestId("compare-panel");
  await compare.getByLabel("Experiment A").selectOption(id);
  await compare.getByLabel("Point A").selectOption("0");
  await compare.getByLabel("Experiment B").selectOption(id);
  await compare.getByLabel("Point B").selectOption("1");
  await compare.getByTestId("compare-run").click();
  const expected = await getJson<ComparisonResultOut>(
    request,
    `/api/experiments/compare?a=${id}&b=${id}&point_a=0&point_b=1`,
  );
  const rows = compare.getByTestId("compare-row");
  await expect(rows).toHaveCount(expected.metrics.length);
  for (const [index, metric] of expected.metrics.entries()) {
    const cells = rows.nth(index);
    await expect(cells).toHaveAttribute("data-metric", metric.metric);
    await expect(cells.locator('[data-column="mean_a"]')).toHaveText(fmtMetric(metric.mean_a));
    await expect(cells.locator('[data-column="mean_b"]')).toHaveText(fmtMetric(metric.mean_b));
    await expect(cells.locator('[data-column="difference"]')).toHaveText(
      fmtDifference(metric.difference),
    );
    await expect(cells.locator('[data-column="t"]')).toHaveText(fmtT(metric.t));
    await expect(cells.locator('[data-column="df"]')).toHaveText(fmtDf(metric.df));
    await expect(cells.locator('[data-column="p"]')).toHaveText(fmtP(metric.p_value));
    await expect(cells).toHaveAttribute(
      "data-significant",
      String(metric.p_value !== null && metric.p_value < 0.05),
    );
  }
  await expect(compare.getByRole("link", { name: "CSV", exact: true })).toHaveAttribute(
    "href",
    `${API_URL}/api/experiments/compare.csv?a=${id}&b=${id}&point_a=0&point_b=1`,
  );
});

test("the backend's validation errors are shown with the form", async ({ page }) => {
  await page.goto("/experiments");
  const form = page.getByTestId("experiment-form");
  await form.getByLabel("Runs", { exact: true }).fill("0");
  await page.getByTestId("experiment-create").click();
  const error = page.getByTestId("experiment-form-error");
  await expect(error).toContainText("validation_error");
  await expect(error).toContainText("runs:");

  // A sweep value that is not a number is caught before anything is sent.
  await form.getByLabel("Runs", { exact: true }).fill("2");
  await form.getByRole("button", { name: "+ Add sweep" }).click();
  await form.getByLabel("Sweep values 1").fill("1, many");
  await page.getByTestId("experiment-create").click();
  await expect(error).toContainText("values must be numbers");
});

test("an experiment is deleted only after confirming", async ({ page, request }) => {
  const created = await request.post(`${API_URL}/api/experiments`, {
    data: { name: `e2e delete ${Date.now()}`, runs: 1, duration_seconds: 5 },
  });
  expect(created.status()).toBe(202);
  const experiment = (await created.json()) as ExperimentOut;

  await page.goto("/experiments");
  const row = page.locator(`[data-testid="experiment-row"][data-id="${experiment.id}"]`);
  await row.click();
  const detail = page.getByTestId("experiment-detail");
  await expect(detail).toHaveAttribute("data-id", experiment.id);
  await detail.getByRole("button", { name: "Delete", exact: true }).click();
  await detail.getByRole("button", { name: "Keep" }).click();
  await expect(row).toHaveCount(1); // Not deleted: the confirmation was declined.

  await detail.getByRole("button", { name: "Delete", exact: true }).click();
  await detail.getByRole("button", { name: "Confirm delete" }).click();
  await expect(row).toHaveCount(0);
  const gone = await request.get(`${API_URL}/api/experiments/${experiment.id}`);
  expect(gone.status()).toBe(404);
});
