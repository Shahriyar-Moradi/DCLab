import { expect, test, type Page } from "@playwright/test";
import { axeViolations, login, shot, trainedProject } from "./support/studio-fixture";

/**
 * UI-D (v7 parity): Model → Versions, Final test (2x2), Saved file & checks, columns new data must have, known limits; and
 * Predictions → header tiles, "This week's list" with reasons (50 rows a page, order), Score a file / Weekly schedule tabs.
 * A real trained model and a real scoring (needs an API + worker); refusals and older-model states are stubbed reads.
 */
const BANNED = /\b(release|champion|digest|manifest|package|pickle|skops|worker|artifact|holdout|cursor)s?\b/i;
const UUID_TEXT = /[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}/;

function scoringCsv(rows: number): string {
  const out = ["tenure,monthly_spend,customer_code,plan"];
  for (let i = 7000; i < 7000 + rows; i += 1) out.push(`${1 + ((i * 13) % 72)},${(10 + ((i * 17) % 110)).toFixed(2)},C${i},${["basic", "plus", "pro"][i % 3]}`);
  return `${out.join("\n")}\n`;
}

async function modelInUse(page: Page, projectId: string): Promise<string> {
  let id = "";
  await expect.poll(async () => {
    const refs = (await (await page.request.get(`/api/backend/v1/projects/${projectId}/refs`)).json()) as { items: Array<{ ref_kind: string; target: { id: string } }> };
    id = refs.items.find((r) => r.ref_kind === "champion_model")?.target.id ?? "";
    return id;
  }, { timeout: 150_000, intervals: [2_000] }).toMatch(/^[0-9a-f-]{36}$/);
  return id;
}

/** axe 0 serious/critical in light and dark, with a screenshot of each. */
async function a11yBoth(page: Page, name: string): Promise<void> {
  for (const colorScheme of ["light", "dark"] as const) {
    await page.emulateMedia({ colorScheme });
    expect(await axeViolations(page), `${name} (${colorScheme})`).toEqual([]);
    await shot(page, `ui-d-${name}-${colorScheme}`);
  }
  await page.emulateMedia({ colorScheme: "light" });
}

async function noSideScroll(page: Page, name: string): Promise<void> {
  for (const width of [1280, 800, 375]) {
    await page.setViewportSize({ width, height: 900 });
    await page.waitForTimeout(150);
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth), `${name} at ${width}px`).toBe(true);
    if (width === 375) await shot(page, `ui-d-${name}-375`);
  }
  await page.setViewportSize({ width: 1280, height: 900 });
}

test("Model: versions with CV numbers, the final test with its 2x2, the saved file and its checks, the columns, known limits", async ({ page }) => {
  await login(page);
  const { projectId } = await trainedProject(page, "UI-D model");
  const modelId = await modelInUse(page, projectId);
  const main = page.locator("main");

  // Versions: the first-run model is in use with no approval recorded; numbers are cross-validation only.
  await page.goto(`/projects/${projectId}/models`);
  await expect(page.getByRole("tab", { name: "Versions" })).toHaveAttribute("aria-selected", "true");
  const versions = page.getByRole("table", { name: "Model versions" });
  await expect(versions).toContainText("In use");
  await expect(versions).toContainText("no approval recorded");
  await expect(versions.getByRole("columnheader", { name: "Main score (cross-validation)" })).toBeVisible();
  await expect(main).toContainText("never the final test");
  await a11yBoth(page, "versions");

  // Final test: beside cross-validation, the row count and the 2x2 at the locked threshold.
  await page.goto(`/projects/${projectId}/models/${modelId}?tab=final`);
  await expect(page.getByRole("heading", { name: "Final test (used once per run)" })).toBeVisible();
  await expect(page.getByRole("table", { name: "Cross-validation beside the final test" })).toBeVisible();
  const matrix = page.getByRole("table", { name: "Final test rows by what the model said and what really happened" });
  await expect(matrix).toContainText("caught");
  await expect(matrix).toContainText("flagged by mistake");
  await expect(main).toContainText(/It flagged [\d,]+ of [\d,]+ rows \(\d+%\)/);
  await expect(main).toContainText(/rows in the final test/);
  expect(await main.innerText()).not.toMatch(BANNED);
  await a11yBoth(page, "final-test");
  await noSideScroll(page, "final-test");

  // Saved file & checks + the columns new data must have (names and types only).
  await page.goto(`/projects/${projectId}/models/${modelId}?tab=file`);
  await expect(main).toContainText("Safe format");
  await expect(main).toContainText(/Size[\s\S]*(KB|MB|bytes)/);
  const checks = page.getByRole("list", { name: "Checks on the saved file" });
  await expect(checks.getByRole("listitem")).toHaveCount(3);
  await expect(checks).toContainText(/\d+ training rows score exactly the same with the saved file as with the trained model\./);
  await expect(checks).not.toContainText("fail");
  const columns = page.getByRole("table", { name: "Columns new data must have" });
  // The run decides which columns the model uses (an ID-like or leaking column can be left out): two of them are always kept here.
  await expect(columns).toContainText("monthly_spend");
  await expect(columns).toContainText("plan");
  await expect(columns).toContainText("number");
  await expect(columns).toContainText("category");
  await expect(main).toContainText("The column churn is what the model predicts; it is ignored if a file has it.");
  expect(await main.innerText()).not.toMatch(BANNED);
  await a11yBoth(page, "saved-file");
  await noSideScroll(page, "saved-file");

  // Known limits on the model card.
  await page.goto(`/projects/${projectId}/models/${modelId}?tab=card`);
  const limits = page.getByRole("list", { name: "Known limits of this model" });
  await expect(limits).toContainText(/New files must have the same \d+ columns? it was trained on/);
  await expect(limits).toContainText("they are not exact chances");

  // Older models (stubbed reads): checks not recorded, no readable column list, no 2x2 sent. Never the server's words.
  await page.route(`**/api/backend/v1/model-versions/${modelId}`, async (route) => {
    if (route.request().method() !== "GET") return route.continue();
    const response = await route.fetch();
    const body = (await response.json()) as Record<string, unknown>;
    await route.fulfill({ response, json: { ...body, package_verification: { status: "not_recorded", note: "SERVER WORDS", checks: [] } } });
  });
  await page.route(`**/api/backend/v1/model-versions/${modelId}/feature-contract`, (route) =>
    route.fulfill({ status: 409, contentType: "application/json", body: JSON.stringify({ error: { code: "feature_contract_unavailable", message: "SERVER WORDS manifest", retryable: false, details: {} } }) }));
  await page.goto(`/projects/${projectId}/models/${modelId}?tab=file`);
  await expect(main).toContainText("Not recorded: this model was saved before the file checks were kept.");
  await expect(main).toContainText("The list of columns is not available for this model");
  expect(await main.innerText()).not.toContain("SERVER WORDS");
  await shot(page, "ui-d-saved-file-older");
  await page.route(`**/api/backend/v1/model-versions/${modelId}/card`, async (route) => {
    const response = await route.fetch();
    const body = (await response.json()) as { final_evaluation: Record<string, unknown> };
    await route.fulfill({ response, json: { ...body, final_evaluation: { ...body.final_evaluation, confusion: null } } });
  });
  await page.goto(`/projects/${projectId}/models/${modelId}?tab=final`);
  await expect(page.getByRole("heading", { name: "Final test (used once per run)" })).toBeVisible();
  await expect(page.getByRole("table", { name: "Final test rows by what the model said and what really happened" })).toHaveCount(0);
});

test("Predictions: tiles and this week's list with reasons from a real scoring, paging, order, refusals, tabs", async ({ page }) => {
  await login(page);
  const { projectId } = await trainedProject(page, "UI-D list");
  // The run reads "completed" a little before its model is registered and put in use.
  await modelInUse(page, projectId);
  const main = page.locator("main");

  await page.goto(`/projects/${projectId}/predictions`);
  await expect(page.getByRole("tab", { name: "This week's list" })).toHaveAttribute("aria-selected", "true");
  await expect(main).toContainText("No finished scoring is listed on this page yet.");

  // Score a file (the existing component, unchanged) in its own tab.
  await page.getByRole("tab", { name: "Score a file" }).click();
  await page.getByLabel("Scoring file").setInputFiles({ name: "this-week.csv", mimeType: "text/csv", buffer: Buffer.from(scoringCsv(120)) });
  await page.getByRole("button", { name: "Score this file" }).click();
  await expect(page.getByRole("heading", { name: /Scoring of this-week.csv done/ })).toBeVisible({ timeout: 150_000 });

  // The list: tiles from the read's own counts, 50 rows a page, highest score first, reasons as plain text.
  await page.getByRole("tab", { name: "This week's list" }).click();
  const tiles = page.getByRole("region", { name: "The list shown below" });
  await expect(tiles).toContainText("rows scored");
  await expect(tiles).toContainText("120");
  await expect(tiles).toContainText(/[\d,]+ \(\d+\.\d%\)\s*rows flagged/);
  const list = page.getByRole("table", { name: "Scored rows of this list" });
  await expect(list.locator("tbody tr")).toHaveCount(50);
  await expect(list.getByRole("columnheader", { name: /Top reasons \(what the model used, not causes\)/ })).toBeVisible();
  const scoreColumn = (await list.locator("thead th").allTextContents()).findIndex((h) => h.trim() === "Score") + 1;
  expect(scoreColumn).toBeGreaterThan(0);
  const scores = await list.locator(`tbody tr td:nth-child(${scoreColumn})`).allInnerTexts();
  const numbers = scores.map(Number);
  expect(numbers.every((n, i) => i === 0 || numbers[i - 1] >= n), `scores descend: ${scores.slice(0, 5).join(", ")}`).toBe(true);
  await expect(list.locator("tbody tr").first().locator("ol li").first()).not.toBeEmpty();
  await expect(main).toContainText("Reasons name what the model used for a score, not causes");
  await page.getByRole("button", { name: "Show the next 50 rows" }).click();
  await expect(list.locator("tbody tr")).toHaveCount(100);
  await page.getByRole("button", { name: "Show the next 50 rows" }).click();
  await expect(list.locator("tbody tr")).toHaveCount(120);
  await expect(page.getByRole("button", { name: "Show the next 50 rows" })).toHaveCount(0);
  await expect(page.getByRole("list", { name: "About this list" })).toContainText("Showing 120 of 120 rows, highest score first.");
  const text = await main.innerText();
  expect(text).not.toMatch(BANNED);
  expect(text).not.toMatch(UUID_TEXT);
  await a11yBoth(page, "list");
  await noSideScroll(page, "list");

  await page.getByLabel("Order").selectOption("row");
  await expect(list.locator("tbody tr").first().locator("td").first()).toHaveText("1");

  // Refusals (stubbed rows read): fixed sentences, never the server's words.
  const rows = "**/api/backend/v1/predictions/*/rows*";
  await page.route(rows, (route) => route.fulfill({ status: 409, contentType: "application/json", body: JSON.stringify({ error: { code: "prediction_rows_too_large", message: "SERVER WORDS", retryable: false, details: {} } }) }));
  await page.reload();
  await expect(main).toContainText("This file is too large to list here (more than 200,000 rows or 64 MB). Download it to see every row.");
  await expect(page.getByRole("button", { name: "Try again" })).toHaveCount(0);
  await page.unroute(rows);
  await page.route(rows, (route) => route.fulfill({ status: 429, contentType: "application/json", body: JSON.stringify({ error: { code: "rows_busy", message: "SERVER WORDS", retryable: true, details: {} } }) }));
  await page.reload();
  await expect(main).toContainText("Too many lists are being read right now. Wait a moment, then try again.");
  expect(await main.innerText()).not.toContain("SERVER WORDS");
  await shot(page, "ui-d-list-busy");
  await page.unroute(rows);
  await page.getByRole("button", { name: "Try again" }).click();
  await expect(page.getByRole("table", { name: "Scored rows of this list" }).locator("tbody tr")).toHaveCount(50);

  // The weekly schedule is its own tab (the existing card, unchanged).
  await page.getByRole("tab", { name: "Weekly schedule" }).click();
  await expect(page.getByRole("heading", { name: /^Weekly scoring/ })).toBeVisible();
  await page.goto(`/projects/${projectId}/predictions?tab=schedule`);
  await expect(page.getByRole("heading", { name: /^Weekly scoring/ })).toBeVisible();
  await a11yBoth(page, "schedule-tab");
});
