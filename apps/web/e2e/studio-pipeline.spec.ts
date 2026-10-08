import { expect, test, type Page } from "@playwright/test";
import path from "node:path";

/** P4.17-UI: a completed run's pipeline page shows every returned stage, digests, decision links, AI-off rows, downloads and (mocked) replay. */
const EMAIL = process.env.DCLAB_E2E_STUDIO_EMAIL ?? "business-admin-a@verification.invalid";
const PASSWORD = process.env.DCLAB_E2E_STUDIO_PASSWORD ?? "VerificationOnly123!";
const AXE = path.resolve(__dirname, "../node_modules/axe-core/axe.min.js");
const UUID = /[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}/;

type Stage = { key: string; title: string; sequence: number; status: string; configuration?: Record<string, unknown> };

function churnCsv(): string {
  const rows = ["tenure,monthly_spend,plan,churn"];
  for (let i = 0; i < 300; i += 1) {
    const churn = (i * 7) % 11 < 3 ? "yes" : "no";
    rows.push(`${1 + ((i * 7) % 72) + (churn === "yes" ? 0 : 20)},${(10 + ((i * 17) % 110)).toFixed(2)},${["basic", "plus", "pro"][i % 3]},${churn}`);
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
  if (dir) await page.screenshot({ path: path.join(dir, `pipeline-${name}.png`), fullPage: true });
}

test("pipeline evidence page for a completed run", async ({ page }) => {
  await login(page);
  await page.goto("/projects/new");
  const name = `Pipeline ${Date.now()}`;
  await page.getByLabel("Project name").fill(name);
  await page.getByLabel("Data file").setInputFiles({ name: "churn.csv", mimeType: "text/csv", buffer: Buffer.from(churnCsv()) });
  await page.getByRole("button", { name: "Upload and continue" }).click();
  await page.getByLabel("Task").selectOption("binary");
  await page.getByLabel("Target column").selectOption("churn");
  await page.getByRole("button", { name: "Continue" }).click();
  await page.getByRole("button", { name: "Continue" }).click();
  await page.getByRole("button", { name: "Train" }).click();
  await expect(page).toHaveURL(/\/experiments\/[0-9a-f-]{36}$/);
  const projectId = page.url().split("/projects/")[1].split("/")[0];
  const experimentId = page.url().split("/experiments/")[1];
  await expect.poll(async () => ((await (await page.request.get(`/api/backend/v1/experiments/${experimentId}`)).json()) as { status: string }).status, { timeout: 170_000, intervals: [2_000] }).toBe("completed");

  // Verification finishes just after the run: wait for every stage so the API snapshot equals what the page shows.
  const readBuild = async () => (await (await page.request.get(`/api/backend/v1/model-builds/${experimentId}`)).json()) as { stages: Stage[] };
  await expect.poll(async () => (await readBuild()).stages.every((s) => ["completed", "complete", "succeeded"].includes(s.status.toLowerCase())), { timeout: 60_000, intervals: [1_000] }).toBe(true);
  const build = await readBuild();
  const stages = [...build.stages].sort((a, b) => a.sequence - b.sequence);
  expect(stages.length).toBeGreaterThan(10);
  const artifacts = (await (await page.request.get(`/api/backend/v1/model-builds/${experimentId}/artifacts`)).json()) as Array<{ artifact_type: string; content_digest: string }>;

  // The list page picks a run; the run page shows every stage the API returned, in order.
  await page.goto(`/projects/${projectId}/pipeline`);
  await page.getByRole("table", { name: "Runs of this project" }).getByRole("link").first().click();
  await expect(page).toHaveURL(new RegExp(`/projects/${projectId}/pipeline/${experimentId}$`));
  const cards = page.getByTestId("stage-card");
  await expect(cards).toHaveCount(stages.length);
  const keys = await cards.evaluateAll((els) => els.map((el) => el.getAttribute("data-stage")));
  expect(keys).toEqual(stages.map((s) => s.key));
  await expect(page.getByRole("heading", { name: "Run evidence", level: 1 })).toBeVisible();
  await expect(page.getByLabel("Run summary")).toContainText(`${stages.filter((s) => ["completed", "complete", "succeeded"].includes(s.status.toLowerCase())).length} / ${stages.length}`);

  // Digests come from the API: every shown digest is one the API returned for this run.
  const apiText = JSON.stringify(build) + JSON.stringify(artifacts);
  const shown = await page.getByTestId("digest").allTextContents();
  expect(shown.length).toBeGreaterThan(0);
  for (const digest of shown) expect(apiText).toContain(digest);
  await expect(page.getByRole("button", { name: /Copy .* digest/ }).first()).toBeVisible();

  // The final holdout stage shows its status and a locked note, never the holdout metric values.
  const holdout = page.locator('[data-stage="final_holdout"]');
  if (await holdout.count()) {
    await expect(holdout).toContainText("not shown on this page");
    const values = JSON.stringify(stages.find((s) => s.key === "final_holdout")?.configuration ?? {}).match(/\d\.\d{4,}/g) ?? [];
    for (const value of values) await expect(holdout).not.toContainText(value);
  }

  // Decision points: one row per point from the pipeline events; AI is off in this stack, so the rule answer is used.
  const points = page.getByRole("table", { name: "Decision points in this run" });
  await expect(points).toBeVisible();
  await expect(points.getByText("AI off").first()).toBeVisible();
  await expect(page.getByText("No AI runs are recorded for this run")).toBeVisible();
  await expect(page.getByRole("button", { name: /Replay AI/ })).toHaveCount(0);

  // Decision links open the Decisions page with that record selected.
  const link = page.getByTestId("decision-link").first();
  await expect(link).toBeVisible();
  const href = (await link.getAttribute("href")) ?? "";
  expect(href).toMatch(new RegExp(`^/projects/${projectId}/decisions\\?record=${UUID.source}$`));
  const recordId = href.split("record=")[1];
  await shot(page, "1-run");
  await link.click();
  await expect(page).toHaveURL(new RegExp(`/decisions\\?record=${recordId}`));
  await expect(page.locator("#decision-drawer-title")).toContainText(recordId.slice(0, 8));

  // A hostile record parameter is ignored.
  await page.goto(`/projects/${projectId}/decisions?record=<script>alert(1)</script>`);
  await expect(page.getByRole("heading", { name: "History", level: 1 })).toBeVisible();
  await expect(page.locator("#decision-drawer-title")).toHaveCount(0);

  // Artifacts: digest only for model files, a real download for source code; the reproduction script downloads too.
  await page.goto(`/projects/${projectId}/pipeline/${experimentId}`);
  const table = page.getByRole("table", { name: "Artifacts of this run" });
  await expect(table).toBeVisible();
  for (const artifact of artifacts.filter((a) => ["model", "preprocessor", "predictions"].includes(a.artifact_type))) {
    await expect(table.getByRole("row", { name: new RegExp(artifact.artifact_type) }).getByRole("button", { name: /Download/ })).toHaveCount(0);
  }
  const script = page.getByRole("button", { name: "Download reproduction script" });
  const [download] = await Promise.all([page.waitForEvent("download"), script.click()]);
  expect(download.suggestedFilename()).toMatch(/^[\w.\- ]+$/);
  await shot(page, "2-artifacts");

  // Run switcher and unknown ids.
  await expect(page.getByLabel("Switch run")).toHaveValue(experimentId);
  expect((await page.request.get(`/projects/${projectId}/pipeline/not-a-uuid`)).status()).toBe(404);

  // Light and dark themes: no serious axe violations.
  expect(await axeViolations(page)).toEqual([]);
  await page.emulateMedia({ colorScheme: "dark" });
  await expect(cards.first()).toBeVisible();
  expect(await axeViolations(page)).toEqual([]);
  await shot(page, "3-dark");
  await page.emulateMedia({ colorScheme: "light" });

  // Replay needs a recorded specialist run; this stack has AI off, so the replay flow is exercised against mocked reads.
  const runId = "44444444-4444-4444-8444-444444444444";
  await page.route("**/api/backend/v1/agent-runs?*", (route) => route.fulfill({
    json: { items: [{ id: runId, agent_key: "experiment_critic", agent_version: "1", kind: "specialist", status: "completed", cost_micros: 1500, currency: "USD", created_at: new Date().toISOString(), subject: { kind: "experiment", id: experimentId }, usage: {} }], limit: 100, next_cursor: null },
  }));
  const posts: string[] = [];
  let attempt = 0;
  await page.route(`**/api/backend/v1/agent-runs/${runId}/replay`, async (route) => {
    posts.push(route.request().headers()["idempotency-key"] ?? "");
    attempt += 1;
    await new Promise((resolve) => setTimeout(resolve, 400));
    if (attempt === 1) return route.fulfill({ status: 429, json: { error: { code: "rate_limited", message: "slow down" } } });
    return route.fulfill({ json: { run_id: runId, equal: true, mismatches: [], tool_sequence: [{ tool: "get_experiment", argument_digest: "a".repeat(64) }], incident_id: null, output_digest: "b".repeat(64), same_failure: false, not_comparable: false } });
  });
  await page.goto(`/projects/${projectId}/pipeline/${experimentId}`);
  const replay = page.getByRole("button", { name: /Replay AI/ });
  await expect(replay).toBeVisible();
  await replay.dblclick();
  await expect(page.getByText("Too many replays")).toBeVisible();
  expect(posts).toHaveLength(1);
  expect(posts[0]).not.toBe("");
  await replay.click();
  await expect(page.getByTestId("replay-result")).toContainText("Replay matches the record");
  expect(posts).toHaveLength(2);
  expect(posts[1]).toBe(posts[0]); // a retry of the refused action replays its key
  await replay.click();
  await expect.poll(() => posts.length).toBe(3);
  expect(posts[2]).not.toBe(posts[1]); // after a success the next click is a new action
  await shot(page, "4-replay");
});
