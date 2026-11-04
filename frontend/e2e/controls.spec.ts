import { expect, test, type Locator, type Page } from "@playwright/test";

import { API_URL } from "../playwright.config";
import { fmtSimTime } from "../src/lib/format";
import type { SimulationOut } from "../src/lib/types";
import { getJson } from "./api";

function simulation(request: Parameters<typeof getJson>[0], id: string) {
  return getJson<SimulationOut>(request, `/api/simulations/${id}`);
}

async function openDialog(page: Page): Promise<Locator> {
  await page.getByTestId("new-simulation").click();
  const dialog = page.getByTestId("new-simulation-dialog");
  await expect(dialog).toBeVisible();
  return dialog;
}

test("a simulation configured in the dialog runs through its whole lifecycle", async ({
  page,
  request,
}) => {
  await page.goto("/");
  const dialog = await openDialog(page);
  const field = (label: string) => dialog.getByLabel(label, { exact: true });
  await field("Seed").fill("3");
  await field("Duration (min)").fill("20");
  await field("Random traders").fill("2");
  await field("Market makers").fill("1");
  await field("Momentum traders").fill("1");
  await field("Fundamental traders").fill("1");
  await field("Pairs traders").fill("1");
  await expect(dialog.getByTestId("agent-total")).toHaveText("6");
  await dialog.getByLabel("Start immediately").uncheck();
  await dialog.getByTestId("sim-create").click();
  await expect(dialog).toBeHidden();

  // The terminal switched to the new simulation, which the server made the active one.
  const created = (await getJson<SimulationOut[]>(request, "/api/simulations")).find(
    (candidate) => candidate.active,
  );
  if (created === undefined) throw new Error("no active simulation after creating one");
  expect(created.seed).toBe(3);
  expect(created.duration_seconds).toBe(1_200);
  expect(created.agents).toEqual({
    random_traders: 2,
    market_makers: 1,
    momentum_traders: 1,
    fundamental_traders: 1,
    pairs_traders: 1,
    order_frequency: 1,
  });
  expect(created.market?.pairs?.map((pair) => [pair.leader, pair.follower])).toEqual([
    ["ACME", "NOVA"],
  ]);
  const id = created.id;
  await expect(page.getByTestId("sim-picker")).toHaveValue(id);
  await expect(page.getByTestId("connection")).toHaveAttribute("data-state", "live");
  await expect(page.getByTestId("sim-state")).toHaveText("created");
  await expect(page.getByTestId("sim-clock")).toHaveText("00:00:00");
  await expect(page.getByTestId("sim-duration")).toHaveText("/ 00:20:00");

  await page.getByTestId("sim-start").click();
  await expect(page.getByTestId("sim-state")).toHaveText("running");
  await expect(page.getByTestId("sim-clock")).not.toHaveText("00:00:00"); // The clock moves.

  await page.getByTestId("sim-pause").click();
  await expect(page.getByTestId("sim-state")).toHaveText("paused");
  const paused = (await simulation(request, id)).time_seconds;
  await page.getByTestId("sim-step").click();
  await expect
    .poll(async () => (await simulation(request, id)).time_seconds)
    .toBeGreaterThan(paused);
  const stepped = (await simulation(request, id)).time_seconds;
  await expect(page.getByTestId("sim-clock")).toHaveText(fmtSimTime(stepped));
  await expect(page.getByTestId("sim-state")).toHaveText("paused");

  await page.getByTestId("sim-resume").click();
  await expect(page.getByTestId("sim-state")).toHaveText("running");
  const tenTimes = page.getByTestId("sim-speed").getByRole("radio", { name: "10×" });
  await tenTimes.click();
  await expect.poll(async () => (await simulation(request, id)).speed).toBe(10);
  await expect(tenTimes).toHaveAttribute("aria-checked", "true");

  await page.getByTestId("sim-stop").click();
  await expect(page.getByTestId("sim-state")).toHaveText("stopped");
  expect((await simulation(request, id)).state).toBe("stopped");
  await page.getByTestId("sim-reset").click();
  await expect(page.getByTestId("sim-state")).toHaveText("created");
  await expect(page.getByTestId("sim-clock")).toHaveText("00:00:00");
  expect((await simulation(request, id)).state).toBe("created");
});

test("an invalid configuration shows the server's error and creates nothing", async ({
  page,
  request,
}) => {
  await page.goto("/");
  const ids = async () =>
    (await getJson<SimulationOut[]>(request, "/api/simulations")).map((item) => item.id);
  const before = await ids();
  const dialog = await openDialog(page);
  const error = dialog.getByTestId("new-simulation-error");

  await dialog.getByLabel("Duration (min)", { exact: true }).fill("0");
  await dialog.getByTestId("sim-create").click();
  await expect(error).toContainText("validation_error");
  await expect(error).toContainText("duration_seconds: Input should be greater than 0");

  // A valid duration, but pairs traders with no pair to trade.
  await dialog.getByLabel("Duration (min)", { exact: true }).fill("5");
  await dialog.getByLabel("ACME/NOVA pair").uncheck();
  await dialog.getByTestId("sim-create").click();
  await expect(error).toContainText("pairs traders need a pair");
  await expect(error).toContainText("invalid_configuration");

  // Text that is not a number never reaches the server.
  await dialog.getByLabel("Speed", { exact: true }).fill("");
  await dialog.getByTestId("sim-create").click();
  await expect(error).toContainText("Speed: enter a number");

  await expect(dialog).toBeVisible();
  await page.keyboard.press("Escape");
  await expect(dialog).toBeHidden();
  expect(await ids()).toEqual(before);
});

test("with no simulation, the terminal offers a quick start", async ({ page, request }) => {
  for (const existing of await getJson<SimulationOut[]>(request, "/api/simulations")) {
    const response = await request.delete(`${API_URL}/api/simulations/${existing.id}`);
    expect(response.status()).toBe(204);
  }
  await page.goto("/");
  const overlay = page.getByTestId("no-simulation");
  await expect(overlay).toBeVisible();

  await overlay.getByTestId("quick-start").click();
  await expect(overlay).toBeHidden();
  await expect(page.getByTestId("sim-state")).toHaveText("running");
  const [started, ...others] = await getJson<SimulationOut[]>(request, "/api/simulations");
  expect(others).toEqual([]);
  expect(started?.state).toBe("running");
  expect(started?.agents).toEqual({
    random_traders: 10,
    market_makers: 5,
    momentum_traders: 5,
    fundamental_traders: 5,
    pairs_traders: 1,
    order_frequency: 1,
  });

  // Deleting it (a second click confirms) leaves the server empty again.
  await page.getByTestId("sim-delete").click();
  await expect(page.getByTestId("sim-delete")).toHaveText("Confirm delete");
  await page.getByTestId("sim-delete").click();
  await expect(overlay).toBeVisible();
  expect(await getJson<SimulationOut[]>(request, "/api/simulations")).toEqual([]);
});
