import { expect, test, type Browser, type Page } from "@playwright/test";
import { axeLightDarkAt1280And375, churnCsv, shot } from "./support/studio-fixture";

/**
 * D7b: questions to the business, person to person. A data scientist asks on the project's "Ask the business" page; a
 * business user (client account) sees it in the Business view and replies; the asker sees the reply in the Inbox, opens
 * the question and closes it. A viewer (read only) cannot ask: no form, and the API refuses. No worker needed.
 */
const ADMIN = process.env.DCLAB_E2E_STUDIO_EMAIL ?? "business-admin-a@verification.invalid";
const CLIENT = process.env.DCLAB_E2E_CLIENT_EMAIL ?? "client-user@verification.invalid";
const VIEWER = process.env.DCLAB_E2E_VIEWER_EMAIL ?? "viewer-a@verification.invalid";
const PASSWORD = process.env.DCLAB_E2E_STUDIO_PASSWORD ?? "VerificationOnly123!";
const UUID_RE = /[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}/;

async function signIn(browser: Browser, email: string): Promise<Page> {
  const context = await browser.newContext({ colorScheme: "light" });
  const page = await context.newPage();
  await page.goto("/login");
  await page.getByLabel("Email").fill(email);
  await page.getByLabel("Password").fill(PASSWORD);
  await page.getByRole("button", { name: "Sign in" }).click();
  await expect(page).not.toHaveURL(/\/login/);
  return page;
}

async function writeHeaders(page: Page, label: string): Promise<Record<string, string>> {
  const csrf = (await page.context().cookies()).find((c) => c.name === "dclab_csrf")?.value ?? "";
  return { "Idempotency-Key": `e2e-d7b-${label}-${Date.now()}`, "X-CSRF-Token": csrf, Origin: new URL(page.url()).origin };
}

test("Ask the business: a data scientist asks, a business user replies, the asker reads it in the Inbox and closes it", async ({ browser }) => {
  const stamp = Date.now();
  const questionText = `Should customers on family_5g be treated like the others for now? <b>${stamp}</b>\nSecond line.`;
  const replyText = `Yes, for now. ${stamp}`;

  // 1. A data scientist creates a project (an upload is enough) and asks the business.
  const admin = await signIn(browser, ADMIN);
  const name = `Questions ${stamp}`;
  await admin.goto("/projects/new");
  await admin.getByLabel("Project name").fill(name);
  await admin.getByLabel("Data file").setInputFiles({ name: "churn.csv", mimeType: "text/csv", buffer: Buffer.from(churnCsv()) });
  await admin.getByRole("button", { name: "Upload and continue" }).click();
  await expect(admin.getByRole("table", { name: "Columns of the uploaded file" })).toContainText("customer_code");
  const projects = (await admin.request.get("/api/backend/v1/projects").then((r) => r.json())) as Array<{ id: string; name: string }>;
  const projectId = projects.find((p) => p.name === name)?.id ?? "";
  expect(projectId).toMatch(UUID_RE);

  await admin.goto(`/projects/${projectId}/questions`);
  await expect(admin.getByRole("heading", { name: "Ask the business", level: 1 })).toBeVisible();
  await expect(admin.getByText("No questions to the business yet.", { exact: false })).toBeVisible();
  const box = admin.getByLabel("Your question");
  await box.fill("x".repeat(2001));
  await expect(admin.getByText("2,001 of 2,000 characters", { exact: false })).toBeVisible();
  await expect(admin.getByRole("button", { name: "Send the question" })).toBeDisabled();
  await box.fill(questionText);
  await expect(admin.getByLabel("About (optional)")).toHaveValue("");
  const asked = admin.waitForRequest((r) => r.method() === "POST" && r.url().endsWith(`/v1/projects/${projectId}/questions`));
  await admin.getByRole("button", { name: "Send the question" }).click();
  expect((await asked).headers()["idempotency-key"]).toBeTruthy();
  await expect(admin.getByRole("status").filter({ hasText: "Sent. The business users of this workspace see it" })).toBeVisible();
  const card = admin.getByTestId("business-question").first();
  await expect(card.getByTestId("question-text")).toHaveText(questionText); // plain text: the tag is shown, not parsed
  await expect(card.locator("b")).toHaveCount(0);
  await expect(card).toContainText("Waiting for a reply");
  await expect(card).toContainText("You");
  await axeLightDarkAt1280And375(admin, "Ask the business page");
  await shot(admin, "questions-1-asked");

  // 2. A viewer reads the page but cannot ask (no form), and the API refuses the write.
  const viewer = await signIn(browser, VIEWER);
  await viewer.goto(`/projects/${projectId}/questions`);
  await expect(viewer.getByText("Only members of this workspace's data team can ask the business.", { exact: false })).toBeVisible();
  await expect(viewer.getByLabel("Your question")).toHaveCount(0);
  await expect(viewer.getByTestId("business-question").first().getByTestId("question-text")).toHaveText(questionText);
  const refused = await viewer.request.post(`/api/backend/v1/projects/${projectId}/questions`, { data: { text: "Can I ask?" }, headers: await writeHeaders(viewer, "viewer") });
  expect(refused.status()).toBe(403);
  expect(((await refused.json()) as { error: { code: string } }).error.code).toBe("question_ask_not_permitted");
  await viewer.context().close();

  // 3. The business user sees it in the Business view (open first, reply box open) and replies.
  const client = await signIn(browser, CLIENT);
  await client.goto("/outcomes#questions");
  const section = client.locator("#questions");
  await expect(section.getByRole("heading", { name: "Questions from the data team" })).toBeVisible({ timeout: 30_000 });
  const theirs = section.getByTestId("business-question").filter({ hasText: String(stamp) }).first();
  await expect(theirs.getByTestId("question-text")).toHaveText(questionText);
  await expect(theirs).toContainText("A person on the data team");
  await expect(theirs).toContainText(`Project: ${name}`);
  await expect(theirs).not.toContainText(ADMIN);
  const sidebar = client.getByRole("navigation", { name: "Studio" });
  await expect(sidebar.getByRole("link", { name: "Questions from the data team" })).toHaveAttribute("href", "/outcomes#questions");
  await theirs.getByRole("textbox", { name: "Your reply" }).fill(replyText);
  const replied = client.waitForRequest((r) => r.method() === "POST" && /\/v1\/questions\/[0-9a-f-]{36}\/replies$/.test(r.url()));
  await theirs.getByRole("button", { name: "Send the reply" }).click();
  expect((await replied).headers()["idempotency-key"]).toBeTruthy();
  await expect(theirs.getByRole("status").filter({ hasText: "Your reply was sent to the data team." })).toBeVisible();
  await expect(theirs.getByTestId("question-reply").filter({ hasText: replyText })).toContainText("You");
  await expect(theirs).toContainText("Answered");
  const text = await client.locator("main").innerText();
  expect(text).not.toMatch(UUID_RE);
  expect(text).not.toMatch(/\b(AI|assistant|agent)\b/);
  await axeLightDarkAt1280And375(client, "Business view questions");
  await shot(client, "questions-2-business-replied");
  await client.context().close();

  // 4. The asker sees the reply in the Inbox, opens the question and closes it (If-Match).
  await admin.goto("/inbox");
  const item = admin.getByRole("article", { name: "A business user replied to your question" }).first();
  await expect(item).toBeVisible({ timeout: 30_000 });
  await expect(item).toContainText("Replied by a business user");
  await item.getByRole("link", { name: /Open the question/ }).click();
  await expect(admin).toHaveURL(new RegExp(`/projects/${projectId}/questions\\?question=[0-9a-f-]{36}$`));
  const mine = admin.getByTestId("business-question").filter({ hasText: String(stamp) }).first();
  await expect(mine.getByTestId("question-reply").filter({ hasText: replyText })).toContainText("A business user");
  const closed = admin.waitForRequest((r) => r.method() === "POST" && /\/v1\/questions\/[0-9a-f-]{36}\/close$/.test(r.url()));
  await mine.getByRole("button", { name: "Close the question" }).click();
  await expect(mine).toContainText("Close this question? It will take no more replies, from anyone.");
  await mine.getByRole("button", { name: "Yes, close it" }).click();
  expect((await closed).headers()["if-match"]).toMatch(/^"\d+"$/);
  await expect(mine).toContainText("Closed by you. It takes no more replies.");
  await expect(mine.getByRole("heading", { level: 3 })).toBeFocused();
  await expect(mine.getByRole("button", { name: "Close the question" })).toHaveCount(0);
  await expect(mine.getByLabel("Follow-up")).toHaveCount(0);
  await shot(admin, "questions-3-closed");
  await admin.context().close();
});
