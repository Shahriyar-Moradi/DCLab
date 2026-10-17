import { expect, test, type Page, type Route } from "@playwright/test";
import { PASSWORD, axeViolations, login, shot, trainedProject } from "./support/studio-fixture";

/**
 * D5c (ADR 0012): retrain on newest data and editable thresholds on the Monitoring screen.
 *
 * Real: training, the thresholds read and an administrator's thresholds change (reason, If-Match, confirm), the retrain policy
 * read and change, a viewer's read-only view of both. STUBBED in the browser (page.route): the monitoring overview (weeks need
 * scored files over time) and the retrain status read in each state (a real retrain needs a model in use, an alert week, a newer
 * labelled file and a second finished run). The stubbed reads carry server words ("SERVER") that must never reach the page.
 */
const VIEWER = process.env.DCLAB_E2E_VIEWER_EMAIL ?? "viewer-a@verification.invalid";
const BANNED = /\b(release|champion|ref|digest|manifest|package|slot|tick|revision|holdout|window|proposal)s?\b/i;
const SAME_TEST = /same test/i;
const MV = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa";
const DS = "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb";
const RUN = "cccccccc-cccc-4ccc-8ccc-cccccccccccc";
const PROPOSAL = "dddddddd-dddd-4ddd-8ddd-dddddddddddd";
const LIMITS = { psi_watch: 0.1, psi_alert: 0.2, significance_level: 0.01, flagged_share_relative_watch: 0.3, flagged_share_absolute_watch: 0.02, min_rows: 200, min_labels: 100, min_per_class: 10 };

const week = (start: string, extra: Record<string, unknown> = {}) => ({
  window_start: `${start}T00:00:00Z`, window_end: `${start}T00:00:00Z`, revision: 1, model_version_id: MV, model_release_id: null, status: "ok", summary_code: "stable",
  summary: "SERVER SUMMARY", n_rows: 1200, n_batch_predictions: 1, max_psi: 0.05, prediction_psi: 0.03, outcomes_status: "measured", n_labelled: 400, coverage: 0.5,
  outcome_metric: "pr_auc", outcome_metric_value: 0.6, outcome_precision: 0.48, outcome_recall: 0.8, thresholds_used: null, floor_status: null, computed_at: `${start}T08:00:00Z`, ...extra,
});
const OVERVIEW = (() => {
  const weeks = [
    week("2026-10-05", { status: "alert", summary_code: "below_floor", thresholds_used: { version: 1, psi_watch: 0.15, psi_alert: 0.2, precision_floor: 0.6, recall_floor: null }, floor_status: "below" }),
    week("2026-09-28"),
  ];
  return { project_id: "p", enabled: true, model_version_id: MV, model_version: "v1", model_release_id: null, status: "alert", latest: weeks[0], windows: weeks, feature_psi_series: [], finding: null, thresholds: LIMITS, notes: ["SERVER NOTE"] };
})();

const side = (v: number, t: number, n: number) => ({ metric: "pr_auc", metric_value: v, metrics: { pr_auc: v, precision: v - 0.1, recall: 0.8 }, decision_threshold: t, n_rows: n });
const COMPARED = {
  status: "compared", reason_code: null, message: "SERVER MESSAGE", wording: "SERVER WORDING", time_column: "signup_date", incumbent_training_end: "2026-06-30",
  final_test_start: "2026-07-01", final_test_rows: 1200, challenger: side(0.69, 0.42, 1200), incumbent: side(0.6, 0.5, 1200), proposal_refusal: null, computed_at: "2026-10-12T08:00:00Z",
};
const request = (status: string) => ({
  id: RUN, origin: "person", created_at: "2026-10-10T08:00:00Z", incumbent_model_version_id: MV, dataset_id: DS, experiment_id: RUN, experiment_status: status,
  window_start: null, carried: ["target_column", "problem_spec"], not_carried: [],
});
function status(projectId: string, state: string, extra: Record<string, unknown> = {}) {
  return {
    project_id: projectId, policy: "ask", state, model_in_use_id: MV, latest_window_start: "2026-10-05T00:00:00Z", latest_window_status: "alert", newer_dataset_id: DS,
    request: null, comparison: null, comparison_withheld: false, real_outcomes: { label: "not the same data", window_start: "2026-10-05T00:00:00Z", precision: 0.48, recall: 0.8, n_labelled: 400 },
    // D8a: the server's rule decides the retrain button (here: only before a retrain on this file exists).
    proposal_id: null, proposal_state: null, can_start: state === "drift_found", notes: ["SERVER NOTE"], ...extra,
  };
}

async function stubReads(page: Page, retrain: () => unknown): Promise<void> {
  await page.route(/\/api\/backend\/v1\/projects\/[^/]+\/monitoring(\/windows\/[^/?]+|\/outcomes)?(\?.*)?$/, async (route: Route) => {
    const url = new URL(route.request().url());
    if (url.pathname.endsWith("/monitoring")) return route.fulfill({ json: OVERVIEW });
    if (url.pathname.endsWith("/outcomes")) return route.fulfill({ json: { project_id: "p", items: [], notes: [] } });
    return route.fulfill({ json: { ...OVERVIEW.latest, input_drift: { available: false }, feature_sentences: [], prediction_drift: { available: false }, outcomes: { status: "measured", floors: { precision: { floor: 0.6, value: 0.48, status: "below" } } }, outcome_sentences: ["SERVER"], finding: null, thresholds: LIMITS, notes: [] } });
  });
  await page.route(/\/api\/backend\/v1\/projects\/[^/]+\/retrain-status(\?.*)?$/, (route) => route.fulfill({ json: retrain() }));
}

async function noLeaks(page: Page): Promise<void> {
  const text = await page.locator("main").innerText();
  expect(text).not.toContain("SERVER");
  expect(text).not.toMatch(SAME_TEST);
  expect(text.replace(/signup_date/g, "")).not.toMatch(BANNED);
}

test("D5c: thresholds and retrain policy edited by an administrator, read-only for a viewer; retrain states and the retrained model's card", async ({ page, browser }) => {
  test.setTimeout(420_000);
  await login(page);
  const { projectId } = await trainedProject(page, "D5c");
  let retrain: unknown = status(projectId, "drift_found");
  await stubReads(page, () => retrain);
  await page.goto(`/projects/${projectId}/monitoring`);

  // ---------- thresholds: an administrator edits them (bounds, reason, confirm; If-Match and Idempotency-Key are sent) ----------
  const th = page.getByTestId("thresholds");
  await expect(th.getByTestId("thresholds-set-by")).toHaveText("DCLab defaults: never changed for this project.");
  await expect(th).toContainText("Watch line: from 0.05 to 0.5. Alert line: above the watch line, at most 1.");
  await th.getByLabel("Watch line (PSI)").fill("0.04");
  await th.getByRole("button", { name: "Review the change" }).click();
  await expect(th).toContainText("Enter a number from 0.05 to 0.5.");
  await expect(th).toContainText("Give a reason; it is saved in History.");
  await th.getByLabel("Watch line (PSI)").fill("0.15");
  await th.getByRole("checkbox", { name: "Use a precision floor" }).check();
  await th.getByLabel("Precision floor", { exact: true }).fill("0.6");
  await th.getByLabel("Your reason (saved in History)").fill("Seasonal plans: watch earlier, and hold precision.");
  await th.getByRole("button", { name: "Review the change" }).click();
  const confirm = th.getByRole("group", { name: "Save these thresholds?" });
  await expect(confirm.getByRole("heading", { name: "Save these thresholds?" })).toBeFocused();
  await expect(confirm).toContainText("Watch line (PSI): 0.1 → 0.15");
  await expect(confirm).toContainText("Precision floor: off → 0.6");
  await expect(confirm).toContainText("past weeks, and a week recomputed without new scorings, keep the values they used");
  const put = page.waitForRequest((r) => r.method() === "PUT" && r.url().endsWith("/monitoring-thresholds"));
  await confirm.getByRole("button", { name: "Yes, save thresholds" }).click();
  const sent = await put;
  expect(sent.headers()["if-match"]).toBe('"0"');
  expect(sent.headers()["idempotency-key"]).toBeTruthy();
  await expect(th).toContainText("Saved. Weeks computed with new scored data from now on use the new values");
  await expect(th.getByTestId("thresholds-set-by")).toHaveText(/^Set by you, version 1 on .* UTC\. Weeks computed with new scored data after that use these values; past weeks, and a week recomputed without new scorings, keep the values they used\.$/);

  // Weeks show the values they used and the floor verdict; a floor alert is never called drift.
  const table = page.getByRole("table", { name: "Weekly checks of the model in use, newest first" });
  await expect(table).toContainText("Alert: below a floor");
  await expect(table).toContainText("Below a floor");
  await expect(table).toContainText("watch 0.15 · alert 0.2 · precision floor 0.6 · recall floor off");
  await expect(page.getByTestId("monitoring-status")).toContainText("This is not drift");
  await expect(page.getByTestId("week-floors")).toContainText("Precision floor (60%): real precision 48% is below it");

  // ---------- retrain policy: Off → Ask me, with a reason ----------
  const policy = page.getByTestId("retrain-policy");
  await expect(policy).toContainText("Never changed: Off is the default.");
  await policy.getByRole("radio", { name: "Ask me", exact: true }).check();
  await policy.getByLabel("Your reason (saved in History)").fill("Tell me when a newer file can help.");
  const putPolicy = page.waitForRequest((r) => r.method() === "PUT" && r.url().endsWith("/retrain-policy"));
  await policy.getByRole("button", { name: "Save: Ask me" }).click();
  expect((await putPolicy).headers()["if-match"]).toBe('"0"');
  await expect(policy).toContainText("Saved: the retrain policy is now Ask me.");
  await expect(policy).toContainText("Set by you on");

  // ---------- "What the system does on its own": the retrain steps in each state ----------
  const steps = page.getByTestId("own-steps");
  await expect(steps.locator("li").nth(1)).toContainText("real outcomes fell below a floor set for this project. This is not drift.");
  await expect(steps.locator("li").nth(2)).toContainText("A newer labelled file exists.");
  await expect(steps).not.toContainText("Planned");
  await expect(page.getByRole("button", { name: "Retrain on newest data" })).toBeVisible();
  await page.getByRole("button", { name: "Retrain on newest data" }).click();
  const ask = page.getByRole("form", { name: "Retrain on newest data?" });
  await expect(ask).toContainText("with the same target and goal as the model in use");
  await expect(ask).toContainText("Switching is your choice");
  await expect(ask).toContainText("The two can be compared only if that final test is the latest months of the new file");
  await expect(ask).toContainText("Branch settings of the model in use (model families, class weighting, column treatments, model settings, a changed objective) are not carried over");
  await ask.getByRole("button", { name: "Cancel" }).click();

  retrain = status(projectId, "retraining", { request: request("running") });
  await page.reload();
  await expect(steps.locator("li").nth(2)).toContainText("Retraining");
  await expect(steps.locator("li").nth(2)).toContainText("Started by a person.");
  await expect(page.getByRole("button", { name: "Retrain on newest data" })).toHaveCount(0);

  retrain = status(projectId, "ready_to_compare", { request: request("completed") });
  await page.reload();
  await expect(steps.locator("li").nth(3)).toContainText("DCLab is comparing it with the model in use");

  // ---------- the retrained model beside the model in use ----------
  retrain = status(projectId, "waiting_for_you", { request: request("completed"), comparison: COMPARED, proposal_id: PROPOSAL, proposal_state: "proposed" });
  await page.reload();
  const card = page.getByTestId("challenger-card");
  await expect(page.getByText("Same kind of test, latest months", { exact: true })).toBeVisible();
  await expect(card.getByRole("table")).toContainText("PR AUC (higher is better)");
  await expect(card.getByRole("table")).toContainText("0.690");
  await expect(card.getByRole("table")).toContainText("Threshold (each model's own)");
  await expect(card).toContainText("every one later than the training rows of the model in use (by signup_date: the final test starts 2026-07-01");
  await expect(card).toContainText("Not the same data");
  await expect(card.getByTestId("real-beside")).toContainText("precision 0.48, recall 0.80 on 400 rows with a real outcome");
  await expect(card.getByRole("button", { name: "Switch to the retrained model" })).toBeVisible();
  // The model in use was already retrained on the newest file: no second retrain on it (one final test per model and file).
  await expect(page.getByRole("button", { name: "Retrain on newest data" })).toHaveCount(0);
  await card.getByRole("button", { name: "Keep the model in use" }).click();
  await expect(card.getByRole("form", { name: "Keep the model in use?" })).toContainText("nothing is deleted");
  await expect(card.getByRole("button", { name: "Yes, keep the model in use" })).toBeDisabled(); // a reason is required
  await card.getByRole("button", { name: "Cancel" }).click();
  await expect(steps.locator("li").nth(4)).toContainText("Waiting for you");
  await noLeaks(page);
  for (const scheme of ["light", "dark"] as const) {
    await page.emulateMedia({ colorScheme: scheme });
    expect(await axeViolations(page), scheme).toEqual([]);
    await shot(page, `d5c-monitoring-${scheme}`);
  }
  await page.emulateMedia({ colorScheme: "light" });
  for (const width of [800, 375]) {
    await page.setViewportSize({ width, height: 900 });
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= document.documentElement.clientWidth), `${width}px`).toBe(true);
  }
  for (const scheme of ["light", "dark"] as const) { // axe at 375 px too (the last width above)
    await page.emulateMedia({ colorScheme: scheme });
    expect(await axeViolations(page), `375px ${scheme}`).toEqual([]);
  }
  await page.emulateMedia({ colorScheme: "light" });
  await page.setViewportSize({ width: 1280, height: 900 });

  // Not comparable: the named reason in plain words, the retrained model's own result alone, nothing invented beside it.
  retrain = status(projectId, "waiting_for_you", {
    request: request("completed"), proposal_id: PROPOSAL, proposal_state: "proposed",
    comparison: { ...COMPARED, status: "not_comparable", reason_code: "final_test_overlaps_training", incumbent: null },
  });
  await page.reload();
  await expect(card.getByTestId("not-comparable")).toHaveText("Not comparable: the new final test overlaps the training period of the model in use.");
  await expect(card).toContainText("The model in use was not scored on these rows, so nothing is put beside it.");
  await expect(card.getByRole("table")).toHaveCount(0);
  await noLeaks(page);

  // ---------- a viewer: read-only thresholds and policy, no Switch / Keep ----------
  const viewerContext = await browser.newContext({ baseURL: new URL(page.url()).origin });
  const viewer = await viewerContext.newPage();
  await viewer.goto("/login");
  await viewer.getByLabel("Email").fill(VIEWER);
  await viewer.getByLabel("Password").fill(PASSWORD);
  await viewer.getByRole("button", { name: "Sign in" }).click();
  await expect(viewer).not.toHaveURL(/\/login/);
  await stubReads(viewer, () => status(projectId, "waiting_for_you", { request: request("completed"), comparison: COMPARED, proposal_id: PROPOSAL, proposal_state: "proposed" }));
  await viewer.goto(`/projects/${projectId}/monitoring`);
  await expect(viewer.getByTestId("thresholds-read-only")).toContainText("Only workspace owners and administrators can change the thresholds.");
  await expect(viewer.getByTestId("thresholds-set-by")).toHaveText(/^Set by a person, version 1 on .* UTC\./);
  await expect(viewer.getByRole("button", { name: "Review the change" })).toHaveCount(0);
  await expect(viewer.getByTestId("policy-read-only")).toBeVisible();
  await expect(viewer.getByRole("radio", { name: "Automatic" })).toHaveCount(0);
  await expect(viewer.getByTestId("decide-viewer")).toBeVisible();
  await expect(viewer.getByRole("button", { name: "Switch to the retrained model" })).toHaveCount(0);
  await expect(viewer.getByRole("button", { name: "Retrain on newest data" })).toHaveCount(0);
  await noLeaks(viewer);
  await viewerContext.close();
});
