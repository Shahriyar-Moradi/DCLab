import { expect, test, type Page } from "@playwright/test";
import { axeViolations, login, shot } from "./support/studio-fixture";

/**
 * V7 UI-C: Data (summary strip of training-period values, columns filter and "Why", versions with uploader, newer / in use,
 * upload a newer file with a plain refusal) and Goal & test design (always reachable, three goal cards from the problem spec,
 * the time line to scale from per-fold row counts, chips and a why sentence from row counts only; no final test dates).
 * Runs on a real stack (API + worker, AI off): the assistant-vs-rules column must stay hidden.
 */
const VIEWER = process.env.DCLAB_E2E_VIEWER_EMAIL ?? "viewer-a@verification.invalid";
const PASSWORD = process.env.DCLAB_E2E_STUDIO_PASSWORD ?? "VerificationOnly123!";

function monthlyCsv(): string {
  const rows = ["snapshot_month,customer_id,tenure,monthly_fee,plan,churn"];
  for (let i = 0; i < 540; i += 1) {
    const month = new Date(Date.UTC(2025, Math.floor(i / 30), 1)).toISOString().slice(0, 10);
    const tenure = 1 + ((i * 13) % 72);
    const churn = tenure < 14 && i % 4 !== 0 ? "yes" : i % 9 === 0 ? "yes" : "no";
    rows.push(`${month},C${1000 + i},${tenure},${(10 + ((i * 17) % 110)).toFixed(2)},${["basic", "plus", "pro"][i % 3]},${churn}`);
  }
  return `${rows.join("\n")}\n`;
}

async function writeHeaders(page: Page, key: string): Promise<Record<string, string>> {
  const csrf = (await page.context().cookies()).find((c) => c.name === "dclab_csrf")?.value ?? "";
  return { "Idempotency-Key": `e2e-uic-${key}-${Date.now()}`, "X-CSRF-Token": csrf, Origin: new URL(page.url()).origin };
}

/** A finished run moves the project's versions in use and writes its column facts just after its status turns completed. */
async function settled(page: Page, projectId: string): Promise<void> {
  await expect.poll(async () => {
    const refs = (await (await page.request.get(`/api/backend/v1/projects/${projectId}/refs`)).json()) as { items?: Array<{ ref_kind: string; target: { id: string } }> };
    const dataset = refs.items?.find((r) => r.ref_kind === "dataset")?.target.id;
    if (!dataset || !refs.items?.some((r) => r.ref_kind === "champion_model")) return false;
    const profile = (await (await page.request.get(`/api/backend/v1/datasets/${dataset}/profile`)).json()) as { columns?: Array<{ role_used?: string | null }> };
    return Boolean(profile.columns?.some((c) => c.role_used));
  }, { timeout: 60_000, intervals: [1_000] }).toBe(true);
}

async function noHorizontalScroll(page: Page): Promise<void> {
  for (const width of [1280, 800, 375]) {
    await page.setViewportSize({ width, height: 900 });
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= document.documentElement.clientWidth), `no horizontal scroll at ${width}px`).toBe(true);
  }
  await page.setViewportSize({ width: 1280, height: 900 });
}

test("Data and Goal v7: strip, columns, versions, upload; goal cards and the time line to scale", async ({ page, browser }) => {
  test.setTimeout(420_000);
  await login(page);
  await page.goto("/home");
  const created = await page.request.post("/api/backend/v1/projects", { headers: await writeHeaders(page, "project"), data: { name: `Data goal v7 ${Date.now()}` } });
  expect(created.status(), await created.text()).toBe(201);
  const projectId = ((await created.json()) as { id: string }).id;

  // Goal is reachable before anything exists: an honest empty state, from the sidebar.
  await page.goto(`/projects/${projectId}/goal`);
  await expect(page.getByRole("heading", { name: "Goal & test design", level: 1 })).toBeVisible();
  await expect(page.getByText("No goal yet.")).toBeVisible();
  await expect(page.getByText(/No test design yet\. It is made when the first run starts/)).toBeVisible();
  await expect(page.getByRole("link", { name: /Status of this screen: Working today/ })).toBeVisible();
  await expect(page.getByRole("navigation", { name: "Studio" }).getByRole("link", { name: "Goal & test design" })).toHaveAttribute("href", `/projects/${projectId}/goal`);

  const upload = await page.request.post("/api/backend/v1/datasets", {
    headers: await writeHeaders(page, "upload"), multipart: { project_id: projectId, file: { name: "customers.csv", mimeType: "text/csv", buffer: Buffer.from(monthlyCsv()) } },
  });
  expect(upload.status(), await upload.text()).toBe(201);
  const datasetId = ((await upload.json()) as { id: string }).id;
  const spec = await page.request.post(`/api/backend/v1/projects/${projectId}/problem-specs`, {
    headers: await writeHeaders(page, "spec"),
    data: { task_type: "binary", business_objective: "Who will cancel next month?", target_column: "churn", primary_metric: "pr_auc", constraints: { metric_constraints: [{ metric: "recall", op: ">=", value: 0.5 }] }, status: "locked" },
  });
  expect(spec.status(), await spec.text()).toBe(201);
  const specId = ((await spec.json()) as { id: string }).id;

  // Before a run: the goal cards from the problem spec; still no test design.
  await page.goto(`/projects/${projectId}/goal`);
  const cards = page.getByRole("region", { name: "The goal" });
  await expect(cards).toContainText("churn");
  await expect(cards).toContainText("“Who will cancel next month?”");
  await expect(cards).toContainText("PR-AUC");
  await expect(cards).toContainText("Recall ≥ 0.50");
  await expect(cards).toContainText("Catch at least 50% of the rows that really are positive.");
  await expect(page.getByText(/No test design yet/)).toBeVisible();
  expect(await axeViolations(page)).toEqual([]);
  await shot(page, "uic-1-goal-before-run");

  // Data before a run: names and types only; the upload button for a person who can change ML work; no training-period values.
  await page.goto(`/projects/${projectId}/data`);
  await expect(page.getByRole("button", { name: "Upload a newer file" })).toBeVisible();
  const strip = page.getByRole("region", { name: "File summary" });
  await expect(strip).toContainText("540");
  await expect(strip).not.toContainText("training period");

  // Train one run (answer a test-design question if the rules ask one).
  const run = await page.request.post("/api/backend/v1/experiments", { headers: await writeHeaders(page, "run"), data: { project_id: projectId, dataset_id: datasetId, problem_spec_id: specId } });
  expect([201, 202], await run.text()).toContain(run.status());
  const experimentId = ((await run.json()) as { id: string }).id;
  await expect.poll(async () => {
    const body = (await (await page.request.get(`/api/backend/v1/experiments/${experimentId}`)).json()) as { status: string; lineage?: { execution_request_id?: string | null } };
    if (body.status === "needs_input" && body.lineage?.execution_request_id) {
      await page.request.post(`/api/backend/v1/execution-requests/${body.lineage.execution_request_id}/split-confirmation`, { headers: await writeHeaders(page, "split"), data: { answer: "keep_rule_split" } });
    }
    return body.status;
  }, { timeout: 240_000, intervals: [3_000] }).toBe("completed");
  await settled(page, projectId!);

  // Data after the run: training-period values, filter, why; the assistant column is hidden while AI is off.
  await page.goto(`/projects/${projectId}/data`);
  await expect(strip).toContainText("training period");
  await expect(strip).toContainText(/rows/);
  await expect(page.getByText("“Training period” values count the training rows only; the final test set is never counted.")).toBeVisible();
  await page.getByRole("tab", { name: /^Columns/ }).click();
  const columns = page.getByRole("table", { name: "Columns", exact: true });
  await expect(columns).toContainText("This is what we predict");
  await expect(columns.getByRole("columnheader", { name: "Assistant vs rules" })).toHaveCount(0);
  await page.getByLabel("Filter columns").fill("fee");
  await expect(page.getByText("Showing 1 of 6 columns")).toBeVisible();
  await expect(columns).toContainText("monthly_fee");
  await expect(columns).not.toContainText("customer_id");
  await page.getByLabel("Filter columns").fill("");
  expect(await axeViolations(page)).toEqual([]);
  await shot(page, "uic-2-data-columns");
  const dataText = await page.locator("main").innerText();
  expect(dataText).not.toMatch(/holdout|champion|\bdigest\b|service token/i);

  // Upload a newer file: a broken file gets the fixed plain sentence; a good one becomes a newer version (the old one stays in use).
  await page.getByRole("button", { name: "Upload a newer file" }).click();
  const form = page.getByRole("form", { name: "Upload a newer file" });
  await form.getByLabel(/^File/).setInputFiles({ name: "broken.xlsx", mimeType: "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", buffer: Buffer.from("not a spreadsheet") });
  await form.getByRole("button", { name: "Upload" }).click();
  await expect(form.getByText("That file could not be read as a table.")).toBeVisible();
  await expect(form.getByText(/Nothing was added\./)).toBeVisible();
  await form.getByLabel(/^File/).setInputFiles({ name: "customers_newer.csv", mimeType: "text/csv", buffer: Buffer.from(monthlyCsv()) });
  await form.getByRole("button", { name: "Upload" }).click();
  await expect(page.getByText(/Uploaded customers_newer(\.csv)? as a new version: 540 rows, 6 columns\. Nothing was trained and the data in use did not change\./)).toBeVisible();
  await page.getByRole("tab", { name: /^Versions/ }).click();
  const versions = page.getByRole("table", { name: "Data versions" });
  await expect(versions.getByRole("columnheader", { name: "File" })).toBeVisible();
  await expect(versions.getByRole("columnheader", { name: "By" })).toBeVisible();
  await expect(versions).toContainText("newer");
  await expect(versions).toContainText("in use");
  await expect(versions).toContainText("You");
  await expect(versions).toContainText(/just now|minutes? ago/);
  await expect(page.getByText(/Old files are never changed or deleted\./)).toBeVisible();
  expect(await axeViolations(page)).toEqual([]);
  await shot(page, "uic-3-data-versions");
  await page.emulateMedia({ colorScheme: "dark" });
  expect(await axeViolations(page)).toEqual([]);
  await page.emulateMedia({ colorScheme: "light" });
  await noHorizontalScroll(page);

  // Goal after the run: the sidebar opens the test design; /goal redirects to it.
  await page.goto(`/projects/${projectId}/goal`);
  await expect(page).toHaveURL(new RegExp(`/projects/${projectId}/splits/[0-9a-f-]{36}$`));
  const splitId = page.url().split("/splits/")[1];
  const plan = (await (await page.request.get(`/api/backend/v1/split-plans/${splitId}`)).json()) as { train_row_count: number; evaluation_row_count: number; folds: Array<{ fold_number: number }> };
  await expect(page.getByRole("region", { name: "The goal" })).toContainText("Recall ≥ 0.50");
  const figure = page.getByRole("img", { name: /^Test design drawn to scale by rows:/ });
  await expect(figure).toBeVisible();
  await expect(figure).toHaveAttribute("aria-label", new RegExp(`${plan.train_row_count.toLocaleString("en-GB")} training rows`));
  await expect(figure.locator("text", { hasText: "Fold 1" })).toHaveCount(1);
  await expect(figure.locator("text", { hasText: "Final test" }).first()).toBeVisible();
  const chips = page.getByRole("list", { name: "Rows of the test design" });
  await expect(chips).toContainText(`Training rows: ${plan.train_row_count.toLocaleString("en-GB")}`);
  await expect(chips).toContainText(`Final test set: ${plan.evaluation_row_count.toLocaleString("en-GB")} rows`);
  await expect(page.getByText("Why this design?")).toBeVisible();
  // No dates for the final test period, and no final test values: only its row count.
  const testCard = await page.locator("section.card", { has: page.getByRole("heading", { name: "How we test" }) }).innerText();
  expect(testCard).not.toMatch(/\b20\d\d\b|\b(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\b/);
  expect(await page.locator("main").innerText()).not.toMatch(/holdout|final_evaluation|champion/i);
  await expect(page.getByRole("navigation", { name: "Studio" }).getByRole("link", { name: "Goal & test design" })).toHaveAttribute("href", `/projects/${projectId}/splits/${splitId}`);
  expect(await axeViolations(page)).toEqual([]);
  await shot(page, "uic-4-goal");
  await page.emulateMedia({ colorScheme: "dark" });
  expect(await axeViolations(page)).toEqual([]);
  await shot(page, "uic-5-goal-dark");
  await page.emulateMedia({ colorScheme: "light" });
  await noHorizontalScroll(page);
  await page.setViewportSize({ width: 375, height: 900 });
  await shot(page, "uic-6-goal-375");
  await page.setViewportSize({ width: 1280, height: 900 });

  // A viewer reads the Data page but gets no upload or "use this data" button.
  const viewerContext = await browser.newContext({ baseURL: new URL(page.url()).origin });
  const viewer = await viewerContext.newPage();
  await viewer.goto("/login");
  await viewer.getByLabel("Email").fill(VIEWER);
  await viewer.getByLabel("Password").fill(PASSWORD);
  await viewer.getByRole("button", { name: "Sign in" }).click();
  await expect(viewer).not.toHaveURL(/\/login/);
  await viewer.goto(`/projects/${projectId}/data`);
  await expect(viewer.getByText("Only people who can change ML work in this workspace can upload a file or change the data in use.")).toBeVisible();
  await expect(viewer.getByRole("button", { name: "Upload a newer file" })).toHaveCount(0);
  await viewer.getByRole("tab", { name: /^Versions/ }).click();
  await expect(viewer.getByRole("button", { name: /Use this data/ })).toHaveCount(0);
  await viewerContext.close();
});
