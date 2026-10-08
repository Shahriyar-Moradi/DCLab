import { expect, test, type Page } from "@playwright/test";
import path from "node:path";

/** P4.3-A: node inspectors (experiment tabs, dataset, split plan, feature recipe, model version) from the Graph drawer and by URL. */
const EMAIL = process.env.DCLAB_E2E_STUDIO_EMAIL ?? "business-admin-a@verification.invalid";
const PASSWORD = process.env.DCLAB_E2E_STUDIO_PASSWORD ?? "VerificationOnly123!";
const AXE = path.resolve(__dirname, "../node_modules/axe-core/axe.min.js");

function churnCsv(rows: number): string {
  const out = ["tenure,monthly_spend,customer_code,plan,churn"];
  for (let i = 0; i < rows; i += 1) {
    const tenure = 1 + ((i * 13) % 72);
    out.push(`${tenure},${(10 + ((i * 17) % 110)).toFixed(2)},C${1000 + i},${["basic", "plus", "pro"][i % 3]},${tenure < 12 && i % 5 !== 0 ? "yes" : "no"}`);
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
  if (dir) await page.screenshot({ path: path.join(dir, `inspect-${name}.png`), fullPage: true });
}

test("Inspectors: reason, evidence and code for every node kind, from the drawer and by URL", async ({ page, context }) => {
  await context.grantPermissions(["clipboard-read", "clipboard-write"]);
  await login(page);
  await page.goto("/projects/new");
  await page.getByLabel("Project name").fill(`Inspectors ${Date.now()}`);
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
  await expect.poll(async () => ((await (await page.request.get(`/api/backend/v1/experiments/${experimentId}`)).json()) as { status: string }).status, { timeout: 150_000, intervals: [2_000] }).toBe("completed");

  // Graph drawer: compact inspector with a link to the full one, for each kind.
  await page.goto(`/projects/${projectId}/graph`);
  const kinds: Array<[RegExp, RegExp, RegExp]> = [
    [/^Split plan/, /\/splits\/[0-9a-f-]{36}$/, /Split plan/],
    [/^Feature recipe/, /\/features\/[0-9a-f-]{36}$/, /Feature recipe/],
    [/^Dataset version/, /\/data\/[0-9a-f-]{36}$/, /Dataset version/],
    [/^Model version/, /\/models\/[0-9a-f-]{36}$/, /Model version/],
  ];
  const hrefs: string[] = [];
  for (const [node, route, title] of kinds) {
    await page.goto(`/projects/${projectId}/graph`);
    await page.getByRole("button", { name: node }).first().click();
    const drawer = page.getByRole("complementary", { name: title });
    await expect(drawer.getByRole("heading", { name: "Reason", level: 3 })).toBeVisible();
    const open = drawer.getByRole("link", { name: "Open full inspector" });
    await expect(open).toHaveAttribute("href", route);
    hrefs.push((await open.getAttribute("href")) ?? "");
    if (node.source.includes("Split")) await shot(page, "1-drawer");
    await open.click();
    await expect(page).toHaveURL(route);
    await expect(page.getByRole("heading", { name: node.source.includes("Split") ? "Goal & test design" : "Reason", level: node.source.includes("Split") ? 1 : undefined })).toBeVisible();
    await expect(page.getByText(/^Loading/)).toHaveCount(0);
    expect(await axeViolations(page)).toEqual([]);
    await shot(page, `2-${hrefs.length}-${title.source.replace(/\W/g, "").toLowerCase()}`);
  }

  // Goal & test design: the target, the picture of the folds and counts only, no final test set values; feature recipe: formula and code snippet.
  await page.goto(hrefs[0]);
  await expect(page.getByRole("heading", { name: "How we test" })).toBeVisible();
  await expect(page.getByRole("heading", { name: "What we predict" })).toBeVisible();
  await expect(page.getByText("How we rank models")).toBeVisible();
  await expect(page.getByRole("img", { name: /cross-validation folds/ })).toBeVisible();
  await expect(page.getByText("Why this design?")).toBeVisible();
  await expect(page.getByText(/(Time-ordered|Grouped|Random)/).first()).toBeVisible();
  await expect(page.getByText("Training rows", { exact: true })).toBeVisible();
  await page.goto(hrefs[1]);
  await expect(page.getByRole("table", { name: "Features of this recipe" })).toContainText("tenure");
  await page.getByRole("button", { name: /^monthly_spend/ }).click();
  await expect(page.locator("pre.code").first()).toContainText("Fitted inside each CV fold");
  await expect(page.getByRole("table", { name: "Preprocessing steps" })).toContainText("StandardScaler");
  expect(await axeViolations(page)).toEqual([]);
  await shot(page, "2-2b-feature-code");
  await page.goto(hrefs[3]);
  await expect(page.getByRole("link", { name: /^[0-9a-f]{8}$/ }).first()).toBeVisible();
  await expect(page.getByText(/is on the Card tab/)).toBeVisible();

  // Experiment inspector tabs.
  await page.goto(`/projects/${projectId}/experiments/${experimentId}`);
  const tabs = ["Overview", "Candidates", "Per-fold and threshold", "Feature importance", "Code", "Evidence"];
  for (const name of tabs) {
    await page.getByRole("tab", { name: new RegExp(`^${name}`) }).click();
    await expect(page.getByRole("tab", { name: new RegExp(`^${name}`) })).toHaveAttribute("aria-selected", "true");
    await expect(page.getByText(/^Loading/)).toHaveCount(0);
    expect(await axeViolations(page)).toEqual([]);
    await shot(page, `3-tab-${name.toLowerCase().replace(/\W+/g, "-")}`);
  }
  await page.getByRole("tab", { name: /^Candidates/ }).click();
  await expect(page.getByRole("table", { name: "Candidates and their CV scores" }).getByRole("row")).not.toHaveCount(1);
  await page.getByRole("tab", { name: /^Per-fold/ }).click();
  await expect(page.getByRole("table", { name: "Per-fold CV metrics" })).toBeVisible();
  await page.getByRole("tab", { name: /^Overview/ }).click();
  await expect(page.getByText("No Critic review for this run")).toBeVisible();
  await expect(page.getByTestId("findings-summary")).toContainText(/trust checks/);

  // Code: copy and download are plain text, nothing runs.
  await page.getByRole("tab", { name: /^Code/ }).click();
  const code = page.getByRole("region", { name: /Generated script/ }).or(page.locator("pre.code").first());
  await expect(code.first()).toContainText(/import|def /);
  await page.getByRole("button", { name: /^Copy/ }).first().click();
  await expect(page.getByText(/Copied the script/)).toBeVisible();
  expect(await page.evaluate(() => navigator.clipboard.readText())).toMatch(/import|def /);
  const [download] = await Promise.all([page.waitForEvent("download"), page.getByRole("button", { name: /^Download/ }).first().click()]);
  expect(download.suggestedFilename()).toMatch(/\.py$/);

  // Dark theme with tabs open.
  await page.emulateMedia({ colorScheme: "dark" });
  expect(await axeViolations(page)).toEqual([]);
  await shot(page, "4-code-dark");
  await page.getByRole("tab", { name: /^Candidates/ }).click();
  expect(await axeViolations(page)).toEqual([]);
  await shot(page, "5-candidates-dark");

  // Route gating: non-UUID ids are a 404.
  for (const bad of ["splits/not-a-uuid", "features/x", "models/1", "data/00000000-0000-4000-8000-00000000000g"]) {
    const response = await page.goto(`/projects/${projectId}/${bad}`);
    expect(response?.status(), bad).toBe(404);
  }
});
