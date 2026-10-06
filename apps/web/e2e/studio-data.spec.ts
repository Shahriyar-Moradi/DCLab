import { expect, test, type Page } from "@playwright/test";
import path from "node:path";

/** P4.1-C: the project Data page before and after the first run, on the seeded stack. */
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
  if (dir) await page.screenshot({ path: path.join(dir, `data-${name}.png`), fullPage: true });
}

test("Data page: names only before a split, training-row profile after the first run", async ({ page }) => {
  await login(page);
  await page.goto("/projects/new");
  const name = `Data page ${Date.now()}`;
  await page.getByLabel("Project name").fill(name);
  await page.getByLabel("Data file").setInputFiles({ name: "churn.csv", mimeType: "text/csv", buffer: Buffer.from(churnCsv()) });
  await page.getByRole("button", { name: "Upload and continue" }).click();
  await expect(page.getByRole("table", { name: "Columns of the uploaded file" })).toContainText("customer_code");
  const projectId = (await page.request.get("/api/backend/v1/projects").then((r) => r.json()) as Array<{ id: string; name: string }>)
    .find((p) => p.name === name)?.id;
  expect(projectId).toBeTruthy();

  // Before any run: no split plan, so names and types only (statistics withheld).
  await page.goto(`/projects/${projectId}/data`);
  await expect(page.getByRole("heading", { name: "Data", level: 1 })).toBeVisible();
  await page.getByRole("tab", { name: /Columns & roles/ }).click();
  await expect(page.getByText("No split plan yet.")).toBeVisible();
  await expect(page.getByRole("table", { name: "Columns and roles" })).toContainText("customer_code");
  expect(await axeViolations(page)).toEqual([]);
  await shot(page, "1-before-run");

  // Train through New run (target picked from the profile's column dropdown).
  await page.goto(`/projects/${projectId}/experiments/new`);
  await expect(page.getByLabel("Dataset in this project")).not.toHaveValue("");
  await page.getByRole("button", { name: "Continue" }).click();
  await page.getByLabel("Task").selectOption("binary");
  await page.getByLabel("Target column").selectOption("churn");
  await page.getByRole("button", { name: "Continue" }).click();
  await page.getByRole("button", { name: "Continue" }).click();
  await page.getByRole("button", { name: "Train" }).click();
  await expect(page).toHaveURL(/\/experiments\/[0-9a-f-]{36}$/);
  const experimentId = page.url().split("/experiments/")[1];
  await expect.poll(async () => {
    const run = await page.request.get(`/api/backend/v1/experiments/${experimentId}`);
    return ((await run.json()) as { status: string }).status;
  }, { timeout: 150_000, intervals: [2_000] }).toBe("completed");

  // After the run: statistics over the split plan's training rows, rule role beside role used.
  await page.goto(`/projects/${projectId}/data`);
  await page.getByRole("tab", { name: /Columns & roles/ }).click();
  await expect(page.getByText(/training rows/).first()).toBeVisible();
  await expect(page.getByText("Holdout rows are never counted.", { exact: false })).toBeVisible();
  const table = page.getByRole("table", { name: "Columns and roles" });
  await expect(table).toContainText("identifier");
  await expect(table).toContainText("target");
  await expect(table).toContainText("imputer:median");
  await expect(page.getByText("AI (Jev) role answers appear here when a decision point is promoted.")).toBeVisible();
  expect(await axeViolations(page)).toEqual([]);
  await shot(page, "2-columns");

  await page.getByRole("tab", { name: /Leakage audit/ }).click();
  await expect(page.getByRole("table", { name: "Columns excluded by the leakage plan" })).toContainText("customer_code");
  expect(await axeViolations(page)).toEqual([]);
  await shot(page, "3-leakage");

  await page.getByRole("tab", { name: /Versions/ }).click();
  await expect(page.getByRole("table", { name: "Dataset versions" })).toContainText("current");
  await expect(page.getByRole("table", { name: "Dataset versions" })).toContainText("1 split plan");
  await expect(page.getByRole("table", { name: "Dataset versions" })).toContainText("prepared by a run");
  await shot(page, "4-versions");

  await page.getByRole("tab", { name: /Policy & access/ }).click();
  await expect(page.getByText("internal_training")).toBeVisible();
  await expect(page.getByText("Data class for AI")).toBeVisible();
  expect(await axeViolations(page)).toEqual([]);
  await shot(page, "5-policy");

  // The profile read itself never carries holdout fields.
  const refs = await page.request.get(`/api/backend/v1/projects/${projectId}/refs`).then((r) => r.json()) as { items: Array<{ ref_kind: string; target: { id: string } }> };
  const datasetId = refs.items.find((ref) => ref.ref_kind === "dataset")?.target.id;
  const profile = await page.request.get(`/api/backend/v1/datasets/${datasetId}/profile`);
  expect(profile.ok()).toBeTruthy();
  const body = await profile.json();
  expect(body.scope).toBe("training_rows");
  expect(JSON.stringify(body)).not.toMatch(/holdout|final_test/i);
});
