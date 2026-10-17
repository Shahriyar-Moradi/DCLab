import { expect, test, type Page } from "@playwright/test";
import path from "node:path";

/** V7-B6: /settings, members and roles (read only) on the real backend: administrators see the people, viewers get an explanation. */
const ADMIN = "business-admin-a@verification.invalid";
const VIEWER = process.env.DCLAB_E2E_VIEWER_EMAIL ?? "viewer-a@verification.invalid";
const PASSWORD = "VerificationOnly123!";
const AXE = path.resolve(__dirname, "../node_modules/axe-core/axe.min.js");

async function login(page: Page, email: string): Promise<void> {
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

const NEVER = /password|hash|\$2[aby]\$|argon|dclab_st_[0-9a-f]{8}/i;

test("Settings: an administrator sees the people and the role table, nothing else is invented", async ({ page }) => {
  await login(page, ADMIN);
  await page.goto("/home");
  await page.getByRole("navigation", { name: "Studio" }).getByRole("link", { name: "Settings", exact: true }).click();
  await expect(page).toHaveURL(/\/settings$/);
  await expect(page.getByRole("heading", { name: "Settings", level: 1 })).toBeVisible();

  const people = page.getByRole("table", { name: "People in this workspace" });
  await expect(people).toBeVisible();
  const me = people.getByRole("row").filter({ hasText: ADMIN });
  await expect(me).toContainText("you");
  await expect(me.getByRole("cell", { name: "Admin", exact: true })).toBeVisible();
  await expect(people.getByRole("row").filter({ hasText: VIEWER }).getByRole("cell", { name: "Analyst (view only)", exact: true })).toBeVisible();
  // A client account is listed as a Business user.
  await expect(people.getByRole("row").filter({ hasText: "client-user@verification.invalid" }).getByRole("cell", { name: "Business user", exact: true })).toBeVisible();
  await expect(people.getByRole("row").filter({ hasText: "you" })).toHaveCount(1);

  const roles = page.getByRole("table", { name: "What each role can and cannot do" });
  for (const role of ["Admin · Owner", "Admin", "Data scientist", "Analyst (view only)"]) await expect(roles.getByRole("rowheader", { name: role, exact: true })).toBeVisible();
  // D1: no raw role code (workspace_owner, ml_engineer, ...) is shown anywhere on the page.
  expect(await page.locator("main").innerText()).not.toMatch(/workspace_owner|workspace_admin|ml_engineer|business_admin|business_developer|personal_developer|client_user/);
  const viewerRow = roles.getByRole("row").filter({ has: page.getByRole("rowheader", { name: "Analyst (view only)" }) });
  await expect(viewerRow).toContainText("Create projects, upload data, start runs or decide in the Inbox");

  await expect(page.getByText("Adding or removing people is not available in Studio yet; contact DCLab.")).toBeVisible();
  await expect(page.getByText("More settings arrive with later releases.")).toBeVisible();
  await expect(page.getByRole("link", { name: "Connect", exact: true }).last()).toHaveAttribute("href", "/agents");
  // Negative: no pretend tabs or controls, no secrets on the page. (D8b: Notifications and Integrations are real tabs now;
  // studio-leftovers-d8.spec.ts covers them.)
  for (const tab of ["Notifications", "Integrations"]) await expect(page.getByRole("tab", { name: tab, exact: true })).toHaveCount(1);
  for (const fake of ["Invite", "Remove", "Add a member"]) {
    await expect(page.getByRole("tab", { name: new RegExp(fake, "i") })).toHaveCount(0);
    await expect(page.getByRole("button", { name: new RegExp(fake, "i") })).toHaveCount(0);
  }
  for (const fake of ["Invite", "Add a member", "Remove"]) await expect(page.getByRole("link", { name: new RegExp(fake, "i") })).toHaveCount(0);
  expect(await page.locator("main").innerText()).not.toMatch(NEVER);
  expect(await page.content()).not.toMatch(/password_hash/i);
  await shot(page, "settings-1-admin");

  // The raw answer: only the listed fields, never cached, never another workspace.
  const workspaceId = (await (await page.request.get("/api/backend/v1/me")).json()).active_workspace_id as string;
  const api = await page.request.get(`/api/backend/v1/workspaces/${workspaceId}/members`);
  expect(api.status()).toBe(200);
  expect(api.headers()["cache-control"]).toContain("no-store");
  const body = await api.json();
  expect(Object.keys(body).sort()).toEqual(["members", "more", "roles"]);
  for (const member of body.members) expect(Object.keys(member).sort()).toEqual(["email", "full_name", "is_you", "joined_at", "legacy_access", "role"]);
  expect(JSON.stringify(body)).not.toMatch(NEVER);
  const other = await page.request.get("/api/backend/v1/workspaces/00000000-0000-4000-8000-000000000000/members");
  expect([403, 404]).toContain(other.status());

  for (const scheme of ["light", "dark"] as const) {
    await page.emulateMedia({ colorScheme: scheme });
    expect(await axeSeriousOrCritical(page), scheme).toEqual([]);
  }
  await shot(page, "settings-2-admin-dark");
  await page.emulateMedia({ colorScheme: "light" });

  await page.setViewportSize({ width: 375, height: 812 });
  await expect(people).toBeVisible();
  const overflow = await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);
  expect(overflow).toBeLessThanOrEqual(0);
  await shot(page, "settings-3-mobile");
  await page.setViewportSize({ width: 1280, height: 720 });

  expect((await page.goto("/settings/x"))?.status()).toBe(404);
});

test("Settings: a viewer gets an explanation, no list, and the API refuses", async ({ page }) => {
  const members: string[] = [];
  page.on("request", (request) => { if (/\/members(\?|$)/.test(request.url())) members.push(request.url()); });
  await login(page, VIEWER);
  await page.goto("/settings");
  await expect(page.getByRole("heading", { name: "Settings", level: 1 })).toBeVisible();
  await expect(page.getByText("Only admins can see the people in a workspace. Your role cannot.")).toBeVisible();
  await expect(page.getByRole("table")).toHaveCount(0);
  await expect(page.getByText(ADMIN)).toHaveCount(0);
  await expect(page.getByText("You cannot change work here: no uploads, runs or decisions.")).toBeVisible();
  expect(members).toEqual([]);
  expect(await axeSeriousOrCritical(page)).toEqual([]);
  await shot(page, "settings-4-viewer");

  const workspaceId = (await (await page.request.get("/api/backend/v1/me")).json()).active_workspace_id as string;
  const denied = await page.request.get(`/api/backend/v1/workspaces/${workspaceId}/members`);
  expect(denied.status()).toBe(403);
  const text = await denied.text();
  expect(JSON.parse(text).error.code).toBe("members_not_permitted");
  expect(text).not.toContain(ADMIN);
});
