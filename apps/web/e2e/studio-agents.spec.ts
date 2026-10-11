import { expect, test, type Page } from "@playwright/test";
import path from "node:path";

/** P4.8-UI / V7-A6: /agents Connect and Access tokens (create, copy once, revoke) on the real backend, in plain words. */
const EMAIL = "business-admin-a@verification.invalid";
const PASSWORD = "VerificationOnly123!";
const AXE = path.resolve(__dirname, "../node_modules/axe-core/axe.min.js");

async function login(page: Page, email = EMAIL): Promise<void> {
  await page.goto("/login");
  await page.getByLabel("Email").fill(email);
  await page.getByLabel("Password").fill(PASSWORD);
  await page.getByRole("button", { name: "Sign in" }).click();
  await expect(page).not.toHaveURL(/\/login/);
}

async function axeSeriousOrCritical(page: Page): Promise<string[]> {
  await page.addScriptTag({ path: AXE });
  return page.evaluate(async () => {
    const axe = (window as unknown as { axe: { run: (ctx: Document, opts: object) => Promise<{ violations: Array<{ id: string; impact?: string; nodes: unknown[] }> }> } }).axe;
    const result = await axe.run(document, { runOnly: { type: "tag", values: ["wcag2a", "wcag2aa"] } });
    return result.violations.filter((v) => v.impact === "serious" || v.impact === "critical").map((v) => `${v.id} (${v.nodes.length})`);
  });
}

async function shot(page: Page, name: string): Promise<void> {
  const dir = process.env.DCLAB_E2E_SHOTS;
  if (dir) await page.screenshot({ path: path.join(dir, `${name}.png`), fullPage: true });
}

test("Agents page: Connect snippets, token create (secret once, never stored), revoke, empty tabs, access", async ({ page, context }) => {
  await context.grantPermissions(["clipboard-read", "clipboard-write"]);
  await login(page);
  await page.goto("/home");
  await page.getByRole("link", { name: "Connect" }).first().click();
  await expect(page).toHaveURL(/\/agents$/);
  await expect(page.getByRole("heading", { name: "Connect", level: 1 })).toBeVisible();

  // Connect: .mcp.json with a placeholder token only.
  await expect(page.getByText("dclab_st_…").first()).toBeVisible();
  const mcp = await page.getByLabel(".mcp.json example", { exact: true }).innerText();
  expect(JSON.parse(mcp).mcpServers.dclab.env.DCLAB_TOKEN).toBe("dclab_st_…");
  await page.getByRole("button", { name: "Copy .mcp.json example" }).click();
  await expect(page.getByRole("button", { name: "Copy .mcp.json example" })).toHaveText("Copied");
  expect(await page.evaluate(() => navigator.clipboard.readText())).toContain('"DCLAB_MCP_WRITE_ENABLED"');
  await page.getByRole("tab", { name: "Python", exact: true }).click();
  await expect(page.getByLabel("Python SDK example", { exact: true })).toContainText("DCLabClient");
  await page.getByRole("tab", { name: "Terminal", exact: true }).click();
  await expect(page.getByLabel("CLI example", { exact: true })).toContainText("dclab-cli login");
  await expect(page.getByLabel("CLI example", { exact: true })).toContainText("dclab-cli predict create");
  await page.getByRole("tab", { name: "Web API", exact: true }).click();
  await expect(page.getByText("Idempotency-Key", { exact: true })).toBeVisible();
  // Honest claims: what a token can do, what it cannot, and no old jargon or over-claims.
  await expect(page.getByText("cannot accept a decision or choose a model for use").first()).toBeVisible();
  const connectText = await page.locator("main").innerText();
  expect(connectText).not.toMatch(/service token|scopes?\b|governance|raw rows|Tool registry|Agent catalog|Planned in/i);
  await shot(page, "agents-connect-light");
  expect(await axeSeriousOrCritical(page)).toEqual([]);

  // Service tokens: create, secret once, list, revoke.
  await page.getByRole("tab", { name: "Access tokens" }).click();
  await expect(page.getByText("What it can do").first()).toBeVisible();
  await expect(page.getByLabel(/Propose decisions/)).toBeVisible();
  const name = `e2e-token-${Date.now()}`;
  await page.getByLabel("Name", { exact: true }).fill(name);
  await page.getByLabel("Current password").fill(PASSWORD);
  await page.getByRole("button", { name: "Create token" }).click();
  const secretBox = page.getByRole("status").filter({ hasText: "will not be shown again" });
  await expect(secretBox).toBeVisible();
  const secret = (await secretBox.locator("code").innerText()).trim();
  expect(secret).toMatch(/^dclab_st_/);
  await shot(page, "agents-tokens-secret-light");
  await secretBox.getByRole("button", { name: "Copy" }).click();
  expect(await page.evaluate(() => navigator.clipboard.readText())).toBe(secret);
  const row = page.getByRole("row", { name: new RegExp(name) });
  await expect(row).toBeVisible();
  await expect(row).not.toContainText(secret);
  // Never persisted client-side.
  const stored = await page.evaluate(() => JSON.stringify({ ...localStorage }) + JSON.stringify({ ...sessionStorage }) + document.cookie);
  expect(stored).not.toContain(secret);
  expect(page.url()).not.toContain(secret);
  await secretBox.getByRole("button", { name: "Done" }).click();
  await expect(page.getByText(secret)).toHaveCount(0);
  await page.reload();
  await page.getByRole("tab", { name: "Access tokens" }).click();
  await expect(page.getByText(secret)).toHaveCount(0);
  await expect(page.getByRole("row", { name: new RegExp(name) })).toBeVisible();

  // Revoke.
  await page.getByRole("row", { name: new RegExp(name) }).getByRole("button", { name: "Revoke" }).click();
  await expect(page.getByRole("row", { name: new RegExp(name) })).toContainText("revoked");
  await expect(page.getByRole("row", { name: new RegExp(name) }).getByRole("button", { name: "Revoke" })).toHaveCount(0);
  await shot(page, "agents-tokens-list-light");
  expect(await axeSeriousOrCritical(page)).toEqual([]);

  // Unbuilt tabs are hidden, not shown as "planned".
  await expect(page.getByRole("tab", { name: "Tool registry" })).toHaveCount(0);
  await expect(page.getByRole("tab", { name: "Agent catalog" })).toHaveCount(0);
  await expect(page.getByRole("tab", { name: "Agent runs" })).toHaveCount(0);

  // Dark theme.
  await page.emulateMedia({ colorScheme: "dark" });
  await page.getByRole("tab", { name: "Use DCLab from code" }).click();
  await shot(page, "agents-connect-dark");
  expect(await axeSeriousOrCritical(page)).toEqual([]);


  // Mobile: no horizontal page scroll.
  await page.setViewportSize({ width: 375, height: 812 });
  await page.goto("/agents");
  await expect(page.getByRole("heading", { level: 1, name: "Connect" })).toBeVisible();
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
  await page.setViewportSize({ width: 1280, height: 720 });
  // Old location keeps working.
  await page.goto("/app/settings");
  await expect(page.getByText("Access tokens").first()).toBeVisible();
});

test("Agents page: clients get the forbidden page, unknown sub-paths 404", async ({ page }) => {
  await login(page, "client-user@verification.invalid");
  const denied = await page.goto("/agents");
  expect(denied?.status()).toBe(403);
  await page.context().clearCookies();
  await login(page);
  const missing = await page.goto("/agents/x");
  expect(missing?.status()).toBe(404);
});
