import { expect, test, type Page } from "@playwright/test";
import { axeViolations, login, shot } from "./support/studio-fixture";

/**
 * D3b on a real stack (API + worker, AI off): "Target over time" with the final-test months sealed (a shaded band, no line,
 * point or rate) for a file whose final test has not run for the model in use, and drawn apart once it has; the upload
 * log with a rejected upload and its DCLab-written reason; "Checked on upload" vs "Checked inside each run"; and the
 * Goal page's "Who decided" table (four rows).
 */
type Period = { status: string; buckets: Array<{ part: string }>; final_test: { layout: string; sealed: boolean; rate: number | null; start_month: string | null; end_month: string | null; row_count: number; disclosed_after_final_test: boolean } | null };

/** 18 monthly snapshots. The rule only orders the split by time for a strongly named date column ("as_of_…"): a
 * "snapshot_month" column (18 values over 540 rows) is a date candidate only, which gives a random final test. */
function monthlyCsv(seed = 0): string {
  const rows = ["as_of_date,customer_id,tenure,monthly_fee,plan,churn"];
  for (let i = 0; i < 540; i += 1) {
    const month = new Date(Date.UTC(2025, Math.floor(i / 30), 1)).toISOString().slice(0, 10);
    const tenure = 1 + ((i * 13 + seed) % 72);
    const churn = tenure < 14 && i % 4 !== 0 ? "yes" : i % 9 === 0 ? "yes" : "no";
    rows.push(`${month},C${1000 + i},${tenure},${(10 + ((i * 17) % 110)).toFixed(2)},${["basic", "plus", "pro"][i % 3]},${churn}`);
  }
  return `${rows.join("\n")}\n`;
}

async function writeHeaders(page: Page, key: string): Promise<Record<string, string>> {
  const csrf = (await page.context().cookies()).find((c) => c.name === "dclab_csrf")?.value ?? "";
  return { "Idempotency-Key": `e2e-d3b-${key}-${Date.now()}`, "X-CSRF-Token": csrf, Origin: new URL(page.url()).origin };
}

async function train(page: Page, projectId: string, datasetId: string, specId: string, key: string): Promise<string> {
  const run = await page.request.post("/api/backend/v1/experiments", { headers: await writeHeaders(page, key), data: { project_id: projectId, dataset_id: datasetId, problem_spec_id: specId } });
  expect([201, 202], await run.text()).toContain(run.status());
  const experimentId = ((await run.json()) as { id: string }).id;
  await expect.poll(async () => {
    const body = (await (await page.request.get(`/api/backend/v1/experiments/${experimentId}`)).json()) as { status: string; lineage?: { execution_request_id?: string | null } };
    if (body.status === "needs_input" && body.lineage?.execution_request_id) {
      await page.request.post(`/api/backend/v1/execution-requests/${body.lineage.execution_request_id}/split-confirmation`, { headers: await writeHeaders(page, `${key}-split`), data: { answer: "keep_rule_split" } });
    }
    return body.status;
  }, { timeout: 300_000, intervals: [3_000] }).toBe("completed");
  return experimentId;
}

async function axeBothThemes(page: Page, label: string): Promise<void> {
  for (const width of [1280, 375]) {
    await page.setViewportSize({ width, height: 900 });
    for (const scheme of ["light", "dark"] as const) {
      await page.emulateMedia({ colorScheme: scheme });
      expect(await axeViolations(page), `${label} ${scheme} ${width}px`).toEqual([]);
      expect(await page.evaluate(() => document.documentElement.scrollWidth <= document.documentElement.clientWidth), `${label}: no horizontal scroll at ${width}px`).toBe(true);
      await shot(page, `d3b-${label}-${scheme}-${width}`);
    }
  }
  await page.emulateMedia({ colorScheme: "light" });
  await page.setViewportSize({ width: 1280, height: 900 });
}

/** No training point sits inside the sealed band, and the band is drawn. */
async function bandHasNoPoints(page: Page): Promise<void> {
  const chart = page.getByTestId("churn-chart");
  const band = chart.getByTestId("sealed-band");
  await expect(band).toHaveCount(1);
  const box = await band.boundingBox();
  expect(box).not.toBeNull();
  const dots = await chart.locator(".v7-mon-dot").evaluateAll((els) => els.map((el) => { const r = el.getBoundingClientRect(); return r.left + r.width / 2; }));
  expect(dots.length).toBeGreaterThan(0);
  for (const x of dots) expect(x < box!.x || x > box!.x + box!.width, `a point at ${x} is inside the band`).toBe(true);
  await expect(chart.locator(".v7-mon-dot.s2")).toHaveCount(0);
}

test("D3b: target over time sealed then disclosed, upload log with a rejected upload, checks on upload, who decided", async ({ page }) => {
  test.setTimeout(900_000);
  await login(page);
  await page.goto("/home");
  const created = await page.request.post("/api/backend/v1/projects", { headers: await writeHeaders(page, "project"), data: { name: `D3b ${Date.now()}` } });
  expect(created.status(), await created.text()).toBe(201);
  const projectId = ((await created.json()) as { id: string }).id;
  const upload = await page.request.post("/api/backend/v1/datasets", {
    headers: await writeHeaders(page, "upload"), multipart: { project_id: projectId, file: { name: "customers.csv", mimeType: "text/csv", buffer: Buffer.from(monthlyCsv()) } },
  });
  expect(upload.status(), await upload.text()).toBe(201);
  const firstId = ((await upload.json()) as { id: string }).id;
  const spec = await page.request.post(`/api/backend/v1/projects/${projectId}/problem-specs`, {
    headers: await writeHeaders(page, "spec"),
    data: { task_type: "binary", business_objective: "Who will cancel next month?", target_column: "churn", primary_metric: "pr_auc", constraints: { metric_constraints: [{ metric: "recall", op: ">=", value: 0.5 }] }, status: "locked" },
  });
  expect(spec.status(), await spec.text()).toBe(201);
  const specId = ((await spec.json()) as { id: string }).id;

  // Before any run: no test design, so a plain sentence and no chart.
  await page.goto(`/projects/${projectId}/data`);
  await page.getByRole("tab", { name: /^Target over time/ }).click();
  await expect(page.getByTestId("churn-sentence")).toHaveText(/^No test design uses this data version yet, so nothing is counted\./);
  await expect(page.getByTestId("churn-chart")).toHaveCount(0);

  // Run 1 on the first file: its model becomes the model in use and its final test runs at the end.
  await train(page, projectId, firstId, specId, "run1");
  await expect.poll(async () => {
    const refs = (await (await page.request.get(`/api/backend/v1/projects/${projectId}/refs`)).json()) as { items?: Array<{ ref_kind: string }> };
    return Boolean(refs.items?.some((r) => r.ref_kind === "champion_model"));
  }, { timeout: 60_000, intervals: [1_000] }).toBe(true);

  // Upload log through the page: a broken file is rejected (with a DCLab reason), a newer file is added.
  await page.goto(`/projects/${projectId}/data`);
  await page.getByRole("button", { name: "Upload a newer file" }).click();
  const form = page.getByRole("form", { name: "Upload a newer file" });
  await form.getByLabel(/^File/).setInputFiles({ name: "customers_extra.xlsx", mimeType: "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", buffer: Buffer.from("not a spreadsheet") });
  await form.getByRole("button", { name: "Upload" }).click();
  await expect(form.getByText(/Nothing was added\./)).toBeVisible();
  await form.getByLabel(/^File/).setInputFiles({ name: "customers_newer.csv", mimeType: "text/csv", buffer: Buffer.from(monthlyCsv(5)) });
  await form.getByRole("button", { name: "Upload" }).click();
  await expect(page.getByText(/Uploaded customers_newer(\.csv)? as a new version/)).toBeVisible();
  const attempts = (await (await page.request.get(`/api/backend/v1/projects/${projectId}/upload-attempts`)).json()) as { retention_days: number; items: Array<{ file_name: string; outcome: string; reason: string | null; dataset_id: string | null }> };
  const rejected = attempts.items.find((a) => a.outcome === "rejected");
  expect(rejected?.reason, JSON.stringify(attempts.items)).toBeTruthy();
  const newerId = attempts.items.find((a) => a.file_name.startsWith("customers_newer"))?.dataset_id;
  expect(newerId).toBeTruthy();
  await page.getByRole("tab", { name: /^Versions/ }).click();
  const log = page.getByRole("table", { name: "Upload log" });
  const rejectedRow = log.getByRole("row", { name: /customers_extra\.xlsx/ });
  await expect(rejectedRow).toContainText("rejected");
  await expect(rejectedRow).toContainText(rejected!.reason!);
  await expect(rejectedRow).toContainText("You");
  await expect(rejectedRow.getByRole("link")).toHaveCount(0);
  const addedRow = log.getByRole("row", { name: /customers_newer/ });
  await expect(addedRow).toContainText("added");
  await expect(addedRow.getByRole("link", { name: /Open this version/ })).toHaveAttribute("href", `/projects/${projectId}/data/${newerId}`);
  await expect(log.getByRole("row")).toHaveCount(4); // header + 3 attempts
  await expect(page.getByText(`Upload attempts are kept for ${attempts.retention_days} days. Each one keeps the file name, size, outcome and reason, who uploaded it and when — never the file's contents or any row values.`)).toBeVisible();
  await axeBothThemes(page, "data-versions");

  // Run 2 on the newer file: the model in use stays run 1's, so the newer file's final test has not run for it.
  await train(page, projectId, newerId!, specId, "run2");
  const sealed = (await (await page.request.get(`/api/backend/v1/datasets/${newerId}/target-by-period`)).json()) as Period;
  expect(sealed.status, JSON.stringify(sealed)).toBe("computed");
  expect(sealed.final_test?.layout).toBe("after_training");
  expect(sealed.final_test?.sealed).toBe(true);
  expect(sealed.final_test?.rate).toBeNull();
  expect(sealed.buckets.every((b) => b.part === "training")).toBe(true);

  await page.goto(`/projects/${projectId}/data`);
  await page.getByRole("tab", { name: /^Versions/ }).click();
  await page.getByRole("table", { name: "Data versions" }).getByRole("row", { name: /customers_newer/ }).filter({ hasNotText: "made by a run" }).first().getByRole("button", { name: /Show columns/ }).click();
  await page.getByRole("tab", { name: /^Target over time/ }).click();
  const chart = page.getByTestId("churn-chart");
  await expect(chart.getByTestId("sealed-band-label")).toContainText(`Final test — sealed until the final test runs · ${sealed.final_test!.row_count.toLocaleString("en-GB")} rows`);
  await expect(chart).not.toContainText("Disclosed after the final test");
  await expect(chart).toContainText("Months are calendar months in UTC.");
  await bandHasNoPoints(page);
  const summary = await chart.getByRole("img").getAttribute("aria-label");
  expect(summary).toMatch(/Final test — sealed until the final test runs \([\d,]+ rows\); no rate is shown for these months\./);
  await chart.getByText("The numbers as a table").click();
  const bandRow = chart.getByRole("row", { name: /Final test — sealed/ });
  await expect(bandRow.getByRole("cell").last()).toHaveText("sealed");
  await shot(page, "d3b-churn-sealed");
  await axeBothThemes(page, "data-churn-sealed");

  // The first file, whose final test ran for the model in use: final-test months drawn apart, labelled.
  const disclosed = (await (await page.request.get(`/api/backend/v1/datasets/${firstId}/target-by-period`)).json()) as Period;
  expect(disclosed.final_test?.disclosed_after_final_test).toBe(true);
  await page.goto(`/projects/${projectId}/data`);
  await page.getByRole("tab", { name: /^Target over time/ }).click();
  await expect(chart.getByTestId("sealed-band")).toHaveCount(0);
  await expect(chart.getByRole("list", { name: "What the chart shows" })).toContainText("Disclosed after the final test");
  await expect(chart.locator(".v7-mon-dot.s2").first()).toBeAttached();
  await shot(page, "d3b-churn-disclosed");

  // Data checks: checked on upload (the whole file) vs checked inside each run (leakage and the 15 trust checks).
  await page.getByRole("tab", { name: /^Data checks/ }).click();
  const onUpload = page.getByTestId("checked-on-upload");
  await expect(onUpload.getByRole("heading", { name: "Checked on upload" })).toBeVisible();
  await expect(onUpload).toContainText(/UTC/);
  await expect(onUpload.getByRole("table", { name: "Checked on upload" })).toContainText("ID-like columns");
  // The intro says a warning is "not a failure"; no check result itself reads as a failure.
  await expect(onUpload.getByRole("table", { name: "Checked on upload" })).not.toContainText(/fail/i);
  const inRun = page.getByTestId("checked-in-run");
  await expect(inRun.getByRole("heading", { name: "Checked inside each run" })).toBeVisible();
  await expect(inRun).toContainText("Leakage and the run's 15 trust checks are checked inside each run once the goal is known. Most use the training rows; four compare the training rows with the final test set's columns, never its answers.");
  await axeBothThemes(page, "data-checks");
  expect(await page.locator("main").innerText()).not.toMatch(/holdout|champion|\bdigest\b|service token/i);

  // Goal: "Who decided", four rows in a fixed order.
  await page.goto(`/projects/${projectId}/goal`);
  const who = page.getByTestId("who-decided").getByRole("table", { name: "Who decided" });
  await expect(who.getByRole("row")).toHaveCount(5); // header + 4
  for (const col of ["What", "Value", "Suggested by", "Confirmed by", "When", "Why"]) await expect(who.getByRole("columnheader", { name: col, exact: true })).toBeVisible();
  const whats = await who.locator("tbody tr td:first-child").allInnerTexts();
  expect(whats).toEqual(["What we predict", "Test design", "Ranking metric", "Business rule"]);
  await expect(who).not.toContainText("undefined");
  await shot(page, "d3b-goal-who-decided");
  await axeBothThemes(page, "goal");
});
