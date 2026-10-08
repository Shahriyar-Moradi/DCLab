import { expect, test, type Page } from "@playwright/test";
import path from "node:path";

/** P4.2-A: the project Graph page — lineage, a stale marker after a ref move, the drawer impact list, the list fallback. */
const EMAIL = process.env.DCLAB_E2E_STUDIO_EMAIL ?? "business-admin-a@verification.invalid";
const PASSWORD = process.env.DCLAB_E2E_STUDIO_PASSWORD ?? "VerificationOnly123!";
const AXE = path.resolve(__dirname, "../node_modules/axe-core/axe.min.js");

function churnCsv(rows: number, offset = 0): string {
  const out = ["tenure,monthly_spend,customer_code,plan,churn"];
  for (let i = 0; i < rows; i += 1) {
    const tenure = 1 + (((i + offset) * 13) % 72);
    const churn = tenure < 12 && i % 5 !== 0 ? "yes" : "no";
    out.push(`${tenure},${(10 + (((i + offset) * 17) % 110)).toFixed(2)},C${1000 + i + offset},${["basic", "plus", "pro"][i % 3]},${churn}`);
  }
  return `${out.join("\n")}\n`;
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
  if (dir) await page.screenshot({ path: path.join(dir, `graph-${name}.png`), fullPage: true });
}

test("Graph page: lineage, stale marker after a dataset ref move, drawer impact, list fallback", async ({ page }) => {
  await login(page);
  await page.goto("/projects/new");
  const name = `Graph page ${Date.now()}`;
  await page.getByLabel("Project name").fill(name);
  await page.getByLabel("Data file").setInputFiles({ name: "churn.csv", mimeType: "text/csv", buffer: Buffer.from(churnCsv(300)) });
  await page.getByRole("button", { name: "Upload and continue" }).click();
  await page.getByLabel("Task").selectOption("binary");
  await page.getByLabel("Target column").selectOption("churn");
  await page.getByRole("button", { name: "Continue" }).click();
  await page.getByRole("button", { name: "Continue" }).click();
  await page.getByRole("button", { name: "Train" }).click();
  await expect(page).toHaveURL(/\/experiments\/[0-9a-f-]{36}$/);
  const projectId = page.url().split("/projects/")[1].split("/")[0];
  const experimentId = page.url().split("/experiments/")[1];
  await expect.poll(async () => {
    const run = await page.request.get(`/api/backend/v1/experiments/${experimentId}`);
    return ((await run.json()) as { status: string }).status;
  }, { timeout: 150_000, intervals: [2_000] }).toBe("completed");

  // After the first run: refs set, nothing stale.
  await page.goto(`/projects/${projectId}/graph`);
  await expect(page.getByRole("heading", { name: "Graph", level: 1 })).toBeVisible();
  await expect(page.getByText("nothing stale")).toBeVisible();
  const experimentNode = page.getByRole("button", { name: new RegExp(`^Experiment ${experimentId.slice(0, 8)}`) });
  await expect(experimentNode).toBeVisible();
  await expect(page.getByRole("button", { name: /^Model version .*ref champion/ })).toBeVisible();
  expect(await axeViolations(page)).toEqual([]);
  await shot(page, "1-fresh");

  // A second upload, then "Make current" on the Data page: the dataset ref moves.
  await page.goto(`/projects/${projectId}/experiments/new`);
  await expect(page.getByLabel("Dataset in this project")).not.toHaveValue(""); // prefilled from the latest run first
  await page.getByLabel("Dataset in this project").selectOption("");
  await page.getByLabel("Data file").setInputFiles({ name: "churn-q3.csv", mimeType: "text/csv", buffer: Buffer.from(churnCsv(320, 7)) });
  await page.getByRole("button", { name: "Upload and continue" }).click();
  await expect(page.getByLabel("Task")).toBeVisible();
  await expect(page.getByText("churn-q3", { exact: true })).toBeVisible();
  await page.goto(`/projects/${projectId}/data`);
  await page.getByRole("tab", { name: /Versions/ }).click();
  await page.getByRole("button", { name: /^Make current/ }).click();
  await page.getByLabel("Why (recorded with the decision)").fill("Newer quarter of data");
  await page.getByRole("button", { name: "Move the dataset ref" }).click();
  await expect(page.getByText(/\(version 2\)/)).toBeVisible();
  await expect(page.getByRole("row", { name: /churn-q3/ })).toContainText("current");

  // The graph marks the old run stale (orange + text); the drawer shows why and what becomes stale.
  await page.goto(`/projects/${projectId}/graph`);
  await expect(page.getByLabel("Graph summary")).toContainText("stale");
  const staleNode = page.getByRole("button", { name: new RegExp(`^Experiment ${experimentId.slice(0, 8)}.*, stale`) });
  await expect(staleNode).toBeVisible();
  await expect(staleNode).toContainText("stale");
  await staleNode.click();
  const drawer = page.getByRole("complementary", { name: /Experiment/ });
  await expect(drawer).toBeVisible();
  await expect(drawer).toContainText("data ref points at dataset version");
  await expect(drawer.getByRole("list", { name: "What becomes stale" })).toContainText("Model version");
  await expect(drawer).toContainText("No AI decision point is recorded for this experiment");
  await expect(drawer.getByRole("link", { name: "Open the experiment" })).toHaveAttribute("href", `/projects/${projectId}/experiments/${experimentId}`);
  expect(await axeViolations(page)).toEqual([]);
  await shot(page, "2-stale-drawer");

  // The superseded upload: its impact covers the split plan, the run and the model.
  await drawer.getByRole("button", { name: /^Dataset version/ }).first().click();
  await expect(page.getByRole("complementary", { name: /Dataset version/ }).getByRole("list", { name: "What becomes stale" })).toContainText("Split plan");
  await page.keyboard.press("Escape");
  await expect(page.getByRole("complementary", { name: /Dataset version/ })).toHaveCount(0);

  // Accessible list fallback.
  await page.getByRole("tab", { name: /As a list/ }).click();
  const experiments = page.getByRole("table", { name: "Experiment nodes" });
  await expect(experiments).toContainText("stale");
  await expect(experiments).toContainText("trained on dataset version");
  await expect(page.getByRole("table", { name: "Model version nodes" })).toContainText("★ champion");
  expect(await axeViolations(page)).toEqual([]);
  await shot(page, "3-list");

  // Dark theme: same checks with the drawer open and on the list.
  await page.emulateMedia({ colorScheme: "dark" });
  expect(await axeViolations(page)).toEqual([]);
  await shot(page, "4-list-dark");
  await page.getByRole("tab", { name: /^Graph/ }).click();
  await staleNode.click();
  await expect(page.getByRole("complementary", { name: /Experiment/ })).toBeVisible();
  expect(await axeViolations(page)).toEqual([]);
  await shot(page, "5-drawer-dark");
});
