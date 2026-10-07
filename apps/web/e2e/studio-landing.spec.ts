import { expect, test, type Page } from "@playwright/test";
import path from "node:path";

/** P4.12-A: one audience, one story. Role landings, marketing redirects, next= safety, axe on `/`. */
const PASSWORD = "VerificationOnly123!";
const AXE = path.resolve(__dirname, "../node_modules/axe-core/axe.min.js");
const MARKETING = ["industries", "solutions", "pricing", "showcase", "platform", "company", "resources"];

async function login(page: Page, email: string, next?: string): Promise<void> {
  await page.goto(next ? `/login?next=${encodeURIComponent(next)}` : "/login");
  await page.getByLabel("Email").fill(email);
  await page.getByLabel("Password").fill(PASSWORD);
  await page.getByRole("button", { name: "Sign in" }).click();
  await expect(page).not.toHaveURL(/\/login/);
}

async function axeViolations(page: Page): Promise<string[]> {
  await page.addScriptTag({ path: AXE });
  return page.evaluate(async () => {
    const axe = (window as unknown as { axe: { run: (ctx: Document, opts: object) => Promise<{ violations: Array<{ id: string; impact: string; nodes: unknown[] }> }> } }).axe;
    const result = await axe.run(document, { runOnly: { type: "tag", values: ["wcag2a", "wcag2aa"] } });
    return result.violations.map((v) => `${v.id} ${v.impact} (${v.nodes.length})`);
  });
}

test.describe.configure({ mode: "serial" });

test("each role lands in its workspace after login", async ({ page }) => {
  const landings: Array<[string, RegExp]> = [
    ["dclab-admin@verification.invalid", /\/projects$/],
    ["dclab-developer@verification.invalid", /\/projects$/],
    ["business-developer-a@verification.invalid", /\/projects$/],
    ["business-admin-a@verification.invalid", /\/projects$/],
    ["client-user@verification.invalid", /\/app\/labs$/],
  ];
  for (const [email, expected] of landings) {
    await page.context().clearCookies();
    await login(page, email);
    await expect(page).toHaveURL(expected);
    // eslint-disable-next-line no-console
    console.log(`landing ${email} -> ${new URL(page.url()).pathname}`);
  }
});

test("the story page has one call to action and passes axe", async ({ page, context }) => {
  await context.clearCookies();
  await page.goto("/");
  await expect(page.getByRole("heading", { level: 1 })).toContainText("An ML lab your AI agent can drive");
  await expect(page.getByRole("link", { name: /sign in/i }).filter({ has: page.locator("svg") })).toHaveCount(1);
  await expect(page.getByTestId("home-cta")).toHaveAttribute("href", "/login");
  await expect(page.getByRole("link", { name: /book a demo|pricing|industries|solutions/i })).toHaveCount(0);
  expect(await axeViolations(page)).toEqual([]);
  await page.emulateMedia({ colorScheme: "dark" });
  expect(await axeViolations(page)).toEqual([]);
  await page.emulateMedia({ colorScheme: "light" });

  await login(page, "client-user@verification.invalid");
  await page.goto("/");
  await expect(page.getByTestId("home-cta")).toHaveAttribute("href", "/app/labs");
});

test("marketing routes redirect to the story page, signed out and signed in", async ({ page, context }) => {
  await context.clearCookies();
  for (const route of MARKETING) {
    await page.goto(`/${route}`);
    await expect(page, route).toHaveURL(/\/$/);
  }
  await login(page, "dclab-developer@verification.invalid");
  for (const route of MARKETING) {
    await page.goto(`/${route}`);
    await expect(page, route).toHaveURL(/\/$/);
  }
  // Frozen page source stays: the business administration area is not a marketing page.
  const business = await page.request.get("/business", { maxRedirects: 0 });
  expect(business.status()).not.toBe(404);
});

test("next= is honored for allowed internal routes only", async ({ page, context }) => {
  for (const bad of ["https://evil.example/x", "//evil.example", "/\\evil.example", "/app/..//evil.example", "javascript:alert(1)"]) {
    await context.clearCookies();
    await login(page, "client-user@verification.invalid", bad);
    await expect(page).toHaveURL(/\/app\/labs$/);
    expect(new URL(page.url()).hostname).not.toContain("evil");
  }
  await context.clearCookies();
  await login(page, "client-user@verification.invalid", "/projects"); // not allowed for this role
  await expect(page).toHaveURL(/\/app\/labs$/);
  await context.clearCookies();
  await login(page, "client-user@verification.invalid", "/app/opportunities");
  await expect(page).toHaveURL(/\/app\/opportunities$/);
});
