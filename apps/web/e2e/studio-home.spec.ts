import { expect, test } from "@playwright/test";
import { axeViolations, login, proposeDecision, shot, trainedProject } from "./support/studio-fixture";

/** V7-A6: Home on the seeded stack in plain words — what needs you, the latest project as steps, projects table, activity sentences, AI-off state. */
test("home shows real numbers, projects, activity and the inbox preview", async ({ page }) => {
  await login(page);
  const { projectId, experimentId, name } = await trainedProject(page, "Home");
  await proposeDecision(page, projectId, experimentId, "Home spec proposal");

  await page.goto("/home");
  await expect(page.getByRole("heading", { level: 1, name: /^Good (morning|afternoon|evening)/ })).toBeVisible();
  await expect(page.getByText(/Good (morning|afternoon|evening)/)).toBeVisible();
  // AI is off on this stack: said plainly, no AI health card, no spend.
  await expect(page.getByText("AI suggestions are switched off.")).toBeVisible();
  await expect(page.getByText(/AI health|Spend|scope/i)).toHaveCount(0);

  // What needs you: plain titles from the inbox, with the count in the card title.
  const waitingCard = page.locator(".card", { has: page.getByRole("heading", { name: /things? needs? you/ }) });
  await expect(waitingCard).toBeVisible();
  const waitingList = page.getByRole("list", { name: "Waiting for you" });
  await expect(waitingList).toContainText("Accept this run?");
  await expect(waitingList).not.toContainText(/experiment_accepted|Experiment acceptance|proposal/i);
  const waiting = Number((await waitingCard.getByRole("heading").first().innerText()).match(/^(\d+)/)?.[1]);
  expect(waiting).toBeGreaterThanOrEqual(1);

  // The latest project as its steps (flow bar semantics: done / next only where known).
  const steps = page.getByRole("navigation", { name: "Steps of the active project" });
  await expect(steps).toContainText("Data");
  await expect(steps).toContainText("Experiments");

  const table = page.getByRole("table", { name: "Projects, most recently changed first" });
  const row = table.getByRole("row", { name: new RegExp(name) });
  await expect(row).toContainText("churn");
  await expect(row).toContainText("Finished");
  await expect(row.getByRole("link", { name })).toHaveAttribute("href", `/projects/${projectId}/experiments`);
  await expect(table.getByRole("columnheader", { name: "Model in use", exact: true })).toBeVisible();

  const feed = page.getByRole("list", { name: "Recent activity" });
  await expect(feed).toContainText(/Run \w+ (finished|was queued)|Waiting for your decision/);
  await expect(feed).not.toContainText(/Ref move|Champion|Winner locked|Agent /);
  await expect(feed.locator(".pill").first()).toBeVisible();
  await page.getByRole("link", { name: "Open the Inbox" }).click();
  await expect(page).toHaveURL(/\/inbox$/);
  await page.goBack();

  // Quick actions and the sidebar badge.
  await expect(page.getByRole("link", { name: "Connect" }).first()).toHaveAttribute("href", "/agents");
  await expect(page.getByRole("navigation", { name: "Studio" }).getByRole("link", { name: /Inbox/ })).toContainText(String(waiting));
  for (const scheme of ["light", "dark"] as const) {
    await page.emulateMedia({ colorScheme: scheme });
    expect(await axeViolations(page)).toEqual([]);
    await shot(page, `home-${scheme}`);
  }
  await page.emulateMedia({ colorScheme: "light" });
  // A failed governance read says nothing about AI: no "switched off" claim, and the page still works.
  await page.route("**/api/backend/v1/governance", (route) => route.fulfill({ status: 500, contentType: "application/json", body: JSON.stringify({ error: { code: "internal_error", message: "boom", retryable: false, details: {} } }) }));
  await page.goto("/home");
  await expect(page.getByRole("heading", { level: 1, name: /^Good (morning|afternoon|evening)/ })).toBeVisible();
  await expect(page.getByRole("list", { name: "Waiting for you" })).toBeVisible();
  await expect(page.getByText("AI suggestions are switched off.")).toHaveCount(0);
  expect(await page.locator("main").innerText()).not.toMatch(/AI is off|switched off|AI health/i);
  await page.unroute("**/api/backend/v1/governance");
  // A failed count read is its own notice, never "0 things need you".
  await page.route("**/api/backend/v1/inbox/counts", (route) => route.fulfill({ status: 500, contentType: "application/json", body: JSON.stringify({ error: { code: "internal_error", message: "boom", retryable: false, details: {} } }) }));
  await page.goto("/home");
  await expect(page.getByText("Could not load how many things need you.")).toBeVisible();
  expect(await page.locator("main").innerText()).not.toMatch(/\b0 things need you|Nothing needs you right now/);
  await page.unroute("**/api/backend/v1/inbox/counts");
  // Stored error scores never print: a negated RMSE champion shows as a positive error in natural units.
  await page.route("**/api/backend/v1/projects", async (route) => {
    const response = await route.fetch();
    const body = await response.json();
    for (const project of body) project.summary = { ...project.summary, champion: { model_version_id: "11111111-1111-4111-8111-111111111111", version: "v2", experiment_id: "22222222-2222-4222-8222-222222222222", selection_metric: "rmse", metric_scope: "cv_aggregate", cv_metrics: { rmse: -2.5 } } };
    await route.fulfill({ response, json: body });
  });
  await page.goto("/home");
  const natural = page.getByRole("table", { name: "Projects, most recently changed first" }).getByRole("row", { name: new RegExp(name) });
  await expect(natural).toContainText("RMSE 2.50");
  await expect(natural).toContainText("Model v2");
  await expect(natural).not.toContainText("-2.5");
  await page.unroute("**/api/backend/v1/projects");

  // Mobile: no horizontal page scroll.
  await page.setViewportSize({ width: 375, height: 812 });
  await page.goto("/home");
  await expect(page.getByRole("heading", { level: 1, name: /^Good (morning|afternoon|evening)/ })).toBeVisible();
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
  await page.setViewportSize({ width: 1280, height: 720 });
  // No holdout values on Home.
  expect(await page.locator("main").innerText()).not.toMatch(/holdout_/i);
});
