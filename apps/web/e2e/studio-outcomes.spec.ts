import { expect, test, type Page } from "@playwright/test";
import fs from "node:fs";
import path from "node:path";

/**
 * P7.6-A: the Business view. A data scientist trains a model and scores a file (real worker); a business user
 * (client role) then lands on /outcomes, reads the outcome, how it was built, the latest list (downloads it), the
 * limits and the boundary, and cannot open developer routes. Needs a worker (models train and score for real).
 */
const ADMIN = process.env.DCLAB_E2E_STUDIO_EMAIL ?? "business-admin-a@verification.invalid";
const CLIENT = process.env.DCLAB_E2E_CLIENT_EMAIL ?? "client-user@verification.invalid";
const PASSWORD = process.env.DCLAB_E2E_STUDIO_PASSWORD ?? "VerificationOnly123!";
const AXE = path.resolve(__dirname, "../node_modules/axe-core/axe.min.js");
const BANNED = /\b(release|champion|ref|digest|holdout|feature contract|trust check|CV|cross-validation|experiment|candidate|fold)s?\b/i;

function churnCsv(rows: number, opts: { target?: boolean; offset?: number } = {}): string {
  const offset = opts.offset ?? 0;
  const out = [["tenure", "monthly_spend", "plan", ...(opts.target === false ? [] : ["churn"])].join(",")];
  for (let i = offset; i < offset + rows; i += 1) {
    const tenure = 1 + ((i * 13) % 72);
    const row = [String(tenure), (10 + ((i * 17) % 110)).toFixed(2), ["basic", "plus", "pro"][i % 3]];
    if (opts.target !== false) row.push(tenure < 12 && i % 5 !== 0 ? "yes" : "no");
    out.push(row.join(","));
  }
  return `${out.join("\n")}\n`;
}

async function login(page: Page, email: string): Promise<void> {
  await page.goto("/login");
  await page.getByLabel("Email").fill(email);
  await page.getByLabel("Password").fill(PASSWORD);
  await page.getByRole("button", { name: "Sign in" }).click();
  await expect(page).not.toHaveURL(/\/login/);
}

async function writeHeaders(page: Page, label: string): Promise<Record<string, string>> {
  const csrf = (await page.context().cookies()).find((c) => c.name === "dclab_csrf")?.value ?? "";
  return { "Idempotency-Key": `e2e-p76a-${label}-${Date.now()}`, "X-CSRF-Token": csrf, Origin: new URL(page.url()).origin };
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
  if (dir) await page.screenshot({ path: path.join(dir, `outcomes-${name}.png`), fullPage: true });
}

test("Business view: a business user reads the outcome, downloads the list and cannot open developer routes", async ({ browser }) => {
  // 1. A data scientist trains a model (the first run puts it in use) and scores a file with it.
  const admin = await browser.newPage();
  await login(admin, ADMIN);
  const name = `Outcomes ${Date.now()}`;
  await admin.goto("/projects/new");
  await admin.getByLabel("Project name").fill(name);
  await admin.getByLabel("Data file").setInputFiles({ name: "churn.csv", mimeType: "text/csv", buffer: Buffer.from(churnCsv(300)) });
  await admin.getByRole("button", { name: "Upload and continue" }).click();
  await admin.getByLabel("Task").selectOption("binary");
  await admin.getByLabel("Target column").selectOption("churn");
  await admin.getByRole("button", { name: "Continue" }).click();
  await admin.getByRole("button", { name: "Continue" }).click();
  await admin.getByRole("button", { name: "Train" }).click();
  await expect(admin).toHaveURL(/\/experiments\/[0-9a-f-]{36}$/);
  const projectId = admin.url().split("/projects/")[1].split("/")[0];
  const experimentId = admin.url().split("/experiments/")[1];
  await expect.poll(async () => ((await (await admin.request.get(`/api/backend/v1/experiments/${experimentId}`)).json()) as { status: string }).status, { timeout: 240_000, intervals: [2_000] }).toBe("completed");
  let modelId = "";
  await expect.poll(async () => {
    const refs = (await (await admin.request.get(`/api/backend/v1/projects/${projectId}/refs`)).json()) as { items: Array<{ ref_kind: string; target: { id: string } }> };
    modelId = refs.items.find((r) => r.ref_kind === "champion_model")?.target.id ?? "";
    return modelId;
  }, { timeout: 30_000 }).toMatch(/^[0-9a-f-]{36}$/);
  const upload = await admin.request.post("/api/backend/v1/datasets", {
    multipart: { project_id: projectId, purpose: "scoring", file: { name: "next-week.csv", mimeType: "text/csv", buffer: Buffer.from(churnCsv(40, { target: false, offset: 5000 })) } },
    headers: await writeHeaders(admin, "upload"),
  });
  expect(upload.ok(), await upload.text()).toBeTruthy();
  const datasetId = ((await upload.json()) as { id: string }).id;
  const scored = await admin.request.post(`/api/backend/v1/model-versions/${modelId}/predictions`, { data: { dataset_id: datasetId }, headers: await writeHeaders(admin, "score") });
  expect(scored.status(), await scored.text()).toBe(202);
  const predictionId = ((await scored.json()) as { id: string }).id;
  await expect.poll(async () => ((await (await admin.request.get(`/api/backend/v1/predictions/${predictionId}`)).json()) as { status: string }).status, { timeout: 120_000, intervals: [2_000] }).toBe("completed");
  await admin.close();

  // 2. The business user lands on the Business view.
  const context = await browser.newContext({ colorScheme: "light" });
  const page = await context.newPage();
  const requested: string[] = [];
  page.on("request", (request) => requested.push(new URL(request.url()).pathname));
  await login(page, CLIENT);
  await expect(page).toHaveURL(/\/outcomes$/);
  const choices = page.getByRole("combobox", { name: "Project" });
  if (await choices.count()) await choices.selectOption({ label: name });
  await expect(page.getByRole("heading", { name, level: 1 })).toBeVisible({ timeout: 30_000 });
  const main = page.locator("main");
  const outcome = page.locator("section.card", { has: page.getByRole("heading", { name: "What it does for you" }) });
  await expect(outcome).toContainText("final test");
  await expect(outcome).toContainText(/Of every 10 rows with .+ in the final test, the model flagged \d+\./);
  await expect(page.locator("section.card", { has: page.getByRole("heading", { name: "How it was built" }) }).locator("p")).toHaveCount(3);
  const list = page.locator("section.card", { has: page.getByRole("heading", { name: "The latest list" }) });
  await expect(list).toContainText("The latest list has 40 rows, each with a score");
  await expect(page.locator("section.card", { has: page.getByRole("heading", { name: "What it cannot do" }) })).toContainText("The list is not a decision");
  await expect(main).toContainText("DCLab scores and explains; it does not contact customers or take business actions.");
  await expect(main).toContainText("Planned, not available yet");
  // Only the minimal project choice is read, never the developer project list.
  expect(requested).toContain("/api/backend/v1/outcomes/projects");
  expect(requested.filter((p) => p === "/api/backend/v1/projects")).toEqual([]);
  const text = await main.innerText();
  expect(text).not.toMatch(BANNED);
  expect(text).not.toMatch(/[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}/);
  // Only the Business view in the sidebar: no developer item.
  const nav = page.getByRole("navigation", { name: "Studio" });
  await expect(nav.getByRole("link")).toHaveCount(1);
  await expect(nav.getByRole("link")).toContainText("Outcomes");
  const [download] = await Promise.all([page.waitForEvent("download"), list.getByRole("button", { name: "Download the list" }).click()]);
  const file = fs.readFileSync((await download.path())!, "utf8");
  expect(file.trim().split("\n")).toHaveLength(41);
  expect(await axeViolations(page)).toEqual([]);
  await shot(page, "light");

  // 3. Dark theme and phone width: same checks, no sideways scroll.
  await page.emulateMedia({ colorScheme: "dark" });
  expect(await axeViolations(page)).toEqual([]);
  await shot(page, "dark");
  await page.setViewportSize({ width: 375, height: 812 });
  await page.reload();
  await expect(page.getByRole("heading", { name: "What it does for you" })).toBeVisible({ timeout: 30_000 });
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth + 1)).toBe(true);
  await shot(page, "phone");
  await page.setViewportSize({ width: 1280, height: 900 });

  // 4. Developer routes are closed to the business user; Labs stays reachable.
  for (const route of ["/projects", "/home", "/inbox", "/settings", "/agents", `/projects/${projectId}/experiments`]) {
    const response = await page.goto(route);
    expect(response?.status(), route).toBe(403);
  }
  const labs = await page.goto("/app/labs");
  expect(labs?.status()).toBe(200);
  await context.close();
});
