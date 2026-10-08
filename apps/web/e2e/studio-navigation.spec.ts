import { expect, test, type Page } from "@playwright/test";
import path from "node:path";

/** P4.1-A + V7-A1: Developer Studio information architecture (grouped sidebar, project flow bar) on the seeded real backend. */
const PASSWORD = "VerificationOnly123!";
const WEB_ORIGIN = process.env.DCLAB_E2E_WEB_URL ?? "http://127.0.0.1:3001";
const AXE = path.resolve(__dirname, "../node_modules/axe-core/axe.min.js");

async function login(page: Page, email: string): Promise<void> {
  await page.goto("/login");
  await page.getByLabel("Email").fill(email);
  await page.getByLabel("Password").fill(PASSWORD);
  await page.getByRole("button", { name: "Sign in" }).click();
  await expect(page).not.toHaveURL(/\/login/);
}

async function csrf(page: Page): Promise<Record<string, string>> {
  const cookies = await page.context().cookies();
  return { "X-CSRF-Token": cookies.find((c) => c.name === "dclab_csrf")?.value ?? "", Origin: WEB_ORIGIN };
}

async function axeViolations(page: Page): Promise<string[]> {
  await page.addScriptTag({ path: AXE });
  return page.evaluate(async () => {
    const axe = (window as unknown as { axe: { run: (ctx: Document, opts: object) => Promise<{ violations: Array<{ id: string; nodes: unknown[] }> }> } }).axe;
    const result = await axe.run(document, { runOnly: { type: "tag", values: ["wcag2a", "wcag2aa"] } });
    return result.violations.map((v) => `${v.id} (${v.nodes.length})`);
  });
}

test.describe.configure({ mode: "serial" });

test("Studio: sidebar, project pages, empty states, command bar and access", async ({ page }) => {
  await login(page, "business-admin-a@verification.invalid");
  const name = `Studio nav ${Date.now()}`;
  const created = await page.request.post("/api/backend/v1/projects", {
    data: { name, description: "Playwright navigation project" },
    headers: { ...(await csrf(page)), "Idempotency-Key": `studio-nav-${Date.now()}` },
  });
  expect(created.ok(), await created.text()).toBeTruthy();
  const projectId = String(((await created.json()) as { id: string }).id);

  await page.goto("/home");
  const sidebar = page.getByRole("navigation", { name: "Studio" });
  await expect(page.getByRole("heading", { name: "Home", level: 1 })).toBeVisible();
  await expect(sidebar.getByRole("link", { name: "Projects" })).toBeVisible();
  await expect(sidebar.getByRole("link", { name: "Inbox" })).toBeVisible();
  for (const hidden of ["Settings", "AI settings", "Governance", "Agents & tools"]) await expect(sidebar.getByRole("link", { name: hidden })).toHaveCount(0);
  await expect(sidebar.getByRole("link", { name: "Connect" })).toBeVisible();
  await expect(sidebar.getByRole("link", { name: "Experiments" })).toHaveCount(0); // outside a project
  expect(await axeViolations(page)).toEqual([]);

  await page.goto("/projects");
  await page.getByRole("link", { name, exact: true }).click();
  await expect(page).toHaveURL(new RegExp(`/projects/${projectId}/experiments$`));
  await expect(page.getByRole("heading", { name: "Experiments", level: 1 })).toBeVisible();
  await expect(page.getByRole("region", { name: "Project" })).toContainText(name);
  // A new project has no test design and no model in use yet, so Goal & test design and Predictions are not listed.
  for (const label of ["Data", "Experiments", "Model", "History"]) {
    await expect(sidebar.getByRole("link", { name: label, exact: true })).toBeVisible();
  }
  for (const hidden of ["Lab (chat)", "Pipeline", "Graph", "Improve", "Monitoring", "Goal & test design", "Predictions"]) {
    await expect(sidebar.getByRole("link", { name: hidden, exact: true })).toHaveCount(0);
  }
  await expect(sidebar.getByText("Project · " + name)).toBeVisible();
  // The flow bar names the steps; the current one is marked and the steps link to their pages.
  const flow = page.getByRole("navigation", { name: "Project steps" });
  await expect(flow.getByRole("link", { name: "Data" })).toHaveAttribute("href", `/projects/${projectId}/data`);
  await expect(flow.getByRole("link", { name: "Experiments" })).toHaveAttribute("aria-current", "step");
  await expect(flow.getByText("Goal & test design")).toBeVisible();
  await expect(flow.getByRole("link", { name: "Goal & test design" })).toHaveCount(0); // no test design yet
  await expect(sidebar.getByRole("link", { name: "Experiments" })).toHaveAttribute("aria-current", "page");
  expect(await axeViolations(page)).toEqual([]);
  await page.emulateMedia({ colorScheme: "dark" });
  expect(await axeViolations(page)).toEqual([]);
  await page.goto("/home");
  await expect(page.getByRole("heading", { name: "Home", level: 1 })).toBeVisible();
  expect(await axeViolations(page)).toEqual([]);
  await page.emulateMedia({ colorScheme: "light" });

  await page.goto(`/projects/${projectId}/monitoring`);
  await expect(page.getByText("Monitoring arrives with releases.")).toBeVisible();
  await page.goto(`/projects/${projectId}/decisions`);
  await expect(page.getByRole("heading", { name: "History", level: 1 })).toBeVisible();
  await page.goto("/inbox");
  await expect(page.getByRole("heading", { name: "Inbox", level: 1 })).toBeVisible();
  expect((await page.goto(`/projects/${projectId}/nope`))?.status()).toBe(404);
  expect((await page.goto("/projects/not-a-uuid/experiments"))?.status()).toBe(404);

  // The 404 pages have no command bar: search from a real Studio page.
  await page.goto("/projects");
  await page.waitForLoadState("networkidle"); // the shortcut listener is attached after hydration
  await page.keyboard.press("ControlOrMeta+K");
  const search = page.getByRole("combobox", { name: "Search" });
  await search.fill(name.slice(0, 14));
  const option = page.getByRole("option", { name: new RegExp(name) });
  await expect(option).toBeVisible();
  await search.press("Enter");
  await expect(page).toHaveURL(new RegExp(`/projects/${projectId}/experiments$`));
});

test("Studio routes are refused for the client role", async ({ page }) => {
  await login(page, "client-user@verification.invalid");
  for (const route of ["/home", "/projects", "/inbox"]) {
    expect((await page.goto(route))?.status(), route).toBe(403);
  }
});
