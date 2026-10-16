import { expect, test, type Page } from "@playwright/test";
import { axeViolations, churnCsv, login, shot } from "./support/studio-fixture";

/**
 * D2b (founder decision 15): New project is a three-step dialog on a real stack. 1 a name and a file (creates the project
 * and uploads), 2 what to predict and a success rule (nothing sent), 3 the test design the rules will make, which the
 * person confirms. Nothing is trained before the confirm; a double click starts exactly one run. Home's "Upload data"
 * opens the same dialog and the older four-step page stays reachable. axe light + dark at 1280, 800 and 375 px.
 */
const SIZES = [1280, 800, 375] as const;

async function axeEverywhere(page: Page, where: string): Promise<void> {
  for (const width of SIZES) {
    await page.setViewportSize({ width, height: 900 });
    for (const scheme of ["light", "dark"] as const) {
      await page.emulateMedia({ colorScheme: scheme });
      expect(await axeViolations(page), `${where} ${width}px (${scheme})`).toEqual([]);
    }
    expect(await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth), `${where} ${width}px side scroll`).toBeLessThanOrEqual(1);
  }
  await page.emulateMedia({ colorScheme: "light" });
  await page.setViewportSize({ width: 1280, height: 900 });
}

async function runCount(page: Page, projectId: string): Promise<number> {
  const listed = await page.request.get(`/api/backend/v1/experiments?project_id=${projectId}&limit=10`);
  expect(listed.ok()).toBeTruthy();
  return ((await listed.json()) as { items: unknown[] }).items.length;
}

test("New project dialog: name and file, what to predict, confirm the test design, then training starts", async ({ page }) => {
  await login(page);
  await page.goto("/projects");
  await page.getByRole("button", { name: "New project" }).click();
  const dialog = page.getByRole("dialog", { name: "New project" });
  await expect(dialog).toBeVisible();
  await expect(dialog.getByRole("navigation", { name: "New project steps" }).getByRole("listitem")).toHaveCount(3);
  await expect(dialog.getByRole("link", { name: "Use the four-step page" })).toHaveAttribute("href", "/projects/new");
  await shot(page, "d2b-dialog-1-file");
  await axeEverywhere(page, "dialog step 1");

  // 1 Name and file: creates the project and uploads the file; nothing is trained.
  const name = `Dialog churn ${Date.now()}`;
  await dialog.getByLabel("Project name").fill(name);
  await dialog.getByLabel("Data file").setInputFiles({ name: "churn.csv", mimeType: "text/csv", buffer: Buffer.from(churnCsv()) });
  await dialog.getByRole("button", { name: "Upload and continue" }).click();

  // 2 What to predict: the column list comes from the upload read.
  await expect(dialog.getByRole("combobox", { name: "Column to predict" }).locator("option", { hasText: "churn" })).toHaveCount(1);
  const projects = (await page.request.get("/api/backend/v1/projects").then((r) => r.json())) as Array<{ id: string; name: string }>;
  const projectId = projects.find((p) => p.name === name)?.id;
  expect(projectId).toBeTruthy();
  expect(await runCount(page, projectId!)).toBe(0);
  await dialog.getByRole("combobox", { name: /^Kind of answer/ }).selectOption("binary");
  await dialog.getByRole("combobox", { name: "Column to predict" }).selectOption("churn");
  await dialog.getByLabel("Score that picks the best model").selectOption("pr_auc");
  await dialog.getByLabel("Success rule (optional)").selectOption("recall");
  await dialog.getByLabel("Value").fill("0.5");
  await shot(page, "d2b-dialog-2-goal");
  await axeEverywhere(page, "dialog step 2");
  await dialog.getByRole("button", { name: "Continue" }).click();

  // 3 Test design: the rules' words; still nothing trained until the confirm.
  const design = dialog.getByRole("group", { name: "Test design suggested by the rules" });
  await expect(design).toContainText("A random 20% of the rows is set aside as the final test set, keeping the share of each answer the same in both parts.");
  await expect(design.getByRole("listitem")).toHaveCount(3);
  await expect(dialog).toContainText("churn");
  await expect(dialog).toContainText(/recall at least 0\.5/i);
  expect(await runCount(page, projectId!)).toBe(0);
  await shot(page, "d2b-dialog-3-design");
  await axeEverywhere(page, "dialog step 3");

  // Back keeps the answers; a double click on the confirm starts exactly one run.
  await dialog.getByRole("button", { name: "Back" }).click();
  await expect(dialog.getByRole("combobox", { name: "Column to predict" })).toHaveValue("churn");
  await dialog.getByRole("button", { name: "Continue" }).click();
  await dialog.getByRole("button", { name: "Confirm and start training" }).dblclick();
  await expect(page).toHaveURL(new RegExp(`/projects/${projectId}/experiments/[0-9a-f-]{36}$`));
  expect(await runCount(page, projectId!)).toBe(1);
  const spec = (await page.request.get(`/api/backend/v1/projects/${projectId}/problem-specs`).then((r) => r.json())) as { items?: Array<{ target_column?: string; primary_metric?: string }> } | Array<{ target_column?: string; primary_metric?: string }>;
  const specs = Array.isArray(spec) ? spec : spec.items ?? [];
  expect(specs.map((s) => [s.target_column, s.primary_metric])).toEqual([["churn", "pr_auc"]]);
  await shot(page, "d2b-dialog-4-run");
});

test("Home's Upload data opens the same dialog; the four-step page stays reachable", async ({ page }) => {
  await login(page);
  await page.goto("/home");
  await page.locator(".page-head").getByRole("button", { name: "Upload data" }).click();
  const dialog = page.getByRole("dialog", { name: "New project" });
  await expect(dialog).toBeVisible();
  await expect(dialog.getByLabel("Project name")).toBeVisible();
  await page.keyboard.press("Escape");
  await expect(dialog).toBeHidden();
  await page.goto("/projects/new");
  await expect(page.getByRole("heading", { name: "Create a project from your data", level: 1 })).toBeVisible();
});
