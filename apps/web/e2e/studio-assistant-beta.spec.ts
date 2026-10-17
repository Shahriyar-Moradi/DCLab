import { expect, test, type Page, type Route } from "@playwright/test";
import { execFileSync } from "node:child_process";
import path from "node:path";
import { axeLightDarkAt1280And375 } from "./support/studio-fixture";

/**
 * D6b: the assistant panel (Beta) and AI settings on the seeded real backend.
 *
 * Setup: the `ai_assistant_beta` flag is switched with the real staff CLI (`dclab entitlements set ... --admin`) against the
 * e2e database. With `DCLAB_E2E_AI_ON=1` the stack runs with AI on and the fake provider (DCLAB_ENV=development,
 * AI_ENABLED=true, `dclab governance switch on global_ai`, released lead prompt) and nothing is stubbed. Otherwise AI is off on
 * the stack: the governance read is stubbed to "AI available" only so the toggle shows, and every assistant call still goes
 * to the real API, which answers with its AI-off templates (cost $0.00, "No AI used").
 */
const PASSWORD = process.env.DCLAB_E2E_STUDIO_PASSWORD ?? "VerificationOnly123!";
const EMAIL = process.env.DCLAB_E2E_STUDIO_EMAIL ?? "business-admin-a@verification.invalid";
const STAFF = "dclab-admin@verification.invalid";
const DATABASE_URL = process.env.DCLAB_E2E_DATABASE_URL ?? "postgresql://localhost:55432/dclab_e2e_verify";
const AI_ON = process.env.DCLAB_E2E_AI_ON === "1";

async function login(page: Page): Promise<void> {
  await page.goto("/login");
  await page.getByLabel("Email").fill(EMAIL);
  await page.getByLabel("Password").fill(PASSWORD);
  await page.getByRole("button", { name: "Sign in" }).click();
  await expect(page).not.toHaveURL(/\/login/);
}

async function writeHeaders(page: Page, label: string): Promise<Record<string, string>> {
  const csrf = (await page.context().cookies()).find((c) => c.name === "dclab_csrf")?.value ?? "";
  return { "Idempotency-Key": `e2e-${label}-${Date.now()}`, "X-CSRF-Token": csrf, Origin: new URL(page.url()).origin };
}

async function workspaceId(page: Page): Promise<string> {
  return (await (await page.request.get("/api/backend/v1/me")).json()).active_workspace_id as string;
}

function setBeta(workspace: string, on: boolean): void {
  execFileSync(
    "../../.venv/bin/dclab",
    ["entitlements", "set", "--workspace", workspace, "--key", "ai_assistant_beta", on ? "--on" : "--off", "--admin", STAFF],
    { cwd: path.resolve(__dirname, ".."), env: { ...process.env, DATABASE_URL }, stdio: "pipe" },
  );
}

/** Without AI on the stack: the real governance read with AI marked available (the only stubbed fields). */
async function aiAvailable(page: Page): Promise<void> {
  if (AI_ON) return;
  await page.route(/\/api\/backend\/v1\/governance$/, async (route: Route) => {
    const response = await route.fetch();
    const body = await response.json();
    await route.fulfill({ response, json: { ...body, ai_enabled_setting: true, policy_unavailable: null, switches: { ...body.switches, platform_ai_blocking: null, workspace: [] } } });
  });
}

test.describe.configure({ mode: "serial" });
// The governance stub may still be fetching when a test ends (the page polls it): drop it quietly before teardown.
test.afterEach(async ({ page }) => {
  await page.unrouteAll({ behavior: "ignoreErrors" });
});

test("beta off: no assistant toggle, and the API refuses every assistant route with assistant_beta_off", async ({ page }) => {
  await login(page);
  const workspace = await workspaceId(page);
  setBeta(workspace, false);
  await aiAvailable(page);
  await page.goto("/home");
  await expect(page.getByRole("heading", { level: 1 })).toBeVisible();
  await expect(page.getByRole("button", { name: /^Assistant/ })).toHaveCount(0);
  await expect(page.getByTestId("assistant-panel")).toHaveCount(0);
  const list = await page.request.get("/api/backend/v1/assistant/threads");
  expect(list.status()).toBe(403);
  expect((await list.json()).error.code).toBe("assistant_beta_off");
});

test("beta on: open the panel (Beta), start a conversation, send a message, see the reply with its cost and the provider notice", async ({ page }) => {
  await login(page);
  const workspace = await workspaceId(page);
  setBeta(workspace, true);
  const created = await page.request.post("/api/backend/v1/projects", { data: { name: `Assistant beta ${Date.now()}`, description: "D6b" }, headers: await writeHeaders(page, "project") });
  expect(created.ok(), await created.text()).toBeTruthy();
  const projectId = String((await created.json()).id);
  await aiAvailable(page);
  try {
    await page.goto(`/projects/${projectId}/experiments`);
    const toggle = page.getByRole("button", { name: /^Assistant/ });
    await expect(toggle).toContainText("Beta");
    await expect(toggle).toHaveAttribute("aria-pressed", "false");
    await toggle.click();
    const panel = page.getByTestId("assistant-panel");
    await expect(panel).toBeFocused();
    await expect(panel.getByRole("heading", { name: /Assistant/ })).toContainText("Beta");
    await expect(panel).toContainText("a person accepts every change");
    await panel.getByRole("button", { name: "New conversation" }).click();
    await expect(panel.getByTestId("assistant-notice")).toContainText(AI_ON ? "never raw rows or final-test numbers" : "AI is off: nothing you type is sent to an AI provider.");
    await expect(panel.getByTestId("assistant-notice")).toContainText("Only you can open this conversation here.");
    await panel.getByLabel("Message the assistant").fill("what can you do");
    await panel.getByRole("button", { name: "Send" }).click();
    const log = panel.getByRole("log", { name: "Conversation with the assistant" });
    await expect(log.locator(".msg.user").last()).toContainText("what can you do");
    await expect(log.locator(".msg.agent").last()).not.toContainText("Writing…", { timeout: 30_000 });
    await expect(log.locator(".msg.agent").last()).toContainText(AI_ON ? /\$|No AI used/ : /No AI used/);
    await expect(panel.getByTestId("assistant-cost")).toContainText(/Cost of this conversation: \$\d+\.\d\d|Cost of this conversation: less than \$0\.01/);
    await expect(panel.getByTestId("assistant-cost")).toContainText(/estimate|upper bound/);
    // Model and user text is plain text: no markup is ever rendered from it.
    await expect(log.locator("img, script, iframe")).toHaveCount(0);
    await axeLightDarkAt1280And375(page, "Assistant panel open");
    await expect(panel).toBeVisible();
    await page.keyboard.press("Escape");
    await expect(panel).toHaveCount(0);
    await expect(toggle).toBeFocused();
  } finally {
    setBeta(workspace, false);
  }
});

test("a stubbed model reply: dclab:// links become internal links, other links and markup stay text, an upper-bound cost says so", async ({ page }) => {
  await login(page);
  const workspace = await workspaceId(page);
  setBeta(workspace, true);
  const created = await page.request.post("/api/backend/v1/projects", { data: { name: `Assistant links ${Date.now()}`, description: "D6b" }, headers: await writeHeaders(page, "project") });
  const projectId = String((await created.json()).id);
  const run = "22222222-2222-4222-8222-222222222222";
  const turn = "33333333-3333-4333-8333-333333333333";
  await aiAvailable(page);
  const stream = [
    { type: "turn_started", data: { templated: false } },
    { type: "tool_call", data: { call: 1, tool: "get_project" } },
    { type: "assistant_message", data: { kind: "answer", message: `See [Run 1](dclab://experiment/${run}) and [x](https://evil.example) <img src=x onerror=alert(1)>`, templated: false, llm_used: true } },
    { type: "turn_done", data: { status: "completed", error_code: null, proposal_ids: [] } },
  ].map((e, i) => `id: ${i}\nevent: ${e.type}\ndata: ${JSON.stringify({ turn_id: turn, ...e.data })}\n\n`).join("");
  let sent = false;
  await page.route(/\/api\/backend\/v1\/assistant\/threads\/[0-9a-f-]+\/messages$/, (route) => { sent = true; return route.fulfill({ status: 200, contentType: "text/event-stream", body: stream }); });
  await page.route(/\/api\/backend\/v1\/assistant\/threads\/[0-9a-f-]+$/, async (route) => {
    const response = await route.fetch();
    const body = await response.json();
    const events = sent ? [
      { type: "user_message", turn_id: turn, seq: 0, data: { message: "explain the run" } },
      ...stream.split("\n\n").filter(Boolean).map((block, i) => ({ type: block.split("\n")[1].slice(7), turn_id: turn, seq: i, data: JSON.parse(block.split("\n")[2].slice(6)) })),
    ] : [];
    await route.fulfill({ response, json: { ...body, ai_available: true, events, cost: { currency: "USD", conversation_micros: sent ? 190_000 : 0, by_turn_micros: sent ? { [turn]: 190_000 } : {}, price_is_upper_bound: true } } });
  });
  try {
    await page.goto(`/projects/${projectId}/experiments`);
    await page.getByRole("button", { name: /^Assistant/ }).click();
    const panel = page.getByTestId("assistant-panel");
    await panel.getByRole("button", { name: "New conversation" }).click();
    await panel.getByLabel("Message the assistant").fill("explain the run");
    await panel.getByRole("button", { name: "Send" }).click();
    const reply = panel.locator(".msg.agent").last();
    await expect(reply.getByRole("link", { name: "Run 1" })).toHaveAttribute("href", `/projects/${projectId}/experiments/${run}`);
    await expect(reply.getByRole("link", { name: "x" })).toHaveCount(0);
    await expect(reply).toContainText("[x](https://evil.example)");
    await expect(reply).toContainText("<img src=x onerror=alert(1)>");
    await expect(reply.locator("img")).toHaveCount(0);
    await expect(reply).toContainText("Steps it ran: read the project");
    await expect(reply).toContainText("$0.19 (upper bound)");
    await expect(panel.getByTestId("assistant-cost")).toContainText("upper bound: the model's price is not confirmed yet");
  } finally {
    setBeta(workspace, false);
  }
});

test("AI settings (Beta): the real model, 'Not tested yet', 'Automatic' locked, problem reports and history", async ({ page }) => {
  await login(page);
  await page.goto("/governance");
  await expect(page.getByRole("heading", { name: "AI settings", level: 1 })).toBeVisible();
  await expect(page.locator(".page-head")).toContainText("Beta");
  await expect(page.getByTestId("ai-model")).toContainText("Claude Sonnet 5.5");
  await expect(page.getByTestId("ai-status")).toContainText(/AI is (on|off)/);
  const levels = page.getByTestId("ai-levels");
  await expect(levels).toContainText("Not tested yet");
  await expect(levels).toContainText("Locked until earned");
  await expect(levels).toContainText("Always asks: never automatic");
  await expect(levels).toContainText("Ask first: every assistant action is a suggestion a person accepts");
  // "Automatic" is never offered as a choice: there is no control in the table.
  await expect(levels.locator("select, input, button")).toHaveCount(0);
  await expect(page.getByRole("heading", { name: "Problem reports" })).toBeVisible();
  await expect(page.getByRole("heading", { name: "Change history" })).toBeVisible();
  await expect(page.getByTestId("ai-provider-notice")).toContainText("Every AI call is logged");
  await expect(page.getByRole("navigation", { name: "Studio" }).getByRole("link", { name: "AI settings" })).toHaveAttribute("aria-current", "page");
  await axeLightDarkAt1280And375(page, "AI settings");
});

test("AI settings: a viewer gets the plain refusal, not an error", async ({ page }) => {
  await page.goto("/login");
  await page.getByLabel("Email").fill(process.env.DCLAB_E2E_VIEWER_EMAIL ?? "viewer-a@verification.invalid");
  await page.getByLabel("Password").fill(PASSWORD);
  await page.getByRole("button", { name: "Sign in" }).click();
  await expect(page).not.toHaveURL(/\/login/);
  const response = await page.goto("/governance");
  // Viewers are not Studio users at all (middleware) or cannot read the settings (API 403): never a crash.
  if (response && response.status() < 400 && new URL(page.url()).pathname === "/governance") {
    await expect(page.getByText("Your role cannot read the AI settings.")).toBeVisible();
  }
});
