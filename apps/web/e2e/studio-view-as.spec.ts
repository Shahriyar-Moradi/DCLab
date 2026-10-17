import { expect, test, type Page } from "@playwright/test";
import { axeViolations, login, shot } from "./support/studio-fixture";

/**
 * D1 (founder decision 8): role names and the admin-only "View as" preview. Real backend, nothing stubbed.
 * The preview changes only the sidebar and which screens open, on this browser tab; it resets on reload and never
 * changes the session or what is sent to the API.
 */
const VIEWER = process.env.DCLAB_E2E_VIEWER_EMAIL ?? "viewer-a@verification.invalid";
const PASSWORD = process.env.DCLAB_E2E_STUDIO_PASSWORD ?? "VerificationOnly123!";
const NOTE = "Preview: you still have admin rights; nothing is changed.";

async function axeBoth(page: Page, where: string): Promise<void> {
  for (const scheme of ["light", "dark"] as const) {
    await page.emulateMedia({ colorScheme: scheme });
    expect(await axeViolations(page), `${where} (${scheme})`).toEqual([]);
  }
  await page.emulateMedia({ colorScheme: "light" });
}

test("an admin previews each role's sidebar and screens; nothing changes and a reload ends it", async ({ page }) => {
  const sent: string[] = [];
  page.on("request", (request) => {
    if (request.url().includes("/api/backend/")) sent.push(`${request.method()} ${request.url()} ${request.postData() ?? ""} ${JSON.stringify(request.headers())}`);
  });
  await login(page);
  await page.goto("/home");
  const sidebar = page.getByRole("navigation", { name: "Studio" });
  const viewAs = page.getByRole("banner").getByLabel("View as", { exact: true });
  await expect(viewAs).toBeVisible();
  await expect(viewAs).toHaveValue("");
  await expect(viewAs.locator("option")).toHaveText(["Admin (your own view)", "Data scientist", "Analyst (view only)", "Business user"]);
  await expect(page.locator(".nav .ws small")).toHaveText("business workspace · Admin");
  const meBefore = await (await page.request.get("/api/backend/v1/me")).json();

  // Business user: only the Business view entry point; Studio screens say they are not open to this role.
  await viewAs.selectOption("business_user");
  await expect(page.getByText(NOTE)).toBeVisible();
  await expect(sidebar.getByRole("link")).toHaveCount(2); // Outcomes and (D7b) the questions from the data team
  await expect(sidebar.getByRole("link", { name: "Outcomes" })).toHaveAttribute("href", "/outcomes");
  await expect(page.getByRole("heading", { name: "This screen is not open to the Business user role", level: 2 })).toBeVisible();
  await expect(page.getByRole("main").getByRole("link", { name: "Open the Business view" })).toHaveAttribute("href", "/outcomes");
  await shot(page, "view-as-1-business-user");
  await axeBoth(page, "business user preview");

  // Analyst (view only): the Studio sidebar, the screen opens; moving to another screen keeps the preview.
  await viewAs.selectOption("analyst");
  await expect(page.getByRole("heading", { name: /This screen is not open/ })).toHaveCount(0);
  await expect(page.getByRole("heading", { name: /^Good (morning|afternoon|evening)/, level: 1 })).toBeVisible();
  for (const name of [/^Home$/, /^Inbox/, /^Projects$/, /^Settings$/]) await expect(sidebar.getByRole("link", { name })).toBeVisible();
  await expect(sidebar.getByRole("link", { name: "Operator console" })).toHaveCount(0);
  await sidebar.getByRole("link", { name: "Settings", exact: true }).click();
  await expect(page).toHaveURL(/\/settings$/);
  await expect(page.getByLabel("View as", { exact: true })).toHaveValue("analyst");
  await expect(page.getByText(NOTE)).toBeVisible();
  // The screen itself still shows the admin's own access: the API answered for the real session.
  await expect(page.getByRole("table", { name: "People in this workspace" })).toBeVisible();
  const previewCard = page.locator("section.card", { has: page.getByRole("heading", { name: "What the Analyst (view only) role can do (preview)" }) });
  await expect(previewCard).toContainText("See projects, data, runs, results and the Inbox");
  await expect(previewCard).toContainText("Create projects, upload data, start runs or decide in the Inbox");
  await shot(page, "view-as-2-analyst-settings");

  // A reload ends the preview.
  await page.reload();
  await expect(page.getByLabel("View as", { exact: true })).toHaveValue("");
  await expect(page.getByText(NOTE)).toHaveCount(0);

  // Stop the preview from the banner.
  await page.getByLabel("View as", { exact: true }).selectOption("data_scientist");
  await expect(page.getByText(NOTE)).toBeVisible();
  await page.getByRole("button", { name: "Stop the preview" }).click();
  await expect(page.getByText(NOTE)).toHaveCount(0);
  await expect(page.getByLabel("View as", { exact: true })).toHaveValue("");

  // Nothing about the preview reached the API, and the session is unchanged.
  for (const line of sent) expect(line).not.toMatch(/view.?as|preview|business_user|data_scientist|analyst/i);
  const meAfter = await (await page.request.get("/api/backend/v1/me")).json();
  expect(meAfter.capabilities).toEqual(meBefore.capabilities);
  expect(meAfter.role).toBe(meBefore.role);
});

test("people who are not admins never see View as", async ({ page }) => {
  await page.goto("/login");
  await page.getByLabel("Email").fill(VIEWER);
  await page.getByLabel("Password").fill(PASSWORD);
  await page.getByRole("button", { name: "Sign in" }).click();
  await expect(page).not.toHaveURL(/\/login/);
  for (const path of ["/home", "/settings", "/projects"]) {
    await page.goto(path);
    await expect(page.locator(".page-head h1")).toBeVisible();
    await expect(page.getByLabel("View as", { exact: true })).toHaveCount(0);
  }
  // A stored preview from another session in this tab is ignored for a non-admin.
  await page.evaluate(() => sessionStorage.setItem("dclab.viewAsPreview", "business_user"));
  await page.goto("/home");
  await expect(page.locator(".page-head h1")).toBeVisible();
  await expect(page.getByText(NOTE)).toHaveCount(0);
  await expect(page.locator(".nav .ws small")).toHaveText("business workspace · Analyst (view only)");
  await shot(page, "view-as-3-analyst-own");
});
