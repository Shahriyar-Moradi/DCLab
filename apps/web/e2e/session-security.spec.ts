import { expect, test, type Page } from "@playwright/test";

const PASSWORD = "VerificationOnly123!";
const EMAIL = "client-user@verification.invalid";

function backend(endpoint: string): string {
  return `/api/backend${endpoint}`;
}

async function signIn(page: Page): Promise<void> {
  await page.goto("/login");
  await page.getByLabel("Email").fill(EMAIL);
  await page.getByLabel("Password").fill(PASSWORD);
  await page.getByRole("button", { name: "Sign in" }).click();
  await expect(page).not.toHaveURL(/\/login/);
}

test.describe("S0-P02B browser session security", () => {
  test("login issues HttpOnly session and readable CSRF cookie", async ({
    page,
  }) => {
    await signIn(page);
    const cookies = await page.context().cookies();
    const session = cookies.find((cookie) => cookie.name === "dclab_session");
    const csrf = cookies.find((cookie) => cookie.name === "dclab_csrf");
    expect(session?.httpOnly).toBe(true);
    expect(csrf?.httpOnly).toBe(false);
    expect(cookies.find((cookie) => cookie.name === "dclab_token")).toBeFalsy();
    const me = await page.request.get(backend("/auth/me"));
    expect(me.ok()).toBeTruthy();
    expect((await me.json()).email).toBe(EMAIL);
    await expect(page.getByLabel("Active workspace")).toBeVisible();
    await expect(page.locator("#active-workspace")).toBeVisible();
    await page.goto("/app/insights");
    await expect(page.getByRole("heading", { name: "Insights", exact: true })).toBeVisible();
    await expect(page.getByText("12,482")).toHaveCount(0);
  });

  test("reload keeps the server session", async ({ page }) => {
    await signIn(page);
    await page.reload();
    await expect(page).not.toHaveURL(/\/login/);
    const me = await page.request.get(backend("/auth/me"));
    expect(me.ok()).toBeTruthy();
  });

  test("logout revokes the session cookie", async ({ page }) => {
    await signIn(page);
    const before = await page.context().cookies();
    const raw = before.find((cookie) => cookie.name === "dclab_session")?.value;
    expect(raw).toBeTruthy();
    await page.getByRole("button", { name: "Sign out" }).click();
    await expect(page).toHaveURL(/\/login/);
    await page.goto("/app/dashboards");
    await expect(page).toHaveURL(/\/login/);
    const me = await page.request.get(backend("/auth/me"));
    expect(me.status()).toBe(401);
  });

  test("logout-all revokes the presented session", async ({ page }) => {
    await signIn(page);
    const cookies = await page.context().cookies();
    const csrf = cookies.find((cookie) => cookie.name === "dclab_csrf")?.value;
    const revoked = await page.request.post(backend("/auth/logout-all"), {
      headers: {
        "X-CSRF-Token": csrf ?? "",
        Origin: "http://127.0.0.1:3001",
      },
    });
    expect(revoked.status()).toBe(204);
    const me = await page.request.get(backend("/auth/me"));
    expect(me.status()).toBe(401);
    await page.goto("/app/dashboards");
    await expect(page).toHaveURL(/\/login/);
  });

  test("cookie-authenticated mutation without CSRF is rejected", async ({
    page,
  }) => {
    await signIn(page);
    const denied = await page.request.post(backend("/auth/logout"), {
      headers: { Origin: "https://evil.example" },
    });
    expect(denied.status()).toBe(403);
  });
});
