import { expect, test } from "@playwright/test";
import { axeViolations, login, proposeDecision, shot, trainedProject } from "./support/studio-fixture";

/** P4.16-UI: a proposed decision -> Inbox -> approve -> the decision record is accepted; reject, read-only and conflict paths. */
test("inbox: approve a proposed decision, reject another, read-only and conflict states", async ({ page }) => {
  await login(page);
  const { projectId, experimentId } = await trainedProject(page, "Inbox");
  const approveId = await proposeDecision(page, projectId, experimentId, "Inbox spec approve");
  const rejectId = await proposeDecision(page, projectId, experimentId, "Inbox spec reject");

  await page.goto("/inbox");
  await expect(page.getByRole("heading", { level: 1, name: "Inbox" })).toBeVisible();
  const needs = page.getByRole("tab", { name: /Needs a decision/ });
  await expect(needs).toHaveAttribute("aria-selected", "true");
  const cards = page.locator("article.card", { hasText: "Experiment acceptance" });
  await expect(cards.first()).toBeVisible();
  const before = await cards.count();
  expect(before).toBeGreaterThanOrEqual(2);
  await expect(cards.first()).toContainText("Evidence");
  await expect(cards.first().getByRole("link", { name: /Run [0-9a-f]{8}/ }).first()).toHaveAttribute("href", new RegExp(`/projects/${projectId}/experiments/${experimentId}`));
  await shot(page, "inbox-needs-decision");
  for (const scheme of ["light", "dark"] as const) {
    await page.emulateMedia({ colorScheme: scheme });
    expect(await axeViolations(page)).toEqual([]);
    await shot(page, `inbox-${scheme}`);
  }
  await page.emulateMedia({ colorScheme: "light" });

  // Read-only role: the same page with the API's answer rewritten to "may not act" disables the buttons and says why.
  await page.route("**/api/backend/v1/inbox?*", async (route) => {
    const response = await route.fetch();
    const body = await response.json();
    body.viewer.can_decide = false;
    for (const item of body.items) { item.can_act = false; for (const a of item.actions) a.allowed = false; }
    await route.fulfill({ response, json: body });
  });
  await page.reload();
  await expect(page.getByText("You can read everything here but not decide")).toBeVisible();
  await expect(page.locator("article.card").first().getByRole("button", { name: /^Approve/ })).toBeDisabled();
  await expect(page.getByText("Your role can read the inbox but not decide.").first()).toBeVisible();
  await shot(page, "inbox-read-only");
  await page.unroute("**/api/backend/v1/inbox?*");
  await page.reload();

  // Approve: a reason is required; the record becomes accepted and the item leaves the list.
  const card = cards.first();
  await card.getByRole("button", { name: /^Approve/ }).click();
  const confirm = page.getByRole("button", { name: "Confirm approve" });
  await expect(confirm).toBeDisabled();
  await page.getByLabel("Reason (recorded)").fill("Agreed in the inbox");
  const accept = page.waitForResponse((r) => r.url().includes("/decisions/") && r.url().endsWith("/accept") && r.request().method() === "POST");
  await confirm.click();
  expect((await accept).status()).toBe(201);
  await expect(page.getByText("Approved. The decision was recorded.")).toBeVisible();
  await expect(cards).toHaveCount(before - 1);
  await shot(page, "inbox-approved");

  const decisions = await page.request.get(`/api/backend/v1/projects/${projectId}/decisions?limit=100`).then((r) => r.json()) as { items: Array<{ id: string; decision_type: string; effective_state: string }> };
  expect(decisions.items.filter((d) => d.decision_type === "experiment_accepted").map((d) => d.effective_state)).toContain("accepted");
  await page.getByRole("tab", { name: /^Done/ }).click();
  await expect(page.locator("article.card", { hasText: "Experiment acceptance accepted" }).first()).toBeVisible();
  await page.getByRole("tab", { name: /Applied automatically/ }).click();
  await expect(page.getByText("Every decision point is below level L2 today")).toBeVisible();
  await page.getByRole("tab", { name: /Needs a decision/ }).click();

  // Conflict: the API says it was already decided -> plain language, nothing else changes.
  await page.route("**/api/backend/v1/decisions/*/reject", (route) => route.fulfill({
    status: 409, contentType: "application/json",
    body: JSON.stringify({ error: { code: "invalid_decision_transition", message: "decision already resolved", retryable: false, details: {} } }),
  }));
  await cards.first().getByRole("button", { name: /^Reject/ }).click();
  await page.getByLabel("Reason (recorded)").fill("Gain is within noise");
  await page.getByRole("button", { name: "Confirm reject" }).click();
  await expect(page.getByText("Already decided")).toBeVisible();
  await page.unroute("**/api/backend/v1/decisions/*/reject");
  await page.getByRole("button", { name: "Confirm reject" }).click();
  await expect(page.getByText("Rejected. The decision was recorded.")).toBeVisible();
  // Both proposals are resolved (no longer proposed) and the record holds one accepted and one rejected resolution.
  const final = await page.request.get(`/api/backend/v1/projects/${projectId}/decisions?limit=100`).then((r) => r.json()) as { items: Array<{ id: string; decision_type: string; effective_state: string }> };
  for (const id of [approveId, rejectId]) expect(final.items.find((d) => d.id === id)?.effective_state).not.toBe("proposed");
  const resolved = final.items.filter((d) => d.decision_type === "experiment_accepted").map((d) => d.effective_state);
  expect(resolved).toEqual(expect.arrayContaining(["accepted", "rejected"]));
});
