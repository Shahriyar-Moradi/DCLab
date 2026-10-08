import { expect, test } from "@playwright/test";
import { axeViolations, login, proposeDecision, shot, trainedProject } from "./support/studio-fixture";

/** P4.15-UI: Home on the seeded stack — stat cards, projects table, activity, inbox preview, quick actions, AI-off state. */
test("home shows real numbers, projects, activity and the inbox preview", async ({ page }) => {
  await login(page);
  const { projectId, experimentId, name } = await trainedProject(page, "Home");
  await proposeDecision(page, projectId, experimentId, "Home spec proposal");

  await page.goto("/home");
  await expect(page.getByRole("heading", { level: 1, name: "Home" })).toBeVisible();
  const numbers = page.getByRole("region", { name: "Workspace numbers" });
  await expect(numbers).toContainText("Projects");
  await expect(numbers).toContainText("Waiting for a decision");
  await expect(numbers).toContainText("Runs in progress");
  // The AI-off stack says so explicitly; plan spend is not shown (P8.4).
  await expect(numbers).toContainText("AI is off");
  await expect(page.getByText("Nothing is spent on AI while it is off.")).toBeVisible();
  const waiting = Number((await numbers.locator(".stat", { hasText: "Waiting for a decision" }).locator(".v").innerText()).trim());
  expect(waiting).toBeGreaterThanOrEqual(1);

  const table = page.getByRole("table", { name: "Projects, most recently changed first" });
  const row = table.getByRole("row", { name: new RegExp(name) });
  await expect(row).toContainText("churn");
  await expect(row).toContainText("completed");
  await expect(row.getByRole("link", { name })).toHaveAttribute("href", `/projects/${projectId}/experiments`);

  const feed = page.getByRole("list", { name: "Recent activity" });
  await expect(feed).toContainText(/Run/);
  await expect(feed.locator(".pill").first()).toBeVisible();
  const preview = page.getByRole("list", { name: "Inbox preview" });
  await expect(preview).toContainText("Experiment acceptance");
  await page.getByRole("link", { name: "Open inbox" }).click();
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
  // A failed governance read is "unavailable", never "AI is off".
  await page.route("**/api/backend/v1/governance", (route) => route.fulfill({ status: 500, contentType: "application/json", body: JSON.stringify({ error: { code: "internal_error", message: "boom", retryable: false, details: {} } }) }));
  await page.goto("/home");
  await expect(page.getByText("AI health unavailable")).toBeVisible();
  await expect(page.getByText("AI health could not be loaded.")).toBeVisible();
  await expect(page.getByText("AI is off")).toHaveCount(0);
  await page.unroute("**/api/backend/v1/governance");
  // No holdout values on Home.
  expect(await page.locator("main").innerText()).not.toMatch(/holdout_/i);
});
