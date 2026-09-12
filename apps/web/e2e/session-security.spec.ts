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

test.describe("S0-P02D BFF contract", () => {
  test("anonymous BFF session is 401 with no-store and request id", async ({
    request,
  }) => {
    const me = await request.get(backend("/auth/me"), {
      headers: { "X-Request-Id": "e2e-anonymous-1" },
    });
    expect(me.status()).toBe(401);
    expect(me.headers()["cache-control"]).toMatch(/no-store/i);
    expect(me.headers()["x-request-id"]).toBe("e2e-anonymous-1");
    expect(await me.json()).toMatchObject({ detail: expect.any(String) });
  });

  test("forged session cookie is 401 through the BFF", async ({ page }) => {
    await page.context().addCookies([
      {
        name: "dclab_session",
        value: "forged-or-expired-session",
        url: "http://127.0.0.1:3001",
        httpOnly: true,
      },
    ]);
    const me = await page.request.get(backend("/auth/me"));
    expect(me.status()).toBe(401);
  });

  test("BFF does not forward Authorization as a session", async ({ request }) => {
    const tokens = await request.post(backend("/auth/tokens"), {
      headers: {
        "Content-Type": "application/json",
        Origin: "http://127.0.0.1:3001",
      },
      data: { email: EMAIL, password: PASSWORD },
    });
    expect(tokens.ok()).toBeTruthy();
    const access = ((await tokens.json()) as { access_token: string }).access_token;
    const me = await request.get(backend("/auth/me"), {
      headers: { Authorization: `Bearer ${access}` },
    });
    expect(me.status()).toBe(401);
  });

  test("HEAD is proxied", async ({ request }) => {
    const head = await request.fetch(backend("/health"), { method: "HEAD" });
    expect(head.status()).toBe(200);
    expect(head.headers()["cache-control"]).toMatch(/no-store/i);
  });
});

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
    expect(me.headers()["cache-control"]).toMatch(/no-store/i);
    expect((await me.json()).email).toBe(EMAIL);
    await expect(page.getByLabel("Active workspace")).toBeVisible();
    await expect(page.locator("#active-workspace")).toBeVisible();
    await page.goto("/app/opportunities/upload");
    await expect(page.getByTestId("active-workspace-notice")).toBeVisible();
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

test.describe("S0-P02F adversarial completion", () => {
  test("document.cookie cannot read the HttpOnly session", async ({ page }) => {
    const loginResponse = await page.goto("/login");
    expect(loginResponse?.headers()["content-security-policy"] ?? "").toContain(
      "connect-src 'self'",
    );
    await signIn(page);
    const visible = await page.evaluate(() => document.cookie);
    expect(visible).not.toContain("dclab_session");
    const cookies = await page.context().cookies();
    expect(cookies.find((cookie) => cookie.name === "dclab_session")?.httpOnly).toBe(
      true,
    );
  });

  test("stolen session cookie dies after rotation", async ({ browser }) => {
    const victim = await browser.newContext();
    const thief = await browser.newContext();
    const victimPage = await victim.newPage();
    try {
      await signIn(victimPage);
      const session = (await victim.cookies()).find(
        (cookie) => cookie.name === "dclab_session",
      );
      expect(session?.value).toBeTruthy();
      await thief.addCookies([
        {
          name: "dclab_session",
          value: session?.value ?? "",
          url: "http://127.0.0.1:3001",
          httpOnly: true,
        },
      ]);
      const thiefPage = await thief.newPage();
      const stolen = await thiefPage.request.get(backend("/auth/me"));
      expect(stolen.ok()).toBeTruthy();
      expect((await stolen.json()).email).toBe(EMAIL);

      const csrf = (await victim.cookies()).find(
        (cookie) => cookie.name === "dclab_csrf",
      )?.value;
      const rotated = await victimPage.request.post(backend("/auth/login"), {
        headers: {
          "Content-Type": "application/json",
          Origin: "http://127.0.0.1:3001",
          "X-CSRF-Token": csrf ?? "",
        },
        data: { email: EMAIL, password: PASSWORD },
      });
      expect(rotated.ok()).toBeTruthy();
      const replay = await thiefPage.request.get(backend("/auth/me"));
      expect(replay.status()).toBe(401);
    } finally {
      await victim.close();
      await thief.close();
    }
  });

  test("API bearer clients work without browser cookies", async ({ request }) => {
    const tokens = await request.post("http://127.0.0.1:8001/auth/tokens", {
      headers: { "Content-Type": "application/json" },
      data: { email: EMAIL, password: PASSWORD },
    });
    expect(tokens.ok()).toBeTruthy();
    const access = ((await tokens.json()) as { access_token: string }).access_token;
    const me = await request.get("http://127.0.0.1:8001/auth/me", {
      headers: { Authorization: `Bearer ${access}` },
    });
    expect(me.ok()).toBeTruthy();
    expect((await me.json()).email).toBe(EMAIL);
  });
});
