import { expect, test, type Page } from "@playwright/test";
import path from "node:path";

/**
 * P4.5-A: the Labs run page for a client shows no technical panel and, while processing, only
 * "Analyzing your data"; it also never requests technical data. A developer is redirected as before.
 * The "completed" test needs a worker (DCLAB_E2E_WORKER=1); the processing test needs none.
 */
const PASSWORD = "VerificationOnly123!";
const WEB_ORIGIN = process.env.DCLAB_E2E_WEB_URL ?? "http://127.0.0.1:3001";
const AXE = path.resolve(__dirname, "../node_modules/axe-core/axe.min.js");

function churnCsv(): string {
  const rows = ["tenure,monthly_spend,plan,churn"];
  for (let i = 0; i < 240; i += 1) {
    const tenure = 1 + ((i * 13) % 72);
    rows.push(`${tenure},${(10 + ((i * 17) % 110)).toFixed(2)},${["basic", "plus", "pro"][i % 3]},${tenure < 12 && i % 5 !== 0 ? "yes" : "no"}`);
  }
  return `${rows.join("\n")}\n`;
}

async function login(page: Page, email: string): Promise<void> {
  await page.goto("/login");
  await page.getByLabel("Email").fill(email);
  await page.getByLabel("Password").fill(PASSWORD);
  await page.getByRole("button", { name: "Sign in" }).click();
  await expect(page).not.toHaveURL(/\/login/);
}

async function upload(page: Page): Promise<string> {
  const cookies = await page.context().cookies();
  const response = await page.request.post("/api/backend/app/labs/uploads", {
    multipart: {
      category: "Churn & Retention",
      target_column: "churn",
      file: { name: `churn-${Date.now()}.csv`, mimeType: "text/csv", buffer: Buffer.from(churnCsv()) },
    },
    headers: { "X-CSRF-Token": cookies.find((c) => c.name === "dclab_csrf")?.value ?? "", Origin: WEB_ORIGIN },
  });
  expect(response.ok(), await response.text()).toBeTruthy();
  return ((await response.json()) as { run_id: string }).run_id;
}

async function axeSeriousOrCritical(page: Page): Promise<string[]> {
  await page.addScriptTag({ path: AXE });
  return page.evaluate(async () => {
    const axe = (window as unknown as { axe: { run: (ctx: Document, opts: object) => Promise<{ violations: Array<{ id: string; impact?: string; nodes: unknown[] }> }> } }).axe;
    const result = await axe.run(document, { runOnly: { type: "tag", values: ["wcag2a", "wcag2aa"] } });
    return result.violations.filter((v) => v.impact === "serious" || v.impact === "critical").map((v) => `${v.id} (${v.nodes.length})`);
  });
}

async function shot(page: Page, name: string): Promise<void> {
  const dir = process.env.DCLAB_E2E_SHOTS;
  if (dir) await page.screenshot({ path: path.join(dir, `${name}.png`), fullPage: true });
}

const TECHNICAL_REQUEST = /\/(v1\/experiments|v1\/model-builds|development|admin|business\/explorer|technical)/;

test("client: processing shows only 'Analyzing your data', no technical panel, no technical requests", async ({ page }) => {
  await login(page, "client-user@verification.invalid");
  const runId = await upload(page);
  const requests: string[] = [];
  page.on("request", (request) => requests.push(new URL(request.url()).pathname));
  await page.goto(`/lab/runs/${runId}`);
  await expect(page).toHaveURL(new RegExp(`/lab/runs/${runId}`));
  await expect(page.getByRole("heading", { name: "Analyzing your data" })).toBeVisible();
  await expect(page.getByText("Technical information")).toHaveCount(0);
  await expect(page.getByText("Pipeline status")).toHaveCount(0);
  await expect(page.getByText(runId)).toHaveCount(0);
  await expect(page.getByText("Dataset ID")).toHaveCount(0);
  await shot(page, "client-run-processing-light");
  expect(await axeSeriousOrCritical(page)).toEqual([]);
  await page.emulateMedia({ colorScheme: "dark" });
  expect(await axeSeriousOrCritical(page)).toEqual([]);
  expect(requests.filter((p) => TECHNICAL_REQUEST.test(p) && p.startsWith("/api/backend"))).toEqual([]);
});

test("developer: the run opens on the project experiment page and ?view=labs keeps the technical panel", async ({ page }) => {
  await login(page, "business-admin-a@verification.invalid");
  const runId = await upload(page);
  await page.goto(`/lab/runs/${runId}`);
  await expect(page).toHaveURL(/\/projects\/[0-9a-f-]{36}\/experiments\/[0-9a-f-]{36}/, { timeout: 30_000 });
  await page.goto(`/lab/runs/${runId}?view=labs`);
  await expect(page.getByRole("heading", { name: "Technical information" })).toBeVisible();
  await expect(page.getByText(runId).first()).toBeVisible();
  await shot(page, "developer-run-labs-view");
});

test("client: a completed run shows results without ids or the technical panel", async ({ page }) => {
  test.skip(process.env.DCLAB_E2E_WORKER !== "1", "needs a running worker");
  await login(page, "client-user@verification.invalid");
  const runId = await upload(page);
  await expect.poll(async () => {
    const run = await page.request.get(`/api/backend/app/labs/uploads/${runId}`);
    return ((await run.json()) as { status: string }).status;
  }, { timeout: 170_000, intervals: [2_000] }).toBe("completed");
  await page.goto(`/lab/runs/${runId}`);
  await expect(page.getByRole("heading", { name: "Summary" })).toBeVisible();
  await expect(page.getByText("Technical information")).toHaveCount(0);
  await expect(page.getByText(runId)).toHaveCount(0);
  await shot(page, "client-run-completed-light");
  expect(await axeSeriousOrCritical(page)).toEqual([]);
});
