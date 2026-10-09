import { expect, test, type Page } from "@playwright/test";
import fs from "node:fs";
import path from "node:path";

/** P4.11-UI: the model card on the model page: drivers, baseline, the labelled final evaluation, risks, Markdown download, print layout. */
const EMAIL = process.env.DCLAB_E2E_STUDIO_EMAIL ?? "business-admin-a@verification.invalid";
const PASSWORD = process.env.DCLAB_E2E_STUDIO_PASSWORD ?? "VerificationOnly123!";
const AXE = path.resolve(__dirname, "../node_modules/axe-core/axe.min.js");

function churnCsv(rows: number): string {
  const out = ["tenure,monthly_spend,customer_code,plan,churn"];
  for (let i = 0; i < rows; i += 1) {
    const tenure = 1 + ((i * 13) % 72);
    out.push([tenure, (10 + ((i * 17) % 110)).toFixed(2), `C${1000 + i}`, ["basic", "plus", "pro"][i % 3], tenure < 12 && i % 5 !== 0 ? "yes" : "no"].join(","));
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
  if (dir) await page.screenshot({ path: path.join(dir, `card-${name}.png`), fullPage: true });
}

test("Model card: drivers, baseline, labelled final evaluation, risks, Markdown download, print layout", async ({ page }) => {
  await login(page);
  await page.goto("/projects/new");
  await page.getByLabel("Project name").fill(`Card ${Date.now()}`);
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

  await expect.poll(async () => ((await (await page.request.get(`/api/backend/v1/projects/${projectId}/refs`)).json()) as { items: Array<{ ref_kind: string }> }).items.some((r) => r.ref_kind === "champion_model"), { timeout: 60_000 }).toBe(true);

  // Models list: the version, the champion marker, a link to the card.
  await page.goto(`/projects/${projectId}/models`);
  await expect(page.getByRole("heading", { name: "Model", level: 1 })).toBeVisible();
  const row = page.getByRole("table", { name: "Models" }).getByRole("row").nth(1);
  await expect(row).toContainText("In use");
  await expect(row).toContainText("Open card");
  await expect(row).toContainText("Run 1");
  await expect(row).toContainText("In use"); // the only model is the one in use
  await expect(row).not.toContainText("Not in use");
  await shot(page, "1-models-list");
  await page.getByRole("link", { name: /^Open the card of/ }).click();
  await expect(page).toHaveURL(/\/models\/[0-9a-f-]{36}\?tab=card$/);
  const modelId = page.url().split("/models/")[1].split("?")[0];

  // The Summary tab shows no final test and points to the Model card tab.
  await page.goto(`/projects/${projectId}/models/${modelId}`);
  await expect(page.getByText("is on the Model card tab")).toBeVisible();
  // The summary is one plain sentence that names the run and says whether the model is in use.
  await expect(page.getByRole("heading", { name: /^Model v\d+$/, level: 1 })).toBeVisible();
  await expect(page.getByRole("tabpanel", { name: "Summary" }).locator(".mc-words")).toContainText(/built by Run 1.*It is the model in use\./);
  await expect(page.getByRole("tabpanel", { name: "Summary" })).not.toContainText(/champion|holdout|fingerprint|lineage/i);
  await expect(page.getByText(/P4\.11-UI/)).toHaveCount(0);
  await expect(page.getByRole("heading", { name: /Final test/ })).toHaveCount(0);
  const apiCard = (await (await page.request.get(`/api/backend/v1/model-versions/${modelId}/card`)).json()) as { final_evaluation: { value: number } };
  await expect(page.getByRole("tabpanel", { name: "Summary" })).not.toContainText(String(Number(apiCard.final_evaluation.value.toPrecision(3))));

  await page.getByRole("tab", { name: "Model card" }).click();
  const card = page.locator(".model-card-print");
  await expect(card.getByRole("heading", { name: /^Model card:/ })).toBeVisible();
  await expect(card.getByRole("heading", { name: "What it predicts" })).toBeVisible();

  // Drivers from stored importance.
  const drivers = card.getByRole("list", { name: "Columns that matter most, by importance" });
  await expect(drivers).toBeVisible();
  const api = (await (await page.request.get(`/api/backend/v1/model-versions/${modelId}/card`)).json()) as { drivers: { features: Array<{ rank: number; column: string }> }; final_evaluation: { value: number } };
  const top = [...api.drivers.features].sort((a, b) => a.rank - b.rank);
  await expect(drivers.getByRole("listitem")).toHaveCount(top.length);
  await expect(drivers.getByRole("listitem").first()).toContainText(top[0].column);
  // Baseline comparison.
  await expect(card.getByRole("heading", { name: /dummy baseline/ })).toBeVisible();
  await expect(card.getByText(/Dummy baseline \(.*cross-validation\)/)).toBeVisible();
  // The labelled single final evaluation, in its own section.
  const final = page.getByRole("region", { name: /^Final test \(used once per run\)/ });
  await expect(final).toBeVisible();
  await expect(final).toContainText("scored them once, for its chosen model only");
  await expect(final).toContainText("not a cross-validation score");
  await expect(final).toContainText("on the final test");
  await expect(final).toContainText(String(Number(api.final_evaluation.value.toPrecision(3))));
  await expect(final).toContainText("never used for selection");
  await expect(final).toContainText("Do not compare final tests across models to pick one");
  // Plain words: no holdout or code words anywhere on the card.
  await expect(card).not.toContainText(/hold-?out|out-of-fold|feature contract|Pareto/i);
  // Risks and the link to the experiment's Findings card.
  await expect(card.getByRole("heading", { name: "Known limits and risks" })).toBeVisible();
  await expect(card.getByRole("link", { name: /Trust checks card/ })).toHaveAttribute("href", `/projects/${projectId}/experiments/${experimentId}#findings`);
  await expect(card.getByText("AI used: no")).toBeVisible();
  await expect(card.getByRole("heading", { name: "Data and test design" })).toBeVisible();
  await expect(card.getByText("counts only")).toBeVisible();
  await shot(page, "2-card");
  expect(await axeViolations(page)).toEqual([]);

  // Markdown download: plain text, starts with the title, contains the labelled final evaluation.
  const [download] = await Promise.all([page.waitForEvent("download"), card.getByRole("button", { name: "Download Markdown" }).click()]);
  expect(download.suggestedFilename()).toBe(`model-card-${modelId.slice(0, 8)}.md`);
  const md = fs.readFileSync((await download.path())!, "utf8");
  expect(md.startsWith("# Model card:")).toBe(true);
  expect(md).toContain("## Final evaluation");
  expect(md).toContain("Single final evaluation of the locked winner on held-out rows; never used for selection.");
  expect(md).toContain("LLM used: no");
  expect(page.url()).not.toMatch(/token|signature/i);

  // Print layout: the shell chrome is hidden, the card stays.
  await page.emulateMedia({ media: "print" });
  await expect(page.locator(".studio .nav")).toBeHidden();
  await expect(page.locator(".studio .topbar")).toBeHidden();
  await expect(page.getByRole("tablist")).toBeHidden();
  await expect(card.getByRole("button", { name: "Print / save as PDF" })).toBeHidden();
  await expect(card.getByRole("heading", { name: /^Model card:/ })).toBeVisible();
  await expect(final).toBeVisible();
  const colours = await final.evaluate((el) => ({ color: getComputedStyle(el).color, background: getComputedStyle(el).backgroundColor }));
  expect(colours.color).toBe("rgb(0, 0, 0)");
  await shot(page, "3-print");
  await page.emulateMedia({ media: "screen" });

  // The print button calls window.print().
  await page.evaluate(() => { (window as unknown as { __printed: number }).__printed = 0; window.print = () => { (window as unknown as { __printed: number }).__printed += 1; }; });
  await card.getByRole("button", { name: "Print / save as PDF" }).click();
  expect(await page.evaluate(() => (window as unknown as { __printed: number }).__printed)).toBe(1);

  // Dark mode.
  await page.emulateMedia({ colorScheme: "dark" });
  expect(await axeViolations(page)).toEqual([]);
  await shot(page, "4-dark");

  // A link that is not a model id never shows a name, an in-use badge or a loading state.
  const bad = await page.goto(`/projects/${projectId}/models/not-a-model`);
  if (bad?.status() !== 404) {
    await expect(page.getByRole("heading", { name: "Model not found", level: 1 })).toBeVisible();
    await expect(page.getByText("not found", { exact: true })).toBeVisible();
    await expect(page.getByText(/^Loading/)).toHaveCount(0);
  }

  // Mobile: no horizontal scroll on the summary, threshold, card and score tabs.
  await page.emulateMedia({ colorScheme: "light" });
  await page.setViewportSize({ width: 375, height: 812 });
  for (const tab of ["version", "threshold", "card", "score"]) {
    await page.goto(`/projects/${projectId}/models/${modelId}?tab=${tab}`);
    await expect(page.getByRole("heading", { name: /^Model v\d+$/, level: 1 })).toBeVisible();
    await expect(page.getByText(/^Loading/)).toHaveCount(0);
    // Charts size themselves after layout, so give the page a moment to settle before judging.
    await expect.poll(async () => page.evaluate(() => {
      if (document.documentElement.scrollWidth <= window.innerWidth) return [];
      const clipped = (el: HTMLElement) => { for (let p = el.parentElement; p && p !== document.body; p = p.parentElement) if (/(auto|scroll|hidden|clip)/.test(getComputedStyle(p).overflowX)) return true; return false; };
      return [...document.querySelectorAll<HTMLElement>("main *")]
        .filter((el) => el.getBoundingClientRect().right > window.innerWidth + 1 && !clipped(el) && !el.classList.contains("sr-only") && ![...el.children].some((c) => c.getBoundingClientRect().right > window.innerWidth + 1))
        .map((el) => `${el.tagName.toLowerCase()}.${el.className}@${Math.round(el.getBoundingClientRect().right)}w${Math.round(el.getBoundingClientRect().width)}`);
    }), { message: `no horizontal scroll on ${tab}`, timeout: 8_000 }).toEqual([]);
    expect(await axeViolations(page)).toEqual([]);
  }
});
