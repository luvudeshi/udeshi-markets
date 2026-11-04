import { defineConfig, devices } from "@playwright/test";

/**
 * End-to-end tests against the real stack: the FastAPI backend (from the repository's uv
 * environment) and a production build of this app. Ports are configurable so several copies
 * can run side by side.
 */
const apiPort = Number(process.env.E2E_API_PORT ?? 8011);
const webPort = Number(process.env.E2E_WEB_PORT ?? 3100);
const ci = Boolean(process.env.CI);

export const API_URL = `http://127.0.0.1:${apiPort}`;

export default defineConfig({
  testDir: "e2e",
  timeout: 90_000,
  expect: { timeout: 20_000 },
  // One backend holds every test's simulations, and the terminal opens the most recent one:
  // tests run one at a time.
  workers: 1,
  fullyParallel: false,
  forbidOnly: ci,
  retries: ci ? 1 : 0,
  reporter: ci ? [["github"], ["html", { open: "never" }]] : [["list"]],
  use: {
    baseURL: `http://127.0.0.1:${webPort}`,
    trace: "retain-on-failure",
    screenshot: "only-on-failure",
  },
  projects: [
    {
      name: "chromium",
      use: { ...devices["Desktop Chrome"], viewport: { width: 1600, height: 960 } },
    },
  ],
  webServer: [
    {
      command: process.env.E2E_API_COMMAND ?? "uv run --no-sync python -m udeshi_api",
      cwd: "..",
      url: `${API_URL}/api/health`,
      env: {
        UDESHI_HOST: "127.0.0.1",
        UDESHI_PORT: String(apiPort),
        UDESHI_CORS_ORIGINS: `http://127.0.0.1:${webPort},http://localhost:${webPort}`,
        UDESHI_MAX_SESSIONS: "64",
        UDESHI_EXPERIMENT_WORKERS: "2",
      },
      reuseExistingServer: !ci,
      timeout: 120_000,
      stdout: "ignore",
      stderr: "pipe",
    },
    {
      command:
        process.env.E2E_WEB_COMMAND ??
        `npm run build && npm run start -- --port ${webPort} --hostname 127.0.0.1`,
      url: `http://127.0.0.1:${webPort}`,
      env: { UDESHI_API_URL: API_URL, NEXT_TELEMETRY_DISABLED: "1" },
      reuseExistingServer: !ci,
      timeout: 300_000,
    },
  ],
});
