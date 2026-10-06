import { expect, test, type Page } from "@playwright/test";
import path from "node:path";

/** P4.1-A: Developer Studio information architecture on the seeded real backend. */
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
  await expect(sidebar.getByRole("link", { name: "Inbox" })).toHaveCount(0); // no backend yet
  await expect(sidebar.getByRole("link", { name: "Experiments" })).toHaveCount(0); // outside a project
  expect(await axeViolations(page)).toEqual([]);

  await page.goto("/projects");
  await page.getByRole("link", { name, exact: true }).click();
  await expect(page).toHaveURL(new RegExp(`/projects/${projectId}/experiments$`));
  await expect(page.getByRole("heading", { name: "Experiments", level: 1 })).toBeVisible();
  await expect(page.getByRole("region", { name: "Project" })).toContainText(name);
  for (const label of ["Pipeline", "Graph", "Data", "Experiments", "Models", "Decisions"]) {
    await expect(sidebar.getByRole("link", { name: label, exact: true })).toBeVisible();
  }
  for (const hidden of ["Lab (chat)", "Improve", "Monitoring"]) {
    await expect(sidebar.getByRole("link", { name: hidden })).toHaveCount(0);
  }
  await expect(sidebar.getByRole("link", { name: "Experiments" })).toHaveAttribute("aria-current", "page");
  expect(await axeViolations(page)).toEqual([]);

  await page.goto(`/projects/${projectId}/monitoring`);
  await expect(page.getByText("Monitoring arrives with releases.")).toBeVisible();
  await page.goto(`/projects/${projectId}/decisions`);
  await expect(page.getByRole("heading", { name: "Decisions", level: 1 })).toBeVisible();
  await page.goto("/inbox");
  await expect(page.getByText("Planned in P4.16.")).toBeVisible();
  expect((await page.goto(`/projects/${projectId}/nope`))?.status()).toBe(404);
  expect((await page.goto("/projects/not-a-uuid/experiments"))?.status()).toBe(404);

  // The 404 pages have no command bar: search from a real Studio page.
  await page.goto("/projects");
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
