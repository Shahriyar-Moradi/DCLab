import { expect, test, type Page } from "@playwright/test";
import { axeViolations, login, shot, trainedProject } from "./support/studio-fixture";

/**
 * V7 UI-A: status tags under each page title, "What's real today", guide toggle (remembered), glossary panel,
 * "Next step" in flow order, sidebar links, and the responsive shell at 1280 / 800 / 375 with axe in light and dark.
 * Real backend (API + worker) and a real trained run; nothing is stubbed.
 */
test.describe.configure({ mode: "serial" });

async function noPageScroll(page: Page): Promise<void> {
  expect(await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth)).toBeLessThanOrEqual(0);
}

async function axeBoth(page: Page, where: string): Promise<void> {
  for (const scheme of ["light", "dark"] as const) {
    await page.emulateMedia({ colorScheme: scheme });
    expect(await axeViolations(page), `${where} (${scheme})`).toEqual([]);
  }
  await page.emulateMedia({ colorScheme: "light" });
}

test("status tags open What's real today; sidebar, glossary and guide toggle", async ({ page }) => {
  await login(page);
  await page.goto("/home");
  const head = page.locator(".page-head");
  await expect(page.getByRole("heading", { name: /^Good (morning|afternoon|evening)/, level: 1 })).toBeVisible();
  await expect(head.getByRole("link", { name: /Status of this screen: Working today/ })).toBeVisible();

  const sidebar = page.getByRole("navigation", { name: "Studio" });
  await expect(sidebar.getByRole("link", { name: "What's real today" })).toHaveAttribute("href", "/real");
  await expect(sidebar.getByRole("link", { name: "Business view" })).toHaveAttribute("href", "/outcomes");
  // D6b: AI settings (Beta) is in the menu; the assistant is a top-bar toggle, never a menu item.
  await expect(sidebar.getByRole("link", { name: "AI settings" })).toHaveAttribute("href", "/governance");
  for (const hidden of ["Assistant", "Open the assistant", "Governance"]) await expect(sidebar.getByRole("link", { name: hidden })).toHaveCount(0);

  await head.getByRole("link", { name: /Status of this screen/ }).click();
  await expect(page).toHaveURL(/\/real$/);
  await expect(page.getByRole("heading", { name: "What's real today", level: 1 })).toBeVisible();
  await expect(page.getByText(/The whole project flow works today/)).toBeVisible();
  const table = page.getByRole("table", { name: "Every screen, its status and what is missing" });
  const row = (name: string) => table.getByRole("row").filter({ has: page.getByRole("rowheader", { name, exact: true }) });
  await expect(row("Improve")).toContainText("Working today");
  await expect(row("Improve")).toContainText("Guided");
  await expect(row("Monitoring")).toContainText("Working today");
  await expect(row("Business view")).toContainText("Working today");
  await expect(row("Usage & cost (in Settings)")).toContainText("Working today");
  await expect(row("Notifications (email)")).toContainText("Partly working · no screen yet");
  await expect(row("Integrations (webhooks)")).toContainText("Partly working · no screen yet");
  await expect(row("Assistant")).toContainText("Partly working · Beta");
  await expect(row("AI settings")).toContainText("Partly working · Beta");
  await expect(table.getByRole("link", { name: "Business view" })).toHaveAttribute("href", "/outcomes");
  await expect(page.locator(".page-head .status-tag")).toHaveCount(0); // the list itself has no tag
  await shot(page, "shell-1-real");

  // Glossary panel: the 16 terms, closed with Escape.
  await page.getByRole("button", { name: "Glossary" }).click();
  const dialog = page.getByRole("dialog", { name: "Glossary" });
  await expect(dialog).toBeVisible();
  await expect(dialog.locator("dt")).toHaveCount(16);
  for (const term of ["LightGBM", "CatBoost", "fold", "trust checks", "access token", "final test set (used once)"]) await expect(dialog.getByText(term, { exact: true })).toBeVisible();
  await shot(page, "shell-2-glossary");
  await axeBoth(page, "glossary");
  await page.keyboard.press("Escape");
  await expect(dialog).toBeHidden();

  // Guide toggle: hidden on every screen, remembered after a reload, shown again.
  await page.goto("/inbox");
  const guide = page.getByRole("region", { name: "About this screen" });
  await expect(guide.getByRole("heading", { name: "What this is" })).toBeVisible();
  await guide.getByRole("button", { name: "Hide the guide" }).click();
  await expect(page.getByRole("button", { name: "Show the guide" })).toBeFocused();
  await expect(guide.getByRole("heading", { name: "What this is" })).toHaveCount(0);
  await page.goto("/projects");
  await expect(page.getByRole("button", { name: "Show the guide" })).toBeVisible();
  await page.reload();
  await expect(page.getByRole("button", { name: "Show the guide" })).toBeVisible();
  await shot(page, "shell-3-guide-hidden");
  await page.getByRole("button", { name: "Show the guide" }).click();
  await expect(page.getByRole("region", { name: "About this screen" }).getByRole("heading", { name: "What this is" })).toBeVisible();
  await page.reload();
  await expect(page.getByRole("button", { name: "Hide the guide" })).toBeVisible();
  await axeBoth(page, "/projects");
});

test("Next step follows the flow order on every step screen; responsive shell at 1280 / 800 / 375", async ({ page }) => {
  await login(page);
  const { projectId, experimentId } = await trainedProject(page, "Shell v7");
  const base = `/projects/${projectId}`;
  // The test design ref is recorded with the run's results; read it once it is there.
  let split: string | undefined;
  await expect.poll(async () => {
    const refs = (await page.request.get(`/api/backend/v1/projects/${projectId}/refs`).then((r) => r.json())) as { items: Array<{ ref_kind: string; target: { id: string } }> };
    split = refs.items.find((r) => r.ref_kind === "split_plan")?.target.id;
    return split ?? null;
  }, { message: "the trained run has a test design", timeout: 30_000 }).not.toBeNull();

  const chain: Array<[string, string, string]> = [
    [`${base}/data`, "See the goal and test design", `${base}/splits/${split}`],
    [`${base}/splits/${split}`, "Run and compare experiments", `${base}/experiments`],
    [`${base}/experiments`, "Try to improve the best run", `${base}/improve`],
    [`${base}/improve`, "Look at the model", `${base}/models`],
    [`${base}/models`, "Score new data", `${base}/predictions`],
    [`${base}/predictions`, "Check how the model is doing", `${base}/monitoring`],
    [`${base}/monitoring`, "See the history", `${base}/decisions`],
  ];
  for (const [path, title, target] of chain) {
    await page.goto(path);
    await expect(page.locator(".page-head .status-tag").first(), path).toBeVisible();
    const next = page.getByRole("navigation", { name: "Next step" }).getByRole("link");
    await expect(next, path).toContainText(title);
    await expect(next, path).toHaveAttribute("href", target);
  }
  await page.goto(`${base}/decisions`);
  await expect(page.getByRole("heading", { name: "History", level: 1 })).toBeVisible();
  await expect(page.getByRole("navigation", { name: "Next step" })).toHaveCount(0);
  // A page reached from a step (a run) is not a step screen: no forward button, but it keeps the step's status tag.
  await page.goto(`${base}/experiments/${experimentId}`);
  await expect(page.locator(".page-head .status-tag").first()).toBeVisible();
  await expect(page.getByRole("navigation", { name: "Next step" })).toHaveCount(0);
  await page.goto(`${base}/experiments`);
  await page.getByRole("navigation", { name: "Next step" }).getByRole("link").click();
  await expect(page).toHaveURL(new RegExp(`${base}/improve$`));
  await expect(page.locator(".page-head").getByRole("link", { name: /Status of this screen: Working today/ })).toBeVisible();

  for (const [width, height, name] of [[1280, 900, "1280"], [800, 1000, "800"], [375, 812, "375"]] as const) {
    await page.setViewportSize({ width, height });
    for (const path of [`${base}/experiments`, "/real"]) {
      await page.goto(path);
      await expect(page.locator(".page-head h1")).toBeVisible();
      // D1: an admin, so the top bar also carries "View as"; the page must still not scroll sideways.
      await expect(page.getByLabel("View as", { exact: true })).toBeVisible();
      await noPageScroll(page);
      const nav = await page.locator(".studio .nav").evaluate((el) => ({ position: getComputedStyle(el).position, direction: getComputedStyle(el).flexDirection }));
      const guideCols = await page.locator(".studio .guide").first().evaluate((el) => getComputedStyle(el).gridTemplateColumns.split(" ").length);
      if (width <= 860) {
        expect(nav, `${path} at ${width}`).toEqual({ position: "sticky", direction: "row" });
        expect(guideCols, `${path} at ${width}`).toBe(1);
      } else {
        expect(nav.direction, `${path} at ${width}`).toBe("column");
      }
      if (path.endsWith("/experiments")) {
        const flow = await page.locator(".steps.flow").evaluate((el) => ({ overflow: getComputedStyle(el).overflowX, wrap: getComputedStyle(el).flexWrap }));
        if (width <= 860) expect(flow).toEqual({ overflow: "auto", wrap: "nowrap" });
        await expect(page.getByRole("navigation", { name: "Next step" })).toBeVisible();
      }
      await axeBoth(page, `${path} at ${width}`);
      await shot(page, `shell-4-${path === "/real" ? "real" : "experiments"}-${name}`);
    }
  }
  // The strip stays reachable while scrolling at 375.
  await page.goto(`${base}/experiments`);
  await expect(page.locator(".page-head h1")).toBeVisible();
  await page.mouse.wheel(0, 1200);
  await expect.poll(() => page.evaluate(() => window.scrollY)).toBeGreaterThan(0);
  expect(await page.locator(".studio .nav").evaluate((el) => Math.round(el.getBoundingClientRect().top))).toBe(0);
  await shot(page, "shell-5-strip-scrolled-375");
  await page.setViewportSize({ width: 1280, height: 900 });
});
