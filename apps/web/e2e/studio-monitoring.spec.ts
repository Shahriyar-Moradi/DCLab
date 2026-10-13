import { expect, test, type BrowserContext, type Page, type Route } from "@playwright/test";
import { axeViolations, login, shot, trainedProject } from "./support/studio-fixture";

/**
 * P7.5-A: the Monitoring screen. The weekly checks table and a week's detail (chart, plain sentences, date/ID caveat, real-world
 * results), every headline state, linking an outcomes file (one confirm step, mapped refusals, one key per action), going back
 * to the previous approved model from an alert (the Model screen's own confirm step and write), and a viewer who sees no buttons.
 *
 * The monitoring reads and the link write are STUBBED in the browser (page.route): this spec runs against an API without the
 * monitoring routes. Training, approvals, going back and the file upload are real. Needs a worker (or ML_JOB_DISPATCHER=thread).
 */
const BANNED = /\b(release|champion|ref|digest|manifest|package|slot|tick|revision|holdout|window)s?\b/i;
const VIEWER = process.env.DCLAB_E2E_VIEWER_EMAIL ?? "viewer-a@verification.invalid";
const PASSWORD = process.env.DCLAB_E2E_STUDIO_PASSWORD ?? "VerificationOnly123!";
const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/;
const THRESHOLDS = { psi_watch: 0.1, psi_alert: 0.2, significance_level: 0.01, flagged_share_relative_watch: 0.3, flagged_share_absolute_watch: 0.02, min_rows: 200, min_labels: 100, min_per_class: 10 };
const NOTES = ["Drift means the inputs look different from training; it does not mean the model is wrong."];

async function writeHeaders(page: Page, label: string): Promise<Record<string, string>> {
  const csrf = (await page.context().cookies()).find((c) => c.name === "dclab_csrf")?.value ?? "";
  return { "Idempotency-Key": `e2e-${label}-${Date.now()}`, "X-CSRF-Token": csrf, Origin: new URL(page.url()).origin };
}

/** A second model on the same test design: a branch of the first run with balanced class weights. */
async function branchRun(page: Page, parentId: string): Promise<string> {
  let id = "";
  await expect.poll(async () => {
    const r = await page.request.post(`/api/backend/v1/experiments/${parentId}/branches`, {
      headers: await writeHeaders(page, "branch"),
      data: { intent: "Balanced class weights", changes: [{ kind: "class_weighting", mode: "balanced" }] },
    });
    if (r.status() < 300) id = ((await r.json()) as { id: string }).id;
    return r.status() < 300 ? "started" : await r.text();
  }, { timeout: 120_000, intervals: [3_000] }).toBe("started");
  await expect.poll(async () => ((await page.request.get(`/api/backend/v1/experiments/${id}`).then((r) => r.json())) as { status: string }).status,
    { timeout: 170_000, intervals: [2_000] }).toBe("completed");
  return id;
}

async function modelOf(page: Page, projectId: string, experimentId: string): Promise<{ id: string; version: string; name: string }> {
  let found = { id: "", version: "", name: "" };
  await expect.poll(async () => {
    const graph = (await page.request.get(`/api/backend/v1/projects/${projectId}/graph`).then((r) => r.json())) as {
      nodes: Array<{ kind: string; id: string; version?: string }>; edges: Array<{ from: { kind: string; id: string }; to: { id: string }; relation: string }>;
    };
    const id = graph.edges.find((e) => e.relation === "produced_by" && e.from.kind === "model_version" && e.to.id === experimentId)?.from.id ?? "";
    const version = graph.nodes.find((n) => n.id === id)?.version ?? "";
    found = { id, version, name: `Model v${version.replace(/^v/i, "")}` };
    return UUID.test(id) && version !== "";
  }, { timeout: 60_000, intervals: [2_000] }).toBe(true);
  return found;
}

const UID = "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb";
/** One weekly summary, in the shape of `MonitoringWindowSummary` (P7.4-A after the review fixes). */
const week = (modelId: string, start: string, status: string, extra: Record<string, unknown> = {}) => ({
  window_start: `${start}T00:00:00Z`, window_end: `${start}T00:00:00Z`, revision: 2, model_version_id: modelId, model_release_id: null,
  status, summary_code: status === "not_enough_data" ? "too_few_rows" : status === "ok" ? "stable" : "inputs_and_scores_moved",
  summary: "SERVER SUMMARY WORDS", n_rows: 1200, n_batch_predictions: 2,
  max_psi: 0.05, prediction_psi: 0.03, outcomes_status: "not_linked", n_labelled: null, coverage: null, outcome_metric: null, outcome_metric_value: null,
  computed_at: `${start}T08:00:00Z`, ...extra,
});
const weeksFor = (modelId: string) => [
  week(modelId, "2026-10-05", "alert", { max_psi: 0.31, prediction_psi: 0.12, outcomes_status: "measured", n_labelled: 140, coverage: 0.35, outcome_metric: "pr_auc", outcome_metric_value: 0.58 }),
  week(modelId, "2026-09-28", "watch", { max_psi: 0.14, outcomes_status: "not_enough_labels", n_labelled: 40 }),
  week(modelId, "2026-09-21", "ok", { max_psi: 0.18, outcomes_status: "key_not_unique" }),
  week(modelId, "2026-09-14", "not_enough_data", { n_rows: 12, max_psi: null, prediction_psi: null }),
];
const feature = (name: string, kind: string, psi: number, status: string, extra: Record<string, unknown> = {}) => ({
  name, kind, psi, p_value: status === "ok" ? 0.2 : 0.00001, significant: status !== "ok", status, counted: true, missing_share: 0, missing_share_reference: 0, ...extra,
});
const SIGNIFICANCE = { test: "g_test_bonferroni", alpha: 0.01, per_feature_alpha: 0.0025 };
const DETAIL: Record<string, Record<string, unknown>> = {
  "2026-10-05": {
    input_drift: {
      available: true, n_rows_compared: 1200, features_compared: 4, features_not_shown: 0, features_not_monitored: 1, dates_compared: 1,
      status_counts: { alert: 1, watch: 1, ok: 2 }, max_psi: 0.31, significance: SIGNIFICANCE, files_unavailable: 0,
      features: [
        feature("plan", "categorical", 0.31, "alert", { top_change: { bucket: "category", value: "family_5g", share: 0.06, share_reference: 0.01 } }),
        feature("monthly_spend", "numeric", 0.15, "ok", { mean: 64.2, mean_reference: 61.5, mean_change_relative: 0.0439, missing_share: 0.05, missing_share_reference: 0.05 }),
        feature("customer_code", "categorical", 0.14, "watch", { top_change: { bucket: "other", value: null, share: 0.9, share_reference: 0.1 } }),
        feature("tenure", "numeric", 0.02, "ok", { mean: 36.1, mean_reference: 36.4, mean_change_relative: -0.0082 }),
        { name: "signup_date", kind: "date", psi: 8.3, p_value: 0, significant: true, status: "ok", counted: false, missing_share: 0, missing_share_reference: 0,
          range: ["2026-09-28", "2026-10-04"], range_reference: ["2024-01-02", "2026-06-30"], later_than_training: true },
      ],
    },
    prediction_drift: {
      available: true, column: "probability", kind: "numeric", n_scores: 1200, psi: 0.12, p_value: 0.00002, status: "watch",
      flagged_share: 0.18, flagged_share_reference: 0.12, flagged_share_change_relative: 0.5, flagged_p_value: 0.0001, flagged_status: "watch",
    },
    outcomes: {
      outcome_set_id: "cccccccc-cccc-4ccc-8ccc-cccccccccccc", status: "measured", n_labelled: 140, n_scored: 1200, n_keyed: 400, coverage: 0.35,
      metric: "pr_auc", metric_value: 0.58, metrics: { precision: 0.48, recall: 0.8, f1: 0.6, accuracy: 0.81, roc_auc: 0.77, pr_auc: 0.58 },
      confusion: [[90, 10], [10, 30]], positive_rate: 0.29, label_positive_rate: 0.29, positive_rate_reference: 0.2,
      cv_reference: { metric: "pr_auc", value: 0.64 }, unrecognised_outcome_values: 2, refused_sets: [], outcome_spellings: "training_labels",
    },
  },
  "2026-09-28": {
    input_drift: {
      available: true, n_rows_compared: 1200, features_compared: 4, features_not_shown: 0, features_not_monitored: 1, dates_compared: 0,
      status_counts: { alert: 0, watch: 1, ok: 3 }, max_psi: 0.14, significance: SIGNIFICANCE,
      features: [feature("plan", "categorical", 0.14, "watch", { top_change: { bucket: "category", value: "pro", share: 0.4, share_reference: 0.33 } })],
    },
    prediction_drift: { available: true, column: "probability", kind: "numeric", n_scores: 1200, psi: 0.03, p_value: 0.4, status: "ok" },
    outcomes: { outcome_set_id: "cccccccc-cccc-4ccc-8ccc-cccccccccccc", status: "not_enough_labels", n_labelled: 40, n_scored: 1200, n_keyed: 400, per_class: [30, 10],
      coverage: 0.1, cv_reference: null, unrecognised_outcome_values: 0, refused_sets: [], outcome_spellings: "training_labels" },
  },
};

type Stub = { overview: Record<string, unknown> | null; linked: Array<Record<string, unknown>>; linkStatus: number; linkCode: string; linkKeys: string[] };

/** The overview read (`MonitoringOverviewRead`) of `model`; its weeks belong to that model. */
function overviewFor(model: { id: string; version: string }, status: string, extra: Record<string, unknown> = {}): Record<string, unknown> {
  const weeks = weeksFor(model.id);
  return {
    project_id: "p", enabled: true, model_version_id: model.id, model_version: model.version, model_release_id: null, status,
    latest: weeks[0], windows: weeks, finding: null, thresholds: THRESHOLDS, notes: NOTES, ...extra,
  };
}

/** Stubs the three monitoring reads and the link write on a page or a whole context. */
async function stubMonitoring(target: Page | BrowserContext, state: Stub): Promise<void> {
  await target.route(/\/api\/backend\/v1\/projects\/[^/]+\/monitoring(\/.*)?(\?.*)?$/, async (route: Route) => {
    const request = route.request();
    const url = new URL(request.url());
    const json = (status: number, body: unknown) => route.fulfill({ status, contentType: "application/json", body: JSON.stringify(body) });
    if (url.pathname.endsWith("/monitoring")) {
      return state.overview ? json(200, state.overview) : json(500, { error: { code: "internal_error", message: "SERVER INTERNAL WORDS", retryable: false, details: {} } });
    }
    const weekMatch = url.pathname.match(/\/windows\/(\d{4}-\d{2}-\d{2})$/);
    if (weekMatch) {
      const weeks = (state.overview?.windows ?? []) as Array<ReturnType<typeof week>>;
      const summary = weeks.find((w) => w.window_start.startsWith(weekMatch[1]));
      if (!summary) return json(404, { error: { code: "not_found", message: "monitoring window not found", retryable: false, details: {} } });
      const detail = DETAIL[weekMatch[1]] ?? {
        input_drift: { available: true, n_rows_compared: summary.n_rows, features_compared: 4, features_not_shown: 0, features_not_monitored: 0, dates_compared: 0, status_counts: { alert: 0, watch: 0, ok: 4 }, max_psi: summary.max_psi ?? 0, significance: SIGNIFICANCE,
          features: [feature("plan", "categorical", summary.max_psi ?? 0, "ok", { top_change: { bucket: "category", value: "pro", share: 0.4, share_reference: 0.33 } })] },
        prediction_drift: { available: false, reason_code: "too_few_scores", n_scores: 12 },
        outcomes: { status: summary.outcomes_status, side: "outcomes", n_scored: summary.n_rows, n_keyed: 400, refused_sets: [{ outcome_set_id: "cccccccc-cccc-4ccc-8ccc-cccccccccccc", reason_code: "key_not_unique" }] },
      };
      return json(200, { ...summary, ...detail, feature_sentences: ["SERVER SENTENCE"], outcome_sentences: ["SERVER OUTCOME SENTENCE"], finding: null, thresholds: THRESHOLDS, notes: NOTES });
    }
    if (url.pathname.endsWith("/monitoring/outcomes")) {
      if (request.method() === "GET") return json(200, { project_id: "p", items: state.linked, notes: NOTES });
      const body = request.postDataJSON() as { dataset_id: string; key_column: string; outcome_column: string };
      state.linkKeys.push(request.headers()["idempotency-key"] ?? "");
      if (state.linkStatus !== 201) return json(state.linkStatus, { error: { code: state.linkCode, message: "SERVER INTERNAL WORDS", retryable: false, details: {} } });
      const row = { id: "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa", project_id: "p", dataset_id: body.dataset_id, dataset_name: "outcomes-october.csv", key_column: body.key_column, outcome_column: body.outcome_column, linked_at: "2026-10-12T09:30:00Z", linked_by_user_id: UID };
      state.linked = [row];
      return json(201, row);
    }
    return route.continue();
  });
}

async function checkA11y(page: Page, name: string): Promise<void> {
  for (const scheme of ["light", "dark"] as const) {
    await page.emulateMedia({ colorScheme: scheme });
    expect(await axeViolations(page), `${name} ${scheme}`).toEqual([]);
    await shot(page, `monitoring-${name}-${scheme}`);
  }
  await page.emulateMedia({ colorScheme: "light" });
}

test("monitoring: weekly checks and a week's detail, link outcomes, go back from an alert; a viewer sees no buttons", async ({ page, browser }) => {
  await login(page);
  const { projectId, experimentId } = await trainedProject(page, "Monitoring");
  const branchId = await branchRun(page, experimentId);
  const first = await modelOf(page, projectId, experimentId);
  const second = await modelOf(page, projectId, branchId);

  // Real approvals on the Model screen: approve the second model, then the first; the second becomes "the previous model".
  await page.goto(`/projects/${projectId}/models?tab=approved`);
  const panel = page.getByRole("tabpanel", { name: "Approved for scoring" });
  for (const model of [second, first]) {
    await panel.getByRole("button", { name: `Approve for scoring: ${model.name}` }).click();
    await panel.getByRole("button", { name: `Yes, approve ${model.name}` }).click();
    await expect(panel.getByTestId("approval-summary")).toContainText(`${model.name} is the model in use.`);
  }

  const state: Stub = { overview: overviewFor(first, "alert"), linked: [], linkStatus: 201, linkCode: "", linkKeys: [] };
  await stubMonitoring(page, state);
  await page.goto(`/projects/${projectId}/monitoring`);
  await expect(page.getByRole("heading", { name: "Monitoring", level: 1 })).toBeVisible();
  await expect(page.locator(".page-head").getByText("Working today")).toBeVisible();
  await expect(page.getByRole("navigation", { name: "Project steps" }).getByRole("link", { name: "Monitoring" })).toHaveAttribute("aria-current", "step");
  const status = page.getByTestId("monitoring-status");
  await expect(status).toContainText(`${first.name}: Alert: in the week of Mon, 5 Oct 2026 (UTC), the inputs or the scores moved a lot.`);
  await expect(status).toContainText("The largest input change (PSI) is 0.31; the score change (PSI) is 0.12. The watch line is 0.10 and the alert line 0.20; a change counts only when a statistical test says it is not chance.");
  await expect(status).toContainText("Drift means the inputs look different from training. It does not mean the model is wrong.");
  await expect(page.locator("main")).not.toContainText("SERVER");
  // PSI is explained once in plain words.
  await expect(page.getByRole("button", { name: /change score \(PSI\)/ })).toHaveAccessibleDescription(/^PSI \(population stability index\) compares how a column's values are spread.*only when PSI passes the line AND a statistical test says the difference is not chance/);

  // Weekly table, newest first, and the newest week's detail.
  const table = page.getByRole("table", { name: "Weekly checks of the model in use, newest first" });
  await expect(table.locator("tbody tr")).toHaveCount(4);
  await expect(table.locator("tbody tr").first()).toContainText("Mon, 5 Oct 2026");
  await expect(table.locator("tbody tr").first()).toContainText("PR AUC 0.58 on 140 rows");
  await expect(table.locator("tbody tr").nth(1)).toContainText("Not enough labelled rows yet (40)");
  // An ok week above the line says it is within chance, next to its "look like training" status.
  await expect(table.locator("tbody tr").nth(2)).toContainText("Compared columns look like training");
  await expect(table.locator("tbody tr").nth(2)).toContainText("0.18 (within chance)");
  await expect(page.getByTestId("week-scope")).toHaveText("4 columns compared with training. 1 column is not monitored. 1 date column is shown but not judged.");
  // The date column (PSI 8.3) is not in the chart: the bars and the scale are the judged columns only.
  const chart = page.getByTestId("drift-chart");
  await expect(chart).toHaveAttribute("aria-label", /^Input change \(PSI\) of the 4 judged columns that moved most, largest first: plan 0\.31 \(alert\), monthly_spend 0\.15 \(within chance\), customer_code 0\.14 \(watch\), tenure 0\.02 \(little change\)\./);
  await expect(chart).not.toContainText("signup_date");
  await expect(chart).toContainText("0.15 · Within chance");
  await expect(page.getByText("plan moved a lot (PSI 0.31): the value 'family_5g' is 6% of rows vs 1% in training.")).toBeVisible();
  await expect(page.getByText("monthly_spend changed (PSI 0.15), above the line but within what chance allows for this many rows and columns: the average is 64.2 vs 61.5 in training.")).toBeVisible();
  await expect(page.getByTestId("date-columns")).toHaveText("signup_date: dates from 2026-09-28 to 2026-10-04 (training: 2024-01-02 to 2026-06-30).");
  await expect(page.getByTestId("date-note")).toHaveText("Dates in new files are later than in training, as expected over time. Date columns are shown but not judged.");
  await expect(page.locator("main")).not.toContainText("8.30");
  await expect(page.getByTestId("id-caveat")).toHaveText(/^The name of customer_code suggests an ID\. ID-like columns can look different simply because new customers appear in new files/);
  await expect(page.getByText("The share of rows flagged is 18% this week vs 12% during training, a change big enough to watch.")).toBeVisible();
  const real = page.getByTestId("real-results");
  await expect(real).toContainText("Real outcomes");
  await expect(real).toContainText("Measured on 140 of 400 scored rows with an ID (35%).");
  await expect(real).toContainText("Rows with outcomes may differ from rows without");
  await expect(real).toContainText("PR AUC on these real outcomes is 0.580; during training (cross-validation) it was 0.640.");
  await expect(real).toContainText("2 outcome values did not match the training outcome labels and were left out.");
  await expect(real).toContainText("Precision");
  await expect(real).toContainText("not the final test");
  await page.getByText("Watch and alert lines (read-only)").click();
  await expect(page.getByText("These lines are set by DCLab and cannot be changed here.")).toBeVisible();
  expect(await page.locator("main").innerText()).not.toMatch(BANNED);
  await checkA11y(page, "0-alert");

  // Another week: its own detail.
  await expect(table.getByRole("button", { name: /^Shown below: week of Mon, 5 Oct 2026/ })).toHaveAttribute("aria-pressed", "true");
  await table.getByRole("button", { name: /^Show: week of Mon, 28 Sept? 2026 \(UTC\)$/ }).click();
  await expect(page.locator("#week-detail")).toBeFocused();
  await expect(page.getByTestId("real-results")).toContainText("Not enough labelled rows yet: 40 of this week's scored rows have a recognised real outcome; at least 100 are needed");
  await expect(page.getByTestId("id-caveat")).toHaveCount(0);
  await expect(page.getByTestId("date-columns")).toHaveCount(0);

  // Go back to the previous model: a mapped refusal first (stubbed 412), then the real write through the same confirm step.
  const offer = page.getByTestId("go-back-offer");
  await expect(offer).toHaveText(`If these differences mean the model in use should not be used for now, you can go back to the previous approved model, ${second.name}. Check whether the data source or the population scored has changed first.`);
  await page.route("**/api/backend/v1/projects/*/refs/champion_model", (route) => (route.request().method() === "POST"
    ? route.fulfill({ status: 412, contentType: "application/json", body: JSON.stringify({ error: { code: "precondition_failed", message: "If-Match is stale", retryable: false, details: {} } }) })
    : route.continue()));
  await page.getByRole("button", { name: `Go back to the previous model: ${second.name}` }).click();
  const back = page.getByRole("form", { name: `Go back to ${second.name}?` });
  await expect(back.getByRole("heading", { name: `Go back to ${second.name}?` })).toBeFocused();
  await expect(back).toContainText(`${second.name} becomes the model in use`);
  await expect(back).toContainText(`${first.name} stops being the model in use.`);
  await expect(back).toContainText("Predictions already made are not changed.");
  await expect(back).toContainText("Nothing is retrained or tested again.");
  await expect(back).toContainText("saved in History");
  await checkA11y(page, "1-go-back-confirm");
  await back.getByRole("button", { name: `Yes, go back to ${second.name}` }).click();
  await expect(back.getByRole("alert")).toContainText("Someone else just changed the model in use");
  await expect(back).not.toContainText(/If-Match|stale/);
  await page.unroute("**/api/backend/v1/projects/*/refs/champion_model");
  await back.getByRole("button", { name: "Cancel" }).click();
  await expect(page.locator("#go-back-open")).toBeFocused();
  await page.getByRole("button", { name: `Go back to the previous model: ${second.name}` }).click();
  await back.getByRole("button", { name: `Yes, go back to ${second.name}` }).click();
  await expect(page.getByRole("status").filter({ hasText: `${second.name} is now the model in use and approved for scoring.` })).toBeVisible();
  const refs = (await page.request.get(`/api/backend/v1/projects/${projectId}/refs`).then((r) => r.json())) as { items: Array<{ ref_kind: string; target: { id: string } }> };
  expect(refs.items.find((r) => r.ref_kind === "champion_model")?.target.id).toBe(second.id);
  // The History entry names where it was done.
  const history = (await page.request.get(`/api/backend/v1/projects/${projectId}/decisions`).then((r) => r.json())) as unknown;
  expect(JSON.stringify(history)).toContain(`Went back to ${second.name} on the Monitoring screen (no reason given).`);
  // The alert now belongs to a model that is no longer in use: no go-back is offered for it.
  await page.reload();
  await expect(status).toContainText(`${first.name}: Alert:`);
  await expect(page.getByTestId("go-back-offer")).toHaveCount(0);
  state.overview = overviewFor(second, "alert");

  // No earlier approval: it says so and offers no button (stubbed approvals read).
  await page.route("**/api/backend/v1/projects/*/releases", (route) => route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({ project_id: projectId, current_release_id: null, items: [] }) }));
  await page.reload();
  await expect(page.getByTestId("go-back-none")).toHaveText("No earlier model was approved for scoring, so there is no previous model to go back to.");
  await expect(page.getByRole("button", { name: /Go back to the previous model/ })).toHaveCount(0);
  await page.unroute("**/api/backend/v1/projects/*/releases");

  // Every headline state, from the stubbed read.
  const states: Array<[Record<string, unknown> | null, string]> = [
    [overviewFor(first, "ok", { latest: weeksFor(first.id)[2], windows: [weeksFor(first.id)[2]] }), "the columns compared look like training (1,200 rows scored). The largest input change (PSI) is 0.18, above the line but within what chance allows"],
    [overviewFor(first, "watch", { latest: weeksFor(first.id)[1], windows: [weeksFor(first.id)[1]] }), "Watch: in the week of"],
    [overviewFor(first, "watch", { latest: { ...weeksFor(first.id)[1], max_psi: 0.03, prediction_psi: 0.04 }, windows: [{ ...weeksFor(first.id)[1], max_psi: 0.03, prediction_psi: 0.04 }] }), "the share of rows the model flagged moved (see Scores in the week's details)"],
    [overviewFor(first, "not_enough_data", { latest: weeksFor(first.id)[3], windows: [weeksFor(first.id)[3]] }), "Fewer than 200 rows could be compared with training in the week of Mon, 14 Sep"],
    [overviewFor(first, "no_windows", { latest: null, windows: [] }), "no week has been checked yet"],
    [overviewFor(first, "no_model_in_use", { latest: null, windows: [], model_version: null, model_version_id: null }), "No model is in use yet, so there is nothing to monitor."],
    [overviewFor(first, "no_windows", { enabled: false, latest: null, windows: [] }), "monitoring is switched off on this server"],
  ];
  for (const [body, text] of states) {
    state.overview = body;
    await page.reload();
    await expect(status).toContainText(text);
    await expect(page.getByRole("button", { name: /Go back to the previous model/ })).toHaveCount(0);
  }
  state.overview = null;
  await page.reload();
  await expect(page.getByRole("alert").filter({ hasText: "The monitoring status could not be loaded." })).toBeVisible();
  await expect(page.locator("main")).not.toContainText("SERVER INTERNAL WORDS");
  state.overview = overviewFor(second, "ok", { latest: weeksFor(second.id)[2], windows: [weeksFor(second.id)[2]] });
  await page.reload();
  await checkA11y(page, "2-ok");

  // Link an outcomes file: real upload, then the stubbed link with mapped refusals and one key per action.
  const outcomesCsv = ["customer_code,churned", ...Array.from({ length: 50 }, (_, i) => `C${1000 + i},${i % 3 === 0 ? "yes" : "no"}`)].join("\n");
  await page.getByLabel("Upload a new outcomes file (optional)").setInputFiles({ name: "outcomes-october.csv", mimeType: "text/csv", buffer: Buffer.from(outcomesCsv) });
  await page.getByRole("button", { name: "Upload file" }).click();
  await expect(page.getByLabel("File with the real outcomes")).not.toHaveValue("");
  await page.getByLabel("Key column (the same IDs as the scored rows' ID column)").selectOption("customer_code");
  await page.getByLabel("Outcome column (what really happened)").selectOption("churned");
  await page.getByRole("button", { name: "Link this file", exact: true }).click();
  const link = page.getByRole("form", { name: /^Link outcomes-october.* as real outcomes\?$/ });
  await expect(link.getByRole("heading")).toBeFocused();
  await expect(link).toContainText("DCLab joins the outcomes in churned to the rows the model in use scored, by matching customer_code with the scored rows' ID column.");
  await expect(link).toContainText("Once at least 100 scored rows have a real outcome");
  await expect(link).toContainText("Outcomes are never used to train or to choose a model, and nothing is retrained or switched.");
  await expect(link).toContainText("The link is saved in History.");
  await expect(link).toContainText("A link cannot be removed; the newest linked file that shares IDs with a week is the one used.");
  await expect(link).toContainText("reload in a few minutes to see the result.");
  await expect(link).toContainText(/File: outcomes-october\S* · 50 rows/);
  await expect(page.getByRole("button", { name: "Link this file", exact: true })).toHaveCount(0);
  await checkA11y(page, "3-link-confirm");
  state.linkStatus = 500; state.linkCode = "internal_error";
  await link.getByRole("button", { name: "Yes, link this file" }).click();
  await expect(link.getByRole("alert")).toContainText("Could not confirm the link. Look at the linked files before trying again.");
  await link.getByRole("button", { name: "Yes, link this file" }).click();
  await expect.poll(() => state.linkKeys.length).toBe(2);
  expect(state.linkKeys[1]).toBe(state.linkKeys[0]);
  state.linkStatus = 409; state.linkCode = "outcomes_from_training_data";
  await link.getByRole("button", { name: "Yes, link this file" }).click();
  await expect(link.getByRole("alert")).toContainText("This file trained a model in this project");
  await expect(link).not.toContainText("SERVER INTERNAL WORDS");
  state.linkStatus = 422; state.linkCode = "key_column_unknown";
  await link.getByRole("button", { name: "Yes, link this file" }).click();
  await expect(link.getByRole("alert")).toContainText("The file has no such key column");
  expect(state.linkKeys[3]).not.toBe(state.linkKeys[2]); // a refusal that can never succeed renews the key
  state.linkStatus = 409; state.linkCode = "already_linked";
  await link.getByRole("button", { name: "Yes, link this file" }).click();
  await expect(link.getByRole("alert")).toContainText("This file is already linked with these columns. Check the list of linked files.");
  state.linkStatus = 429; state.linkCode = "rate_limited";
  await link.getByRole("button", { name: "Yes, link this file" }).click();
  await expect(link.getByRole("alert")).toContainText("more than 10 files linked to this project in the last hour");
  state.linkStatus = 201;
  await link.getByRole("button", { name: "Yes, link this file" }).click();
  await expect(page.getByRole("status").filter({ hasText: /Linked outcomes-october\S* as real outcomes\./ })).toBeVisible();
  await expect(page.locator("#linked-files")).toBeFocused();
  const linked = page.getByRole("list", { name: "Linked outcomes files, newest first" });
  await expect(linked).toContainText("Key column: customer_code · outcome column: churned");
  await expect(linked).toContainText("Linked Mon, 12 Oct 2026, 09:30 UTC by a person. File: 50 rows");
  expect(await page.locator("main").innerText()).not.toMatch(BANNED);
  await checkA11y(page, "4-linked");

  // 375 px: no horizontal scroll.
  state.overview = overviewFor(second, "alert");
  await page.setViewportSize({ width: 375, height: 812 });
  await page.reload();
  await expect(status).toContainText("Alert:");
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= document.documentElement.clientWidth)).toBe(true);
  await shot(page, "monitoring-5-phone-light");
  await page.setViewportSize({ width: 1280, height: 900 });

  // A viewer reads everything and sees no buttons.
  const viewerContext = await browser.newContext({ baseURL: new URL(page.url()).origin });
  await stubMonitoring(viewerContext, { ...state, overview: overviewFor(second, "alert"), linkKeys: [] });
  const viewer = await viewerContext.newPage();
  await viewer.goto("/login");
  await viewer.getByLabel("Email").fill(VIEWER);
  await viewer.getByLabel("Password").fill(PASSWORD);
  await viewer.getByRole("button", { name: "Sign in" }).click();
  await expect(viewer).not.toHaveURL(/\/login/);
  await viewer.goto(`/projects/${projectId}/monitoring`);
  await expect(viewer.getByTestId("monitoring-status")).toContainText("Alert:");
  await expect(viewer.getByTestId("go-back-offer")).toHaveText(`The previous approved model is ${first.name}. A person who can change this workspace can go back to it if these differences mean the model in use should not be used for now.`);
  await expect(viewer.getByText("Only people who can run ML work in this workspace can link an outcomes file.")).toBeVisible();
  await expect(viewer.getByRole("button", { name: /Go back|Link this file|Upload file/ })).toHaveCount(0);
  await expect(viewer.getByLabel("File with the real outcomes")).toHaveCount(0);
  await expect(viewer.getByRole("list", { name: "Linked outcomes files, newest first" })).toContainText("customer_code");
  await viewerContext.close();
});
