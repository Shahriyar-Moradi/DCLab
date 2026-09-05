import { expect, test, type Page, type TestInfo } from "@playwright/test";
import { execFileSync } from "node:child_process";
import { mkdir, writeFile } from "node:fs/promises";
import path from "node:path";

const API_URL = "http://127.0.0.1:8001";
const PASSWORD = "VerificationOnly123!";
const DATABASE_URL =
  process.env.DCLAB_E2E_DATABASE_URL ??
  "postgresql://localhost:55432/dclab_e2e_verify";
const ARTIFACTS = path.resolve(
  __dirname,
  "../../../artifacts/e2e-verification",
);

type JsonRecord = Record<string, unknown>;

async function login(page: Page, email: string): Promise<void> {
  await page.goto("/login");
  await page.getByLabel("Email").fill(email);
  await page.getByLabel("Password").fill(PASSWORD);
  await page.getByRole("button", { name: "Sign in" }).click();
  await expect(page).not.toHaveURL(/\/login/);
}

async function authHeaders(page: Page): Promise<Record<string, string>> {
  const token = (await page.context().cookies()).find(
    (cookie) => cookie.name === "dclab_token",
  )?.value;
  expect(token).toBeTruthy();
  return { Authorization: `Bearer ${token}` };
}

async function apiGet<T = JsonRecord>(page: Page, endpoint: string): Promise<T> {
  const response = await page.request.get(`${API_URL}${endpoint}`, {
    headers: await authHeaders(page),
  });
  expect(response.ok(), `${endpoint}: ${await response.text()}`).toBeTruthy();
  return (await response.json()) as T;
}

function classificationCsv(rows = 100): string {
  const values = ["customer_id,age,income,region,outcome"];
  for (let index = 0; index < rows; index += 1) {
    const age = 20 + (index % 45);
    const income = 30_000 + index * 713;
    const region = ["north", "south", "east", "west"][index % 4];
    const outcome = age + income / 10_000 + (index % 3) > 31 ? 1 : 0;
    values.push(`customer-${index},${age},${income},${region},${outcome}`);
  }
  return `${values.join("\n")}\n`;
}

function regressionCsv(rows = 100): string {
  const values = ["account_id,tenure,usage,segment,revenue"];
  for (let index = 0; index < rows; index += 1) {
    const tenure = 1 + (index % 36);
    const usage = 10 + ((index * 17) % 90);
    const segment = ["small", "mid", "large"][index % 3];
    const revenue = 80 + tenure * 4.2 + usage * 2.7 + (index % 5) * 1.3;
    values.push(
      `account-${index},${tenure},${usage},${segment},${revenue.toFixed(2)}`,
    );
  }
  return `${values.join("\n")}\n`;
}

async function uploadCsv(
  page: Page,
  testInfo: TestInfo,
  filename: string,
  csv: string,
  target: string,
): Promise<JsonRecord> {
  const fixture = testInfo.outputPath(filename);
  await writeFile(fixture, csv);
  await page.goto("/app/labs");
  await expect(
    page.getByRole("heading", { name: "Labs", exact: true }),
  ).toBeVisible();
  const input = page.locator('input[type="file"]').first();
  await input.setInputFiles(fixture);
  await page.locator("select").first().selectOption(target);
  const uploaded = page.waitForResponse(
    (response) =>
      response.request().method() === "POST" &&
      new URL(response.url()).pathname === "/app/labs/uploads",
  );
  await page.getByRole("button", { name: "Save file" }).first().click();
  const response = await uploaded;
  expect(response.ok(), await response.text()).toBeTruthy();
  return (await response.json()) as JsonRecord;
}

async function waitForUpload(
  page: Page,
  endpoint: string,
  statusField: "status" | "pipeline_status",
  expected = "completed",
): Promise<JsonRecord> {
  let latest: JsonRecord = {};
  await expect
    .poll(
      async () => {
        latest = await apiGet(page, endpoint);
        return latest[statusField];
      },
      { timeout: 180_000, intervals: [500, 1_000, 2_000] },
    )
    .toBe(expected);
  return latest;
}

function setCapability(capability: string, state: "missing" | "false" | "true") {
  execFileSync(
    "../../.venv/bin/python",
    ["../../scripts/set_e2e_capability.py", "business-a", capability, state],
    {
      cwd: path.resolve(__dirname, ".."),
      env: { ...process.env, DATABASE_URL },
      stdio: "pipe",
    },
  );
}

test.describe.serial("DCLab whole-system browser acceptance", () => {
  let adminPipelineId = "";
  let businessPipelineId = "";
  let businessWorkspaceId = "";
  let businessBWorkspaceId = "";
  let businessUploadId = "";

  test.beforeAll(async () => {
    await mkdir(ARTIFACTS, { recursive: true });
  });

  test("authenticated routes use the role-aware application shell", async ({ page }) => {
    await page.goto("/");
    await expect(page.getByRole("navigation", { name: "Marketing" })).toBeVisible();
    await expect(page.getByRole("navigation", { name: "Application navigation" })).toHaveCount(0);
    await expect(page.getByRole("contentinfo")).toBeVisible();

    await page.goto("/login");
    await expect(page.getByRole("navigation", { name: "Marketing" })).toHaveCount(0);
    await expect(page.getByRole("navigation", { name: "Application navigation" })).toHaveCount(0);
    await expect(page.getByRole("heading", { name: "Sign in" })).toBeVisible();
    await page.getByLabel("Email").fill("nobody@verification.invalid");
    await page.getByLabel("Password").fill("wrong-password");
    await page.getByRole("button", { name: "Sign in" }).click();
    await expect(page.locator("#login-error")).toHaveText(/invalid email or password/i);

    await login(page, "dclab-admin@verification.invalid");
    await page.goto("/app/dashboards");

    const navigation = page.getByRole("navigation", {
      name: "Application navigation",
    });
    await expect(navigation).toBeVisible();
    await expect(navigation.getByRole("link", { name: "Dashboard" })).toHaveAttribute(
      "aria-current",
      "page",
    );
    await expect(page.getByRole("heading", { name: "Dashboard", exact: true })).toBeVisible();
    const refresh = page.getByRole("button", { name: "Refresh" });
    await expect(refresh).toBeEnabled();
    await refresh.click();
    await expect(page.getByRole("heading", { name: "Dashboard", exact: true })).toBeVisible();
    const uploadOpportunities = page.getByRole("link", { name: "Upload opportunities" });
    if (await uploadOpportunities.count()) {
      await expect(uploadOpportunities).toHaveAttribute("href", "/app/opportunities/upload");
    } else {
      await expect(page.getByRole("heading", { name: "Recommended actions" })).toBeVisible();
      await expect(page.getByRole("heading", { name: "Decision confidence" })).toBeVisible();
      await expect(page.getByRole("heading", { name: "Recent decisions" })).toBeVisible();
      await expect(page.getByRole("link", { name: "View all" })).toHaveAttribute("href", "/app/decisions");
    }
    await expect(navigation.getByRole("link", { name: "Labs", exact: true })).toBeVisible();
    await expect(navigation.getByRole("link", { name: "Businesses" })).toBeVisible();
    await expect(navigation.getByRole("link", { name: "Model Registry" })).toBeVisible();
    await expect(navigation.getByRole("link", { name: "Business Admin" })).toHaveCount(0);
    await expect(page.getByRole("navigation", { name: "Marketing" })).toHaveCount(0);
    await expect(page.getByRole("contentinfo")).toHaveCount(0);
    await expect(page.getByRole("button", { name: "Open application navigation" })).toHaveCount(0);
    await expect(page.getByText("dclab-admin@verification.invalid")).toBeVisible();

    await page.goto("/app/insights");
    await expect(navigation.getByRole("link", { name: "Insights" })).toHaveAttribute(
      "aria-current",
      "page",
    );
    await page.goBack();
    await expect(navigation.getByRole("link", { name: "Dashboard" })).toHaveAttribute(
      "aria-current",
      "page",
    );
    await page.goForward();
    await expect(navigation.getByRole("link", { name: "Insights" })).toHaveAttribute(
      "aria-current",
      "page",
    );

    await page.goto("/app/opportunities");
    await expect(navigation.getByRole("link", { name: "Opportunities" })).toHaveAttribute(
      "aria-current",
      "page",
    );

    await page.goto("/app/opportunities/upload");
    await expect(navigation.getByRole("link", { name: "Upload" })).toHaveAttribute(
      "aria-current",
      "page",
    );
    await expect(navigation.getByRole("link", { name: "Opportunities" })).not.toHaveAttribute(
      "aria-current",
      "page",
    );

    await page.goto("/app/labs");
    await expect(navigation.getByRole("link", { name: "Labs", exact: true })).toHaveAttribute(
      "aria-current",
      "page",
    );

    await page.goto("/admin/lab/experiments");
    await expect(
      navigation.getByRole("link", { name: "Labs & Experiments" }),
    ).toHaveAttribute("aria-current", "page");

    await page.goto("/admin/businesses");
    await expect(navigation.getByRole("link", { name: "Businesses" })).toHaveAttribute(
      "aria-current",
      "page",
    );

    await page.goto("/admin/models");
    await expect(navigation.getByRole("link", { name: "Model Registry" })).toHaveAttribute(
      "aria-current",
      "page",
    );

    await page.goto("/admin/monitoring");
    await expect(navigation.getByRole("link", { name: "Monitoring" })).toHaveAttribute(
      "aria-current",
      "page",
    );

    await page.setViewportSize({ width: 390, height: 844 });
    await page.reload();
    await expect(navigation).toHaveCount(0);
    await expect(page.getByRole("button", { name: "Open application navigation" })).toBeVisible();
    await expect(page.getByRole("button", { name: "Open account" })).toBeVisible();
    await page.getByRole("button", { name: "Open application navigation" }).click();
    await expect(page.getByRole("dialog", { name: "Application navigation" })).toBeVisible();
    await expect(
      page.getByRole("dialog").getByRole("navigation", { name: "Application navigation" }),
    ).toBeVisible();
    await page.keyboard.press("Escape");
    await expect(page.getByRole("dialog", { name: "Application navigation" })).toHaveCount(0);

    await page.getByRole("button", { name: "Open account" }).click();
    await expect(page.getByRole("dialog", { name: "Application navigation" })).toBeVisible();
    await expect(page.getByRole("dialog").getByText("dclab-admin@verification.invalid")).toBeVisible();
    await page.keyboard.press("Escape");

    await page.setViewportSize({ width: 1280, height: 844 });
    await page.reload();
    await page.getByRole("button", { name: "Sign out" }).click();
    await expect(page).toHaveURL(/\/login/);
    await expect(page.getByRole("heading", { name: "Sign in" })).toBeVisible();
    await expect(page.getByRole("navigation", { name: "Marketing" })).toHaveCount(0);
    await expect(page.getByRole("navigation", { name: "Application navigation" })).toHaveCount(0);

    await login(page, "dclab-developer@verification.invalid");
    await expect(navigation.getByRole("link", { name: "Model Registry" })).toBeVisible();
    await expect(navigation.getByRole("link", { name: "Businesses" })).toBeVisible();
    await expect(navigation.getByRole("link", { name: "Business Admin" })).toHaveCount(0);
    await page.getByRole("button", { name: "Sign out" }).click();

    await login(page, "business-admin-a@verification.invalid");
    await expect(navigation.getByRole("link", { name: "Business Admin" })).toBeVisible();
    await expect(navigation.getByRole("link", { name: "Businesses" })).toHaveCount(0);
    await expect(navigation.getByRole("link", { name: "Model Registry" })).toHaveCount(0);
    await page.goto("/app/labs");
    await expect(navigation.getByRole("link", { name: "Labs", exact: true })).toHaveAttribute(
      "aria-current",
      "page",
    );
    await page.getByRole("button", { name: "Sign out" }).click();

    await login(page, "business-developer-a@verification.invalid");
    await expect(navigation.getByRole("link", { name: "Business Admin" })).toBeVisible();
    await expect(navigation.getByRole("link", { name: "Businesses" })).toHaveCount(0);
    await page.getByRole("button", { name: "Sign out" }).click();
    await expect(page).toHaveURL(/\/login/);
    await expect(page.getByRole("heading", { name: "Sign in" })).toBeVisible();
    await expect(page.getByRole("navigation", { name: "Marketing" })).toHaveCount(0);
    await expect(page.getByRole("navigation", { name: "Application navigation" })).toHaveCount(0);
  });

  test("workspace dashboard keeps live overview snapshot states", async ({ page }) => {
    const opportunityId = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa";
    const firstDecisionId = "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb";
    const secondDecisionId = "cccccccc-cccc-4ccc-8ccc-cccccccccccc";
    const thirdDecisionId = "dddddddd-dddd-4ddd-8ddd-dddddddddddd";
    let opportunityTotal = 0;
    let failOverview = false;
    let delayMs = 1200;
    let decisions: JsonRecord[] = [];

    const fulfillJson = async (
      route: { fulfill: (response: { status: number; contentType: string; body: string }) => Promise<void> },
      body: JsonRecord,
      status = 200,
    ) => {
      if (delayMs) {
        await new Promise((resolve) => setTimeout(resolve, delayMs));
      }
      if (failOverview) {
        await route.fulfill({
          status: 500,
          contentType: "application/json",
          body: JSON.stringify({ detail: "overview fixture failure" }),
        });
        return;
      }
      await route.fulfill({
        status,
        contentType: "application/json",
        body: JSON.stringify(body),
      });
    };

    await login(page, "dclab-admin@verification.invalid");
    await page.route(
      (url) => url.origin === "http://127.0.0.1:8001" && url.pathname === "/app/opportunities",
      async (route) => {
        await fulfillJson(route, { items: [], total: opportunityTotal, limit: 1, offset: 0 });
      },
    );
    await page.route(
      (url) => url.origin === "http://127.0.0.1:8001" && url.pathname === "/app/decisions",
      async (route) => {
        await fulfillJson(route, {
          items: decisions,
          total: decisions.length,
          limit: 100,
          offset: 0,
        });
      },
    );

    const pending = page.goto("/app/dashboards");
    await expect(page.getByRole("button", { name: "Refresh" })).toBeDisabled();
    await pending;
    delayMs = 0;
    await expect(page.getByRole("heading", { name: "No opportunities yet" })).toBeVisible();
    const upload = page.getByRole("link", { name: "Upload opportunities" });
    await expect(upload).toHaveAttribute("href", "/app/opportunities/upload");
    await upload.click();
    await expect(page).toHaveURL(/\/app\/opportunities\/upload/);

    opportunityTotal = 7;
    decisions = [
      {
        id: firstDecisionId,
        opportunity_id: opportunityId,
        recommended_action: "CONTACT_TODAY",
        expected_revenue: 1200,
        confidence_band: "High",
        reasoning: ["fixture"],
        policy_version: "v1",
        status: "ready",
        created_at: "2026-01-15T10:00:00.000Z",
        external_id: "OPP-100",
      },
      {
        id: secondDecisionId,
        opportunity_id: opportunityId,
        recommended_action: "CONTACT_TODAY",
        expected_revenue: 800,
        confidence_band: "High",
        reasoning: ["fixture"],
        policy_version: "v1",
        status: "ready",
        created_at: "2026-01-14T10:00:00.000Z",
        external_id: "OPP-101",
      },
      {
        id: thirdDecisionId,
        opportunity_id: opportunityId,
        recommended_action: "NO_ACTION",
        expected_revenue: 0,
        confidence_band: "Low",
        reasoning: ["fixture"],
        policy_version: "v1",
        status: "ready",
        created_at: "2026-01-13T10:00:00.000Z",
        external_id: "OPP-102",
      },
    ];
    await page.goto("/app/dashboards");
    const main = page.locator("#main");
    await expect(page.getByRole("heading", { name: "Dashboard", exact: true })).toBeVisible();
    await expect(page.getByRole("heading", { name: "Recommended actions" })).toBeVisible();
    await expect(page.getByRole("heading", { name: "Decision confidence" })).toBeVisible();
    await expect(page.getByRole("heading", { name: "Recent decisions" })).toBeVisible();
    await expect(main.getByRole("link", { name: /Opportunities 7/ })).toHaveAttribute(
      "href",
      "/app/opportunities",
    );
    await expect(main.getByRole("link", { name: /Decisions 3/ })).toHaveAttribute(
      "href",
      "/app/decisions",
    );
    await expect(main.getByText("CONTACT TODAY").first()).toBeVisible();
    await expect(main.getByText("67%").first()).toBeVisible();
    await expect(main.getByRole("link", { name: "View all" })).toHaveAttribute(
      "href",
      "/app/decisions",
    );
    await expect(main.getByRole("link", { name: "Open" }).first()).toHaveAttribute(
      "href",
      `/app/decisions/${firstDecisionId}`,
    );

    opportunityTotal = 11;
    decisions = [
      ...decisions,
      {
        id: "eeeeeeee-eeee-4eee-8eee-eeeeeeeeeeee",
        opportunity_id: opportunityId,
        recommended_action: "FOLLOW_UP",
        expected_revenue: 400,
        confidence_band: "Medium",
        reasoning: ["fixture"],
        policy_version: "v1",
        status: "ready",
        created_at: "2026-01-16T10:00:00.000Z",
        external_id: "OPP-103",
      },
    ];
    await page.getByRole("button", { name: "Refresh" }).click();
    await expect(main.getByRole("link", { name: /Opportunities 11/ })).toBeVisible();
    await expect(main.getByRole("link", { name: /Decisions 4/ })).toBeVisible();
    await expect(main.getByText("50%").first()).toBeVisible();
    await expect(main.getByText("OPP-103")).toBeVisible();

    failOverview = true;
    await page.getByRole("button", { name: "Refresh" }).click();
    await expect(page.getByRole("heading", { name: "Something went wrong" })).toBeVisible();
    await expect(page.getByText("Could not load overview numbers from the backend.")).toBeVisible();
    failOverview = false;
    await page.getByRole("button", { name: "Try again" }).click();
    await expect(main.getByRole("link", { name: /Opportunities 11/ })).toBeVisible();
  });

  test("workspace insights render live insight payload", async ({ page }) => {
    await login(page, "dclab-admin@verification.invalid");
    await page.goto("/app/insights");
    await expect(page.getByRole("heading", { name: "Insights", exact: true })).toBeVisible();
    await expect(page.getByRole("heading", { name: "No insights yet" })).toBeVisible();

    await page.route(
      (url) => url.origin === "http://127.0.0.1:8001" && url.pathname === "/app/insights",
      async (route) => {
        await route.fulfill({
          status: 200,
          contentType: "application/json",
          body: JSON.stringify({
            categories: [
              { category: "Marketing", insights: [] },
              {
                category: "Sales",
                insights: [
                  {
                    subject_id: "cust_e2e",
                    category: "Sales",
                    headline: "Follow the proposal that is still open",
                    confidence_band: "High",
                    recommended_action: "Contact today",
                    expected_value: 25000,
                    currency: "AED",
                    reasoning: ["This account has an open proposal in the current workspace file."],
                    generated_at: "2026-01-15T10:00:00.000Z",
                  },
                ],
              },
              { category: "Revenue", insights: [] },
              { category: "Churn & Retention", insights: [] },
              { category: "Customer Value", insights: [] },
              { category: "Custom", insights: [] },
            ],
          }),
        });
      },
    );
    await page.getByRole("button", { name: "Refresh" }).click();
    await expect(page.getByRole("heading", { name: "Follow the proposal that is still open" })).toBeVisible();
    await expect(page.getByText("Contact today")).toBeVisible();
    await expect(page.getByText("This account has an open proposal in the current workspace file.")).toBeVisible();
    await expect(page.getByText("12,482")).toHaveCount(0);
  });

  test("workspace labs sample trial returns translated insights", async ({ page }) => {
    await login(page, "dclab-admin@verification.invalid");
    await page.goto("/app/labs");
    await expect(page.getByRole("heading", { name: "Labs", exact: true })).toBeVisible();
    const catalog = await apiGet<JsonRecord[]>(page, "/app/labs/problems");
    const problem = catalog.find((row) => row.use_case === "cross_sell");
    expect(problem, "cross_sell catalog problem").toBeTruthy();
    await page.getByRole("button", { name: String(problem?.category), exact: true }).click();
    await page.getByRole("option", { name: String(problem?.question) }).click();
    await page.getByRole("button", { name: "Run with sample data" }).click();
    await expect(page.getByText(/Results from sample data/)).toBeVisible({ timeout: 60_000 });
    await expect(page.locator("#main").getByRole("alert")).toHaveCount(0);
  });

  test("workspace opportunities upload and decision generation", async ({ page }, testInfo) => {
    const externalId = `e2e-opp-${Date.now()}`;
    const csv = [
      "external_id,customer_id,amount,currency,stage,source,owner_id,created_at",
      `${externalId},cust-e2e,25000,AED,proposal,inbound,rep-e2e,2026-01-15`,
      "",
    ].join("\n");
    const fixture = testInfo.outputPath("e2e-opportunities.csv");
    await writeFile(fixture, csv);

    await login(page, "dclab-admin@verification.invalid");
    await page.goto("/app/dashboards");
    await expect(page.getByRole("heading", { name: "Dashboard", exact: true })).toBeVisible();
    await page.getByRole("navigation", { name: "Application navigation" }).getByRole("link", { name: "Opportunities" }).click();
    await expect(page.getByRole("heading", { name: "Opportunities", exact: true })).toBeVisible();
    await page.locator("#main").getByRole("link", { name: "Upload opportunities" }).or(page.locator("#main").getByRole("link", { name: "Upload", exact: true })).first().click();
    await expect(page).toHaveURL(/\/app\/opportunities\/upload/);
    await expect(page.getByRole("heading", { name: "Upload opportunities" })).toBeVisible();

    const uploaded = page.waitForResponse(
      (response) =>
        response.request().method() === "POST" &&
        new URL(response.url()).pathname === "/app/opportunities/upload",
    );
    await page.locator('input[type="file"]').setInputFiles(fixture);
    const uploadResponse = await uploaded;
    expect(uploadResponse.ok(), await uploadResponse.text()).toBeTruthy();
    await expect(page.getByText("1 inserted")).toBeVisible();
    await page.getByRole("link", { name: "Open opportunities" }).click();
    await expect(page).toHaveURL(/\/app\/opportunities$/);
    await expect(page.getByRole("link", { name: externalId })).toBeVisible();
    await page.getByRole("link", { name: externalId }).click();
    await expect(page).toHaveURL(new RegExp(`/app/opportunities/${externalId}`));
    await expect(page.getByRole("heading", { name: externalId })).toBeVisible();
    await expect(page.getByText("AED 25,000")).toBeVisible();

    await page.getByRole("button", { name: "Generate decision" }).click();
    const generateOutcome = page.getByRole("button", { name: "Regenerate" }).or(page.locator("#main").getByRole("alert"));
    await expect(generateOutcome.first()).toBeVisible({ timeout: 30_000 });
    const generatedOk = await page.getByRole("button", { name: "Regenerate" }).isVisible();

    await page.goto("/app/dashboards");
    await page.getByRole("navigation", { name: "Application navigation" }).getByRole("link", { name: "Decisions" }).click();
    await expect(page.getByRole("heading", { name: "Decisions", exact: true })).toBeVisible();
    if (generatedOk) {
      await expect(page.getByRole("link", { name: externalId }).first()).toBeVisible();
      await page.getByRole("link", { name: "Open" }).first().click();
      await expect(page).toHaveURL(/\/app\/decisions\//);
      await expect(page.getByRole("link", { name: new RegExp(`Source opportunity ${externalId}`) })).toBeVisible();
    } else {
      await expect(page.getByRole("heading", { name: "No decisions yet" })).toBeVisible();
      await expect(page.getByRole("link", { name: "Go to opportunities" })).toHaveAttribute(
        "href",
        "/app/opportunities",
      );
    }
  });

  test("DCLab Admin uploads classification data and replays the monitor", async ({
    page,
  }, testInfo) => {
    await login(page, "dclab-admin@verification.invalid");
    await expect(page.getByText("Business A", { exact: true })).toBeVisible();
    await expect(page.getByText("Business B", { exact: true })).toBeVisible();
    await page.screenshot({
      path: path.join(ARTIFACTS, "dclab-admin-businesses.png"),
      fullPage: true,
    });

    const upload = await uploadCsv(
      page,
      testInfo,
      "classification.csv",
      classificationCsv(),
      "outcome",
    );
    const detail = await waitForUpload(
      page,
      `/admin/client-uploads/${String(upload.id)}`,
      "pipeline_status",
    );
    adminPipelineId = String(detail.experiment_id);
    expect(adminPipelineId).toBeTruthy();

    await page.goto(`/admin/pipeline-runs/${adminPipelineId}/monitor`);
    for (const heading of [
      "Pipeline stage coverage",
      "Preprocessing configuration",
      "Problem Profile",
      "Validation Strategy",
      "Metric Strategy",
      "Leakage Audit",
      "Allowed Features",
      "Excluded Features",
      "Fold-by-fold cross-validation",
      "Candidate comparison",
      "Deterministic verification",
      "Semantic LLM participation",
      "OpenAI Auditor",
      "Predictions",
      "Timeline / replay",
      "Reports",
      "Sanitized raw technical evidence",
    ]) {
      await expect(
        page.getByRole("heading", { name: heading, exact: true, level: 2 }),
      ).toBeVisible();
    }
    await expect(
      page.getByText("DETERMINISTIC VERIFICATION = AUTHORITATIVE"),
    ).toBeVisible();
    await expect(page.getByText(/OPENAI AUDIT = ADVISORY/)).toBeVisible();
    await expect(page.getByText("NOT EVALUATED").first()).toBeVisible();

    const first = await apiGet(
      page,
      `/admin/pipeline-runs/${adminPipelineId}/monitor`,
    );
    const firstSequences = (first.events as JsonRecord[]).map(
      (event) => event.sequence,
    );
    await page.reload();
    await expect(
      page.getByRole("heading", { name: "Pipeline Monitor" }),
    ).toBeVisible();
    const second = await apiGet(
      page,
      `/admin/pipeline-runs/${adminPipelineId}/monitor`,
    );
    expect(
      (second.events as JsonRecord[]).map((event) => event.sequence),
    ).toEqual(firstSequences);
    await page.screenshot({
      path: path.join(ARTIFACTS, "classification-monitor.png"),
      fullPage: true,
    });

    const failedUpload = await uploadCsv(
      page,
      testInfo,
      "too-small.csv",
      classificationCsv(10),
      "outcome",
    );
    const failed = await waitForUpload(
      page,
      `/admin/client-uploads/${String(failedUpload.id)}`,
      "pipeline_status",
      "skipped",
    );
    if (failed.experiment_id) {
      await page.goto(`/admin/pipeline-runs/${String(failed.experiment_id)}/monitor`);
      await expect(
        page.getByRole("heading", { name: "Pipeline Monitor" }),
      ).toBeVisible();
    } else {
      await page.goto(`/admin/models/client-uploads/${String(failedUpload.id)}`);
    }
    await page.screenshot({
      path: path.join(ARTIFACTS, "failed-run-monitor.png"),
      fullPage: true,
    });
  });

  test("DCLab Developer reads both businesses but cannot mutate", async ({
    page,
  }) => {
    await login(page, "dclab-developer@verification.invalid");
    const navigation = page.getByRole("navigation", {
      name: "Application navigation",
    });
    await expect(navigation.getByRole("link", { name: "Registry" })).toBeVisible();
    await expect(navigation.getByRole("link", { name: "Businesses" })).toBeVisible();
    await expect(navigation.getByRole("link", { name: "Business Admin" })).toHaveCount(0);
    await expect(page.getByText("Business A", { exact: true })).toBeVisible();
    await expect(page.getByText("Business B", { exact: true })).toBeVisible();
    await page.goto(`/admin/pipeline-runs/${adminPipelineId}/monitor`);
    await expect(
      page.getByRole("heading", { name: "Pipeline Monitor" }),
    ).toBeVisible();
    await page.goto("/app/labs");
    await expect(page.getByText(/Read-only access/).first()).toBeVisible();
    await expect(page.locator('input[type="file"]').first()).toBeDisabled();
    const denied = await page.request.post(`${API_URL}/admin/environments/dogfood`, {
      headers: await authHeaders(page),
    });
    expect(denied.status()).toBe(403);
    await page.screenshot({
      path: path.join(ARTIFACTS, "readonly-developer.png"),
      fullPage: true,
    });
  });

  test("Business Admin uploads regression data and sees only Business A", async ({
    page,
  }, testInfo) => {
    await login(page, "business-admin-a@verification.invalid");
    const navigation = page.getByRole("navigation", {
      name: "Application navigation",
    });
    await expect(navigation.getByRole("link", { name: "Business Admin" })).toBeVisible();
    await expect(navigation.getByRole("link", { name: "Business Admin" })).toHaveAttribute(
      "aria-current",
      "page",
    );
    await expect(navigation.getByRole("link", { name: "Businesses" })).toHaveCount(0);
    await expect(navigation.getByRole("link", { name: "Model Registry" })).toHaveCount(0);
    await expect(page.getByText("Business A", { exact: true })).toBeVisible();
    await expect(page.getByText("Business B", { exact: true })).toHaveCount(0);
    const workspacesResponse = await page.request.get(
      `${API_URL}/business/workspaces`,
      { headers: await authHeaders(page) },
    );
    expect(workspacesResponse.ok()).toBeTruthy();
    const workspaces = (await workspacesResponse.json()) as JsonRecord[];
    expect(workspaces).toHaveLength(1);
    businessWorkspaceId = String(workspaces[0].id);

    const upload = await uploadCsv(
      page,
      testInfo,
      "regression.csv",
      regressionCsv(),
      "revenue",
    );
    businessUploadId = String(upload.id);
    await expect(page).toHaveURL(new RegExp(`/lab/runs/${String(upload.run_id)}`));
    await waitForUpload(
      page,
      `/app/labs/uploads/${String(upload.run_id)}`,
      "status",
    );
    await expect(page.getByText("Completed", { exact: true }).first()).toBeVisible();
    await expect(page.getByRole("button", { name: "Download results" })).toBeVisible();
    const workspace = await apiGet(
      page,
      `/business/workspaces/${businessWorkspaceId}`,
    );
    const run = (workspace.runs as JsonRecord[]).find(
      (candidate) => String(candidate.source_upload_id) === businessUploadId,
    );
    expect(run).toBeTruthy();
    const runDetail = await apiGet(
      page,
      `/business/workspaces/${businessWorkspaceId}/workflow-runs/${String(run?.id)}`,
    );
    businessPipelineId = String(
      (runDetail.pipelines as JsonRecord[])[0].id,
    );

    await page.goto(
      `/business/workspaces/${businessWorkspaceId}/pipeline-runs/${businessPipelineId}/monitor`,
    );
    await expect(
      page.getByRole("heading", { name: "Pipeline Monitor" }),
    ).toBeVisible();
    await page.screenshot({
      path: path.join(ARTIFACTS, "regression-monitor.png"),
      fullPage: true,
    });
    await page.screenshot({
      path: path.join(ARTIFACTS, "business-admin-monitor.png"),
      fullPage: true,
    });
    await page.goto(`/business/workspaces/${businessWorkspaceId}`);
    await expect(page.getByRole("heading", { name: "Operations" })).toBeVisible();
    await expect(page.getByRole("heading", { name: "Labs" })).toBeVisible();

    const platformBusinesses = await page.request.get(
      `${API_URL}/admin/businesses`,
      { headers: await authHeaders(page) },
    );
    expect(platformBusinesses.status()).toBe(403);
  });

  test("Business capabilities fail closed in API and browser sections", async ({
    page,
  }) => {
    await login(page, "business-admin-a@verification.invalid");
    const monitorPath = `/business/workspaces/${businessWorkspaceId}/pipeline-runs/${businessPipelineId}/monitor`;
    for (const state of ["missing", "false"] as const) {
      setCapability("pipeline_monitor", state);
      const denied = await page.request.get(`${API_URL}${monitorPath}`, {
        headers: await authHeaders(page),
      });
      expect(denied.status()).toBe(403);
    }
    setCapability("pipeline_monitor", "true");

    const sectionCapabilities = {
      cv_fold_details: "cv_fold_details is not enabled",
      semantic_llm_audit: "semantic_llm_audit is not enabled",
      openai_pipeline_audit: "openai_pipeline_audit is not enabled",
      raw_pipeline_debug: "raw_pipeline_debug is not enabled",
      decision_ledger: "decision_ledger is not enabled",
    };
    for (const [capability, unavailableText] of Object.entries(
      sectionCapabilities,
    )) {
      setCapability(capability, "false");
      await page.goto(monitorPath);
      await expect(page.getByText(unavailableText)).toBeVisible();
      setCapability(capability, "true");
      await page.reload();
      await expect(page.getByText(unavailableText)).toHaveCount(0);
    }

    for (const capability of [
      "prediction_download",
      "deep_audit",
      "model_management",
    ]) {
      setCapability(capability, "false");
      const monitor = await page.request.get(`${API_URL}${monitorPath}`, {
        headers: await authHeaders(page),
      });
      expect(monitor.ok()).toBeTruthy();
      if (capability === "prediction_download") {
        const denied = await page.request.get(
          `${API_URL}/business/workspaces/${businessWorkspaceId}/client-uploads/${businessUploadId}/predictions.csv`,
          { headers: await authHeaders(page) },
        );
        expect(denied.status()).toBe(403);
      }
      if (capability === "deep_audit") {
        const denied = await page.request.post(
          `${API_URL}/business/workspaces/${businessWorkspaceId}/lab-runs/${businessUploadId}/verification/deep`,
          { headers: await authHeaders(page) },
        );
        expect(denied.status()).toBe(403);
      }
      if (capability === "model_management") {
        await page.goto(`/business/workspaces/${businessWorkspaceId}`);
        await expect(
          page.getByText("model_management is not enabled for this workspace."),
        ).toBeVisible();
        const models = ((await apiGet(
          page,
          `/business/workspaces/${businessWorkspaceId}`,
        )).models ?? []) as JsonRecord[];
        expect(models).toHaveLength(0);
      }
      setCapability(capability, "true");
    }
  });

  test("Business Developer is tenant-scoped and read-only", async ({ page }) => {
    await login(page, "business-developer-a@verification.invalid");
    const navigation = page.getByRole("navigation", {
      name: "Application navigation",
    });
    await expect(navigation.getByRole("link", { name: "Business Admin" })).toBeVisible();
    await expect(navigation.getByRole("link", { name: "Businesses" })).toHaveCount(0);
    await expect(navigation.getByRole("link", { name: "Model Registry" })).toHaveCount(0);
    await expect(page.getByRole("navigation", { name: "Marketing" })).toHaveCount(0);
    const workspaceList = (await (
      await page.request.get(`${API_URL}/business/workspaces`, {
        headers: await authHeaders(page),
      })
    ).json()) as JsonRecord[];
    expect(workspaceList).toHaveLength(1);
    businessWorkspaceId = String(workspaceList[0].id);

    const adminPage = await page.request.get(`${API_URL}/admin/businesses`, {
      headers: await authHeaders(page),
    });
    expect(adminPage.status()).toBe(403);
    const uploadDenied = await page.request.post(`${API_URL}/app/labs/uploads`, {
      headers: await authHeaders(page),
      multipart: {
        category: "Customer Value",
        file: {
          name: "blocked.csv",
          mimeType: "text/csv",
          buffer: Buffer.from(classificationCsv(40)),
        },
      },
    });
    expect(uploadDenied.status()).toBe(403);
    await page.goto(
      `/business/workspaces/${businessWorkspaceId}/pipeline-runs/${businessPipelineId}/monitor`,
    );
    await expect(
      page.getByRole("heading", { name: "Pipeline Monitor" }),
    ).toBeVisible();
    await expect(navigation.getByRole("link", { name: "Business Admin" })).toHaveAttribute(
      "aria-current",
      "page",
    );
    await expect(
      page.getByRole("button", { name: "Run deep audit" }),
    ).toBeDisabled();
    const workspaces = await apiGet<JsonRecord[]>(page, "/business/workspaces");
    expect(workspaces).toHaveLength(1);
    expect(workspaces[0].id).toBe(businessWorkspaceId);
  });

  test("Business A cannot substitute Business B identifiers", async ({ page }) => {
    await login(page, "business-admin-a@verification.invalid");
    const adminLogin = await page.request.post(`${API_URL}/auth/login`, {
      headers: { "Content-Type": "application/json" },
      data: JSON.stringify({
        email: "dclab-admin@verification.invalid",
        password: PASSWORD,
      }),
    });
    const adminToken = ((await adminLogin.json()) as JsonRecord)
      .access_token as string;
    const allBusinesses = (await (
      await page.request.get(`${API_URL}/admin/businesses`, {
        headers: { Authorization: `Bearer ${adminToken}` },
      })
    ).json()) as JsonRecord[];
    businessBWorkspaceId = String(
      allBusinesses.find((row) => row.slug === "business-b")?.id,
    );
    const foreign = await page.request.get(
      `${API_URL}/business/workspaces/${businessBWorkspaceId}`,
      { headers: await authHeaders(page) },
    );
    expect(foreign.status()).toBe(404);
    const substitutedPipeline = await page.request.get(
      `${API_URL}/business/workspaces/${businessBWorkspaceId}/pipeline-runs/${businessPipelineId}/monitor`,
      { headers: await authHeaders(page) },
    );
    expect(substitutedPipeline.status()).toBe(404);
    await page.goto(`/business/workspaces/${businessBWorkspaceId}`);
    await expect(page.getByText("Business B", { exact: true })).toHaveCount(0);
  });
});
