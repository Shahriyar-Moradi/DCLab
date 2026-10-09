import { expect, test, type Page } from "@playwright/test";
import path from "node:path";

/** P4.4-A: branch -> compare -> accept (make champion) -> the decision appears, plus conflict and supersede paths. */
const EMAIL = process.env.DCLAB_E2E_STUDIO_EMAIL ?? "business-admin-a@verification.invalid";
const PASSWORD = process.env.DCLAB_E2E_STUDIO_PASSWORD ?? "VerificationOnly123!";
const AXE = path.resolve(__dirname, "../node_modules/axe-core/axe.min.js");

function churnCsv(): string {
  const rows = ["tenure,monthly_spend,customer_code,plan,churn"];
  for (let i = 0; i < 300; i += 1) {
    const tenure = 1 + ((i * 13) % 72);
    const churn = tenure < 12 && i % 5 !== 0 ? "yes" : "no";
    rows.push(`${tenure},${i % 20 === 0 ? "" : (10 + ((i * 17) % 110)).toFixed(2)},C${1000 + i},${["basic", "plus", "pro"][i % 3]},${churn}`);
  }
  return `${rows.join("\n")}\n`;
}

async function login(page: Page): Promise<void> {
  await page.goto("/login");
  await page.getByLabel("Email").fill(EMAIL);
  await page.getByLabel("Password").fill(PASSWORD);
  await page.getByRole("button", { name: "Sign in" }).click();
  await expect(page).not.toHaveURL(/\/login/);
}

async function axeViolations(page: Page): Promise<string[]> {
  await page.addScriptTag({ path: AXE });
  return page.evaluate(async () => {
    const axe = (window as unknown as { axe: { run: (ctx: Document, opts: object) => Promise<{ violations: Array<{ id: string; impact?: string; nodes: unknown[] }> }> } }).axe;
    const result = await axe.run(document, { runOnly: { type: "tag", values: ["wcag2a", "wcag2aa"] } });
    return result.violations.filter((v) => v.impact === "serious" || v.impact === "critical").map((v) => `${v.id} (${v.nodes.length})`);
  });
}

async function shot(page: Page, name: string): Promise<void> {
  const dir = process.env.DCLAB_E2E_SHOTS;
  if (dir) await page.screenshot({ path: path.join(dir, `compare-${name}.png`), fullPage: true });
}

async function completed(page: Page, experimentId: string): Promise<void> {
  await expect.poll(async () => {
    const run = await page.request.get(`/api/backend/v1/experiments/${experimentId}`);
    return ((await run.json()) as { status: string }).status;
  }, { timeout: 170_000, intervals: [2_000] }).toBe("completed");
}

test("branch, compare, accept: the champion decision appears", async ({ page }) => {
  await login(page);
  await page.goto("/projects/new");
  const name = `Compare ${Date.now()}`;
  await page.getByLabel("Project name").fill(name);
  await page.getByLabel("Data file").setInputFiles({ name: "churn.csv", mimeType: "text/csv", buffer: Buffer.from(churnCsv()) });
  await page.getByRole("button", { name: "Upload and continue" }).click();
  await expect(page.getByRole("table", { name: "Columns of the uploaded file" })).toContainText("customer_code");
  const projectId = (await page.request.get("/api/backend/v1/projects").then((r) => r.json()) as Array<{ id: string; name: string }>).find((p) => p.name === name)?.id;
  expect(projectId).toBeTruthy();

  await page.goto(`/projects/${projectId}/experiments/new`);
  await expect(page.getByLabel("Dataset in this project")).not.toHaveValue("");
  await page.getByRole("button", { name: "Continue" }).click();
  await page.getByLabel("Task").selectOption("binary");
  await page.getByLabel("Target column").selectOption("churn");
  await page.getByRole("button", { name: "Continue" }).click();
  await page.getByRole("button", { name: "Continue" }).click();
  await page.getByRole("button", { name: "Train" }).click();
  await expect(page).toHaveURL(/\/experiments\/[0-9a-f-]{36}$/);
  const rootId = page.url().split("/experiments/")[1];
  await completed(page, rootId);

  // The run page in plain words: its steps with the time each took, and the facts without raw ids as names.
  await page.goto(`/projects/${projectId}/experiments/${rootId}`);
  await expect(page.getByRole("heading", { name: "Steps of this run" })).toBeVisible();
  const steps = page.getByRole("table", { name: "Steps of this run and the time each took" });
  await expect(steps).toContainText("Train the models, fold by fold");
  await expect(steps).toContainText("Final test, once");
  await expect(steps.getByRole("row", { name: /Final test, once/ })).not.toContainText(/\d\.\d{3,}/);
  await expect(page.getByRole("heading", { name: "About this run" })).toBeVisible();
  await expect(page.getByText("Column to predict").first()).toBeVisible();
  await expect(page.getByRole("heading", { name: "Trust checks", exact: true }).first()).toBeVisible();
  await expect(page.locator("main")).not.toContainText(/Fingerprint|Critic review|Split plan|Feature recipe|champion/);
  for (const scheme of ["light", "dark"] as const) {
    await page.emulateMedia({ colorScheme: scheme });
    expect(await axeViolations(page)).toEqual([]);
    await shot(page, `0-run-${scheme}`);
  }
  await page.emulateMedia({ colorScheme: "light" });

  // A run in progress offers Cancel, a finished one does not (checked on the root later).
  // Branch: an invalid change set is refused by the API with its own message and starts nothing.
  await page.goto(`/projects/${projectId}/experiments/${rootId}`);
  const branch = page.getByRole("region", { name: "Try a change" }).or(page.locator("section", { has: page.getByRole("heading", { name: "Try a change" }) }));
  await expect(branch).toBeVisible();
  await expect(page.getByRole("button", { name: "Start the new run" })).toBeDisabled();
  await page.getByLabel("Why are you trying this change?", { exact: false }).fill("Try balanced class weights");
  await page.getByLabel("What to change", { exact: false }).selectOption("family_include");
  await page.getByRole("textbox", { name: "Model family" }).fill("no_such_family");
  await page.getByRole("button", { name: "Start the new run" }).click();
  await expect(page.getByRole("alert").filter({ hasText: "refused" }).first()).toBeVisible();
  expect(await axeViolations(page)).toEqual([]);
  await shot(page, "1-branch-refused");

  // A valid change set starts a branch and lands on its page.
  await page.getByLabel("What to change", { exact: false }).selectOption("class_weighting");
  await page.getByRole("combobox", { name: /^Class weights/ }).selectOption("balanced");
  await expect(page.getByLabel("Change set that will be sent")).toContainText("balanced");
  await page.getByRole("button", { name: "Start the new run" }).click();
  await page.waitForURL((url) => /\/experiments\/[0-9a-f-]{36}$/.test(url.pathname) && !url.pathname.endsWith(rootId));
  const branchId = page.url().split("/experiments/")[1];
  expect(branchId).not.toBe(rootId);
  await expect(page.getByRole("button", { name: "Cancel run" }).or(page.getByText("completed").first())).toBeVisible();
  await completed(page, branchId);

  // Compare from the list: tick both, open the comparison.
  await page.goto(`/projects/${projectId}/experiments`);
  const runs = page.getByRole("table", { name: "Runs" });
  for (const header of ["Model", "What changed", "Cross-validation score ± spread", "Beats the baseline?", "Trust checks"]) await expect(runs.getByRole("columnheader", { name: header })).toBeVisible();
  // The list shows the API's own numbers: the cross-validation score in natural units and the baseline answer.
  const detail = await page.request.get(`/api/backend/v1/experiments/${rootId}`).then((r) => r.json()) as { metrics: { selection_metric: string; cv: Record<string, number>; holdout?: Record<string, number>; baseline_comparison: { beats_baseline: boolean } | null } };
  const rootRow = runs.getByRole("row").filter({ hasText: "Started from scratch" });
  await expect(rootRow).toContainText(detail.metrics.cv[detail.metrics.selection_metric].toFixed(2));
  await expect(rootRow).toContainText(detail.metrics.baseline_comparison?.beats_baseline ? "Yes" : detail.metrics.baseline_comparison ? "No" : "Not recorded");
  const listText = await page.locator("main").innerText();
  for (const value of Object.values(detail.metrics.holdout ?? {})) if (typeof value === "number") expect(listText).not.toContain(value.toFixed(4));
  await expect(runs).toContainText("Balanced class weights");
  await expect(runs).toContainText("Based on Run 1");
  await expect(runs).toContainText(/✓/);
  await expect(runs).not.toContainText(/E\d|SP-\d|fingerprint/i);
  await page.getByLabel("Show").selectOption("failed");
  await expect(page.getByText("No run matches this filter.")).toBeVisible();
  await page.getByLabel("Show").selectOption("all");
  for (const scheme of ["light", "dark"] as const) {
    await page.emulateMedia({ colorScheme: scheme });
    expect(await axeViolations(page)).toEqual([]);
    await shot(page, `1b-list-${scheme}`);
  }
  await page.emulateMedia({ colorScheme: "light" });
  // A phone-width screen does not scroll the page sideways (the table scrolls inside its own keyboard-focusable box).
  await page.setViewportSize({ width: 390, height: 800 });
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= document.documentElement.clientWidth)).toBe(true);
  await page.goto(`/projects/${projectId}/experiments/${rootId}`);
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= document.documentElement.clientWidth)).toBe(true);
  await page.setViewportSize({ width: 1280, height: 720 });
  await page.goto(`/projects/${projectId}/experiments`);
  await page.getByRole("checkbox", { name: /Try balanced class weights to compare/ }).check();
  await page.getByRole("checkbox", { name: /Select Run 1\b.* to compare/ }).check().catch(async () => {
    await page.getByRole("checkbox").nth(1).check();
  });
  await page.getByRole("button", { name: /Compare selected \(2\)/ }).click();
  await expect(page).toHaveURL(new RegExp("/experiments/compare\\?ids="));
  await expect(page.getByText("Comparable.")).toBeVisible();
  const table = page.getByRole("table", { name: "Cross-validation metric comparison" });
  await expect(table).toBeVisible();
  await expect(page.getByText("Based on", { exact: true }).first()).toBeVisible();
  await expect(page.getByText(/Balanced class weights/).first()).toBeVisible();
  const compared = await page.request.get(`/api/backend/v1/experiments/compare?ids=${rootId},${branchId}`).then((r) => r.json());
  expect(compared.split_plan_id).toBeTruthy();
  expect(await page.locator("main, body").first().innerText()).not.toMatch(/holdout_/i);
  for (const scheme of ["light", "dark"] as const) {
    await page.emulateMedia({ colorScheme: scheme });
    expect(await axeViolations(page)).toEqual([]);
    await shot(page, `2-compare-${scheme}`);
  }
  await page.emulateMedia({ colorScheme: "light" });

  // Accept the branch: it becomes the champion; one decision is recorded.
  const decide = page.locator("section", { has: page.getByRole("heading", { name: /Use the model of .*Try balanced class weights/ }) }).first();
  await decide.getByRole("button", { name: /Put this model in use/ }).click();
  await decide.getByLabel("Why (saved with the change)").fill("Balanced weights improved recall on CV");
  await decide.getByRole("button", { name: "Put in use" }).click();
  await expect(page.getByText("This model is now in use and the change was saved in History.")).toBeVisible();
  const refs = await page.request.get(`/api/backend/v1/projects/${projectId}/refs`).then((r) => r.json()) as { items: Array<{ ref_kind: string; version: number }> };
  expect(refs.items.find((r) => r.ref_kind === "champion_model")?.version).toBeGreaterThanOrEqual(2);
  await shot(page, "3-accepted");

  await page.goto(`/projects/${projectId}/decisions`);
  const decisions = page.getByRole("table", { name: "History" });
  await expect(decisions).toContainText("Put a model in use");
  await decisions.getByRole("row", { name: /Put a model in use/ }).first().getByRole("button", { name: /^Open/ }).click();
  const drawer = page.locator("aside.graph-drawer");
  await expect(drawer).toContainText("Balanced weights improved recall on CV");
  await expect(drawer).toContainText("final test set (used once per run)");
  await expect(drawer.getByText("Change the version in use again; that adds a new entry.")).toBeVisible();
  await expect(drawer.getByRole("button", { name: "Correct" })).toHaveCount(0);
  for (const scheme of ["light", "dark"] as const) {
    await page.emulateMedia({ colorScheme: scheme });
    expect(await axeViolations(page)).toEqual([]);
    await shot(page, `4-decisions-${scheme}`);
  }
  await page.emulateMedia({ colorScheme: "light" });

  // Conflict: the API answers 412 (the ref moved under the open form) -> an explicit message, nothing is written.
  await page.goto(`/projects/${projectId}/experiments/${rootId}`);
  const champion = page.locator("section", { has: page.getByRole("heading", { name: "Model in use", exact: true }) }).first();
  await champion.getByRole("button", { name: /Put this model in use/ }).click();
  await champion.getByLabel("Why (saved with the change)").fill("Back to the first run");
  await page.route(`**/api/backend/v1/projects/${projectId}/refs/champion_model`, (route) => route.fulfill({
    status: 412, contentType: "application/json",
    body: JSON.stringify({ error: { code: "precondition_failed", message: "the champion_model ref changed", retryable: false, details: {} } }),
  }));
  await champion.getByRole("button", { name: "Put in use" }).click();
  await expect(champion.getByText("Someone else changed the model in use")).toBeVisible();
  await expect(champion.getByRole("button", { name: "Reload" })).toBeVisible();
  await page.unroute(`**/api/backend/v1/projects/${projectId}/refs/champion_model`);
  await shot(page, "5-champion-conflict");

  // Proposals: a person's proposal waits; Accept appends a record, which can then be superseded with a reason; Reject is terminal.
  const propose = async (reason: string) => {
    const csrf = (await page.context().cookies()).find((c) => c.name === "dclab_csrf")?.value ?? "";
    const r = await page.request.post(`/api/backend/v1/projects/${projectId}/decisions`, {
      headers: { "Idempotency-Key": `e2e-${reason.replace(/[^A-Za-z0-9]/g, "-")}-${Date.now()}`, "X-CSRF-Token": csrf, Origin: process.env.DCLAB_E2E_WEB_URL ?? "http://127.0.0.1:3001" },
      data: { action: "propose", decision_type: "experiment_accepted", subject: { kind: "experiment", id: branchId }, rationale: reason, evidence_refs: [{ kind: "experiment", id: branchId }] },
    });
    expect(r.status(), await r.text()).toBe(201);
  };
  await propose("Looks good to keep");
  await propose("Not convincing");
  await page.goto(`/projects/${projectId}/decisions`);
  await expect(page.getByText("2 suggestions are waiting for your answer.")).toBeVisible();
  const list = page.getByRole("table", { name: "History" });
  await list.getByRole("row", { name: /Accepted a run.*waiting for your answer/ }).first().getByRole("button", { name: /^Answer/ }).click();
  const drawer2 = page.locator("aside.graph-drawer");
  await drawer2.getByRole("button", { name: /^Accept/ }).click();
  await expect(drawer2.getByRole("button", { name: "Confirm accept" })).toBeDisabled();
  await drawer2.getByLabel("Reason (recorded)").fill("Agreed after review");
  await drawer2.getByRole("button", { name: "Confirm accept" }).click();
  await expect(drawer2.getByText("Accepted: a new entry was added.")).toBeVisible();
  await expect(page.getByText("1 suggestion is waiting for your answer.")).toBeVisible();
  await shot(page, "6-accepted-proposal");

  await list.getByRole("row", { name: /Accepted a run.*waiting for your answer/ }).first().getByRole("button", { name: /^Answer/ }).click();
  await drawer2.getByRole("button", { name: /^Reject/ }).click();
  await drawer2.getByLabel("Reason (recorded)").fill("Gain is within noise");
  await drawer2.getByRole("button", { name: "Confirm reject" }).click();
  await expect(drawer2.getByText("Rejected: a new entry was added.")).toBeVisible();

  // "Correct" is the plain word for superseding: it opens the form with a required reason.
  const accepted = list.getByRole("row", { name: /Accepted a run/ }).filter({ has: page.getByRole("button", { name: /^Correct/ }) }).first();
  await accepted.getByRole("button", { name: /^Correct/ }).click();
  await expect(drawer2.getByRole("button", { name: "Confirm correction" })).toBeDisabled();
  await drawer2.getByLabel("Reason (recorded)").fill("Corrected after a second look");
  await drawer2.getByRole("button", { name: "Confirm correction" }).click();
  await expect(drawer2.getByText("Corrected: a new entry replaces this one.")).toBeVisible();
  await shot(page, "7-superseded");

  // The rules' own records cannot be corrected by a person: no Correct button, in the row or in the drawer.
  const chosen = list.getByRole("row", { name: /Chose the best model of a run/ }).first();
  await expect(chosen).toContainText("Recorded by the rules; only the rules change it.");
  await expect(chosen.getByRole("button", { name: /^Correct/ })).toHaveCount(0);
  await chosen.getByRole("button", { name: /^Open/ }).click();
  await expect(drawer2.getByText("only the rules change it")).toBeVisible();
  await expect(drawer2.getByRole("button", { name: "Correct" })).toHaveCount(0);
  // Plain words on the page: no codes, no "supersede", no "engine", no "proposal".
  const banned = /supersede|engine|proposal|decision_|winner_locked|ref_moved|champion_promoted|experiment_accepted|holdout|\bUndo\b/i;
  await expect(list).not.toContainText(banned);
  await expect(drawer2).not.toContainText(banned);
});
