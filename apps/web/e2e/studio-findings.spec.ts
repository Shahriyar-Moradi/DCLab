import { expect, test, type Page } from "@playwright/test";
import path from "node:path";

/** P4.10-UI: a run with an imbalanced target and a leaky column shows findings, numbers, a badge and a pre-filled Branch link. */
const EMAIL = process.env.DCLAB_E2E_STUDIO_EMAIL ?? "business-admin-a@verification.invalid";
const PASSWORD = process.env.DCLAB_E2E_STUDIO_PASSWORD ?? "VerificationOnly123!";
const AXE = path.resolve(__dirname, "../node_modules/axe-core/axe.min.js");

type Check = { check: string; status: string; severity: string; message: string; evidence?: Record<string, unknown>; recommendation_kind?: string | null };
type Findings = { investigated: boolean; checks: Check[]; summary: { warnings: number; failures: number } };

function leakyImbalancedCsv(): string {
  const rows = ["tenure,monthly_spend,post_outcome_score,plan,churn"];
  for (let i = 0; i < 400; i += 1) {
    const churn = i % 13 === 0 ? "yes" : "no";
    rows.push(`${1 + ((i * 7) % 72)},${(10 + ((i * 17) % 110)).toFixed(2)},${churn === "yes" ? 80 + (i % 7) : 5 + (i % 9)},${["basic", "plus", "pro"][i % 3]},${churn}`);
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
  if (dir) await page.screenshot({ path: path.join(dir, `findings-${name}.png`), fullPage: true });
}

test("findings panel: severity, numbers, badge and a pre-filled Branch link", async ({ page }) => {
  await login(page);
  await page.goto("/projects/new");
  const name = `Findings ${Date.now()}`;
  await page.getByLabel("Project name").fill(name);
  await page.getByLabel("Data file").setInputFiles({ name: "leaky.csv", mimeType: "text/csv", buffer: Buffer.from(leakyImbalancedCsv()) });
  await page.getByRole("button", { name: "Upload and continue" }).click();
  await expect(page.getByRole("table", { name: "Columns of the uploaded file" })).toContainText("post_outcome_score");
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
  const experimentId = page.url().split("/experiments/")[1];

  // Before the run finishes there is nothing recorded: that is "not computed", never "all passed".
  await expect(page.getByTestId("findings-panel")).toBeVisible();
  await expect.poll(async () => {
    const run = await page.request.get(`/api/backend/v1/experiments/${experimentId}`);
    return ((await run.json()) as { status: string }).status;
  }, { timeout: 170_000, intervals: [2_000] }).toBe("completed");

  const findings = await page.request.get(`/api/backend/v1/experiments/${experimentId}/findings`).then((r) => r.json()) as Findings;
  expect(findings.investigated).toBe(true);
  const attention = findings.checks.filter((c) => c.status === "warning" || c.status === "fail");
  expect(attention.length).toBeGreaterThan(0);
  const branchable = attention.find((c) => ["review_columns", "investigate_leakage", "class_weights", "simpler_model", "regularize"].includes(c.recommendation_kind ?? ""));
  expect(branchable, `no branchable finding: ${JSON.stringify(findings.checks.map((c) => [c.check, c.status, c.recommendation_kind, c.message.slice(0, 160)]))}`).toBeTruthy();

  await page.goto(`/projects/${projectId}/experiments/${experimentId}`);
  const panel = page.getByTestId("findings-panel");
  await expect(panel.locator("article")).toHaveCount(findings.checks.length);
  const card = panel.locator(`article[data-check="${branchable!.check}"]`);
  await expect(card).toContainText(branchable!.message);
  await expect(card.getByText(/Warning|Failed/).first()).toBeVisible();
  await expect(card).toContainText("The numbers behind this");
  // A recommendation that maps to no typed change (a data decision) shows its plain text and no link.
  for (const plain of attention.filter((c) => ["deduplicate", "collect_more_data"].includes(c.recommendation_kind ?? ""))) {
    const plainCard = panel.locator(`article[data-check="${plain.check}"]`);
    await expect(plainCard).toContainText("What to do");
    await expect(plainCard.getByTestId("finding-branch-link")).toHaveCount(0);
  }
  await expect(page.getByText(new RegExp(`${attention.length} findings? needs? attention`))).toBeVisible();
  expect(await axeViolations(page)).toEqual([]);
  await shot(page, "1-panel");

  // Badge on the Experiments list matches the API's count.
  await page.goto(`/projects/${projectId}/experiments`);
  await expect(page.getByRole("link", { name: `${attention.length} to review` })).toBeVisible();
  await shot(page, "2-list-badge");

  // "What to do" opens the Branch form pre-filled; nothing starts until the person presses the button.
  await page.goto(`/projects/${projectId}/experiments/${experimentId}`);
  await panel.locator(`article[data-check="${branchable!.check}"]`).getByTestId("finding-branch-link").click();
  await expect(page).toHaveURL(/prefill=/);
  const sent = page.getByLabel("Change set that will be sent");
  await expect(page.getByText("Pre-filled from a finding")).toBeVisible();
  await expect(page.getByLabel("Why are you branching?", { exact: false })).toHaveValue(/Address finding/);
  await expect(sent).toContainText(/"kind"/);
  await shot(page, "3-branch-prefilled");

  // A hostile query is ignored, not rendered as markup.
  await page.goto(`/projects/${projectId}/experiments/${experimentId}?prefill=<script>&intent=%3Cimg%20src%3Dx%3E`);
  await expect(page.getByText("Pre-filled from a finding")).toHaveCount(0);

  // Data page tab uses the same panel with its provenance label.
  await page.goto(`/projects/${projectId}/data`);
  await page.getByRole("tab", { name: /^Data checks/ }).click();
  await expect(page.getByRole("heading", { name: "All trust checks of the run" })).toBeVisible();
  await expect(page.getByTestId("findings-panel").locator("article")).toHaveCount(findings.checks.length);
  expect(await axeViolations(page)).toEqual([]);
  await shot(page, "4-data-tab");

  // Dark mode contrast.
  await page.emulateMedia({ colorScheme: "dark" });
  await page.goto(`/projects/${projectId}/experiments/${experimentId}`);
  await expect(page.getByTestId("findings-panel")).toBeVisible();
  expect(await axeViolations(page)).toEqual([]);
  await shot(page, "5-dark");
});
