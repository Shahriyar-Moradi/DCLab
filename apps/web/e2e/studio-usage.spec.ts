import { expect, test, type Page } from "@playwright/test";
import path from "node:path";

/** P8.4-UI: /settings -> Usage & cost. Real backend for sign-in and the real read; stubbed reads for the warning/limit/error states. */
const ADMIN = "business-admin-a@verification.invalid";
const VIEWER = process.env.DCLAB_E2E_VIEWER_EMAIL ?? "viewer-a@verification.invalid";
const PASSWORD = "VerificationOnly123!";
const AXE = path.resolve(__dirname, "../node_modules/axe-core/axe.min.js");
const USAGE_URL = /\/api\/backend\/v1\/workspaces\/[0-9a-f-]+\/usage$/;
const DISCLAIMER = "Measured from DCLab's own records: an estimate, not an invoice.";

async function login(page: Page, email: string): Promise<void> {
  await page.goto("/login");
  await page.getByLabel("Email").fill(email);
  await page.getByLabel("Password").fill(PASSWORD);
  await page.getByRole("button", { name: "Sign in" }).click();
  await expect(page).not.toHaveURL(/\/login/);
}

async function axeSeriousOrCritical(page: Page): Promise<string[]> {
  await page.addScriptTag({ path: AXE });
  return page.evaluate(async () => {
    const axe = (window as unknown as { axe: { run: (ctx: Document, opts: object) => Promise<{ violations: Array<{ id: string; impact?: string; nodes: unknown[] }> }> } }).axe;
    const result = await axe.run(document, { runOnly: { type: "tag", values: ["wcag2a", "wcag2aa"] } });
    return result.violations.filter((v) => v.impact === "serious" || v.impact === "critical").map((v) => `${v.id} (${v.nodes.length})`);
  });
}

async function shot(page: Page, name: string): Promise<void> {
  const dir = process.env.DCLAB_E2E_SHOTS;
  if (dir) await page.screenshot({ path: path.join(dir, `${name}.png`), fullPage: true });
}

const quota = (key: string, unit: string, limit: number | null, used: number, period: string, resets: string | null) => ({
  key, unit, limit, used, period, resets_at: resets, enforced: true, source: "plan_default",
  percent: limit ? Math.round((used / limit) * 1000) / 10 : null, over: limit !== null && used >= limit, warning: limit !== null && used >= limit * 0.8 && used < limit,
});
function stub(workspace: string) {
  const days = Array.from({ length: 31 }, (_, i) => ({
    day: new Date(Date.UTC(2026, 9, i < 31 ? i + 1 : 1)).toISOString().slice(0, 10),
    compute_seconds: i % 5 === 0 ? 0 : 600 * (i % 7), storage_bytes: 0, llm_tokens: 0, llm_cost_micros: 0, agent_runs: 0, assistant_runs: 0,
  }));
  return {
    workspace_id: workspace, plan: "beta", metering_enabled: true, quotas_enforced: true, measured_at: "2026-10-16T01:00:00Z",
    month_start: "2026-10-01", resets_at: "2026-11-01T00:00:00Z", notes: ["A server note <b>as text</b>."], disclaimer: DISCLAIMER, totals: [],
    quotas: [
      quota("compute_seconds_month", "seconds", 36000, 30000, "month", "2026-11-01T00:00:00Z"),
      quota("storage_bytes", "bytes", 50 * 1024 ** 3, 60 * 1024 ** 3, "none", null),
      quota("runs_per_day", "runs", null, 4, "day", "2026-10-17T00:00:00Z"),
    ],
    ai_spend: { currency: "USD", hard_stop: true, limit_micros: 10_000_000, over: false, percent: 42.5, period_start: "2026-10-01", reserved_micros: 0, spent_micros: 4_250_000, warning: false },
    daily: days,
  };
}

async function workspaceId(page: Page): Promise<string> {
  return (await (await page.request.get("/api/backend/v1/me")).json()).active_workspace_id as string;
}

test("Usage & cost: the real read for an administrator, then warning, limit and unlimited states from a stubbed read", async ({ page }) => {
  await login(page, ADMIN);
  await page.goto("/settings");
  await page.getByRole("tab", { name: "Usage & cost" }).click();
  await expect(page.getByRole("heading", { name: "Your plan" })).toBeVisible();
  await expect(page.getByText("beta", { exact: true })).toBeVisible();
  await expect(page.getByText(DISCLAIMER).first()).toBeVisible();
  await expect(page.getByText("Compute time this month", { exact: true })).toBeVisible();
  await expect(page.getByText("Stored data", { exact: true })).toBeVisible();
  await expect(page.getByText("Runs today", { exact: true })).toBeVisible();
  await expect(page.getByText(/never blocks reading, approving or rolling back/)).toBeVisible();
  for (const fake of ["Upgrade", "Change plan", "Buy"]) {
    await expect(page.getByRole("button", { name: new RegExp(fake, "i") })).toHaveCount(0);
    await expect(page.getByRole("link", { name: new RegExp(fake, "i") })).toHaveCount(0);
  }
  await expect(page.getByText("Show the numbers as a table")).toBeVisible();
  await expect(page.getByText(/Daily totals (last updated|have not been recorded)/)).toBeVisible();
  await shot(page, "usage-1-real");
  await page.goto("/settings?tab=usage");
  await expect(page.getByRole("tab", { name: "Usage & cost" })).toHaveAttribute("aria-selected", "true");

  const id = await workspaceId(page);
  await page.route(USAGE_URL, (route) => route.fulfill({ json: stub(id) }));
  await page.reload();
  await page.getByRole("tab", { name: "Usage & cost" }).click();
  await expect(page.getByText("Warning: at 80% or more").first()).toBeVisible();
  await expect(page.getByText("Limit reached")).toBeVisible();
  await expect(page.getByText("No limit", { exact: true })).toBeVisible();
  await expect(page.getByText("8 hours 20 min of 10 hours (83.3%)")).toBeVisible();
  await expect(page.getByText("60.0 GB of 50.0 GB (120%)")).toBeVisible();
  await expect(page.getByText("Starts again Nov 1, 2026 (UTC)").first()).toBeVisible();
  await expect(page.getByText("4.25 USD of 10.00 USD (42.5%)")).toBeVisible();
  await expect(page.getByText("A server note <b>as text</b>.")).toBeVisible();
  await page.getByText("Show the numbers as a table").click();
  await expect(page.getByRole("table", { name: /Compute time per day/ }).getByRole("row")).toHaveCount(32);
  await shot(page, "usage-2-states");

  for (const scheme of ["light", "dark"] as const) {
    await page.emulateMedia({ colorScheme: scheme });
    expect(await axeSeriousOrCritical(page), scheme).toEqual([]);
  }
  await shot(page, "usage-3-dark");
  await page.emulateMedia({ colorScheme: "light" });
  await page.setViewportSize({ width: 375, height: 812 });
  await expect(page.getByText("Limit reached")).toBeVisible();
  expect(await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth)).toBeLessThanOrEqual(0);
  await shot(page, "usage-4-mobile");
  await page.setViewportSize({ width: 1280, height: 720 });

  // A failed read is an error, never a zero.
  await page.unroute(USAGE_URL);
  await page.route(USAGE_URL, (route) => route.fulfill({ status: 500, json: { error: { code: "internal_error", message: "boom", request_id: "r", retryable: true } } }));
  await page.reload();
  await page.getByRole("tab", { name: "Usage & cost" }).click();
  await expect(page.getByText(/Could not load usage and limits/)).toBeVisible();
  await expect(page.getByText("0 runs")).toHaveCount(0);
  await shot(page, "usage-5-error");
});

test("Usage & cost: a viewer is told to ask an administrator and the API refuses", async ({ page }) => {
  await login(page, VIEWER);
  await page.goto("/settings");
  await page.getByRole("tab", { name: "Usage & cost" }).click();
  await expect(page.getByText("Ask an admin.")).toBeVisible();
  await expect(page.getByText(/Could not load usage/)).toHaveCount(0);
  await expect(page.getByText("Compute time this month")).toHaveCount(0);
  expect(await axeSeriousOrCritical(page)).toEqual([]);
  await shot(page, "usage-6-viewer");
  const denied = await page.request.get(`/api/backend/v1/workspaces/${await workspaceId(page)}/usage`);
  expect(denied.status()).toBe(403);
  expect(JSON.parse(await denied.text()).error.code).toBe("usage_not_permitted");
});
