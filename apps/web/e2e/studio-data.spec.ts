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
  await page.getByRole("tab", { name: /^Columns/ }).click();
  await expect(page.getByText("No test design yet.")).toBeVisible();
  await expect(page.getByRole("table", { name: "Columns", exact: true })).toContainText("customer_code");
  const strip = page.getByRole("region", { name: "File summary" });
  await expect(strip).toContainText("300");
  await expect(strip).not.toContainText("What we predict");
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
  await page.getByRole("tab", { name: /^Columns/ }).click();
  await expect(page.getByText(/Counts are over the .* training rows/)).toBeVisible();
  await expect(page.getByText("Final test set rows are never counted.", { exact: false })).toBeVisible();
  const table = page.getByRole("table", { name: "Columns", exact: true });
  await expect(table).toContainText("ID column, not a predictor");
  await expect(table).toContainText("This is what we predict");
  await expect(page.getByRole("region", { name: "File summary" })).toContainText("What we predict");
  await expect(page.getByRole("region", { name: "File summary" })).toContainText("Columns used");
  await page.getByText("Technical details per column").click();
  const details = page.getByRole("table", { name: "Column details" });
  await expect(details).toContainText("identifier");
  await expect(details).toContainText("imputer:median");
  const pageText = await page.locator("main").innerText();
  expect(pageText).not.toMatch(/service token|\bscope\b|\bdigest\b|holdout|champion|ADR 0005|Split plan/i);
  expect(await axeViolations(page)).toEqual([]);
  await shot(page, "2-columns");

  await page.getByRole("tab", { name: /^Data checks/ }).click();
  await expect(page.getByRole("table", { name: "Data checks" })).toContainText("Duplicates");
  await expect(page.getByRole("table", { name: "Data checks" })).toContainText("Missing values");
  // An ID column is "not a predictor", not leakage: it is not listed as left out by the leakage plan.
  await expect(page.getByRole("table", { name: "Columns left out by the leakage plan" })).not.toContainText("customer_code");
  expect(await axeViolations(page)).toEqual([]);
  await shot(page, "3-leakage");

  // V7-A1 flow bar: with a finished run the data and goal steps are done and link to real pages; Goal opens the test-design page.
  const flow = page.getByRole("navigation", { name: "Project steps" });
  await expect(flow.getByRole("link", { name: "Data" })).toHaveAttribute("aria-current", "step");
  await expect(flow.getByRole("link", { name: "Goal & test design" })).toHaveAttribute("href", new RegExp(`/projects/${projectId}/splits/[0-9a-f-]{36}$`));
  await expect(flow.getByRole("link", { name: "Model" })).toBeVisible();
  const sidebarNav = page.getByRole("navigation", { name: "Studio" });
  await expect(sidebarNav.getByRole("link", { name: "Predictions" })).toHaveAttribute("href", new RegExp(`/projects/${projectId}/models/[0-9a-f-]{36}\\?tab=score$`));
  await sidebarNav.getByRole("link", { name: "Goal & test design" }).click();
  await expect(page.getByRole("heading", { name: "Goal & test design", level: 1 })).toBeVisible();
  await expect(page.getByRole("heading", { name: "How we test" })).toBeVisible();
  await expect(page.getByText("How we rank models")).toBeVisible();
  await expect(page.getByText("Why this design?")).toBeVisible();
  await expect(page.getByRole("heading", { name: "Who decided" })).toBeVisible();
  // No final test set score from the run appears on this page.
  const refsForCard = await page.request.get(`/api/backend/v1/projects/${projectId}/refs`).then((r) => r.json()) as { items: Array<{ ref_kind: string; target: { id: string } }> };
  const championId = refsForCard.items.find((ref) => ref.ref_kind === "champion_model")?.target.id;
  const card = await page.request.get(`/api/backend/v1/model-versions/${championId}/card`).then((r) => r.json()) as { final_evaluation?: unknown };
  const finalNumbers: number[] = [];
  const walk = (value: unknown): void => {
    if (typeof value === "number" && Math.abs(value) < 1 && /\.\d{4,}/.test(String(value))) finalNumbers.push(value);
    else if (value && typeof value === "object") Object.values(value).forEach(walk);
  };
  walk(card.final_evaluation);
  const goalText = await page.locator("main").innerText();
  for (const n of finalNumbers) expect(goalText).not.toContain(n.toFixed(3));
  expect(goalText).not.toMatch(/holdout|final_evaluation/i);
  expect(await axeViolations(page)).toEqual([]);
  await shot(page, "6-goal");
  await page.emulateMedia({ colorScheme: "dark" });
  expect(await axeViolations(page)).toEqual([]);
  await shot(page, "7-goal-dark");
  await page.goto(`/projects/${projectId}/data`);
  await page.getByRole("tab", { name: /^Columns/ }).click();
  await expect(page.getByRole("table", { name: "Columns", exact: true })).toBeVisible();
  expect(await axeViolations(page)).toEqual([]);
  await shot(page, "8-columns-dark");
  await page.getByRole("tab", { name: /^Data checks/ }).click();
  expect(await axeViolations(page)).toEqual([]);
  await page.emulateMedia({ colorScheme: "light" });
  await page.goto(`/projects/${projectId}/data`);
  await page.getByRole("tab", { name: /Versions/ }).click();
  await expect(page.getByRole("table", { name: "Data versions" })).toContainText("in use");
  await expect(page.getByRole("table", { name: "Data versions" })).toContainText("1 test design");
  await expect(page.getByRole("table", { name: "Data versions" })).toContainText("made by a run");
  await shot(page, "4-versions");

  await page.getByRole("tab", { name: /^Access/ }).click();
  await expect(page.getByText("Published for training, predictions and download by members of this workspace; not shared with other workspaces.")).toBeVisible();
  await expect(page.getByText("What AI help may see")).toBeVisible();
  await expect(page.getByText("never leave your workspace")).toHaveCount(0);
  await page.getByText("Technical details", { exact: true }).click();
  await expect(page.getByText("internal_training")).toBeVisible();
  await expect(page.getByText("Retention label (not yet enforced)")).toBeVisible();
  await expect(page.getByText("Storage label (not yet enforced)")).toBeVisible();
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
