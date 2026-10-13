import { expect, test, type Page } from "@playwright/test";
import { axeViolations, login, shot, trainedProject } from "./support/studio-fixture";

/**
 * V7-B2b-UI: the "Weekly scoring" card on the Predictions screen. Turn on (one confirm step that says what will and will not
 * happen), see the next run and the no-approved-model warning, change, turn off; a viewer sees no buttons; refusals are plain
 * sentences (stubbed); paused-by-platform and every run outcome read in plain words (stubbed read). Needs an API with a worker.
 */
const BANNED = /\b(slot|tick|release|ref|digest|champion|worker|idempotency)s?\b/i;
const VIEWER = process.env.DCLAB_E2E_VIEWER_EMAIL ?? "viewer-a@verification.invalid";
const PASSWORD = process.env.DCLAB_E2E_STUDIO_PASSWORD ?? "VerificationOnly123!";

async function checkA11y(page: Page, name: string): Promise<void> {
  for (const scheme of ["light", "dark"] as const) {
    await page.emulateMedia({ colorScheme: scheme });
    expect(await axeViolations(page), `${name} ${scheme}`).toEqual([]);
    await shot(page, `weekly-${name}-${scheme}`);
  }
  await page.emulateMedia({ colorScheme: "light" });
}

test("turn weekly scoring on, change it, turn it off; a viewer cannot; refusals and run outcomes are plain", async ({ page, browser }) => {
  await login(page);
  const { projectId } = await trainedProject(page, "Weekly");
  await page.goto(`/projects/${projectId}/predictions`);
  const heading = page.getByRole("heading", { name: "Weekly scoring", exact: true });
  await expect(heading).toBeVisible();
  const status = page.getByTestId("weekly-status");
  await expect(status).toContainText("Weekly scoring is not set up.");
  // The first-run model is in use but nobody approved it: the warning links to the Approved-for-scoring tab.
  await expect(page.getByTestId("weekly-model")).toContainText("No approved model is in use: nothing will be scored until one is approved");
  await expect(page.getByRole("link", { name: "Open Approved for scoring" })).toHaveAttribute("href", `/projects/${projectId}/models?tab=approved`);
  await expect(page.getByText("It does not retrain, does not fetch data and does not switch models.")).toBeVisible();
  await expect(page.getByText("No week has been handled yet.")).toBeVisible();
  await checkA11y(page, "0-not-set-up");

  // Turn on: Wednesday 14:00 UTC; the confirm step states what happens and what does not; focus moves into it.
  await page.getByLabel("Day of the week").selectOption("2");
  await page.getByLabel("Hour (UTC)").selectOption("14");
  await page.getByRole("button", { name: "Turn on weekly scoring" }).click();
  const confirm = page.getByRole("form", { name: "Turn on weekly scoring?" });
  await expect(confirm.getByRole("heading", { name: "Turn on weekly scoring?" })).toBeFocused();
  await expect(confirm).toContainText("Every Wednesday at 14:00 UTC, DCLab will score the newest file uploaded for scoring after weekly scoring was first set up and after the last week it scored");
  await expect(confirm).toContainText("No approved model is in use");
  await expect(confirm).toContainText("does not retrain, does not fetch data and does not switch models");
  await expect(confirm).toContainText("uploaded with an access token");
  await expect(confirm).toContainText("Only the latest missed week within 24 hours is considered");
  await expect(page.getByRole("button", { name: "Turn on weekly scoring" })).toHaveCount(0);
  await expect(confirm).toContainText("after weekly scoring was first set up and after the last week it scored");
  await expect(confirm).toContainText("Only one file is scored per week; older new files in the same week are not scored.");
  expect(await confirm.innerText()).not.toMatch(BANNED);
  await checkA11y(page, "1-confirm");
  await confirm.getByRole("button", { name: "Cancel" }).click();
  await expect(page.getByRole("button", { name: "Turn on weekly scoring" })).toBeFocused();
  await page.getByRole("button", { name: "Turn on weekly scoring" }).click();
  await confirm.getByLabel("Why (optional, saved with the change)").fill("Weekly churn list for the retention team");
  await confirm.getByRole("button", { name: "Yes, save weekly scoring" }).click();
  await expect(page.getByRole("status").filter({ hasText: "Weekly scoring is saved: every Wednesday at 14:00 UTC." })).toBeVisible();
  await expect(status).toBeFocused();
  // No approved model is in use, so it is on but will score nothing: warn tone, never a plain "on".
  await expect(status).toContainText("Weekly scoring is on, but it will score nothing until the model in use is approved for scoring");
  await expect(page.getByText("On, will score nothing")).toBeVisible();
  expect(await page.locator("main").innerText()).not.toMatch(BANNED);
  await expect(page.getByTestId("weekly-slot")).toContainText("Every Wednesday at 14:00 UTC");
  await expect(page.getByTestId("weekly-slot")).toContainText("The next run is");
  await expect(page.getByTestId("weekly-next")).toContainText(/^Next run: \w{3}, \d{1,2} \w{3} \d{4}, 14:00 UTC\.$/);
  // The save is in History with the reason.
  const decisions = (await page.request.get(`/api/backend/v1/projects/${projectId}/decisions`).then((r) => r.json())) as { items?: Array<{ rationale?: string }> };
  expect(JSON.stringify(decisions)).toContain("Weekly churn list for the retention team");
  await checkA11y(page, "2-on");

  // Change: Friday 08:00 UTC.
  await page.getByLabel("Day of the week").selectOption("4");
  await page.getByLabel("Hour (UTC)").selectOption("8");
  await page.getByRole("button", { name: "Save change" }).click();
  await page.getByRole("button", { name: "Yes, save weekly scoring" }).click();
  await expect(page.getByTestId("weekly-slot")).toContainText("Every Friday at 08:00 UTC");
  expect(await page.locator("main").innerText()).not.toMatch(BANNED);

  // A viewer reads the schedule and sees no buttons.
  const viewerContext = await browser.newContext();
  const viewer = await viewerContext.newPage();
  await viewer.goto("/login");
  await viewer.getByLabel("Email").fill(VIEWER);
  await viewer.getByLabel("Password").fill(PASSWORD);
  await viewer.getByRole("button", { name: "Sign in" }).click();
  await expect(viewer).not.toHaveURL(/\/login/);
  await viewer.goto(`/projects/${projectId}/predictions`);
  await expect(viewer.getByTestId("weekly-slot")).toContainText("Every Friday at 08:00 UTC");
  await expect(viewer.getByText("Only people who can change this workspace can turn weekly scoring on, change it or turn it off.")).toBeVisible();
  await expect(viewer.getByRole("button", { name: /weekly scoring|Save change/ })).toHaveCount(0);
  await viewerContext.close();

  // One key per action (stubbed 500s): a retry of the same request keeps its key; a changed reason is a new action.
  const keys: string[] = [];
  const failing = "**/api/backend/v1/projects/*/scoring-schedule";
  await page.route(failing, (route) => {
    if (route.request().method() !== "PUT") return route.continue();
    keys.push(route.request().headers()["idempotency-key"] ?? "");
    return route.fulfill({ status: 500, contentType: "application/json", body: JSON.stringify({ error: { code: "internal_error", message: "SERVER INTERNAL WORDS", retryable: false, details: {} } }) });
  });
  await page.getByRole("button", { name: "Save change" }).click();
  const keyed = page.getByRole("form", { name: "Turn on weekly scoring?" });
  await keyed.getByLabel("Why (optional, saved with the change)").fill("first reason");
  await keyed.getByRole("button", { name: "Yes, save weekly scoring" }).click();
  await expect(keyed.getByRole("alert")).toContainText("The schedule was not saved");
  await keyed.getByRole("button", { name: "Yes, save weekly scoring" }).click();
  await expect.poll(() => keys.length).toBe(2);
  await keyed.getByLabel("Why (optional, saved with the change)").fill("second reason");
  await keyed.getByRole("button", { name: "Yes, save weekly scoring" }).click();
  await expect.poll(() => keys.length).toBe(3);
  expect(keys[0]).toBeTruthy();
  expect(keys[1]).toBe(keys[0]);
  expect(keys[2]).not.toBe(keys[0]);
  await page.unroute(failing);
  await keyed.getByRole("button", { name: "Cancel" }).click();

  // Refusals are fixed plain sentences (stubbed): 403 for a person without access, 412 conflict.
  const put = "**/api/backend/v1/projects/*/scoring-schedule";
  const refuse = (status: number, code: string) => page.route(put, (route) => (route.request().method() === "PUT"
    ? route.fulfill({ status, contentType: "application/json", body: JSON.stringify({ error: { code, message: "SERVER INTERNAL WORDS", retryable: false, details: {} } }) })
    : route.continue()));
  await refuse(403, "human_session_required");
  await page.getByRole("button", { name: "Save change" }).click();
  await page.getByRole("button", { name: "Yes, save weekly scoring" }).click();
  await expect(confirm.getByRole("alert")).toContainText("Only a signed-in person can change the schedule");
  await expect(confirm.getByRole("alert")).not.toContainText("SERVER INTERNAL WORDS");
  await page.unroute(put);
  await refuse(412, "precondition_failed");
  await page.getByRole("button", { name: "Yes, save weekly scoring" }).click();
  await expect(confirm.getByRole("alert")).toContainText("The schedule was changed by someone else");
  await page.unroute(put);
  await page.getByRole("button", { name: "Cancel" }).click();

  // Turn off.
  await page.getByRole("button", { name: "Turn off weekly scoring" }).click();
  const off = page.getByRole("form", { name: "Turn off weekly scoring?" });
  await expect(off).toContainText("Nothing will be scored on a schedule from now on.");
  await off.getByRole("button", { name: "Yes, turn it off" }).click();
  await expect(status).toContainText("Weekly scoring is off. Nothing is scored on a schedule.");
  await expect(page.getByTestId("weekly-next")).toHaveCount(0);
  await expect(page.getByRole("button", { name: "Turn on weekly scoring" })).toBeVisible();
  expect(await page.locator("main").innerText()).not.toMatch(BANNED);
  await checkA11y(page, "3-off");

  // 375 px: no horizontal scroll.
  await page.setViewportSize({ width: 375, height: 800 });
  await page.goto(`/projects/${projectId}/predictions`);
  await expect(heading).toBeVisible();
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
  await shot(page, "weekly-4-mobile");
});

const PRED_OK = "55555555-5555-4555-8555-555555555555";
const PRED_FAIL = "44444444-4444-4444-8444-444444444444";
const MODEL = "66666666-6666-4666-8666-666666666666";
const run = (n: number, outcome: string, extra: Record<string, unknown> = {}) => ({
  id: `00000000-0000-4000-8000-00000000000${n}`, planned_for: `2026-10-${String(12 - n).padStart(2, "0")}T09:00:00Z`, outcome, message: "SERVER WORDS", created_at: "2026-10-12T09:00:00Z", ...extra,
});
const scheduleBody = (projectId: string, status: string, schedule: Record<string, unknown> | null, runs: unknown[] = []) => JSON.stringify({
  project_id: projectId, status, status_message: "SERVER WORDS", about: "SERVER WORDS", recent_runs: runs,
  schedule: schedule && { id: PRED_FAIL, weekday: 0, weekday_name: "Monday", hour_utc: 9, timezone: "UTC", enabled: true, next_planned_for: "2026-10-19T09:00:00Z", created_at: "2026-10-01T00:00:00Z", updated_at: "2026-10-01T00:00:00Z", owner_can_score: true, ...schedule },
});
const stubSchedule = (page: Page, body: string, status = 200) => page.route("**/api/backend/v1/projects/*/scoring-schedule", (route) => (route.request().method() === "GET"
  ? route.fulfill({ status, contentType: "application/json", body })
  : route.continue()));
const prediction = (id: string, extra: Record<string, unknown>) => JSON.stringify({
  id, model_version_id: MODEL, input_dataset_id: MODEL, status: "completed", output_format: "csv", rows_in: 130, rows_out: 120, contract_check: null, scheduled: true, scheduled_for: "2026-10-11T09:00:00Z",
  created_at: "2026-10-11T09:00:05Z", output: null, ...extra,
});

test("paused by the platform, every run outcome, and what a queued week really did (stubbed read)", async ({ page }) => {
  await login(page);
  const { projectId } = await trainedProject(page, "WeeklyStub");
  // The model version is linked a moment after the run completes; the table below needs a listed model.
  await expect.poll(async () => {
    const graph = (await page.request.get(`/api/backend/v1/projects/${projectId}/graph`).then((r) => r.json())) as { nodes: Array<{ kind: string }> };
    return graph.nodes.some((n) => n.kind === "model_version");
  }, { timeout: 60_000, intervals: [2_000] }).toBe(true);
  await page.route(`**/api/backend/v1/predictions/${PRED_OK}`, (route) => route.fulfill({ status: 200, contentType: "application/json", body: prediction(PRED_OK, {}) }));
  await page.route(`**/api/backend/v1/predictions/${PRED_FAIL}`, (route) => route.fulfill({
    status: 200, contentType: "application/json", body: prediction(PRED_FAIL, { status: "failed", rows_out: null, error_code: "schedule_owner_not_allowed", error_message: "SERVER WORDS", scheduled_for: "2026-10-10T09:00:00Z" }),
  }));
  await stubSchedule(page, scheduleBody(projectId, "paused_by_platform", { owner_can_score: false }, [
    run(1, "queued", { batch_prediction_id: PRED_OK, model_version_id: MODEL, input_dataset_id: MODEL }), run(2, "queued", { batch_prediction_id: PRED_FAIL, model_version_id: MODEL }),
    run(3, "skipped_no_new_file"), run(4, "skipped_no_approval"), run(5, "skipped_owner_not_allowed"), run(6, "skipped_missed"),
    run(7, "failed_to_queue", { reason_code: "run_limit" }), run(8, "skipped_no_model_in_use"), run(9, "brand_new_code"),
  ]));
  await page.goto(`/projects/${projectId}/predictions`);
  const status = page.getByTestId("weekly-status");
  await expect(status).toContainText("paused by the platform");
  await expect(page.getByTestId("weekly-next")).toContainText("none while it is paused by the platform");
  await expect(page.getByText("can no longer score in this workspace, so runs will score nothing until someone who can saves it again.")).toBeVisible();
  const list = page.getByRole("list", { name: "Last weekly runs, newest first" });
  await expect(list.getByRole("listitem")).toHaveCount(9);
  // A queued week only started a scoring: done (with the list link) only when it completed, failed with the plain reason otherwise.
  await expect(list).toContainText("Done: 120 rows scored.");
  await expect(list).toContainText("Failed: This week was not scored. The person who set up this schedule can no longer score");
  await expect(list.getByRole("link", { name: "See the weekly list" })).toHaveCount(1);
  await expect(list).not.toContainText("Scored the newest file");
  await expect(list).toContainText("Nothing scored: no file was uploaded for scoring after weekly scoring was first set up and after the last week it scored.");
  await expect(list).toContainText("Nothing scored: the model in use was not approved for scoring.");
  await expect(list).toContainText("the person who set up this schedule can no longer score in this workspace");
  await expect(list).toContainText("DCLab did not get to it within 24 hours");
  await expect(list).toContainText("Failed: the scoring could not be started (too many jobs were running). Nothing was scored.");
  await expect(list).toContainText("this page does not recognise the result");
  await expect(list.getByText("failed", { exact: true })).toHaveCount(2);
  // The table labels the scheduled weeks in UTC, names no fake file and says why the failed one failed.
  const table = page.getByRole("table", { name: "Predictions run in this browser tab and by the weekly schedule" });
  await expect(table).toContainText("Scheduled run for Sun, 11 Oct 2026, 09:00 UTC");
  await expect(table).toContainText("Scheduled run for Sat, 10 Oct 2026, 09:00 UTC");
  await expect(table).toContainText("Chosen by the weekly schedule (file not named here)");
  await expect(table).toContainText("This week was not scored. The person who set up this schedule can no longer score, so this week was not scored.");
  expect(await page.locator("main").innerText()).not.toContain("SERVER WORDS");
  expect(await page.locator("main").innerText()).not.toMatch(BANNED);
  await checkA11y(page, "5-paused");
});

test("off with an owner who cannot score, an overdue next run, and a failed read (stubbed read)", async ({ page }) => {
  await login(page);
  const { projectId } = await trainedProject(page, "WeeklyStates");
  await stubSchedule(page, scheduleBody(projectId, "off", { enabled: false, owner_can_score: false }));
  await page.goto(`/projects/${projectId}/predictions`);
  await expect(page.getByTestId("weekly-status")).toContainText("Weekly scoring is off.");
  await expect(page.getByText("can no longer score in this workspace")).toHaveCount(0);
  expect(await page.locator("main").innerText()).not.toMatch(BANNED);
  await page.unroute("**/api/backend/v1/projects/*/scoring-schedule");

  await stubSchedule(page, scheduleBody(projectId, "on", { next_planned_for: "2026-10-05T09:00:00Z" }));
  await page.reload();
  await expect(page.getByTestId("weekly-next")).toContainText("Was due Mon, 5 Oct 2026, 09:00 UTC; it has not started. If it is not handled within 24 hours that week is skipped.");
  await expect(page.getByTestId("weekly-next")).not.toContainText("Next run");
  expect(await page.locator("main").innerText()).not.toMatch(BANNED);
  await page.unroute("**/api/backend/v1/projects/*/scoring-schedule");

  await stubSchedule(page, JSON.stringify({ error: { code: "internal_error", message: "SERVER WORDS", retryable: false, details: {} } }), 500);
  await page.reload();
  await expect(page.getByText("The weekly schedule could not be loaded.")).toBeVisible();
  await expect(page.getByRole("button", { name: "Reload" })).toBeVisible();
  expect(await page.locator("main").innerText()).not.toContain("SERVER WORDS");
  await expect(page.getByRole("button", { name: "Turn on weekly scoring" })).toHaveCount(0);
});
