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
  await expect(page.getByRole("heading", { name: "Lineage", level: 1 })).toBeVisible();
  await expect(page.getByText("nothing built on an older version")).toBeVisible();
  const experimentNode = page.getByRole("button", { name: /^Run 1\b/ });
  await expect(experimentNode).toBeVisible();
  await expect(page.getByRole("button", { name: /^Model v1.*in use/ })).toBeVisible();
  // Plain words: items are named (Run 1, Model v1, Test design 1, a file name), never "Experiment 1a2b3c4d" or "Dataset version".
  await expect(page.locator("main")).not.toContainText(/Dataset version|Split plan|Feature recipe|Problem spec|Experiment [0-9a-f]{8}|decision point|\bnodes?\b|\bedges?\b|\brefs?\b/i);
  await expect(page.getByRole("button", { name: /^Test design 1/ })).toBeVisible();
  expect(await axeViolations(page)).toEqual([]);
  await shot(page, "1-fresh");

  // A second upload, then "Use this data" on the Data page: the data in use changes.
  await page.goto(`/projects/${projectId}/experiments/new`);
  await expect(page.getByLabel("Dataset in this project")).not.toHaveValue(""); // prefilled from the latest run first
  await page.getByLabel("Dataset in this project").selectOption("");
  await page.getByLabel("Data file").setInputFiles({ name: "churn-q3.csv", mimeType: "text/csv", buffer: Buffer.from(churnCsv(320, 7)) });
  await page.getByRole("button", { name: "Upload and continue" }).click();
  await expect(page.getByLabel("Task")).toBeVisible();
  await expect(page.getByText("churn-q3", { exact: true })).toBeVisible();
  await page.goto(`/projects/${projectId}/data`);
  await page.getByRole("tab", { name: /Versions/ }).click();
  await page.getByRole("button", { name: /^Use this data.*churn-q3/ }).click();
  await page.getByLabel("Why (saved with the change)").fill("Newer quarter of data");
  await page.locator("form").getByRole("button", { name: "Use this data", exact: true }).click();
  await expect(page.getByRole("row", { name: /churn-q3/ })).toContainText("in use");
  await expect(page.locator("p", { hasText: /History\. is churn-q3/ }).first()).toBeVisible();
  const refsAfter = (await (await page.request.get(`/api/backend/v1/projects/${projectId}/refs`)).json()) as { items: Array<{ ref_kind: string; version: number }> };
  expect(refsAfter.items.find((r) => r.ref_kind === "dataset")?.version).toBeGreaterThanOrEqual(2);

  // The graph marks the old run stale (orange + text); the drawer shows why and what becomes stale.
  await page.goto(`/projects/${projectId}/graph`);
  await expect(page.getByLabel("Lineage summary")).toContainText("built on an older version");
  const staleNode = page.getByRole("button", { name: /^Run 1\b.*, built on an older version/ });
  await expect(staleNode).toBeVisible();
  await expect(staleNode).toContainText("built on an older version");
  await staleNode.click();
  const drawer = page.getByRole("complementary", { name: /^Run 1\b/ });
  await expect(drawer).toBeVisible();
  await expect(drawer).toContainText("Built on an older version: the data in use");
  await expect(drawer).toContainText(/this was built from churn/);
  await expect(page.getByRole("region", { name: "About this screen" })).toContainText("Only moving the goal, the data, the test design or the model in use"); // the accurate caveat
  await expect(drawer.getByRole("list", { name: "What would be built on an older version" })).toContainText("Model v1");
  // AI is off: nothing about AI answers is shown for this run.
  await expect(drawer).not.toContainText(/AI answers|decision point/i);
  await expect(drawer.getByRole("link", { name: "Open full inspector" })).toHaveAttribute("href", `/projects/${projectId}/experiments/${experimentId}`);
  expect(await axeViolations(page)).toEqual([]);
  await shot(page, "2-stale-drawer");

  // The superseded upload: its impact covers the split plan, the run and the model.
  await drawer.getByRole("button", { name: /^churn\b/ }).first().click();
  await expect(page.getByRole("complementary", { name: /^churn\b/ }).getByRole("list", { name: "What would be built on an older version" })).toContainText("Test design 1");
  await page.keyboard.press("Escape");
  await expect(page.getByRole("complementary", { name: /^churn\b/ })).toHaveCount(0);

  // Accessible list fallback.
  await page.getByRole("tab", { name: /As a list/ }).click();
  const experiments = page.getByRole("table", { name: "Run items" });
  await expect(experiments).toContainText("built on an older version");
  await expect(experiments).toContainText(/trained on churn/);
  await expect(page.getByRole("table", { name: "Model items" })).toContainText("★ in use");
  expect(await axeViolations(page)).toEqual([]);
  await shot(page, "3-list");

  // Dark theme: same checks with the drawer open and on the list.
  await page.emulateMedia({ colorScheme: "dark" });
  expect(await axeViolations(page)).toEqual([]);
  await shot(page, "4-list-dark");
  await page.getByRole("tab", { name: /^Diagram/ }).click();
  await staleNode.click();
  await expect(page.getByRole("complementary", { name: /^Run 1\b/ })).toBeVisible();
  expect(await axeViolations(page)).toEqual([]);
  await shot(page, "5-drawer-dark");
});
