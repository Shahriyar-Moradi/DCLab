import { defineConfig, devices } from "@playwright/test";

const databaseUrl =
  process.env.DCLAB_E2E_DATABASE_URL ??
  "postgresql://localhost:55432/dclab_e2e_verify";
const apiUrl = "http://127.0.0.1:8001";
const webUrl = "http://127.0.0.1:3001";

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
    video: "retain-on-failure",
    ...devices["Desktop Chrome"],
  },
  webServer: [
    {
      command:
        "cd ../.. && .venv/bin/uvicorn app.main:app --app-dir apps/api --host 127.0.0.1 --port 8001",
      url: `${apiUrl}/health`,
      timeout: 120_000,
      reuseExistingServer: false,
      env: {
        DATABASE_URL: databaseUrl,
        JWT_SECRET: "e2e-verification-only-secret",
        CORS_ORIGINS: webUrl,
        PIPELINE_LLM_VERIFIER_ENABLED: "false",
        DECISION_AGENT_ENABLED: "false",
      },
    },
    {
      command: "npm run build && npm run start",
      url: `${webUrl}/login`,
      timeout: 180_000,
      reuseExistingServer: false,
      env: {
        NEXT_PUBLIC_API_URL: apiUrl,
        JWT_SECRET: "e2e-verification-only-secret",
      },
    },
  ],
});
