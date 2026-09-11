import { expect, test, type Page, type TestInfo } from "@playwright/test";
import { mkdir, readFile, writeFile } from "node:fs/promises";
import path from "node:path";

const PASSWORD = "VerificationOnly123!";
const ARTIFACTS = path.resolve(
  __dirname,
  "../../../artifacts/e2e-verification",
);

type JsonRecord = Record<string, unknown>;

async function login(page: Page, email: string): Promise<void> {
  await page.goto("/login");
  await page.getByLabel("Email").fill(email);
  await page.getByLabel("Password").fill(PASSWORD);
  await page.getByRole("button", { name: "Sign in" }).click();
  await expect(page).not.toHaveURL(/\/login/);
}

function binaryCsv(rows = 120): string {
  const values = ["age,income,region,outcome"];
  for (let index = 0; index < rows; index += 1) {
    const age = 22 + (index % 40);
    const income = 32_000 + index * 480;
    const region = index % 2 === 0 ? "N" : "S";
    const outcome = age + income / 12_000 > 30 ? 1 : 0;
    values.push(`${age},${income},${region},${outcome}`);
  }
  return `${values.join("\n")}\n`;
}

async function uploadCsv(
  page: Page,
  testInfo: TestInfo,
  filename: string,
  csv: string,
  target: string,
): Promise<JsonRecord> {
  const fixture = testInfo.outputPath(filename);
  await writeFile(fixture, csv);
  await page.goto("/app/labs");
  await expect(
    page.getByRole("heading", { name: "Labs", exact: true }),
  ).toBeVisible();
  const input = page.locator('input[type="file"]').first();
  await input.setInputFiles(fixture);
  await page
    .getByRole("combobox", { name: "Outcome column to predict" })
    .selectOption(target);
  const uploaded = page.waitForResponse(
    (response) =>
      response.request().method() === "POST" &&
      new URL(response.url()).pathname === "/api/backend/app/labs/uploads",
  );
  await page.getByRole("button", { name: "Save file" }).first().click();
  const response = await uploaded;
  expect(response.ok(), await response.text()).toBeTruthy();
  return (await response.json()) as JsonRecord;
}

async function saveDownload(
  page: Page,
  name: string,
  destination: string,
  kind: "notebook" | "script",
) {
  const button = page.getByRole("button", { name });
  await expect(button).toBeVisible();
  await expect(button).toBeEnabled();
  const [download, response] = await Promise.all([
    page.waitForEvent("download"),
    page.waitForResponse(
      (item) =>
        item.request().method() === "GET" &&
        item.url().includes(`/model-build/reproduction/${kind}/download`),
    ),
    button.click(),
  ]);
  expect(response.ok(), await response.text()).toBeTruthy();
  await download.saveAs(destination);
  return {
    filename: download.suggestedFilename(),
    body: await readFile(destination),
  };
}

test.describe.configure({ mode: "serial" });

test("Model Build inspector, live stages, code, candidates, and downloads", async ({
  page,
}, testInfo) => {
  test.setTimeout(240_000);
  await mkdir(ARTIFACTS, { recursive: true });
  await login(page, "business-admin-a@verification.invalid");
  const upload = await uploadCsv(
    page,
    testInfo,
    "model-build-binary.csv",
    binaryCsv(),
    "outcome",
  );
  await expect(page).toHaveURL(new RegExp(`/lab/runs/${String(upload.run_id)}`));

  const modelBuild = page.getByRole("heading", { name: "Model build", exact: true });
  await expect(modelBuild).toBeVisible({ timeout: 120_000 });
  const rail = page.locator("ol.model-build-rail");
  await expect(rail).toBeVisible({ timeout: 120_000 });

  let sawRunning = false;
  await expect
    .poll(
      async () => {
        if ((await page.locator(".model-build-step-running").count()) > 0) sawRunning = true;
        if ((await page.getByText("· live").count()) > 0) sawRunning = true;
        return page.getByRole("button", { name: "Download notebook" }).count();
      },
      { timeout: 180_000, intervals: [250, 500, 1_000] },
    )
    .toBe(1);
  expect(sawRunning, "Model Build should expose a running stage or live indicator").toBeTruthy();
  await expect(page.getByRole("button", { name: "Download script" })).toBeVisible();

  const steps = page.locator("button.model-build-step");
  await expect(steps).toHaveCount(21);
  const stepCount = await steps.count();
  for (let index = 0; index < stepCount; index += 1) {
    const step = steps.nth(index);
    const title = (await step.locator("span.text-helper").innerText()).trim();
    await step.evaluate((node) => {
      node.scrollIntoView({ block: "nearest", inline: "center" });
      (node as HTMLElement).click();
    });
    await expect(page.getByRole("heading", { name: title, exact: true })).toBeVisible();
    await expect(page.locator(".model-build-code-source")).toBeVisible();
    await expect(page.getByText("Generated Python", { exact: true })).toBeVisible();
  }

  await page.getByRole("button", { name: /Candidate generation/ }).evaluate((node) => {
    node.scrollIntoView({ block: "nearest", inline: "center" });
    (node as HTMLElement).click();
  });
  await expect(page.getByRole("heading", { name: "Candidate generation" })).toBeVisible();
  const candidateCards = page.locator("button:not(.model-build-step)").filter({
    hasText: /\d+ folds?/,
  });
  const candidateCount = await candidateCards.count();
  expect(candidateCount).toBeGreaterThan(1);
  await candidateCards.first().evaluate((node) => (node as HTMLElement).click());
  await expect(page.locator("dt").first()).toBeVisible();
  await candidateCards.nth(Math.min(1, candidateCount - 1)).evaluate((node) => (node as HTMLElement).click());

  await page.getByRole("button", { name: /CV training/ }).evaluate((node) => {
    node.scrollIntoView({ block: "nearest", inline: "center" });
    (node as HTMLElement).click();
  });
  await expect(page.getByRole("heading", { name: "CV training" })).toBeVisible();
  await expect(page.getByRole("columnheader", { name: /fold/i })).toBeVisible();
  await expect(page.getByRole("columnheader", { name: /metrics/i })).toBeVisible();

  const notebook = await saveDownload(
    page,
    "Download notebook",
    path.join(ARTIFACTS, "model-build-reproduction.ipynb"),
    "notebook",
  );
  const script = await saveDownload(
    page,
    "Download script",
    path.join(ARTIFACTS, "model-build-reproduction.py"),
    "script",
  );
  expect(notebook.filename).toMatch(/reproduction\.ipynb$/i);
  expect(script.filename).toMatch(/reproduction\.py$/i);
  const notebookText = notebook.body.toString("utf8");
  const scriptText = script.body.toString("utf8");
  const parsed = JSON.parse(notebookText) as { nbformat: number; cells: unknown[] };
  expect(parsed.nbformat).toBe(4);
  expect(parsed.cells.length).toBeGreaterThan(18);
  expect(notebookText).toContain("<authorized-local-dataset-path>");
  expect(scriptText).toContain("<authorized-local-dataset-path>");
  expect(notebookText).not.toContain("PRIVATE-ROW");
  expect(scriptText).not.toContain("sk-proj-");
  await page.screenshot({
    path: path.join(ARTIFACTS, "model-build-inspector.png"),
    fullPage: true,
  });
});
