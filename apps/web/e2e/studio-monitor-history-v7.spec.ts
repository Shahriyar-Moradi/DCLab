import { expect, test, type Page, type Route } from "@playwright/test";
import { axeViolations, churnCsv, login, shot, trainedProject } from "./support/studio-fixture";

/**
 * V7 UI-E: Monitoring trends (input change by week, real precision / recall by week) and "What the system does on its own";
 * History with uploads / final test / drift / weekly scoring events, the approvals shortcut, the Lineage tab and the
 * "newer file exists" banner; the Inbox drift-alert card, agreement pills and Done-tab "who closed it" pills; Home's row.
 *
 * Real: training (upload + run + final test), approving the model, uploading a newer training file, the graph read
 * (`newer_dataset`), the activity read (uploads, final test), decisions. STUBBED in the browser (page.route): the monitoring
 * reads (a real drift scenario needs weeks of scored files), two extra activity items (drift alert, weekly scoring) appended
 * to the real page, the Inbox lists and counts, and (for one check) the governance read with AI switched on.
 */
const BANNED = /\b(release|champion|ref|digest|manifest|package|slot|tick|revision|holdout|window)s?\b/i;
const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/;
const THRESHOLDS = { psi_watch: 0.1, psi_alert: 0.2, significance_level: 0.01, flagged_share_relative_watch: 0.3, flagged_share_absolute_watch: 0.02, min_rows: 200, min_labels: 100, min_per_class: 10 };
const NOTES = ["Drift means the inputs look different from training; it does not mean the model is wrong."];
const WIN = "dddddddd-dddd-4ddd-8ddd-dddddddddddd";

async function writeHeaders(page: Page, label: string): Promise<Record<string, string>> {
  const csrf = (await page.context().cookies()).find((c) => c.name === "dclab_csrf")?.value ?? "";
  return { "Idempotency-Key": `e2e-${label}-${Date.now()}`, "X-CSRF-Token": csrf, Origin: new URL(page.url()).origin };
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

const week = (modelId: string, start: string, status: string, extra: Record<string, unknown> = {}) => ({
  window_start: `${start}T00:00:00Z`, window_end: `${start}T00:00:00Z`, revision: 1, model_version_id: modelId, model_release_id: null,
  status, summary_code: status === "ok" ? "stable" : "inputs_and_scores_moved", summary: "SERVER SUMMARY WORDS", n_rows: 1200, n_batch_predictions: 1,
  max_psi: 0.05, prediction_psi: 0.03, outcomes_status: "not_linked", n_labelled: null, coverage: null, outcome_metric: null, outcome_metric_value: null,
  outcome_precision: null, outcome_recall: null, computed_at: `${start}T08:00:00Z`, ...extra,
});
function overviewFor(modelId: string, version: string): Record<string, unknown> {
  const weeks = [
    week(modelId, "2026-10-05", "alert", { max_psi: 0.31, prediction_psi: 0.12, outcomes_status: "not_enough_labels", n_labelled: 30 }),
    // Hard cases: a week with too little data still carries a large number (not judged); an ok week above the alert line (within chance).
    week(modelId, "2026-09-28", "not_enough_data", { summary_code: "too_few_rows", n_rows: 12, max_psi: 0.5, prediction_psi: null }),
    week(modelId, "2026-09-21", "ok", { max_psi: 0.24, outcomes_status: "measured", n_labelled: 510, outcome_metric: "pr_auc", outcome_metric_value: 0.66, outcome_precision: 0.61, outcome_recall: 0.82 }),
    week(modelId, "2026-09-14", "ok", { max_psi: 0.04, outcomes_status: "measured", n_labelled: 530, outcome_metric: "pr_auc", outcome_metric_value: 0.68, outcome_precision: 0.63, outcome_recall: 0.81 }),
  ];
  const points = (values: Array<[number | null, string | null]>) => ["2026-09-14", "2026-09-21", "2026-09-28", "2026-10-05"].map((d, i) => ({ window_start: `${d}T00:00:00Z`, psi: values[i][0], status: values[i][1] }));
  return {
    project_id: "p", enabled: true, model_version_id: modelId, model_version: version, model_release_id: null, status: "alert", latest: weeks[0], windows: weeks,
    feature_psi_series: [
      { name: "plan", kind: "categorical", points: points([[0.03, "ok"], [0.15, "ok"], [0.5, null], [0.31, "alert"]]) },
      { name: "monthly_spend", kind: "numeric", points: points([[0.02, "ok"], [null, null], [null, null], [0.12, "watch"]]) },
    ],
    finding: null, thresholds: THRESHOLDS, notes: NOTES,
  };
}

async function stubMonitoring(page: Page, overview: Record<string, unknown>): Promise<void> {
  await page.route(/\/api\/backend\/v1\/projects\/[^/]+\/monitoring(\/.*)?(\?.*)?$/, async (route: Route) => {
    const url = new URL(route.request().url());
    const json = (status: number, body: unknown) => route.fulfill({ status, contentType: "application/json", body: JSON.stringify(body) });
    if (url.pathname.endsWith("/monitoring")) return json(200, overview);
    const m = url.pathname.match(/\/windows\/(\d{4}-\d{2}-\d{2})$/);
    if (m) {
      const summary = (overview.windows as Array<ReturnType<typeof week>>).find((w) => w.window_start.startsWith(m[1]));
      if (!summary) return json(404, { error: { code: "not_found", message: "x", retryable: false, details: {} } });
      return json(200, {
        ...summary, input_drift: { available: true, features_compared: 2, features: [{ name: "plan", kind: "categorical", psi: summary.max_psi, status: summary.status === "ok" ? "ok" : summary.status, counted: true }] },
        feature_sentences: ["SERVER SENTENCE"], prediction_drift: { available: false, reason_code: "too_few_scores", n_scores: 12 }, outcomes: { status: summary.outcomes_status, n_labelled: summary.n_labelled },
        outcome_sentences: [], finding: null, thresholds: THRESHOLDS, notes: NOTES,
      });
    }
    if (url.pathname.endsWith("/monitoring/outcomes")) return json(200, { project_id: "p", items: [], notes: NOTES });
    return route.continue();
  });
}

async function checkPage(page: Page, name: string, ready: () => Promise<void>): Promise<void> {
  for (const scheme of ["light", "dark"] as const) {
    await page.emulateMedia({ colorScheme: scheme });
    expect(await axeViolations(page), `${name} ${scheme}`).toEqual([]);
    await shot(page, `ui-e-${name}-${scheme}`);
  }
  await page.emulateMedia({ colorScheme: "light" });
  for (const width of [1280, 800, 375]) {
    await page.setViewportSize({ width, height: 900 });
    await page.reload();
    await ready();
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= document.documentElement.clientWidth), `${name} ${width}px`).toBe(true);
    if (width === 375) await shot(page, `ui-e-${name}-phone`);
  }
  await page.setViewportSize({ width: 1280, height: 900 });
}

const inboxItem = (over: Record<string, unknown>) => ({
  occurred_at: "2026-10-06T08:00:00Z", summary: "SERVER WORDS", status: "proposed", proposed_by: "rule", decision_type: null, proposal_type: null,
  decision_point_key: null, level: null, resolution_record_id: null, expires_at: null, rule_answer: null, ai_answer: null, answers_truncated: false,
  evidence_refs: [], actions: [], can_act: false, agreement: null, resolved_by: null, monitoring: null, ...over,
});

test("UI-E: monitoring trends and steps, history events and lineage tab, inbox drift card and pills", async ({ page }) => {
  test.setTimeout(420_000);
  await login(page);
  const { projectId, experimentId } = await trainedProject(page, "UI-E");
  const model = await modelOf(page, projectId, experimentId);

  // Real approval on the Model screen (a History entry about a model).
  await page.goto(`/projects/${projectId}/models?tab=approved`);
  const panel = page.getByRole("tabpanel", { name: "Approved for scoring" });
  // The first run put the model in use without an approval: approve it so History has an approval entry about a model.
  await expect(panel.getByTestId("approval-summary")).toContainText(`${model.name} is the model in use`);
  await panel.getByRole("button", { name: `Approve for scoring: ${model.name}` }).click();
  await panel.getByRole("button", { name: `Yes, approve ${model.name}` }).click();
  await expect(panel.getByTestId("approval-summary")).toContainText(`${model.name} is the model in use.`);

  // ---------- Monitoring (reads stubbed) ----------
  await stubMonitoring(page, overviewFor(model.id, model.version));
  await page.goto(`/projects/${projectId}/monitoring`);
  const psi = page.getByTestId("psi-trend");
  await expect(psi.getByRole("img")).toHaveAttribute("aria-label", "Input change (PSI) by week, oldest first. plan: 14 Sep 0.03, 21 Sep 0.15 (within chance), 28 Sep 0.50 (not judged: too little data), 5 Oct 0.31. Largest change of any column: 14 Sep 0.04, 21 Sep 0.24 (within chance), 28 Sep 0.50 (not judged: too little data), 5 Oct 0.31. Watch line 0.10, alert line 0.20; crossing a line counts only when the statistical test agrees.");
  await expect(psi.locator(".v7-mon-dot.hollow")).toHaveCount(2);
  await expect(psi).toContainText("A hollow dot is above a line but within what chance allows");
  await expect(psi).toContainText("A week with too little data is not judged and is left off the lines");
  await expect(psi.getByRole("list", { name: "Lines in the chart" })).toContainText("watch 0.10 (in force now)");
  await page.getByLabel("Column", { exact: true }).selectOption({ label: "monthly_spend" });
  await expect(psi.getByRole("img")).toHaveAttribute("aria-label", /monthly_spend: 14 Sep 0\.02, 21 Sep —, 28 Sep —, 5 Oct 0\.12/);
  await psi.getByText("The numbers as a table").click();
  await expect(psi.getByRole("table")).toContainText("21 Sep");
  const real = page.getByTestId("real-trend");
  await expect(real.getByRole("img")).toHaveAttribute("aria-label", /Precision: 14 Sep 0\.63, 21 Sep 0\.61, 28 Sep —, 5 Oct —\. Recall: 14 Sep 0\.81, 21 Sep 0\.82, 28 Sep —, 5 Oct —\./);
  await expect(real).toContainText("2 of 4 weeks have no result yet");
  const steps = page.getByTestId("own-steps");
  // D5c: the retrain steps come from the real retrain status read (policy Off by default); nothing is "Planned".
  await expect(steps.locator("li")).toHaveCount(6);
  await expect(steps.locator("li").nth(0)).toContainText("On. The latest check was the week of Mon, 5 Oct 2026 (UTC).");
  await expect(steps.locator("li").nth(1)).toContainText("Alert in the week of Mon, 5 Oct 2026 (UTC)");
  await expect(steps.locator("li").nth(1).locator(".pill.crit")).toHaveText("Alert");
  await expect(steps.locator("li").nth(2)).toContainText("Retraining on its own is off for this project.");
  await expect(steps.locator("li").nth(3)).toContainText("Nothing to compare until a retrain finishes.");
  await expect(steps.locator("li").nth(4)).toContainText("Automatic never switches.");
  await expect(steps.locator("li").nth(5)).toContainText("There is no earlier approved model to go back to.");
  await expect(steps).not.toContainText("Planned");
  await expect(page.locator("main")).not.toContainText("SERVER");
  expect(await page.locator("main").innerText()).not.toMatch(BANNED);
  await checkPage(page, "monitoring", async () => { await expect(page.getByTestId("own-steps")).toBeVisible(); });

  // ---------- History (real decisions, activity and graph; two activity items appended) ----------
  const upload = await page.request.post("/api/backend/v1/datasets", {
    headers: await writeHeaders(page, "newer"),
    multipart: { project_id: projectId, file: { name: "churn-newer.csv", mimeType: "text/csv", buffer: Buffer.from(churnCsv()) } },
  });
  expect(upload.status(), await upload.text()).toBeLessThan(300);
  await page.route(/\/api\/backend\/v1\/activity\?.*project_id=/, async (route: Route) => {
    const response = await route.fetch();
    const body = (await response.json()) as { items: Array<Record<string, unknown>> };
    const extra = [
      { id: `drift_alert:${WIN}`, kind: "drift_alert", occurred_at: new Date().toISOString(), project_id: projectId, actor: { kind: "rule", is_you: false }, subject: { kind: "model_version", id: model.id, key: null }, summary: "SERVER WORDS", status: "alert", link: { kind: "monitoring_window", id: WIN } },
      { id: `scheduled_scoring:${WIN}`, kind: "scheduled_scoring", occurred_at: new Date(Date.now() - 1000).toISOString(), project_id: projectId, actor: { kind: "rule", is_you: false }, subject: { kind: "project", id: projectId, key: null }, summary: "SERVER WORDS", status: "skipped_no_new_file", link: { kind: "scoring_schedule_run", id: WIN } },
    ];
    await route.fulfill({ response, contentType: "application/json", body: JSON.stringify({ ...body, items: [...extra, ...body.items] }) });
  });
  await page.goto(`/projects/${projectId}/decisions`);
  const log = page.getByRole("table", { name: "History" });
  await expect(log).toContainText("Uploaded a training file");
  await expect(log).toContainText(/Scored the final test set once for Run \S+/);
  await expect(log).toContainText("Cannot be repeated for this run.");
  await expect(log).toContainText(`Weekly monitoring marked a week as alert for ${model.name}`);
  await expect(log).toContainText("Weekly scoring skipped: no new file to score");
  await expect(log.getByRole("row").filter({ hasText: "Weekly monitoring marked a week as alert" }).getByRole("link", { name: /Open Monitoring/ })).toHaveAttribute("href", `/projects/${projectId}/monitoring`);
  // The approval (Put a model in use) links to Model → Approved for scoring; the first versions in use keep their own note.
  await expect(log.getByRole("row").filter({ hasText: "Put a model in use" }).getByRole("link", { name: /Model → Approved for scoring/ })).toHaveAttribute("href", `/projects/${projectId}/models?tab=approved`);
  await expect(log.getByRole("row").filter({ hasText: "Set the first versions in use" }).getByRole("link", { name: /Model → Approved for scoring/ })).toHaveCount(0);
  const newer = page.getByTestId("newer-file");
  await expect(newer).toContainText("A newer file exists. A training file newer than the one this project uses was uploaded on");
  await expect(newer.getByRole("link", { name: "Open Monitoring" })).toHaveAttribute("href", `/projects/${projectId}/monitoring`);
  await expect(page.locator("main")).not.toContainText("SERVER");
  expect(await page.locator("main").innerText()).not.toMatch(BANNED);
  await checkPage(page, "history", async () => { await expect(page.getByRole("table", { name: "History" })).toContainText("Uploaded a training file"); });
  await page.getByRole("tab", { name: "Lineage" }).click();
  await expect(page.getByRole("tabpanel", { name: "Lineage" }).getByRole("region", { name: "Lineage summary" })).toBeVisible();
  await expect(page.getByRole("tab", { name: "Diagram" })).toBeVisible();
  await checkPage(page, "history-lineage", async () => {
    await page.getByRole("tab", { name: "Lineage" }).click();
    await expect(page.getByRole("region", { name: "Lineage summary" })).toBeVisible();
  });
  await page.goto(`/projects/${projectId}/decisions?tab=lineage`);
  await expect(page.getByRole("tab", { name: "Lineage" })).toHaveAttribute("aria-selected", "true");
  await page.goto(`/projects/${projectId}/graph`);
  await expect(page.getByTestId("newer-file")).toBeVisible();

  // ---------- Inbox (lists stubbed) ----------
  const drift = inboxItem({
    id: `drift_alert:${WIN}`, kind: "drift_alert", tab: "needs_decision", project_id: projectId, status: "alert", source: { kind: "monitoring_window", id: WIN },
    subject: { kind: "model_version", id: model.id, key: null }, evidence_refs: [{ kind: "model_version", id: model.id }],
    monitoring: { window_start: "2026-10-05T00:00:00Z", model_version_id: model.id, max_psi: 0.314, prediction_psi: 0.12 },
  });
  const jev = inboxItem({ id: `agent_proposal:${WIN}`, kind: "agent_proposal", tab: "needs_decision", project_id: projectId, proposed_by: "jev", proposal_type: "SemanticReviewProposal", source: { kind: "agent_proposal", id: WIN }, subject: { kind: "project", id: projectId, key: null }, agreement: "disagree" });
  const doneItem = inboxItem({ id: `decision_proposal:${WIN}`, kind: "decision_proposal", tab: "done", project_id: projectId, status: "accepted", proposed_by: "rule", decision_type: "experiment_accepted", source: { kind: "decision_record", id: WIN }, subject: { kind: "experiment", id: experimentId, key: null }, resolved_by: { kind: "person", is_you: true } });
  const lists: Record<string, unknown[]> = { needs_decision: [drift, jev], applied_automatically: [], done: [doneItem] };
  await page.route(/\/api\/backend\/v1\/inbox(\/counts)?(\?.*)?$/, async (route: Route) => {
    const url = new URL(route.request().url());
    if (url.pathname.endsWith("/counts")) return route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({ needs_decision: 2, applied_automatically: 0, done: 1 }) });
    const tab = url.searchParams.get("tab") ?? "needs_decision";
    return route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({ tab, items: lists[tab] ?? [], next_cursor: null, limit: 20, viewer: { is_agent: false, can_decide: true, can_approve_ai_policy: true } }) });
  });
  await page.goto("/inbox");
  const card = page.getByRole("article", { name: "Alert on the model in use" });
  await expect(card.getByTestId("drift-sentence")).toHaveText("Weekly monitoring marked the week of Mon, 5 Oct 2026 (UTC) as alert: the inputs or the scores of this model look different from training (drift), or its real outcomes fell below a floor set for this project. Monitoring says which. It does not mean the model is wrong, and nothing switched the model in use. Open Monitoring to see which columns moved, then decide what to do; going back to an earlier model is your choice.");
  const stats = card.getByRole("definition");
  await expect(stats).toHaveText(["Mon, 5 Oct 2026", "0.31", "0.12"]);
  await expect(card.getByRole("link", { name: /Open Monitoring/ })).toHaveAttribute("href", `/projects/${projectId}/monitoring`);
  await expect(card).toContainText("Flagged by the weekly monitoring rule");
  await expect(card.locator("h3 .pill.crit")).toHaveText("alert");
  await expect(card).toContainText("Which columns count as changed (the statistical test, column by column) is on Monitoring.");
  // AI is switched off on this server: the agreement pill is hidden.
  await expect(page.getByTestId("inbox-agreement")).toHaveCount(0);
  await expect(page.locator("main")).not.toContainText("SERVER");
  await checkPage(page, "inbox", async () => { await expect(page.getByRole("article", { name: "Alert on the model in use" })).toBeVisible(); });
  await page.getByRole("tab", { name: /Done/ }).click();
  await expect(page.getByTestId("inbox-resolver")).toHaveText("Decided by you");
  await shot(page, "ui-e-inbox-done-light");
  // With AI switched on (governance stubbed), the recorded agreement shows.
  await page.route(/\/api\/backend\/v1\/governance(\?.*)?$/, (route) => route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({
    ai_enabled_setting: true, policy_unavailable: null, spend: null, open_incidents: [], switches: { platform_ai_blocking: null, workspace: [] },
  }) }));
  await page.goto("/inbox");
  await expect(page.getByTestId("inbox-agreement")).toHaveText("The rules and the AI disagreed: we asked you");
  expect(await axeViolations(page)).toEqual([]);
  await shot(page, "ui-e-inbox-agreement-light");

  // Home lists the drift alert with its plain title.
  await page.goto("/home");
  await expect(page.locator("main")).toContainText("Alert on the model in use");
  await page.unrouteAll({ behavior: "ignoreErrors" });
});
