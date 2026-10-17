import { defineConfig, devices } from "@playwright/test";

const databaseUrl =
  process.env.DCLAB_E2E_DATABASE_URL ??
  "postgresql://localhost:55432/dclab_e2e_verify";
const apiUrl = process.env.DCLAB_E2E_API_URL ?? "http://127.0.0.1:8001";
const webUrl = process.env.DCLAB_E2E_WEB_URL ?? "http://127.0.0.1:3001";
const apiPort = new URL(apiUrl).port || "8001";
const webPort = new URL(webUrl).port || "3001";

export default defineConfig({
  testDir: "./e2e",
  fullyParallel: false,
  workers: 1,
  timeout: 180_000,
  expect: { timeout: 20_000 },
  outputDir: "../../artifacts/e2e-verification/playwright",
  reporter: [
    ["list"],
    [
      "html",
      {
        outputFolder: "../../artifacts/e2e-verification/playwright-report",
        open: "never",
      },
    ],
  ],
  use: {
    baseURL: webUrl,
    trace: "retain-on-failure",
    screenshot: "only-on-failure",
    video: process.env.DCLAB_E2E_BROWSER_CHANNEL === "chrome" ? "off" : "retain-on-failure",
    ...devices["Desktop Chrome"],
    channel: process.env.DCLAB_E2E_BROWSER_CHANNEL === "chrome" ? "chrome" : undefined,
  },
  webServer: [
    {
      // The API plus two `dclab worker run` (same process group, so all stop together): training,
      // scoring and weekly schedules run in workers as in production; an API process never loads a
      // stored model (P7.1-A), and weekly scoring reports "paused" without the postgres dispatcher.
      // Two workers keep a spec's runs from queueing behind jobs an earlier spec left running.
      command:
        `cd ../.. && { .venv/bin/dclab worker run & .venv/bin/dclab worker run & } && exec .venv/bin/uvicorn app.main:app --app-dir apps/api --host 127.0.0.1 --port ${apiPort}`,
      url: `${apiUrl}/health`,
      timeout: 120_000,
      reuseExistingServer: false,
      env: {
        DATABASE_URL: databaseUrl,
        JWT_SECRET: "e2e-verification-only-secret",
        CORS_ORIGINS: webUrl,
        AI_ENABLED: "false",
        // The existing specs cover the frozen Decision.ai pages (hidden by default since P4.1-A).
        LEGACY_DECISION_LAYER_ENABLED: "true",
        ML_JOB_DISPATCHER: "postgres",
      },
    },
    {
      command: `npm run build && ./node_modules/.bin/next start -p ${webPort}`,
      url: `${webUrl}/login`,
      // Includes the production build (about 2-3 minutes on a busy 4-core machine).
      timeout: 420_000,
      reuseExistingServer: false,
      env: {
        NEXT_PUBLIC_API_URL: apiUrl,
        DCLAB_API_URL: apiUrl,
      },
    },
  ],
});
