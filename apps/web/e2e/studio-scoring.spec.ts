import { expect, test, type Page } from "@playwright/test";
import fs from "node:fs";
import path from "node:path";

/** P4.9-UI: Score new data on the model page: upload, column check in plain words, progress, download, and the refusals. */
const EMAIL = process.env.DCLAB_E2E_STUDIO_EMAIL ?? "business-admin-a@verification.invalid";
const PASSWORD = process.env.DCLAB_E2E_STUDIO_PASSWORD ?? "VerificationOnly123!";
const AXE = path.resolve(__dirname, "../node_modules/axe-core/axe.min.js");

function churnCsv(rows: number, opts: { target?: boolean; dropColumn?: string; offset?: number } = {}): string {
  const offset = opts.offset ?? 0;
  const cols = ["tenure", "monthly_spend", "customer_code", "plan"].filter((c) => c !== opts.dropColumn);
  const header = [...cols, ...(opts.target === false ? [] : ["churn"])];
  const out = [header.join(",")];
  for (let i = offset; i < offset + rows; i += 1) {
    const tenure = 1 + ((i * 13) % 72);
    const values: Record<string, string> = {
      tenure: String(tenure), monthly_spend: (10 + ((i * 17) % 110)).toFixed(2), customer_code: `C${1000 + i}`, plan: ["basic", "plus", "pro"][i % 3],
    };
    out.push([...cols.map((c) => values[c]), ...(opts.target === false ? [] : [tenure < 12 && i % 5 !== 0 ? "yes" : "no"])].join(","));
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
  if (dir) await page.screenshot({ path: path.join(dir, `score-${name}.png`), fullPage: true });
}

test("Score new data: upload, column check, progress, download, missing column, training file refused", async ({ page }) => {
  await login(page);
  await page.goto("/projects/new");
  await page.getByLabel("Project name").fill(`Scoring ${Date.now()}`);
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
  await expect.poll(async () => ((await (await page.request.get(`/api/backend/v1/experiments/${experimentId}`)).json()) as { status: string }).status, { timeout: 240_000, intervals: [2_000] }).toBe("completed");

  await page.goto(`/projects/${projectId}/graph`);
  await page.getByRole("button", { name: /^Model v\d/ }).first().click();
  await page.getByRole("complementary", { name: /^Model v\d/ }).getByRole("link", { name: "Open full inspector" }).click();
  await expect(page).toHaveURL(/\/models\/[0-9a-f-]{36}$/);
  await page.getByRole("tab", { name: "Score new data" }).click();
  await expect(page.getByRole("region", { name: "About this screen" })).toBeVisible();
  await shot(page, "1-empty");

  // 1. A file with new rows: extra column, no target.
  const nextMonth = churnCsv(40, { target: false, offset: 5000 }).trim().split("\n").map((l, n) => `${l},${n === 0 ? "region" : "north"}`).join("\n");
  await page.getByLabel("Scoring file").setInputFiles({ name: "next-month.csv", mimeType: "text/csv", buffer: Buffer.from(`${nextMonth}\n`) });
  await page.getByRole("button", { name: "Score this file" }).click();
  const check = page.getByRole("region", { name: "Column check" }).first();
  const card = page.locator("section.card", { has: page.getByRole("heading", { name: /Scoring of next-month.csv/ }) });
  await expect(page.getByRole("heading", { name: /Scoring of next-month.csv done/ })).toBeVisible({ timeout: 120_000 });
  await expect(check).toContainText("The file has all 2 columns the model needs.");
  await expect(check).toContainText("region");
  await expect(card.getByText("40 of 40")).toBeVisible();
  const [download] = await Promise.all([page.waitForEvent("download"), card.getByRole("button", { name: "Download predictions" }).click()]);
  const saved = await download.path();
  const text = fs.readFileSync(saved!, "utf8");
  expect(text.split("\n")[0]).toMatch(/row_number/);
  expect(text.trim().split("\n")).toHaveLength(41);
  expect(page.url()).not.toMatch(/token|signature/i);
  expect(await axeViolations(page)).toEqual([]);
  await shot(page, "2-completed");

  // 2. A file missing a required column.
  await page.getByLabel("Scoring file").setInputFiles({ name: "no-plan.csv", mimeType: "text/csv", buffer: Buffer.from(churnCsv(30, { target: false, dropColumn: "plan", offset: 7000 })) });
  await page.getByRole("button", { name: "Score this file" }).click();
  const bad = page.locator("section.card", { has: page.getByRole("heading", { name: /Scoring of no-plan.csv/ }) });
  await expect(page.getByRole("region", { name: "Column check" }).first()).toContainText("missing 1 column the model needs: plan.", { timeout: 120_000 });
  await expect(page.getByRole("heading", { name: /Scoring of no-plan.csv failed/ })).toBeVisible();
  await expect(bad.getByRole("button", { name: "Download predictions" })).toHaveCount(0);
  await shot(page, "3-missing-column");

  // 3. The training file is refused in plain words.
  await page.getByLabel("Scoring file").setInputFiles({ name: "churn.csv", mimeType: "text/csv", buffer: Buffer.from(churnCsv(300)) });
  await page.getByRole("button", { name: "Score this file" }).click();
  await expect(page.getByText("That is the file this model learned from.")).toBeVisible();
  await expect(page.getByText(/Upload new rows the model has not seen/)).toBeVisible();
  expect(await axeViolations(page)).toEqual([]);
  await shot(page, "4-training-refused");

  // 4. The session list survives a reload (ids only; each is re-read from the API).
  await page.reload();
  await page.getByRole("tab", { name: "Score new data" }).click();
  await expect(page.getByRole("heading", { name: /Scoring of next-month.csv done/ })).toBeVisible();

  // 4b. The Predictions page: from the sidebar and the flow bar; the model in use is the default; this session's files are listed.
  await page.goto(`/projects/${projectId}/models`);
  await page.getByRole("navigation", { name: "Studio" }).getByRole("link", { name: "Predictions" }).click();
  await expect(page).toHaveURL(new RegExp(`/projects/${projectId}/predictions$`));
  await expect(page.getByRole("heading", { name: "Predictions", level: 1 })).toBeVisible();
  await expect(page.getByRole("navigation", { name: "Project steps" }).getByRole("link", { name: "Predictions" })).toHaveAttribute("aria-current", "step");
  const picker = page.getByRole("combobox", { name: "Model" });
  await expect(picker.locator("option:checked")).toContainText(/^Model v\d+ · Run \d+ \(in use\)$/);
  const history = page.getByRole("table", { name: "Predictions run in this browser tab" });
  await expect(history).toContainText("next-month.csv");
  await expect(history).toContainText("You, in this browser tab");
  await expect(history).toContainText("40 of 40");
  // The list is kept per workspace and per person: the storage key carries both ids.
  expect(await page.evaluate(() => Object.keys(window.sessionStorage).filter((k) => /^dclab\.scorings\.[0-9a-f-]{36}\.[0-9a-f-]{36}\.[0-9a-f-]{36}$/.test(k)).length)).toBeGreaterThan(0);
  expect(await page.evaluate(() => Object.keys(window.sessionStorage).filter((k) => /^dclab\.scorings\.[0-9a-f-]{36}$/.test(k)).length)).toBe(0);
  await page.getByLabel("Scoring file").setInputFiles({ name: "page-run.csv", mimeType: "text/csv", buffer: Buffer.from(churnCsv(25, { target: false, offset: 9000 })) });
  await page.getByRole("button", { name: "Score this file" }).click();
  await expect(page.getByRole("heading", { name: /Scoring of page-run.csv done/ })).toBeVisible({ timeout: 120_000 });
  await expect(page.getByRole("heading", { name: /Scoring of page-run.csv done/ }).locator("xpath=ancestor::section[1]").getByText("Model v")).toBeVisible();
  await expect(history).toContainText("page-run.csv");
  // Only what is built: no schedule, no per-row reasons, no raw terms.
  await expect(page.locator("main")).not.toContainText(/weekly|every Monday|reasons for each|feature contract|holdout|artifact/i);
  expect(await axeViolations(page)).toEqual([]);
  await shot(page, "4b-predictions");
  // The model page's score tab is the same step of the flow bar.
  await page.goto(`/projects/${projectId}/models`);
  await page.getByRole("link", { name: /^Model v\d/ }).first().click();
  await page.goto(`${page.url()}?tab=score`);
  await expect(page.getByRole("navigation", { name: "Project steps" }).getByRole("link", { name: "Predictions" })).toHaveAttribute("aria-current", "step");

  // 5. Dark mode.
  await page.emulateMedia({ colorScheme: "dark" });
  expect(await axeViolations(page)).toEqual([]);
  await shot(page, "5-dark");
});
