import { expect, test, type Page } from "@playwright/test";
import { execFileSync } from "node:child_process";
import { axeViolations, churnCsv, login, shot, trainedProject } from "./support/studio-fixture";

/**
 * V7-B2-UI: the Model screen's "Approved for scoring" tab. Approve a model (the confirm step says what changes), see it in
 * use, go back to the previous one, and see the plain sentence for a model saved in the old format; Predictions say which
 * model scored. Needs a worker (or ML_JOB_DISPATCHER=thread) and `psql` on PATH for the old-format fixture.
 */
const DATABASE_URL = process.env.DCLAB_E2E_DATABASE_URL ?? "postgresql://localhost:5432/dclab_b2ui_e2e_verify";
const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/;

async function writeHeaders(page: Page, label: string): Promise<Record<string, string>> {
  const csrf = (await page.context().cookies()).find((c) => c.name === "dclab_csrf")?.value ?? "";
  return { "Idempotency-Key": `e2e-${label}-${Date.now()}`, "X-CSRF-Token": csrf, Origin: new URL(page.url()).origin };
}

async function completed(page: Page, experimentId: string): Promise<void> {
  await expect.poll(async () => {
    const run = await page.request.get(`/api/backend/v1/experiments/${experimentId}`);
    return ((await run.json()) as { status: string }).status;
  }, { timeout: 170_000, intervals: [2_000] }).toBe("completed");
}

/** A second model on the same test design: a branch of the first run with balanced class weights. */
async function branchRun(page: Page, parentId: string): Promise<string> {
  // The evidence lock lands just after "completed": retry while the parent is not branchable yet.
  let id = "";
  await expect.poll(async () => {
    const r = await page.request.post(`/api/backend/v1/experiments/${parentId}/branches`, {
      headers: await writeHeaders(page, "branch"),
      data: { intent: "Balanced class weights", changes: [{ kind: "class_weighting", mode: "balanced" }] },
    });
    if (r.status() < 300) id = ((await r.json()) as { id: string }).id;
    return r.status() < 300 ? "started" : await r.text();
  }, { timeout: 120_000, intervals: [3_000] }).toBe("started");
  await completed(page, id);
  return id;
}

/** The model each run produced, from the project graph (`model_version produced_by experiment`). */
async function modelOf(page: Page, projectId: string, experimentId: string): Promise<{ id: string; name: string }> {
  let found = { id: "", name: "" };
  // The model version is linked a moment after the run completes.
  await expect.poll(async () => {
    const graph = (await page.request.get(`/api/backend/v1/projects/${projectId}/graph`).then((r) => r.json())) as {
      nodes: Array<{ kind: string; id: string; version?: string }>; edges: Array<{ from: { kind: string; id: string }; to: { id: string }; relation: string }>;
    };
    const id = graph.edges.find((e) => e.relation === "produced_by" && e.from.kind === "model_version" && e.to.id === experimentId)?.from.id ?? "";
    const version = graph.nodes.find((n) => n.id === id)?.version ?? "";
    found = { id, name: `Model v${version.replace(/^v/i, "")}` };
    return UUID.test(id) && version !== "";
  }, { timeout: 60_000, intervals: [2_000] }).toBe(true);
  return found;
}

/** Test fixture only: make a model's stored file look like one saved before the model package format (no package_format). */
function markOldFormat(modelVersionId: string): void {
  expect(modelVersionId).toMatch(UUID);
  // Refuse anything but a disposable end-to-end database.
  const name = new URL(DATABASE_URL).pathname.replace(/^\//, "");
  if (!/e2e/i.test(name)) throw new Error(`refusing to change the database "${name}": its name must contain "e2e"`);
  execFileSync(process.env.DCLAB_E2E_PSQL ?? "psql", [
    DATABASE_URL, "-v", "ON_ERROR_STOP=1", "-c",
    `UPDATE artifacts SET metadata = metadata - 'package_format' WHERE id = (SELECT model_artifact_id FROM model_versions WHERE id = '${modelVersionId}')`,
  ], { stdio: "pipe" });
}

const BANNED = /release|champion|digest|package|manifest|holdout|idempotency/i;

async function checkA11y(page: Page, name: string): Promise<void> {
  for (const scheme of ["light", "dark"] as const) {
    await page.emulateMedia({ colorScheme: scheme });
    expect(await axeViolations(page), `${name} ${scheme}`).toEqual([]);
    await shot(page, `releases-${name}-${scheme}`);
  }
  await page.emulateMedia({ colorScheme: "light" });
}

test("approve a model for scoring, go back to the previous one, and refusals are plain sentences", async ({ page, browser }) => {
  await login(page);
  const { projectId, experimentId } = await trainedProject(page, "Releases");
  const branchId = await branchRun(page, experimentId);
  const first = await modelOf(page, projectId, experimentId);
  const second = await modelOf(page, projectId, branchId);

  // The first run's model is in use by the first-run rule: no approval is invented.
  await page.goto(`/projects/${projectId}/models?tab=approved`);
  const panel = page.getByRole("tabpanel", { name: "Approved for scoring" });
  const summary = panel.getByTestId("approval-summary");
  await expect(summary).toContainText(`${first.name} is the model in use, but no approval is recorded`);
  await expect(panel.getByText("No model has been approved for scoring yet.")).toBeVisible();
  await expect(panel).toContainText("only scoring with the model in use is checked against its approval");
  await checkA11y(page, "0-none");

  // Approve the second model: one confirm step that says exactly what changes; focus moves into it.
  await panel.getByRole("button", { name: `Approve for scoring: ${second.name}` }).click();
  const confirm = panel.getByRole("form", { name: `Approve ${second.name} for scoring?` });
  await expect(confirm.getByRole("heading", { name: `Approve ${second.name} for scoring?` })).toBeFocused();
  await expect(confirm).toContainText(`${second.name} becomes the model in use and its features move with it`);
  await expect(confirm).toContainText(`${first.name} stops being the model in use.`);
  await expect(confirm).toContainText("Predictions already made are not changed.");
  await expect(confirm).toContainText("Nothing is retrained or tested again.");
  await expect(confirm).toContainText("a saved model file exists in a format DCLab can score with");
  await expect(confirm).toContainText("Weekly scoring, if it is on, uses the model in use when it is approved: see the Weekly scoring card on the Predictions screen.");
  await expect(panel).not.toContainText(BANNED);
  await checkA11y(page, "1-confirm");
  // Cancel returns focus to the row and the buttons come back.
  await confirm.getByRole("button", { name: "Cancel" }).click();
  await expect(page.locator(`#approve-row-${second.id}`)).toBeFocused();
  await panel.getByRole("button", { name: `Approve for scoring: ${second.name}` }).click();
  await confirm.getByLabel("Why (optional, saved with the change)").fill("Better recall with balanced weights");
  await confirm.getByRole("button", { name: `Yes, approve ${second.name}` }).click();
  await expect(panel.getByRole("status").filter({ hasText: `${second.name} is now the model in use and approved for scoring.` })).toBeVisible();
  await expect(summary).toBeFocused();
  await expect(summary).toContainText(`${second.name} is the model in use. It was approved for scoring by you on`);
  const list = panel.getByRole("list", { name: "Approvals, newest first" });
  await expect(list.getByRole("listitem").first()).toContainText(second.name);
  await expect(list.getByRole("listitem").first()).toContainText("In use now");
  await expect(list.getByRole("listitem").first()).toContainText("Reason: Better recall with balanced weights");

  // Approve the first model too (no reason: recorded as none given), then go back to the second.
  await panel.getByRole("button", { name: `Approve for scoring: ${first.name}` }).click();
  await panel.getByRole("button", { name: `Yes, approve ${first.name}` }).click();
  await expect(summary).toContainText(`${first.name} is the model in use.`);
  await expect(list.getByRole("listitem").first()).toContainText("(no reason given)");
  await expect(list.getByRole("listitem").nth(1)).toContainText("Earlier approval by you on");
  await expect(list.getByRole("listitem").nth(1)).toContainText("Not the model in use now.");

  // The API's real concurrent-write refusal is 409 concurrent_conflict (retryable): a plain sentence, and the retry keeps the SAME key.
  const keys: string[] = [];
  await page.route("**/api/backend/v1/projects/*/refs/champion_model", (route) => {
    if (route.request().method() !== "POST") return route.continue();
    keys.push(route.request().headers()["idempotency-key"] ?? "");
    return route.fulfill({ status: 409, contentType: "application/json", body: JSON.stringify({ error: { code: "concurrent_conflict", message: "a concurrent request conflicted; retry", retryable: true, details: {} } }) });
  });
  await panel.getByRole("button", { name: `Go back to this model: ${second.name}` }).click();
  const retry = panel.getByRole("form", { name: `Go back to ${second.name}?` });
  await retry.getByRole("button", { name: `Yes, go back to ${second.name}` }).click();
  await expect(retry.getByRole("alert")).toContainText("Another change was being saved");
  await retry.getByRole("button", { name: `Yes, go back to ${second.name}` }).click();
  await expect.poll(() => keys.length).toBe(2);
  expect(keys[0]).toBeTruthy();
  expect(keys[1]).toBe(keys[0]);
  await page.unroute("**/api/backend/v1/projects/*/refs/champion_model");
  await retry.getByRole("button", { name: "Cancel" }).click();

  // A conflict (someone else changed the model in use) is one plain sentence (stubbed 412 from the API).
  await page.route("**/api/backend/v1/projects/*/refs/champion_model", (route) => (route.request().method() === "POST"
    ? route.fulfill({ status: 412, contentType: "application/json", body: JSON.stringify({ error: { code: "precondition_failed", message: "If-Match is stale", retryable: false, details: {} } }) })
    : route.continue()));
  await panel.getByRole("button", { name: `Go back to this model: ${second.name}` }).click();
  const back = panel.getByRole("form", { name: `Go back to ${second.name}?` });
  await back.getByRole("button", { name: `Yes, go back to ${second.name}` }).click();
  await expect(back.getByRole("alert")).toContainText("Someone else just changed the model in use");
  await expect(back).not.toContainText(/If-Match|stale/);
  await page.unroute("**/api/backend/v1/projects/*/refs/champion_model");
  await back.getByRole("button", { name: "Cancel" }).click();

  await panel.getByRole("button", { name: `Go back to this model: ${second.name}` }).click();
  await expect(back).toContainText("Predictions already made are not changed.");
  await back.getByRole("button", { name: `Yes, go back to ${second.name}` }).click();
  await expect(summary).toContainText(`${second.name} is the model in use.`);
  await expect(list.locator(":scope > li")).toHaveCount(3);
  await checkA11y(page, "2-went-back");
  await expect(panel).not.toContainText(BANNED);

  // No horizontal scroll on a phone.
  await page.setViewportSize({ width: 375, height: 812 });
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= document.documentElement.clientWidth)).toBe(true);
  await page.setViewportSize({ width: 1280, height: 900 });

  // A viewer reads the list but sees no approve / go back buttons.
  const viewerContext = await browser.newContext({ baseURL: new URL(page.url()).origin });
  const viewer = await viewerContext.newPage();
  await viewer.goto("/login");
  await viewer.getByLabel("Email").fill("viewer-a@verification.invalid");
  await viewer.getByLabel("Password").fill("VerificationOnly123!");
  await viewer.getByRole("button", { name: "Sign in" }).click();
  await expect(viewer).not.toHaveURL(/\/login/);
  await viewer.goto(`/projects/${projectId}/models?tab=approved`);
  const viewerPanel = viewer.getByRole("tabpanel", { name: "Approved for scoring" });
  await expect(viewerPanel.getByTestId("approval-summary")).toContainText(`${second.name} is the model in use.`);
  await expect(viewerPanel.getByText("Only people who can change this workspace can approve a model")).toBeVisible();
  await expect(viewerPanel.getByRole("button", { name: /Approve for scoring|Go back to this model/ })).toHaveCount(0);
  await viewerContext.close();

  // The model page summary restates the approval.
  await page.goto(`/projects/${projectId}/models/${second.id}`);
  await expect(page.getByText(/Yes, approved on/)).toBeVisible();

  // Predictions name the approved model that scored, following the scoring's status.
  await page.goto(`/projects/${projectId}/predictions`);
  const rows = churnCsv().split("\n").map((line) => line.split(",").slice(0, 4).join(",")).join("\n");
  await page.getByLabel("Scoring file").setInputFiles({ name: "next-month.csv", mimeType: "text/csv", buffer: Buffer.from(rows) });
  await page.getByRole("button", { name: "Score this file" }).click();
  await expect(page.getByText("done", { exact: true }).first()).toBeVisible({ timeout: 120_000 });
  await expect(page.getByTestId("scored-with").first()).toContainText(`Scored with: ${second.name} · approved`);
  const history = page.getByRole("table", { name: "Predictions run in this browser tab" });
  await expect(history.getByRole("columnheader", { name: "Approval used" })).toBeVisible();
  await expect(history).toContainText("approved ");
  await checkA11y(page, "3-scored");

  // A scoring refused because the approval no longer matches (stubbed read): nothing was scored, no approval claimed.
  await page.route("**/api/backend/v1/predictions/*", async (route) => {
    if (route.request().method() !== "GET" || route.request().url().endsWith("/download")) return route.continue();
    const response = await route.fetch();
    const body = (await response.json()) as Record<string, unknown>;
    await route.fulfill({ response, json: { ...body, status: "failed", error_code: "release_mismatch", error_message: "the stored model no longer matches the version approved for scoring", output: null, rows_out: null, completed_at: null } });
  });
  await page.reload();
  // The table row now repeats the reason a failed scoring failed, so the sentence appears twice.
  await expect(page.getByText("The saved model no longer matches the model that was approved").first()).toBeVisible();
  await expect(page.getByTestId("scored-with").first()).toHaveText(`Was to score with: ${second.name}.`);
  await expect(page.getByRole("table", { name: "Predictions run in this browser tab" })).toContainText("none (nothing was scored)");
  await expect(page.locator("main")).not.toContainText(/digest|manifest|package|approve it again/i);
  await shot(page, "releases-3b-mismatch-light");
  await page.unroute("**/api/backend/v1/predictions/*");

  // A model saved in the old format cannot be approved: the API's 422 is one plain sentence with what to do.
  markOldFormat(first.id);
  await page.goto(`/projects/${projectId}/models?tab=approved`);
  await panel.getByRole("button", { name: `Go back to this model: ${first.name}` }).click();
  await panel.getByRole("button", { name: `Yes, go back to ${first.name}` }).click();
  const refused = panel.getByRole("alert").filter({ hasText: "This model was saved in the old format" });
  await expect(refused).toContainText("can't be approved for scoring until it is trained again");
  await expect(summary).toContainText(`${second.name} is the model in use.`);
  await expect(list.locator(":scope > li")).toHaveCount(3);
  await expect(panel).not.toContainText(BANNED);
  await checkA11y(page, "4-old-format");
});
