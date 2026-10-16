import { expect, test, type Page } from "@playwright/test";
import { axeViolations, login, proposeDecision, shot, trainedProject } from "./support/studio-fixture";

/**
 * V7 UI-B: Home v7 (greeting, Upload data, what needs you with a sentence and a button, the latest project as eight tiles,
 * Running now with the fold, activity with model and CV score, See all), Projects v7 (Where it is, the CV score of the
 * model in use, last run, explainer, the three-step New project dialog) and the project flow bar as tiles.
 * Real API + worker and a real trained run; only the fold of a running run is injected into the project list read (a real
 * run finishes too fast to catch mid-fold), and one test fails the flow read on purpose.
 */
test.describe.configure({ mode: "serial" });

let projectId = "";
let projectName = "";

async function axeBoth(page: Page, where: string): Promise<void> {
  for (const scheme of ["light", "dark"] as const) {
    await page.emulateMedia({ colorScheme: scheme });
    expect(await axeViolations(page), `${where} (${scheme})`).toEqual([]);
  }
  await page.emulateMedia({ colorScheme: "light" });
}

async function noPageScroll(page: Page, where: string): Promise<void> {
  expect(await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth), where).toBeLessThanOrEqual(0);
}

test("Home v7 on a trained project: greeting, what needs you, eight tiles, activity with model and score", async ({ page }) => {
  await login(page);
  const trained = await trainedProject(page, "Home v7");
  projectId = trained.projectId;
  projectName = trained.name;
  await proposeDecision(page, projectId, trained.experimentId, "Home v7 proposal");
  // The test design is recorded with the run's results; wait until the project uses it.
  await expect.poll(async () => {
    const refs = (await page.request.get(`/api/backend/v1/projects/${projectId}/refs`).then((r) => r.json())) as { items: Array<{ ref_kind: string }> };
    return refs.items.some((r) => r.ref_kind === "split_plan");
  }, { message: "the trained run has a test design in use", timeout: 30_000 }).toBe(true);

  await page.goto("/home");
  await expect(page.getByRole("heading", { level: 1, name: /^Good (morning|afternoon|evening)/ })).toBeVisible();
  const head = page.locator(".page-head");
  await expect(head.getByRole("button", { name: "Upload data" })).toHaveAttribute("aria-haspopup", "dialog");
  await expect(head).toContainText(/\d+ things? needs? you\.|Nothing needs you right now\./);
  await expect(head).toContainText("Nothing is running.");

  // What needs you: number, title, a sentence from the item's fields (who, which project) and one button.
  const needs = page.getByRole("list", { name: "Waiting for you" });
  const row = needs.getByRole("listitem").filter({ hasText: "Accept this run?" }).first();
  await expect(row).toContainText(`Suggested by a person in ${projectName}.`);
  await expect(row).toContainText("UTC");
  await expect(row.getByRole("link", { name: "Decide" })).toHaveAttribute("href", "/inbox");

  // The latest project as eight tiles, each with a value and a note from the flow read.
  const card = page.locator(".card", { has: page.getByRole("heading", { name: projectName }) });
  const steps = card.getByRole("navigation", { name: "Steps of the active project" });
  await expect(steps.getByRole("listitem")).toHaveCount(8);
  const tile = (label: string) => steps.getByRole("listitem").filter({ hasText: label });
  await expect(tile("1 Data")).toContainText("churn");
  await expect(tile("1 Data")).toContainText(/\d+ rows/);
  await expect(tile("2 Goal & test design")).toContainText("Predict churn");
  await expect(tile("2 Goal & test design")).toContainText(/\d+-fold cross-validation/);
  await expect(tile("3 Experiments")).toContainText("1 run");
  await expect(tile("3 Experiments")).toContainText(/best of recent runs: Run 1, CV [A-Za-z-]+ \d+\.\d\d/);
  await expect(tile("5 Model")).toContainText(/Model v\d+/);
  await expect(tile("6 Predictions")).toContainText("Not scored yet");
  await expect(tile("7 Monitoring")).toContainText("Nothing yet");
  await expect(tile("8 History")).toContainText(/\d+ decisions?/);
  await expect(steps.getByRole("link", { name: /8 History/ })).toHaveAttribute("href", `/projects/${projectId}/decisions`);
  // The state is in words too, never colour alone.
  await expect(tile("1 Data").locator(".v7-flow-state")).toHaveText("done");
  await expect(tile("6 Predictions").locator(".v7-flow-state")).toHaveText("not started");
  await expect(card.getByRole("link", { name: "Open project" })).toHaveAttribute("href", `/projects/${projectId}/experiments`);

  // Recent activity: the finished run with its model family and CV score (never a final test value).
  const feed = page.getByRole("list", { name: "Recent activity" });
  await expect(feed).toContainText(/Run 1 finished · .+ · CV [A-Za-z-]+ \d+\.\d\d/);
  await expect(feed).toContainText("UTC");

  // All projects: See all, the model in use's CV score, last run in words.
  const all = page.locator(".card", { has: page.getByRole("heading", { name: "All projects" }) });
  await expect(all.getByRole("link", { name: "See all" })).toHaveAttribute("href", "/projects");
  const projectRow = all.getByRole("table", { name: "Projects, most recently changed first" }).getByRole("row", { name: new RegExp(projectName) });
  await expect(projectRow).toContainText("Run 1 · Finished");
  await expect(projectRow).toContainText(/Model v\d+/);
  for (const header of ["Project", "Goal", "CV score (model in use)", "Model in use", "Last run"]) await expect(all.getByRole("columnheader", { name: header, exact: true })).toBeVisible();
  // The final test set is named only as a fact ("checked once"), never with a score.
  await expect(feed).toContainText("Run 1 was checked once on its final test set");
  expect(await page.locator("main").innerText()).not.toMatch(/holdout|champion|\bref\b|final test[^\n]*\d\.\d/i);
  await shot(page, "home-v7-1280");
  await axeBoth(page, "/home");
});

test("Running now shows the fold of a live run from the project list read", async ({ page }) => {
  await login(page);
  // A real run finishes in seconds here; the fold a live run is on is injected into the real list response.
  await page.route("**/api/backend/v1/projects", async (route) => {
    const response = await route.fetch();
    const body = (await response.json()) as Array<{ id: string; summary?: Record<string, unknown> }>;
    for (const project of body) {
      if (project.id !== projectId) continue;
      const latest = (project.summary?.latest_run ?? {}) as Record<string, unknown>;
      project.summary = {
        ...project.summary, stage: "running", running_count: 1,
        latest_run: { ...latest, status: "running", run_number: 2, created_at: new Date(Date.now() - 270_000).toISOString(), ended_at: null,
          // Fold events carry no model family (the runner records the fold, not the family).
          progress: { stage: "cross_validation", model_family: null, fold_number: 3, folds_planned: 5, candidates_finished: 2, fold_completed: false, updated_at: new Date().toISOString() } },
      };
    }
    await route.fulfill({ response, json: body });
  });
  await page.goto("/home");
  const runs = page.getByRole("list", { name: "Runs in progress" });
  await expect(runs.getByText("Run 2", { exact: true })).toBeVisible();
  await expect(runs).not.toContainText("CatBoost");
  await expect(runs).toContainText(`${projectName} · Model being trained: fold 3 of 5 · 2 models finished · started 4 minutes ago`);
  const meter = runs.getByRole("progressbar", { name: "Model being trained in Run 2: folds done" });
  await expect(meter).toHaveAttribute("aria-valuetext", "Fold 3 of 5 in progress, 2 done");
  await expect(meter).toHaveAttribute("aria-valuenow", "2");
  await expect(page.locator(".page-head")).toContainText("1 run is not finished yet.");
  await shot(page, "home-v7-running");
  await axeBoth(page, "/home running");
  await page.unroute("**/api/backend/v1/projects");
});

test("Projects v7: where it is, scores, last run, explainer and the New project dialog", async ({ page }) => {
  await login(page);
  await page.goto("/projects");
  await expect(page.getByRole("heading", { level: 1, name: "Projects" })).toBeVisible();
  const table = page.getByRole("table", { name: "Projects, most recently changed first" });
  for (const header of ["Project", "Goal", "Where it is", "CV score (model in use)", "Model in use", "Last run"]) await expect(table.getByRole("columnheader", { name: header, exact: true })).toBeVisible();
  const row = table.getByRole("row", { name: new RegExp(projectName) });
  await expect(row).toContainText("1 decision waiting");
  await expect(row).toContainText("Model in use");
  await expect(row).toContainText("Predict the target column");
  await expect(row).toContainText("Run 1 · Finished");
  await expect(row).toContainText(/[A-Za-z-]+ \d+\.\d\d/);
  await expect(row.getByRole("link", { name: projectName })).toHaveAttribute("href", `/projects/${projectId}/experiments`);
  await expect(page.locator(".card", { has: page.getByRole("heading", { name: "Starting a project" }) })).toContainText("three steps");

  await page.getByRole("button", { name: "New project" }).click();
  const dialog = page.getByRole("dialog", { name: "New project" });
  await expect(dialog).toBeVisible();
  await expect(dialog.getByRole("navigation", { name: "New project steps" }).getByRole("listitem")).toHaveCount(3);
  for (const step of ["1 Name and file", "2 What to predict", "3 Test design"]) await expect(dialog.getByRole("listitem").filter({ hasText: step })).toHaveCount(1);
  await expect(dialog.getByRole("link", { name: "Use the four-step page" })).toHaveAttribute("href", "/projects/new");
  await shot(page, "projects-v7-dialog");
  await axeBoth(page, "/projects dialog");
  await page.keyboard.press("Escape");
  await expect(dialog).toBeHidden();
  await page.getByRole("button", { name: "New project" }).click();
  await dialog.getByRole("link", { name: "Use the four-step page" }).click();
  await expect(page).toHaveURL(/\/projects\/new$/);
  await expect(page.getByLabel("Project name")).toBeVisible();
  await page.goto("/projects");
  await shot(page, "projects-v7-1280");
  await axeBoth(page, "/projects");
});

test("the project flow bar is eight tiles with the current step marked; a failed flow read falls back to the names", async ({ page }) => {
  await login(page);
  const base = `/projects/${projectId}`;
  await page.goto(`${base}/experiments`);
  const flow = page.getByRole("navigation", { name: "Project steps" });
  await expect(flow.getByRole("listitem")).toHaveCount(8);
  await expect(flow.getByRole("link", { name: /3 Experiments/ })).toHaveAttribute("aria-current", "step");
  await expect(flow.getByRole("listitem").filter({ hasText: "3 Experiments" }).locator(".v7-flow-state")).toContainText("you are here");
  await expect(flow.getByRole("link", { name: /1 Data/ })).toHaveAttribute("href", `${base}/data`);
  await expect(flow.getByRole("link", { name: /8 History/ })).toHaveAttribute("href", `${base}/decisions`);
  await expect(flow.getByRole("listitem").filter({ hasText: "3 Experiments" })).toContainText("1 run");
  await page.goto(`${base}/decisions`);
  await expect(page.getByRole("navigation", { name: "Project steps" }).getByRole("link", { name: /8 History/ })).toHaveAttribute("aria-current", "step");
  await shot(page, "flow-tiles-history");
  await axeBoth(page, "flow tiles");

  await page.route("**/api/backend/v1/projects/*/flow", (route) => route.fulfill({ status: 500, contentType: "application/json", body: JSON.stringify({ error: { code: "internal_error", message: "boom", retryable: false, details: {} } }) }));
  await page.goto(`${base}/experiments`);
  await expect(page.getByText("The details of each step could not be loaded; the steps still open their pages.")).toBeVisible();
  const names = page.getByRole("navigation", { name: "Project steps" });
  await expect(names.getByRole("link", { name: "Experiments" })).toHaveAttribute("aria-current", "step");
  await expect(names.locator(".v7-flow-tiles")).toHaveCount(0);
  await page.goto("/home");
  await expect(page.getByText("Could not load the project's steps.")).toBeVisible();
  await page.unroute("**/api/backend/v1/projects/*/flow");
});

test("no horizontal page scroll at 1280 / 800 / 375; axe light and dark at each width", async ({ page }) => {
  await login(page);
  for (const [width, height] of [[1280, 900], [800, 1000], [375, 812]] as const) {
    await page.setViewportSize({ width, height });
    for (const path of ["/home", "/projects", `/projects/${projectId}/experiments`]) {
      await page.goto(path);
      await expect(page.locator(".page-head h1")).toBeVisible();
      if (path !== "/projects") await expect(page.locator(".v7-flow-tiles").first()).toBeVisible();
      await noPageScroll(page, `${path} at ${width}`);
      await axeBoth(page, `${path} at ${width}`);
      await shot(page, `ui-b-${path === "/home" ? "home" : path === "/projects" ? "projects" : "experiments"}-${width}`);
    }
  }
  await page.setViewportSize({ width: 1280, height: 900 });
});
