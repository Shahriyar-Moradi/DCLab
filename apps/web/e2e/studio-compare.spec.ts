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

  // A run in progress offers Cancel, a finished one does not (checked on the root later).
  // Branch: an invalid change set is refused by the API with its own message and starts nothing.
  await page.goto(`/projects/${projectId}/experiments/${rootId}`);
  const branch = page.getByRole("region", { name: "Branch this experiment" }).or(page.locator("section", { has: page.getByRole("heading", { name: "Branch this experiment" }) }));
  await expect(branch).toBeVisible();
  await expect(page.getByRole("button", { name: "Start branch" })).toBeDisabled();
  await page.getByLabel("Why are you branching?", { exact: false }).fill("Try balanced class weights");
  await page.getByLabel("Kind of change", { exact: false }).selectOption("family_include");
  await page.getByRole("textbox", { name: "Model family" }).fill("no_such_family");
  await page.getByRole("button", { name: "Start branch" }).click();
  await expect(page.getByRole("alert").filter({ hasText: "refused" }).first()).toBeVisible();
  expect(await axeViolations(page)).toEqual([]);
  await shot(page, "1-branch-refused");

  // A valid change set starts a branch and lands on its page.
  await page.getByLabel("Kind of change", { exact: false }).selectOption("class_weighting");
  await page.getByRole("combobox", { name: /^Mode/ }).selectOption("balanced");
  await expect(page.getByLabel("Change set that will be sent")).toContainText("balanced");
  await page.getByRole("button", { name: "Start branch" }).click();
  await page.waitForURL((url) => /\/experiments\/[0-9a-f-]{36}$/.test(url.pathname) && !url.pathname.endsWith(rootId));
  const branchId = page.url().split("/experiments/")[1];
  expect(branchId).not.toBe(rootId);
  await expect(page.getByRole("button", { name: "Cancel run" }).or(page.getByText("completed").first())).toBeVisible();
  await completed(page, branchId);

  // Compare from the list: tick both, open the comparison.
  await page.goto(`/projects/${projectId}/experiments`);
  await page.getByRole("checkbox", { name: /Try balanced class weights to compare/ }).check();
  await page.getByRole("checkbox", { name: new RegExp(`experiment ${rootId.slice(0, 8)} to compare`) }).check().catch(async () => {
    await page.getByRole("checkbox").nth(1).check();
  });
  await page.getByRole("button", { name: /Compare selected \(2\)/ }).click();
  await expect(page).toHaveURL(new RegExp("/experiments/compare\\?ids="));
  await expect(page.getByText("Comparable.")).toBeVisible();
  const table = page.getByRole("table", { name: "Cross-validation metric comparison" });
  await expect(table).toBeVisible();
  await expect(page.getByText("Cost", { exact: true }).first()).toBeVisible();
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
  const decide = page.locator("section", { has: page.getByRole("heading", { name: "Accept Run A" }) }).first();
  await decide.getByRole("button", { name: /Make champion/ }).click();
  await decide.getByLabel("Why (recorded with the decision)").fill("Balanced weights improved recall on CV");
  await decide.getByRole("button", { name: "Move the champion ref" }).click();
  await expect(page.getByText("The champion ref moved and one decision was recorded.")).toBeVisible();
  const refs = await page.request.get(`/api/backend/v1/projects/${projectId}/refs`).then((r) => r.json()) as { items: Array<{ ref_kind: string; version: number }> };
  expect(refs.items.find((r) => r.ref_kind === "champion_model")?.version).toBeGreaterThanOrEqual(2);
  await shot(page, "3-accepted");

  await page.goto(`/projects/${projectId}/decisions`);
  const decisions = page.getByRole("table", { name: "Decisions" });
  await expect(decisions).toContainText("Champion promoted");
  await decisions.getByRole("row", { name: /Champion promoted/ }).first().getByRole("button").click();
  const drawer = page.locator("aside.graph-drawer");
  await expect(drawer).toContainText("Balanced weights improved recall on CV");
  await expect(drawer).toContainText("final holdout");
  await expect(drawer.getByText("A ref move is corrected by moving the ref again")).toBeVisible();
  for (const scheme of ["light", "dark"] as const) {
    await page.emulateMedia({ colorScheme: scheme });
    expect(await axeViolations(page)).toEqual([]);
    await shot(page, `4-decisions-${scheme}`);
  }
  await page.emulateMedia({ colorScheme: "light" });

  // Conflict: the API answers 412 (the ref moved under the open form) -> an explicit message, nothing is written.
  await page.goto(`/projects/${projectId}/experiments/${rootId}`);
  const champion = page.locator("section", { has: page.getByRole("heading", { name: "Champion", exact: true }) }).first();
  await champion.getByRole("button", { name: /Make champion/ }).click();
  await champion.getByLabel("Why (recorded with the decision)").fill("Back to the first run");
  await page.route(`**/api/backend/v1/projects/${projectId}/refs/champion_model`, (route) => route.fulfill({
    status: 412, contentType: "application/json",
    body: JSON.stringify({ error: { code: "precondition_failed", message: "the champion_model ref changed", retryable: false, details: {} } }),
  }));
  await champion.getByRole("button", { name: "Move the champion ref" }).click();
  await expect(champion.getByText("Someone else moved the champion")).toBeVisible();
  await expect(champion.getByRole("button", { name: "Reload" })).toBeVisible();
  await page.unroute(`**/api/backend/v1/projects/${projectId}/refs/champion_model`);
  await shot(page, "5-champion-conflict");

  // Proposals: a person's proposal waits; Accept appends a record, which can then be superseded with a reason; Reject is terminal.
  const propose = async (reason: string) => {
    const csrf = (await page.context().cookies()).find((c) => c.name === "dclab_csrf")?.value ?? "";
    const r = await page.request.post(`/api/backend/v1/projects/${projectId}/decisions`, {
      headers: { "Idempotency-Key": `e2e-${reason.replace(/[^A-Za-z0-9]/g, "-")}-${Date.now()}`, "X-CSRF-Token": csrf },
      data: { action: "propose", decision_type: "experiment_accepted", subject: { kind: "experiment", id: branchId }, rationale: reason, evidence_refs: [{ kind: "experiment", id: branchId }] },
    });
    expect(r.status(), await r.text()).toBe(201);
  };
  await propose("Looks good to keep");
  await propose("Not convincing");
  await page.goto(`/projects/${projectId}/decisions`);
  await expect(page.getByText("2 proposals are waiting for a decision.")).toBeVisible();
  const list = page.getByRole("table", { name: "Decisions" });
  await list.getByRole("row", { name: /Experiment accepted.*proposed/ }).first().getByRole("button").click();
  const drawer2 = page.locator("aside.graph-drawer");
  await drawer2.getByRole("button", { name: /^Accept/ }).click();
  await expect(drawer2.getByRole("button", { name: "Confirm accept" })).toBeDisabled();
  await drawer2.getByLabel("Reason (recorded)").fill("Agreed after review");
  await drawer2.getByRole("button", { name: "Confirm accept" }).click();
  await expect(drawer2.getByText("Accepted: a new record was appended.")).toBeVisible();
  await expect(page.getByText("1 proposal is waiting for a decision.")).toBeVisible();
  await shot(page, "6-accepted-proposal");

  await list.getByRole("row", { name: /Experiment accepted.*proposed/ }).first().getByRole("button").click();
  await drawer2.getByRole("button", { name: /^Reject/ }).click();
  await drawer2.getByLabel("Reason (recorded)").fill("Gain is within noise");
  await drawer2.getByRole("button", { name: "Confirm reject" }).click();
  await expect(drawer2.getByText("Rejected: a new record was appended.")).toBeVisible();

  const accepted = list.getByRole("row", { name: /Experiment accepted.*accepted/ }).first();
  await accepted.getByRole("button").click();
  await drawer2.getByRole("button", { name: "Supersede" }).click();
  await expect(drawer2.getByRole("button", { name: "Confirm supersede" })).toBeDisabled();
  await drawer2.getByLabel("Reason (recorded)").fill("Corrected after a second look");
  await drawer2.getByRole("button", { name: "Confirm supersede" }).click();
  await expect(drawer2.getByText("Corrected: a new record supersedes this one.")).toBeVisible();
  await shot(page, "7-superseded");

  // The engine's own records cannot be corrected by a person: no Supersede is offered.
  await list.getByRole("row", { name: /Winner locked/ }).first().getByRole("button").click();
  await expect(drawer2.getByText("only the engine corrects it")).toBeVisible();
  await expect(drawer2.getByRole("button", { name: "Supersede" })).toHaveCount(0);
});
