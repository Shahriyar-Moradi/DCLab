import { expect, test, type Page } from "@playwright/test";
import path from "node:path";

/** P4.1-B: CSV -> trained experiment through the New project wizard on the seeded stack. */
const EMAIL = process.env.DCLAB_E2E_STUDIO_EMAIL ?? "business-admin-a@verification.invalid";
const PASSWORD = process.env.DCLAB_E2E_STUDIO_PASSWORD ?? "VerificationOnly123!";
const AXE = path.resolve(__dirname, "../node_modules/axe-core/axe.min.js");

function churnCsv(): string {
  const rows = ["tenure,monthly_spend,support_calls,plan,churn"];
  for (let i = 0; i < 300; i += 1) {
    const calls = (i * 7) % 9;
    const tenure = 1 + ((i * 13) % 72);
    const churn = (calls > 4 || tenure < 8) && i % 5 !== 0 ? "yes" : "no";
    rows.push(`${tenure},${i % 20 === 0 ? "" : (10 + ((i * 17) % 110)).toFixed(2)},${calls},${["basic", "plus", "pro"][i % 3]},${churn}`);
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

/** Optional review screenshots: only when DCLAB_E2E_SHOTS names a directory outside the repository. */
async function shot(page: Page, name: string): Promise<void> {
  const dir = process.env.DCLAB_E2E_SHOTS;
  if (dir) await page.screenshot({ path: path.join(dir, `${name}.png`), fullPage: true });
}

test("New project wizard: CSV to a trained experiment", async ({ page }) => {
  await login(page);
  await page.goto("/projects/new");
  await expect(page.getByRole("heading", { name: "Create a project from your data", level: 1 })).toBeVisible();
  expect(await axeViolations(page)).toEqual([]);
  await shot(page, "1-data");

  // 1 Data: upload with a profile preview from the API.
  const name = `Wizard churn ${Date.now()}`;
  await page.getByLabel("Project name").fill(name);
  await page.getByLabel("Data file").setInputFiles({ name: "churn.csv", mimeType: "text/csv", buffer: Buffer.from(churnCsv()) });
  await page.getByRole("button", { name: "Upload and continue" }).click();
  await expect(page.getByRole("table", { name: "Columns of the uploaded file" })).toContainText("monthly_spend");
  await expect(page.getByRole("table", { name: "Columns of the uploaded file" })).toContainText("churn");
  expect(await axeViolations(page)).toEqual([]);
  await shot(page, "2-target");

  // 2 Target and task.
  await page.getByLabel("Task").selectOption("binary");
  await page.getByLabel("Target column").selectOption("churn");
  await page.getByRole("button", { name: "Continue" }).click();

  // 3 Objective (optional): metric and one constraint.
  await page.getByLabel("Primary metric").selectOption("pr_auc");
  await page.getByLabel("Constraint metric (optional)").selectOption("recall");
  await page.getByLabel("Value").fill("0.5");
  expect(await axeViolations(page)).toEqual([]);
  await shot(page, "3-objective");
  await page.getByRole("button", { name: "Continue" }).click();

  // 4 Train: a double click starts exactly one experiment.
  expect(await axeViolations(page)).toEqual([]);
  await shot(page, "4-train");
  await page.getByRole("button", { name: "Train" }).dblclick();
  await expect(page).toHaveURL(/\/projects\/[0-9a-f-]{36}\/experiments\/[0-9a-f-]{36}$/);
  const projectId = page.url().split("/projects/")[1].split("/")[0];

  // Live progress to a finished run.
  await expect(page.getByText("Experiment id")).toBeVisible();
  await expect(page.getByText("completed", { exact: true }).first()).toBeVisible({ timeout: 150_000 });

  await shot(page, "5-experiment");
  const listed = await page.request.get(`/api/backend/v1/experiments?project_id=${projectId}&limit=10`);
  expect(listed.ok()).toBeTruthy();
  expect(((await listed.json()) as { items: unknown[] }).items).toHaveLength(1);

  // Experiments tab: New run reuses the form with the dataset and answers prefilled.
  await page.goto(`/projects/${projectId}/experiments`);
  await page.getByRole("link", { name: "New run" }).click();
  await expect(page.getByLabel("Dataset in this project")).not.toHaveValue("");
  await page.getByRole("button", { name: "Continue" }).click();
  await expect(page.getByLabel("Target column (leave empty to let DCLab choose)")).toHaveValue("churn");
  expect(await axeViolations(page)).toEqual([]);
  await shot(page, "6-new-run");
});
