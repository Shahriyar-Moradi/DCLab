import { expect, test, type Page } from "@playwright/test";
import { axeViolations, login, shot, trainedProject } from "./support/studio-fixture";

/**
 * D2b (founder decisions 9 and 10): on a real trained run, the 15 trust checks are listed in the server's registry order
 * with plain result words, the timeline follows the real stage order (the final test set is set aside before the
 * training rows are profiled) with a time per step and per stage, and the tries come from `attempts` on the run read.
 * "retried automatically" is checked with the real response edited by page.route (a real out-of-memory retry cannot be
 * produced on demand); everything else is the real read. "Coming next" cards are pictures, never controls.
 */
type Findings = { checks: Array<{ check: string; status: string }> };
type Build = { stages: Array<{ key: string; sequence: number }> };
type Attempt = { attempt: number; outcome: string; failure_class?: string | null; started_at: string; ended_at?: string | null; duration_seconds?: number | null };
const WORDS: Record<string, RegExp> = { pass: /^Passed$/, warning: /^Warning$/, fail: /^Failed/, not_evaluated: /^Not checked$/ };

async function axeEverywhere(page: Page, where: string): Promise<void> {
  for (const width of [1280, 800, 375]) {
    await page.setViewportSize({ width, height: 900 });
    for (const scheme of ["light", "dark"] as const) {
      await page.emulateMedia({ colorScheme: scheme });
      expect(await axeViolations(page), `${where} ${width}px (${scheme})`).toEqual([]);
    }
  }
  await page.emulateMedia({ colorScheme: "light" });
  await page.setViewportSize({ width: 1280, height: 900 });
}

test("a trained run: 15 checks in registry order, the real timeline with times, and its tries", async ({ page }) => {
  await login(page);
  const { projectId, experimentId } = await trainedProject(page, "Run honesty");
  const findings = (await page.request.get(`/api/backend/v1/experiments/${experimentId}/findings`).then((r) => r.json())) as Findings;
  const build = (await page.request.get(`/api/backend/v1/model-builds/${experimentId}`).then((r) => r.json())) as Build;
  // The worker closes its try a moment after the run is marked finished; wait for the read to say so.
  let run: { attempts?: Attempt[] } = {};
  await expect.poll(async () => {
    run = (await page.request.get(`/api/backend/v1/experiments/${experimentId}`).then((r) => r.json())) as { attempts?: Attempt[] };
    return run.attempts?.map((a) => a.outcome) ?? [];
  }, { timeout: 30_000 }).toEqual(["completed"]);

  await page.goto(`/projects/${projectId}/experiments/${experimentId}`);
  // Checks: one row per registry check, in the order the read sends them, with the read's result in words.
  const list = page.getByTestId("check-list");
  await expect(list.locator("tbody tr")).toHaveCount(15);
  expect(findings.checks).toHaveLength(15);
  expect(await list.locator("tbody tr").evaluateAll((rows) => rows.map((r) => r.getAttribute("data-check-row")))).toEqual(findings.checks.map((c) => c.check));
  for (const [index, check] of findings.checks.entries()) {
    const row = list.locator("tbody tr").nth(index);
    await expect(row.locator("td").first()).toHaveText(String(index + 1));
    expect((await row.locator("td").last().innerText()).trim()).toMatch(WORDS[check.status]);
  }

  // Timeline: steps in the real order with a time each; every engine stage the run recorded appears once, in sequence order.
  const steps = page.getByRole("table", { name: "Steps of this run and the time each took" });
  await expect(steps.locator("tbody tr[data-step='train']")).toBeVisible();
  const ids = await steps.locator("tbody tr").evaluateAll((rows) => rows.map((r) => r.getAttribute("data-step")));
  expect(ids.indexOf("split")).toBeGreaterThan(ids.indexOf("read"));
  expect(ids.indexOf("profile")).toBeGreaterThan(ids.indexOf("split"));
  expect(ids.indexOf("train")).toBeGreaterThan(ids.indexOf("profile"));
  for (const summary of await steps.locator("details summary").all()) await summary.click();
  const shownStages = await steps.locator("[data-stage]").evaluateAll((items) => items.map((i) => i.getAttribute("data-stage")));
  expect(shownStages.slice().sort()).toEqual(build.stages.map((s) => s.key).sort());
  const bySequence = build.stages.slice().sort((a, b) => a.sequence - b.sequence).map((s) => s.key);
  expect(shownStages.indexOf("holdout_lock")).toBeLessThan(shownStages.indexOf("problem_profile"));
  expect(bySequence.indexOf("holdout_lock")).toBeLessThan(bySequence.indexOf("problem_profile"));
  await expect(steps.locator("tbody tr").first().locator("td.r")).toHaveText(/\d+(\.\d)? (ms|s)|\d+ min/);

  // Tries: exactly what the read says (a run that ran once: "Ran once.", "finished"); nothing about retries.
  const tries = page.getByTestId("run-attempts");
  expect(run.attempts?.length ?? 0).toBeGreaterThan(0);
  await expect(tries.getByRole("listitem")).toHaveCount(run.attempts!.length);
  await expect(tries).toContainText("Ran once.");
  await expect(tries).toContainText("Try 1");
  await expect(tries).toContainText("finished");
  await expect(tries).not.toContainText("retried");
  await shot(page, "d2b-run-1280");
  await axeEverywhere(page, "run page");

  // The same page with the real read edited to one out-of-memory retry, then the success.
  const failedFirst: Attempt[] = [
    { attempt: 1, outcome: "retried", failure_class: "out_of_memory", started_at: "2026-10-16T10:00:00Z", ended_at: "2026-10-16T10:01:00Z", duration_seconds: 60 },
    { ...run.attempts![run.attempts!.length - 1], attempt: 2 },
  ];
  await page.route(`**/api/backend/v1/experiments/${experimentId}`, async (route) => {
    const response = await route.fetch().catch(() => null);
    if (!response) return; // the page navigated away mid-request
    await route.fulfill({ response, json: { ...(await response.json()), attempts: failedFirst } }).catch(() => undefined);
  });
  await page.reload();
  await expect(tries).toContainText("failed: out of memory, retried automatically");
  await expect(tries).toContainText("Ran 2 times; the queue started it again on its own after a failed try.");
  await shot(page, "d2b-run-retried");
  // A final failure is not called a retry.
  // A fresh tab (same session) with only this stub, so no earlier stub or in-flight refetch can answer for it.
  const runUrl = page.url();
  await page.unrouteAll({ behavior: "ignoreErrors" });
  const fresh = await page.context().newPage();
  await fresh.route(`**/api/backend/v1/experiments/${experimentId}`, async (route) => {
    const response = await route.fetch().catch(() => null);
    if (!response) return; // the page navigated away mid-request
    await route.fulfill({ response, json: { ...(await response.json()), attempts: [{ ...failedFirst[0], outcome: "failed" }] } }).catch(() => undefined);
  });
  await fresh.goto(runUrl);
  const freshTries = fresh.getByTestId("run-attempts");
  await expect(freshTries).toContainText("failed: out of memory");
  await expect(freshTries).not.toContainText("retried");
  await fresh.unrouteAll({ behavior: "ignoreErrors" });
  await fresh.close();
});

test("Coming next: a picture marked as an example, with nothing to click, and no internal plan ids", async ({ page }) => {
  await login(page);
  await page.goto("/settings");
  const card = page.locator("[data-coming-next='earnedAutonomy']");
  await expect(card.getByRole("heading", { name: /What AI may do on its own/ })).toBeVisible();
  await expect(card).toContainText("Coming next");
  await expect(card).toContainText("Not built yet.");
  await expect(card).toContainText("connected tools act only through the access tokens you give them");
  const picture = card.getByRole("img", { name: /^Example, not your data\./ });
  await expect(picture).toBeVisible();
  await expect(picture.locator("a, button, input, select, textarea, [tabindex]")).toHaveCount(0);
  expect(await page.locator("main").innerText()).not.toMatch(/\bP\d+\.\d+\b|Planned in/);
  await shot(page, "d2b-settings-coming-next");
  await axeEverywhere(page, "settings");
});

test("Coming next on the operator console: the backups tile is an example picture, not a reading", async ({ page }) => {
  await page.goto("/login");
  await page.getByLabel("Email").fill("dclab-admin@verification.invalid");
  await page.getByLabel("Password").fill(process.env.DCLAB_E2E_STUDIO_PASSWORD ?? "VerificationOnly123!");
  await page.getByRole("button", { name: "Sign in" }).click();
  await expect(page).not.toHaveURL(/\/login/);
  await page.goto("/operator");
  const card = page.locator("[data-coming-next='backups']");
  await expect(card.getByRole("heading", { name: /Backups/ })).toBeVisible();
  await expect(card).toContainText("Not configured");
  await expect(card.getByRole("img", { name: /^Example, not your data\./ })).toBeVisible();
  await expect(card.locator("a, button, input, select, textarea")).toHaveCount(0);
  await page.getByRole("tab", { name: /Quarantine/ }).click();
  expect(await page.locator("main").innerText()).not.toMatch(/\bP\d+\.\d+\b|\bR[123]\b/);
  await shot(page, "d2b-operator-coming-next");
  await axeEverywhere(page, "operator");
});
