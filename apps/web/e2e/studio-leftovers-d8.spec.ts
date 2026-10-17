import { expect, test, type Page, type Route } from "@playwright/test";
import { axeLightDarkAt1280And375 } from "./support/studio-fixture";

/**
 * D8b leftovers. Real backend for sign-in and the email-choices read; STUBBED in the browser (page.route) where a state needs
 * data a fresh verification database does not have: webhooks (the server may have no WEBHOOK_SECRET_KEY), an experiments
 * list with a running run's fold and who started each run, and a parent / child comparison with its verdict.
 *
 * Covers: Settings → Notifications (kinds, not-for-your-role, email-off banner, save sends only changes) and Integrations →
 * Webhooks (add, the secret shown once and gone after "hide" and after a reload, make a new secret, test call, delivery log);
 * the operator console's plan / usage / assistant beta columns; Experiments "Started by", "fold k of n", Try a change; the
 * Compare verdict for a run and the run it is based on. Server message text never reaches the page.
 */
const PASSWORD = process.env.DCLAB_E2E_STUDIO_PASSWORD ?? "VerificationOnly123!";
const ADMIN = "business-admin-a@verification.invalid";
const STAFF = "dclab-admin@verification.invalid";
const SERVER = "SERVER WORDS";
const P = "11111111-1111-4111-8111-111111111111";
const ROOT = "22222222-2222-4222-8222-222222222222";
const CHILD = "33333333-3333-4333-8333-333333333333";
const LIVE = "44444444-4444-4444-8444-444444444444";
const PLAN = "55555555-5555-4555-8555-555555555555";
const HOOK = "66666666-6666-4666-8666-666666666666";
const SECRET = "dclab_whsec_AbCdEfGhIjKlMnOpQrStUvWxYz0123456789abcd";
const SECRET2 = "dclab_whsec_ZyXwVuTsRqPoNmLkJiHgFeDcBa9876543210zyxw";

async function login(page: Page, email: string): Promise<void> {
  await page.goto("/login");
  await page.getByLabel("Email").fill(email);
  await page.getByLabel("Password").fill(PASSWORD);
  await page.getByRole("button", { name: "Sign in" }).click();
  await expect(page).not.toHaveURL(/\/login/);
}

const json = (route: Route, body: unknown, status = 200) => route.fulfill({ status, contentType: "application/json", body: JSON.stringify(body) });

test("Settings: Notifications from the real read, save sends only the changed kinds", async ({ page }) => {
  await login(page, ADMIN);
  await page.goto("/settings?tab=notifications");
  await expect(page.getByRole("tab", { name: "Notifications", exact: true })).toHaveAttribute("aria-selected", "true");
  const form = page.getByRole("form", { name: "Email notifications" });
  await expect(form).toBeVisible();
  await expect(form).toContainText("A decision is waiting for a person");
  await expect(form).toContainText("A monitoring alert on the model in use (drift, or real outcomes below a floor)");
  // The verification server has no email service: the page says so in fixed words (never the server's note).
  await expect(page.getByText("Email is turned off on this server, so DCLab sends no email at all.", { exact: false })).toBeVisible();
  await expect(page.locator("main")).not.toContainText("EMAIL_PROVIDER");
  await expect(page.getByRole("button", { name: "No changes to save" })).toBeDisabled();
  const box = form.locator('[data-kind="run_finished"] input[type="checkbox"]');
  const before = await box.isChecked();
  await box.click();
  const put = page.waitForRequest((r) => r.method() === "PUT" && r.url().includes("/v1/me/notification-preferences"));
  await page.getByRole("button", { name: "Save 1 change" }).click();
  const sent = (await put).postDataJSON() as { items: Array<{ kind: string; email_enabled: boolean }> };
  expect(sent.items).toEqual([{ kind: "run_finished", email_enabled: !before }]);
  expect((await put).headers()["idempotency-key"]).toBeTruthy();
  await expect(page.getByText("Saved. Your email choices are updated.")).toBeVisible();
  // Put it back so the spec can run again.
  await box.click();
  await page.getByRole("button", { name: "Save 1 change" }).click();
  await expect(page.getByText("Saved. Your email choices are updated.")).toBeVisible();
});

test("Settings: Integrations → Webhooks, the secret is shown once and never kept", async ({ page }) => {
  await login(page, ADMIN);
  let hooks: unknown[] = [];
  const hook = (over: Record<string, unknown> = {}) => ({
    id: HOOK, workspace_id: P, name: "Ops <b>alerts</b>", url: "https://hooks.example.com/dclab", events: ["decision_needed", "drift_alert"], enabled: true,
    secret_version: 1, created_by_user_id: null, created_at: "2026-10-17T08:00:00Z", updated_at: "2026-10-17T08:00:00Z", secret_rotated_at: null,
    consecutive_failures: 0, paused_until: null, last_delivery_at: null, last_delivery_outcome: null, last_error_code: null, ...over,
  });
  await page.route(/\/api\/backend\/v1\/workspaces\/[^/]+\/webhooks(\?.*)?$/, async (route) => {
    if (route.request().method() === "POST") {
      hooks = [hook()];
      return json(route, { ...hook(), secret: SECRET }, 201);
    }
    return json(route, hooks);
  });
  await page.route(/\/webhooks\/[^/]+\/rotate-secret$/, (route) => json(route, { ...hook({ secret_version: 2 }), secret: SECRET2 }));
  await page.route(/\/webhooks\/[^/]+\/test$/, (route) => json(route, { outbox_id: HOOK, status: "pending", note: SERVER }, 202));
  await page.route(/\/webhooks\/[^/]+\/deliveries(\?.*)?$/, (route) => json(route, [
    { id: "d1", delivery_id: HOOK, event: "ping", attempt: 1, outcome: "failed", status_code: null, error_code: "timeout", duration_ms: 5000, created_at: "2026-10-17T08:05:00Z" },
  ]));
  await page.goto("/settings?tab=integrations");
  await expect(page.getByText("No webhook yet. Add one below.")).toBeVisible();
  const add = page.getByRole("form", { name: "Add a webhook" });
  await add.getByLabel("Name").fill("Ops <b>alerts</b>");
  await add.getByLabel("Address (https)").fill("http://not-https.example.com");
  await add.getByRole("button", { name: "Add webhook" }).click();
  await expect(add).toContainText("Enter an https address");
  await add.getByLabel("Address (https)").fill("https://hooks.example.com/dclab");
  const post = page.waitForRequest((r) => r.method() === "POST" && /\/webhooks$/.test(r.url()));
  await add.getByRole("button", { name: "Add webhook" }).click();
  expect((await post).headers()["idempotency-key"]).toBeTruthy();
  // Shown once, with a copy button; the name is plain text.
  await expect(page.getByTestId("webhook-secret")).toHaveText(SECRET);
  await expect(page.getByText("DCLab shows it only this once", { exact: false })).toBeVisible();
  await expect(page.getByRole("article", { name: "Webhook Ops <b>alerts</b>" })).toBeVisible();
  expect(await page.evaluate(() => JSON.stringify({ ...window.localStorage }) + JSON.stringify({ ...window.sessionStorage }))).not.toContain("dclab_whsec_");
  await page.getByRole("button", { name: "I saved it, hide the secret" }).click();
  await expect(page.getByTestId("webhook-secret")).toHaveCount(0);
  expect(await page.content()).not.toContain(SECRET);
  // A reload never brings it back (the list read carries no secret).
  await page.reload();
  await expect(page.getByRole("article", { name: "Webhook Ops <b>alerts</b>" })).toBeVisible();
  expect(await page.content()).not.toContain("dclab_whsec_");
  // New secret: a confirm step, then shown once again.
  const card = page.getByRole("article", { name: "Webhook Ops <b>alerts</b>" });
  await card.getByRole("button", { name: "Make a new secret" }).click();
  await expect(card).toContainText("The current secret stops working at once.");
  await card.getByRole("button", { name: "Yes, make a new secret" }).click();
  await expect(page.getByTestId("webhook-secret")).toHaveText(SECRET2);
  // Leaving the tab drops it.
  await page.getByRole("tab", { name: "People and roles" }).click();
  await page.getByRole("tab", { name: "Integrations" }).click();
  await expect(page.getByTestId("webhook-secret")).toHaveCount(0);
  // Test call and delivery log in fixed words; the server's note is not shown.
  await card.getByRole("button", { name: "Send a test call" }).click();
  await expect(card).toContainText("A signed test call was queued.");
  await card.getByRole("button", { name: "Show the delivery log" }).click();
  const log = card.getByRole("table", { name: /Delivery log of/ });
  await expect(log).toContainText("Test call");
  await expect(log).toContainText("The address did not answer in time.");
  await expect(page.locator("main")).not.toContainText(SERVER);
  // A refusal is a fixed sentence by code.
  await page.unroute(/\/webhooks\/[^/]+\/test$/);
  await page.route(/\/webhooks\/[^/]+\/test$/, (route) => json(route, { error: { code: "ping_limit_reached", message: SERVER, details: {} } }, 409));
  await card.getByRole("button", { name: "Send a test call" }).click();
  await expect(card).toContainText("Too many test calls in the last hour.");
  await expect(page.locator("main")).not.toContainText(SERVER);
  await page.unrouteAll({ behavior: "ignoreErrors" });
});

test("Settings: Integrations for a person who does not manage the workspace says so", async ({ page }) => {
  await login(page, process.env.DCLAB_E2E_VIEWER_EMAIL ?? "viewer-a@verification.invalid");
  await page.goto("/settings?tab=integrations");
  await expect(page.getByText("Webhooks are managed by the workspace's owners and administrators.", { exact: false })).toBeVisible();
  await expect(page.getByRole("form", { name: "Add a webhook" })).toHaveCount(0);
});

test("Operator console: plan, usage and assistant beta columns from the workspaces read", async ({ page }) => {
  await login(page, STAFF);
  await page.goto("/operator?tab=workspaces");
  const table = page.getByRole("table", { name: "Workspaces, oldest first" });
  await expect(table).toBeVisible();
  for (const header of ["Plan", "Compute, this month", "Storage", "Runs, today", "Assistant beta"]) {
    await expect(table.getByRole("columnheader", { name: header, exact: true })).toBeVisible();
  }
  await expect(table).toContainText(/of (unlimited|[0-9])/);
  await expect(page.getByText("Plan and usage per workspace are not in this read yet", { exact: false })).toHaveCount(0);
});

test("Experiments: started by, the latest run's fold, Try a change, and the Compare verdict against the parent", async ({ page }) => {
  await login(page, ADMIN);
  const item = (id: string, over: Record<string, unknown>) => ({
    id, status: "completed", created_at: "2026-10-16T08:00:00Z", intent: null, project_id: P, parent_experiment_id: null, split_plan_id: PLAN,
    has_change_set: false, ended_at: "2026-10-16T09:00:00Z", evidence_mode: "scored", started_by: null, untrusted_fields: ["intent"], ...over,
  });
  await page.route(/\/api\/backend\/v1\/experiments\?/, (route) => json(route, { items: [
    item(LIVE, { status: "running", created_at: "2026-10-17T08:00:00Z", ended_at: null, started_by: "connected_tool" }),
    item(CHILD, { parent_experiment_id: ROOT, has_change_set: true, created_at: "2026-10-16T10:00:00Z", started_by: "you", intent: "Try class weights" }),
    item(ROOT, { started_by: "assistant" }),
  ], limit: 100, next_cursor: null }));
  await page.route(/\/api\/backend\/v1\/projects(\?.*)?$/, (route) => json(route, [{
    id: P, workspace_id: P, name: "Churn", slug: "churn", description: "", status: "active", updated_at: "2026-10-17T08:00:00Z",
    summary: { running_count: 1, latest_run: { experiment_id: LIVE, status: "running", created_at: "2026-10-17T08:00:00Z", ended_at: null, run_number: 3,
      progress: { stage: "cross_validation", model_family: "catboost", fold_number: 3, folds_planned: 5, candidates_finished: 1, updated_at: "2026-10-17T08:05:00Z", fold_completed: false } } },
  }]));
  // The project frame reads the project itself; the stubbed project exists only in these reads.
  await page.route(new RegExp(`/api/backend/v1/projects/${P}$`), (route) => json(route, {
    id: P, workspace_id: P, name: "Churn", slug: "churn", description: "", status: "active", updated_at: "2026-10-17T08:00:00Z",
  }));
  await page.goto(`/projects/${P}/experiments`);
  const runs = page.getByRole("table", { name: "Runs" });
  await expect(runs.getByTestId("run-progress")).toHaveText("CatBoost: fold 3 of 5 in progress, 2 done");
  await expect(runs.getByRole("cell", { name: "A connected tool", exact: true })).toBeVisible();
  await expect(runs.getByRole("cell", { name: "You", exact: true })).toBeVisible();
  await expect(runs.getByRole("cell", { name: "The assistant (Beta)", exact: true })).toBeVisible();
  // Try a change: choose a finished run, then its change panel opens.
  await page.getByRole("button", { name: "Try a change" }).click();
  const entry = page.getByRole("form", { name: "Try a change" });
  await entry.getByLabel("Start from").selectOption(ROOT);
  await entry.getByRole("button", { name: "Open its change panel" }).click();
  await expect(page).toHaveURL(new RegExp(`/projects/${P}/experiments/${ROOT}#branch$`));

  // Compare the child with its parent: the verdict read against the parent, in fixed words.
  await page.route(/\/api\/backend\/v1\/experiments\/compare\?/, (route) => json(route, {
    split_plan_id: PLAN, common: { cv: ["roc_auc"] },
    experiments: [
      { experiment_id: ROOT, parent_experiment_id: null, family: "xgboost", selection_metric: "roc_auc", selected_score: 0.81, cv: { roc_auc: 0.81 } },
      { experiment_id: CHILD, parent_experiment_id: ROOT, family: "xgboost", selection_metric: "roc_auc", selected_score: 0.83, cv: { roc_auc: 0.83 } },
    ],
  }));
  await page.route(new RegExp(`/api/backend/v1/experiments/${CHILD}/verdict`), (route) => json(route, {
    experiment_id: CHILD, against_experiment_id: ROOT, against_is_parent: true, split_plan_id: PLAN, verdict: "beats_the_noise", reason: null, message: SERVER,
    metric: "roc_auc", direction: "higher_is_better", folds: 5, mean_difference: 0.02, margin: 0.011, comparisons: 1, new_candidates: 0, fold_scores: [],
  }));
  await page.goto(`/projects/${P}/experiments/compare?ids=${ROOT},${CHILD}`);
  const verdict = page.locator(`[data-verdict-for="${CHILD}"]`);
  await expect(verdict).toContainText("Beats the noise");
  await expect(verdict).toContainText("than in the run it is based on.");
  await expect(page.locator("main")).not.toContainText(SERVER);
  await page.unrouteAll({ behavior: "ignoreErrors" });
});

test("Accessibility: Settings Notifications and Integrations, and the operator console, light + dark at 1280 and 375 px", async ({ page }) => {
  await login(page, ADMIN);
  await page.goto("/settings?tab=notifications");
  await expect(page.getByRole("form", { name: "Email notifications" })).toBeVisible();
  await axeLightDarkAt1280And375(page, "Settings Notifications");

  // One stubbed webhook with its delivery log open, so the card and its table are part of the check.
  await page.route(/\/api\/backend\/v1\/workspaces\/[^/]+\/webhooks(\?.*)?$/, (route) => json(route, [{
    id: HOOK, workspace_id: P, name: "Ops alerts", url: "https://hooks.example.com/dclab", events: ["decision_needed", "drift_alert"], enabled: true,
    secret_version: 1, created_by_user_id: null, created_at: "2026-10-17T08:00:00Z", updated_at: "2026-10-17T08:00:00Z", secret_rotated_at: null,
    consecutive_failures: 2, paused_until: null, last_delivery_at: "2026-10-17T08:05:00Z", last_delivery_outcome: "failed", last_error_code: "timeout",
  }]));
  await page.route(/\/webhooks\/[^/]+\/deliveries(\?.*)?$/, (route) => json(route, [
    { id: "d1", delivery_id: HOOK, event: "ping", attempt: 1, outcome: "failed", status_code: null, error_code: "timeout", duration_ms: 5000, created_at: "2026-10-17T08:05:00Z" },
  ]));
  await page.goto("/settings?tab=integrations");
  const card = page.getByRole("article", { name: "Webhook Ops alerts" });
  await expect(card).toBeVisible();
  await card.getByRole("button", { name: "Show the delivery log" }).click();
  await expect(card.getByRole("table", { name: /Delivery log of/ })).toBeVisible();
  await axeLightDarkAt1280And375(page, "Settings Integrations");
  await page.unrouteAll({ behavior: "ignoreErrors" });

  await page.context().clearCookies();
  await login(page, STAFF);
  await page.goto("/operator");
  await expect(page.getByTestId("readiness-sentence")).toBeVisible();
  await axeLightDarkAt1280And375(page, "Operator overview");
  await page.goto("/operator?tab=workspaces");
  await expect(page.getByRole("table", { name: "Workspaces, oldest first" })).toBeVisible();
  await axeLightDarkAt1280And375(page, "Operator workspaces");
});
