import { expect, test, type Page } from "@playwright/test";

const PASSWORD = "VerificationOnly123!";
const EMAIL = "client-user@verification.invalid";
const MULTI_WORKSPACE_EMAIL = "multi-workspace@verification.invalid";
const API_ORIGIN = process.env.DCLAB_E2E_API_URL ?? "http://127.0.0.1:8001";
const WEB_ORIGIN = process.env.DCLAB_E2E_WEB_URL ?? "http://127.0.0.1:3001";

function backend(endpoint: string): string {
  return `/api/backend${endpoint}`;
}

async function signIn(page: Page): Promise<void> {
  return signInAs(page, EMAIL);
}

async function signInAs(page: Page, email: string): Promise<void> {
  await page.goto("/login");
  await page.getByLabel("Email").fill(email);
  await page.getByLabel("Password").fill(PASSWORD);
  await page.getByRole("button", { name: "Sign in" }).click();
  await expect(page).not.toHaveURL(/\/login/);
}

function opportunityPayload(customerId: string, id: string) {
  return {
    items: [
      {
        id,
        org_id: "fixture",
        external_id: customerId,
        customer_id: customerId,
        amount: 100,
        currency: "AED",
        stage: "prospecting",
        source: "workspace-switch-test",
        owner_id: "owner",
        created_at: "2026-09-22T00:00:00Z",
        close_date: null,
        last_contact_days_ago: null,
        engagement_score: null,
        sales_rep_available: null,
        industry: null,
        num_interactions: null,
        converted: null,
      },
    ],
    total: 1,
    limit: 20,
    offset: 0,
  };
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
        url: WEB_ORIGIN,
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
        Origin: WEB_ORIGIN,
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
        Origin: WEB_ORIGIN,
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
          url: WEB_ORIGIN,
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
          Origin: WEB_ORIGIN,
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
    const tokens = await request.post(`${API_ORIGIN}/auth/tokens`, {
      headers: { "Content-Type": "application/json" },
      data: { email: EMAIL, password: PASSWORD },
    });
    expect(tokens.ok()).toBeTruthy();
    const access = ((await tokens.json()) as { access_token: string }).access_token;
    const me = await request.get(`${API_ORIGIN}/auth/me`, {
      headers: { Authorization: `Bearer ${access}` },
    });
    expect(me.ok()).toBeTruthy();
    expect((await me.json()).email).toBe(EMAIL);
  });
});

test.describe("S0-P03C tenant-aware workspace state", () => {
  test("a slow previous-workspace response cannot flash after switching", async ({ page }) => {
    await signInAs(page, MULTI_WORKSPACE_EMAIL);
    const me = await page.request.get(backend("/auth/me"));
    expect(me.ok()).toBeTruthy();
    const identity = (await me.json()) as {
      active_workspace_id: string;
      workspaces: Array<{ id: string; name: string }>;
    };
    const first = identity.workspaces.find((workspace) => workspace.name === "Business A");
    const second = identity.workspaces.find((workspace) => workspace.name === "Business B");
    expect(first?.id).toBeTruthy();
    expect(second?.id).toBeTruthy();

    let releaseOldRequest: (() => void) | undefined;
    const oldRequestStarted = new Promise<void>((resolve) => {
      releaseOldRequest = resolve;
    });
    await page.route("**/api/backend/app/opportunities**", async (route) => {
      const workspaceId = route.request().headers()["x-workspace-id"];
      if (workspaceId === first?.id) {
        releaseOldRequest?.();
        await new Promise((resolve) => setTimeout(resolve, 1200));
        await route.fulfill({
          status: 200,
          contentType: "application/json",
          body: JSON.stringify(
            opportunityPayload(
              "OLD-WORKSPACE-SENTINEL",
              "11111111-1111-4111-8111-111111111111",
            ),
          ),
        }).catch(() => undefined);
        return;
      }
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify(
          opportunityPayload(
            "NEW-WORKSPACE-SENTINEL",
            "22222222-2222-4222-8222-222222222222",
          ),
        ),
      });
    });

    await page.goto("/app/opportunities");
    await oldRequestStarted;
    await page.locator("#active-workspace").selectOption(second?.id ?? "");
    await expect(
      page
        .getByRole("group", { name: "Active workspace" })
        .getByRole("status"),
    ).toContainText("Workspace changed to Business B");
    await expect(page.getByText("NEW-WORKSPACE-SENTINEL").first()).toBeVisible();
    await page.waitForTimeout(1400);
    await expect(page.getByText("OLD-WORKSPACE-SENTINEL")).toHaveCount(0);
  });
});

test.describe("S0-P03D workspace propagation", () => {
  test("a forged workspace selector through the BFF cannot grant tenant access", async ({ page }) => {
    await signInAs(page, MULTI_WORKSPACE_EMAIL);
    const me = await page.request.get(backend("/auth/me"));
    expect(me.ok()).toBeTruthy();
    const workspaces = ((await me.json()) as { workspaces: Array<{ id: string }> }).workspaces;
    expect(workspaces.length).toBeGreaterThanOrEqual(2);

    const allowed = await page.request.get(backend("/v1/projects"), {
      headers: { "X-Workspace-Id": workspaces[0].id },
    });
    expect(allowed.status()).toBe(200);
    const forged = await page.request.get(backend("/v1/projects"), {
      headers: { "X-Workspace-Id": "11111111-1111-4111-8111-111111111111" },
    });
    expect(forged.status()).toBe(403);
    const conflict = await page.request.get(backend(`/workspaces/${workspaces[1].id}/projects`), {
      headers: { "X-Workspace-Id": workspaces[0].id },
    });
    expect(conflict.status()).toBe(404);
  });
});

test.describe("S0-P04D legacy insights isolation gate", () => {
  test("real BFF insights stay tenant-scoped across a slow workspace switch", async ({ page }) => {
    await signInAs(page, MULTI_WORKSPACE_EMAIL);
    const me = await page.request.get(backend("/auth/me"));
    expect(me.ok()).toBeTruthy();
    const identity = (await me.json()) as {
      active_workspace_id: string;
      workspaces: Array<{ id: string; name: string }>;
    };
    const first = identity.workspaces.find((workspace) => workspace.name === "Business A");
    const second = identity.workspaces.find((workspace) => workspace.name === "Business B");
    expect(first?.id).toBeTruthy();
    expect(second?.id).toBeTruthy();
    expect(identity.active_workspace_id).toBe(first?.id);

    let signalOldResponse: (() => void) | undefined;
    const oldResponseReady = new Promise<void>((resolve) => { signalOldResponse = resolve; });
    let oldSubjects: string[] = [];
    await page.route("**/api/backend/app/insights", async (route) => {
      // Fetch the actual BFF/API response. Delay only browser delivery of the
      // old tenant's response; do not replace server data with a mock.
      const response = await route.fetch();
      expect(response.status()).toBe(200);
      const payload = (await response.json()) as {
        categories: Array<{ insights: Array<{ subject_id: string }> }>;
      };
      const subjects = payload.categories.flatMap((group) => group.insights.map((item) => item.subject_id));
      if (route.request().headers()["x-workspace-id"] === first?.id) {
        oldSubjects = subjects;
        signalOldResponse?.();
        await new Promise((resolve) => setTimeout(resolve, 1500));
        await route.fulfill({ response }).catch(() => undefined); // switch may cancel this request
        return;
      }
      await route.fulfill({ response });
    });

    await page.goto("/app/insights");
    await oldResponseReady;
    expect(oldSubjects).toEqual(["S0P04D-ALPHA"]);
    await page.locator("#active-workspace").selectOption(second?.id ?? "");
    await expect(page.getByText("S0P04D-BETA")).toBeVisible();
    await page.waitForTimeout(1700);
    await expect(page.getByText("S0P04D-ALPHA")).toHaveCount(0);
    await expect(page.getByText("S0P04D-ARCHIVED")).toHaveCount(0);

    const current = await page.request.get(backend("/app/insights"));
    expect(current.status()).toBe(200);
    const body = JSON.stringify(await current.json());
    expect(body).toContain("S0P04D-BETA");
    expect(body).not.toContain("S0P04D-ALPHA");
    expect(body).not.toContain("S0P04D-ARCHIVED");
  });
});
