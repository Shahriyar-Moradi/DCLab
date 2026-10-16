import { expect, test, type Page } from "@playwright/test";
import { axeViolations, shot } from "./support/studio-fixture";

/**
 * P8.5-UI: the operator console `/operator`. A platform administrator reads the real overview, workspaces and failed jobs of a
 * seeded stack, opens the planned tabs, and sees fixed sentences for stubbed states the seed cannot produce (truncated failure
 * counts, workers not visible, a failing readiness check, a read error, a second page of workspaces with hostile names: those
 * reads are STUBBED with page.route). Platform developers and workspace roles get the middleware's 403. axe light + dark, 375 px.
 */
const PASSWORD = process.env.DCLAB_E2E_STUDIO_PASSWORD ?? "VerificationOnly123!";
const ADMIN = "dclab-admin@verification.invalid";
const BANNED = /\b(release|champion|ref|digest|manifest|package|slot|tick|revision)s?\b/i;
const W1 = "11111111-1111-4111-8111-111111111111";
const W2 = "33333333-3333-4333-8333-333333333333";
const J1 = "22222222-2222-4222-8222-222222222222";
const J2 = "44444444-4444-4444-8444-444444444444";

async function login(page: Page, email: string): Promise<void> {
  await page.context().clearCookies();
  await page.goto("/login");
  await page.getByLabel("Email").fill(email);
  await page.getByLabel("Password").fill(PASSWORD);
  await page.getByRole("button", { name: "Sign in" }).click();
  await expect(page).not.toHaveURL(/\/login/);
}

async function noSideScroll(page: Page): Promise<void> {
  expect(await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth)).toBeLessThanOrEqual(1);
}

/** Our own sentences and labels only: customer workspace names (marked data-untrusted) are removed first, then the codes. */
async function bannedFree(page: Page): Promise<void> {
  const names = (await page.locator("main [data-untrusted]").allInnerTexts()).filter((n) => n.trim() !== "");
  const strip = (s: string) => names.reduce((acc, name) => acc.split(name).join(" "), s);
  const text = strip(await page.locator("main").innerText());
  const labels = (await page.locator("main [aria-label]").evaluateAll((nodes) => nodes.map((n) => n.getAttribute("aria-label") ?? ""))).map(strip);
  // Handler keys and job-type codes are shown as monospace codes, not words; the sentences and labels around them must be clean.
  const words = text.replace(/\b[a-z]+(?:[._][a-z0-9]+)+\b/g, " ");
  for (const s of [words, ...labels]) expect(s).not.toMatch(BANNED);
}

const OVERVIEW = {
  generated_at: "2026-10-16T08:00:00Z",
  workspaces: { total: 12, active_last_7d: 4 },
  jobs_last_24h: [{ job_type: "auto_train", status: "completed", count: 9 }, { job_type: "batch_predict", status: "failed", count: 5001 }],
  queue: { depth: 3, oldest_age_seconds: 754 },
  workers: { dispatcher: "postgres", count: 0, heartbeat_age_seconds: [], visible: false },
  failed_jobs_last_24h: [{ code: "release_mismatch", count: 4000 }, { code: "see_logs", count: 1000 }],
  failed_jobs_last_24h_truncated: true,
  platform_ai_caps: {
    ai_enabled: false, max_data_class: "aggregates", sample_values_per_column: 0, user_text_to_jev: false, auto_release: false,
    model_allowlist: ["gpt-6-luna", "gpt-6.1-sol", "jev-1.13.0"], budgets: { workspace_month_micros: 25000000, alert_fraction: 0.8, hard_stop: true },
  },
  readiness: {
    status: "not_ready",
    checks: [
      { name: "database", status: "ok", required: true, code: null },
      { name: "migrations", status: "failing", required: true, code: "not_at_head" },
      { name: "object_storage", status: "ok", required: true, code: null },
      { name: "worker", status: "unknown", required: false, code: "activity_not_visible" },
    ],
  },
};
const ws = (id: string, name: string) => ({
  id, name, kind: "business", created_at: "2026-10-01T09:00:00Z", member_count: 2, project_count: 1, jobs_last_30d: 3, failed_jobs_last_24h: 0, active_last_7d: true,
});

test.describe.configure({ mode: "serial" });

test("a platform administrator reads the real console of a seeded stack", async ({ page }) => {
  await login(page, ADMIN);
  const response = await page.goto("/operator");
  expect(response?.status()).toBe(200);
  await expect(page.getByRole("heading", { level: 1, name: "Operator console" })).toBeVisible();
  await expect(page.getByText("Working today").first()).toBeVisible();
  await expect(page.getByTestId("operator-generated")).toContainText("UTC");
  await expect(page.getByTestId("readiness-sentence")).toBeVisible();
  await expect(page.getByRole("list", { name: "Readiness checks" }).getByRole("listitem")).toHaveCount(4);
  await expect(page.getByTestId("queue-sentence")).toBeVisible();
  await expect(page.getByTestId("failures-scope")).toHaveText("These counts cover every job that failed in the last 24 hours.");
  await expect(page.getByText(/never shows customer rows/)).toHaveCount(1);
  await expect(page.getByText(/Set in code for the whole platform and read-only here/)).toBeVisible();
  // Read-only: no form control besides tabs, copy buttons, "show more" and reload.
  await expect(page.locator("main input, main select, main textarea")).toHaveCount(0);
  await bannedFree(page);
  expect(await axeViolations(page)).toEqual([]);
  await shot(page, "operator-overview");
  await page.emulateMedia({ colorScheme: "dark" });
  expect(await axeViolations(page)).toEqual([]);
  await shot(page, "operator-overview-dark");
  await page.emulateMedia({ colorScheme: "light" });

  await page.getByRole("tab", { name: "Workspaces" }).click();
  await expect(page).toHaveURL(/\/operator\?tab=workspaces$/);
  const table = page.getByRole("table", { name: "Workspaces, oldest first" });
  await expect(table).toBeVisible();
  expect(await table.getByRole("row").count()).toBeGreaterThan(1);
  await expect(page.getByText("Plan and usage per workspace are not in this read yet", { exact: false })).toBeVisible();
  await expect(table.getByRole("button", { name: /^Copy the id of workspace / }).first()).toBeVisible();
  await bannedFree(page);
  expect(await axeViolations(page)).toEqual([]);
  await shot(page, "operator-workspaces");

  await page.getByRole("tab", { name: "Failed jobs" }).click();
  await expect(page).toHaveURL(/tab=failed-jobs$/);
  await expect(page.getByText(/Failed jobs, newest first, across every workspace/)).toBeVisible();
  await expect(page.getByText(/No job has failed\.|Showing (all|the first)/).first()).toBeVisible();
  expect(await axeViolations(page)).toEqual([]);

  await page.getByRole("tab", { name: /Quarantine/ }).click();
  await expect(page.getByRole("tabpanel")).toContainText("Planned in P8.7");
  await page.getByRole("tab", { name: /Benchmarks & gates/ }).click();
  await expect(page.getByRole("tabpanel")).toContainText("not in the operator read yet");
  await bannedFree(page);

  // A hostile ?tab= opens Overview; the old monitoring page stays and links here.
  await page.goto("/operator?tab=__proto__");
  await expect(page.getByRole("tab", { name: "Overview" })).toHaveAttribute("aria-selected", "true");
  await page.goto("/admin/monitoring");
  await expect(page.getByTestId("operator-console-link").getByRole("link", { name: "operator console" })).toHaveAttribute("href", "/operator");

  await page.setViewportSize({ width: 375, height: 812 });
  await page.goto("/operator");
  await expect(page.getByTestId("readiness-sentence")).toBeVisible();
  await noSideScroll(page);
  await shot(page, "operator-375");
  await page.goto("/operator?tab=workspaces");
  await expect(page.getByRole("table", { name: "Workspaces, oldest first" })).toBeVisible();
  await noSideScroll(page);
});

test("stubbed states: truncated failures, hidden workers, a failing check, a read error, a second page with hostile names", async ({ page }) => {
  await login(page, ADMIN);
  let overview: "ok" | "error" = "ok";
  await page.route("**/api/backend/v1/operator/overview", (route) =>
    overview === "ok" ? route.fulfill({ json: OVERVIEW }) : route.fulfill({ status: 500, json: { error: { code: "internal", message: "SERVER WORDS release", retryable: true } } }));
  // Workspaces: the first "show more" gets a refused cursor (400 invalid_cursor); the list then starts over from page one.
  let firstPages = 0;
  let cursorRefused = false;
  await page.route("**/api/backend/v1/operator/workspaces**", (route) => {
    const cursor = new URL(route.request().url()).searchParams.get("cursor");
    if (!cursor) firstPages += 1;
    if (cursor && !cursorRefused) {
      cursorRefused = true;
      return route.fulfill({ status: 400, json: { error: { code: "invalid_cursor", message: "SERVER WORDS", retryable: false } } });
    }
    return route.fulfill({ json: cursor ? { items: [ws(W2, "Second <img src=x onerror=alert(1)>")], next_cursor: null }
      : { items: [ws(W1, "<script>alert(1)</script> Acme")], next_cursor: "c1" } });
  });
  // Failed jobs: the first "show more" fails with a 500; "Try again" asks for the same page again.
  const failedCursors: string[] = [];
  await page.route("**/api/backend/v1/operator/failed-jobs**", (route) => {
    const cursor = new URL(route.request().url()).searchParams.get("cursor");
    if (cursor) failedCursors.push(cursor);
    if (cursor && failedCursors.length === 1) return route.fulfill({ status: 500, json: { error: { code: "internal", message: "SERVER WORDS", retryable: true } } });
    const job = (id: string) => ({ id, workspace_id: W1, job_type: "batch_predict", handler_key: "models.batch_predict", code: "release_mismatch", attempts: 2, failed_at: "2026-10-12T14:30:00Z" });
    return route.fulfill({ json: cursor ? { items: [job(J2)], next_cursor: null } : { items: [job(J1)], next_cursor: "f1" } });
  });

  await page.goto("/operator");
  await expect(page.getByTestId("failures-sentence")).toHaveText("At least 5,000 jobs failed in the last 24 hours.");
  await expect(page.getByTestId("failures-scope")).toContainText("lower bound");
  await expect(page.getByTestId("workers-sentence")).toContainText("This does not mean no worker runs.");
  await expect(page.getByTestId("readiness-sentence")).toContainText("answers 503");
  await expect(page.getByText("The database schema is not at the latest migration: run the migrations.")).toBeVisible();
  await expect(page.getByTestId("queue-sentence")).toHaveText("3 jobs are waiting in the queue. The oldest has waited 12 min.");
  await expect(page.getByText("The saved model no longer matches the one approved for scoring.")).toBeVisible();
  await bannedFree(page);
  expect(await axeViolations(page)).toEqual([]);
  await shot(page, "operator-stubbed");

  await page.getByRole("tab", { name: "Workspaces" }).click();
  await expect(page.getByText("<script>alert(1)</script> Acme")).toBeVisible();
  await expect(page.getByText("Showing the first 1 workspace. More are available.")).toBeVisible();
  await bannedFree(page);
  await page.getByRole("button", { name: "Show more workspaces" }).click();
  await expect(page.getByText("The next page of workspaces could not be read: the list changed or the page link expired. Start again from the first page.")).toBeVisible();
  await expect(page.getByText(/SERVER WORDS/)).toHaveCount(0);
  const before = firstPages;
  await page.getByRole("button", { name: "Start again from the first page" }).click();
  await expect.poll(() => firstPages).toBe(before + 1);
  await expect(page.getByRole("button", { name: "Start again from the first page" })).toHaveCount(0);
  await expect(page.getByText("Showing the first 1 workspace. More are available.")).toBeVisible();
  await page.getByRole("button", { name: "Show more workspaces" }).click();
  await expect(page.getByText("Second <img src=x onerror=alert(1)>")).toBeVisible();
  await expect(page.getByText("Showing all 2 workspaces.")).toBeVisible();
  await expect(page.locator("main img, main script")).toHaveCount(0);

  await page.getByRole("tab", { name: "Failed jobs" }).click();
  const row = page.getByRole("table", { name: "Failed jobs, newest first" }).getByRole("row").nth(1);
  await expect(row).toContainText("Mon, 12 Oct 2026, 14:30 UTC");
  await expect(row).toContainText("22222222");
  await expect(row.getByRole("button", { name: "Copy the job id 22222222" })).toBeVisible();
  await expect(row.getByRole("button", { name: "Copy the workspace id of job 22222222" })).toBeVisible();
  await expect(page.locator("main a[href*='javascript']")).toHaveCount(0);
  await page.getByRole("button", { name: "Show more failed jobs" }).click();
  await expect(page.getByText("The failed jobs could not be loaded. Nothing was changed.", { exact: false })).toBeVisible();
  await page.getByRole("button", { name: "Try again" }).click();
  await expect(page.getByText("Showing all 2 failed jobs.")).toBeVisible();
  expect(failedCursors).toEqual(["f1", "f1"]);

  overview = "error";
  await page.goto("/operator");
  await expect(page.getByText("The overview could not be loaded. Nothing was changed.")).toBeVisible();
  await expect(page.getByText(/SERVER WORDS/)).toHaveCount(0);
  await expect(page.getByRole("button", { name: "Reload" })).toBeVisible();
});

test("platform developers and workspace roles cannot open the console", async ({ page }) => {
  for (const email of ["dclab-developer@verification.invalid", "business-admin-a@verification.invalid", "viewer-a@verification.invalid", "client-user@verification.invalid"]) {
    await login(page, email);
    const response = await page.goto("/operator");
    expect(response?.status(), email).toBe(403);
    await expect(page.getByRole("heading", { name: "You do not have access to the operator console" })).toBeVisible();
    // The API refuses them too (platform developers since the founder decision of 2026-10-16: platform administrators only).
    for (const path of ["overview", "workspaces", "failed-jobs"]) {
      expect((await page.request.get(`/api/backend/v1/operator/${path}`)).status(), `${email} ${path}`).toBe(403);
    }
  }
  await page.context().clearCookies();
  await page.goto("/operator");
  await expect(page).toHaveURL(/\/login\?next=%2Foperator$/);
});
