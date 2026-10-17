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
  const needs = page.getByRole("tab", { name: /Needs your decision/ });
  await expect(needs).toHaveAttribute("aria-selected", "true");
  const cards = page.locator("article.card", { hasText: "Accept this run?" });
  await expect(cards.first()).toBeVisible();
  const before = await cards.count();
  expect(before).toBeGreaterThanOrEqual(2);
  await expect(cards.first()).toContainText("Evidence");
  // Who suggested it, what approving does, and the rule's answer in words; no internal words or short ids as names.
  await expect(cards.first()).toContainText("Suggested by");
  await expect(cards.first()).toContainText("Approving records your decision in History.");
  await expect(cards.first()).toContainText("waiting for your decision");
  await expect(cards.first()).not.toContainText(/experiment_accepted|Experiment acceptance|proposal|decision point/i);
  await expect(cards.first().getByRole("link", { name: /^Run \d+$/ }).first()).toHaveAttribute("href", new RegExp(`/projects/${projectId}/experiments/${experimentId}`));
  await shot(page, "inbox-needs-decision");
  for (const scheme of ["light", "dark"] as const) {
    await page.emulateMedia({ colorScheme: scheme });
    expect(await axeViolations(page)).toEqual([]);
    await shot(page, `inbox-${scheme}`);
  }
  await page.emulateMedia({ colorScheme: "light" });

  const writes: string[] = [];
  const onRequest = (request: import("@playwright/test").Request) => { if (request.method() === "POST" && /\/v1\/(decisions|proposals)\//.test(request.url())) writes.push(request.url()); };
  page.on("request", onRequest);
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
  // Negative: the buttons stay off, and nothing was sent.
  expect(writes).toEqual([]);
  page.off("request", onRequest);
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
  await expect(page.locator("article.card", { hasText: "Accept this run?" }).filter({ hasText: "approved" }).first()).toBeVisible();
  await page.getByRole("tab", { name: /Applied automatically/ }).click();
  await expect(page.getByText("Nothing listed here.")).toBeVisible();
  await expect(page.locator("main")).not.toContainText(/Ask first today|Nothing was applied without a person/);
  await page.getByRole("tab", { name: /Needs your decision/ }).click();

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

/** V7-A6: source labels and automatic items, using only combinations the backend can produce. The API answer is rewritten. */
test("inbox: honest source labels, per-type approval notes, automatic items with Undo only where offered", async ({ page }) => {
  await login(page);
  const id = (n: number) => `${String(n).repeat(8)}-${String(n).repeat(4)}-4${String(n).repeat(3)}-8${String(n).repeat(3)}-${String(n).repeat(12)}`;
  const proposalActions = (n: number, names: string[]) => names.map((name) => ({ name, operation: `POST /v1/proposals/{proposal_id}/${name}`, path_params: { proposal_id: id(n) }, body: {}, allowed: true }));
  const base = { occurred_at: "2026-10-01T10:00:00Z", project_id: null, summary: "x", status: "proposed", subject: { kind: "project", id: null }, evidence_refs: [], can_act: true };
  const proposal = (n: number, over: Record<string, unknown>) => ({ ...base, id: `agent_proposal:${id(n)}`, kind: "agent_proposal", tab: "needs_decision", source: { kind: "agent_proposal", id: id(n) }, actions: proposalActions(n, ["accept", "reject"]), ...over });
  const decision = { ...base, id: `decision_proposal:${id(1)}`, kind: "decision_proposal", tab: "needs_decision", source: { kind: "decision_record", id: id(1) }, decision_type: "experiment_accepted", proposed_by: "agent",
    actions: [{ name: "reject", operation: "POST /v1/decisions/{decision_id}/reject", path_params: { decision_id: id(1) }, body: {}, allowed: true }] };
  await page.route("**/api/backend/v1/inbox?*", async (route) => {
    const tab = new URL(route.request().url()).searchParams.get("tab");
    const items = tab === "needs_decision"
      ? [decision,
        proposal(2, { proposed_by: "agent", proposal_type: "ExperimentReviewProposal" }),
        proposal(3, { proposed_by: "assistant", proposal_type: "ToolCallProposal", rule_answer: null }),
        proposal(4, { proposed_by: "jev", proposal_type: "SemanticReviewProposal" })]
      : tab === "applied_automatically"
        ? [proposal(5, { tab: "applied_automatically", status: "applied", proposed_by: "agent", proposal_type: "ExperimentPlanProposal", level: 2, actions: proposalActions(5, ["revert"]) }),
          proposal(6, { tab: "applied_automatically", status: "applied", proposed_by: "agent", proposal_type: "ImprovementActionProposal", level: 2, actions: [] })]
        : [];
    await route.fulfill({ json: { tab, items, next_cursor: null, limit: 20, viewer: { is_agent: false, can_decide: true, can_approve_ai_policy: true } } });
  });
  await page.goto("/inbox");
  const cards = page.locator("article.card");
  await expect(cards).toHaveCount(4);
  // A decision proposed through the web API is a connected tool.
  await expect(cards.nth(0)).toContainText("Suggested by connected tool (access token)");
  await expect(cards.nth(0)).toContainText("Its text is not verified.");
  // A typed suggestion from an agent run: who started the run is unknown, so no "access token" and no "assistant".
  await expect(cards.nth(1)).toContainText("Suggested by an AI agent (review run)");
  await expect(cards.nth(1)).toContainText("Its text is not verified.");
  await expect(cards.nth(1)).not.toContainText(/access token|the assistant/);
  await expect(cards.nth(1)).toContainText("Approving records that you read it; nothing changes.");
  await expect(cards.nth(2)).toContainText("Suggested by the assistant");
  await expect(cards.nth(2)).toContainText("Approving runs this action as you.");
  await expect(cards.nth(3)).toContainText("Suggested by AI reviewer");
  await expect(cards.nth(3)).toContainText("Approving starts a new run with this column change.");
  // AI is off on this stack and no AI answer was recorded: the AI panel is not shown.
  await expect(cards.nth(3)).toContainText("What the rules say");
  await expect(cards.nth(3)).not.toContainText("What the AI says");
  await page.getByRole("tab", { name: /Applied automatically/ }).click();
  await expect(cards).toHaveCount(2);
  await expect(cards.nth(0)).toContainText("Applied automatically.");
  await expect(cards.nth(0)).toContainText("Automatic, you can undo");
  await expect(cards.nth(0).getByRole("button", { name: /^Undo/ })).toBeEnabled();
  // Negative: an automatic item without an undo action never offers one and says so.
  await expect(cards.nth(1)).toContainText("No undo is offered for this one here.");
  await expect(cards.nth(1).getByRole("button", { name: /^Undo/ })).toHaveCount(0);
  await expect(page.getByRole("tab", { name: /Applied automatically/ })).not.toContainText("you can undo");
  for (const scheme of ["light", "dark"] as const) {
    await page.emulateMedia({ colorScheme: scheme });
    expect(await axeViolations(page)).toEqual([]);
  }
  await page.emulateMedia({ colorScheme: "light" });
  expect(await page.locator("main").innerText()).not.toMatch(/proposal|decision point|\bL[123]\b|Jev/);
  // Mobile: no horizontal page scroll.
  await page.setViewportSize({ width: 375, height: 812 });
  await page.goto("/inbox");
  await expect(page.getByRole("heading", { level: 1, name: "Inbox" })).toBeVisible();
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
  await page.setViewportSize({ width: 1280, height: 720 });
});
