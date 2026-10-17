import { expect, test, type Page } from "@playwright/test";
import { axeViolations, login, shot, trainedProject } from "./support/studio-fixture";

/**
 * D4e = P5.5-A: the improvement loop. Start a loop with a small budget from the model in use, watch sealed tries, reach "waiting for
 * you" or a stop reason, then accept (one final run, one final-test number) or keep the current model; a viewer sees no Accept.
 * A default run often has nothing left to try (ADR 0007 section 12): the start is then refused with a fixed sentence and the spec
 * checks that answer instead of a loop.
 */
const VIEWER_EMAIL = process.env.DCLAB_E2E_VIEWER_EMAIL ?? "viewer-a@verification.invalid";
const VIEWER_PASSWORD = process.env.DCLAB_E2E_STUDIO_PASSWORD ?? "VerificationOnly123!";
const SETTLED = ["waiting_user", "completed", "failed", "cancelled"];
const UUID = /[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}/;

type Loop = {
  id: string; status: string; outcome?: string | null; final_experiment_id?: string | null; sealed_set_consulted?: boolean; stop_detail?: string | null;
  goal: { plain?: { metric: string }; objective?: { maximize: string } };
  iterations: Array<{ seq: number; status: string }>; actions: { accept: boolean; keep_current: boolean; cancel: boolean };
};

/** The page's number format (three significant digits, a minus only when negative). */
function fmtScore(value: number): string {
  if (value === 0) return "0";
  const abs = Math.abs(value);
  if (abs < 0.00001) return "about 0";
  const text = Number(abs.toPrecision(3)).toString();
  const body = /e/i.test(text) ? abs.toFixed(6).replace(/0+$/, "") : text;
  return value < 0 ? `\u2212${body}` : body;
}

async function readLoop(page: Page, id: string): Promise<Loop> {
  return (await page.request.get(`/api/backend/v1/improve-runs/${id}`).then((r) => r.json())) as Loop;
}

async function honestText(page: Page) {
  const text = await page.locator("main").innerText();
  expect(text).not.toMatch(/undefined|NaN|\[object|holdout|proposer|digest/i);
  // No raw code reaches the page: nothing that looks like snake_case (statuses, reasons, stop rules, metric keys).
  expect(text.match(/\b[a-z]+_[a-z_]+\b/g) ?? []).toEqual([]);
  // A try is never called an improvement unless it beat the noise.
  expect(text).not.toMatch(/\bimproved the model\b/i);
}

test("Improvement loop: start with a small budget, sealed tries, a stop reason or your decision, accept or keep, viewer sees no Accept", async ({ page, browser }) => {
  test.setTimeout(15 * 60_000);
  await login(page);
  const { projectId } = await trainedProject(page, "Improve loop");

  // A loop link that is not a UUID is not found.
  expect((await page.goto(`/projects/${projectId}/improve/not-a-uuid`))?.status()).toBe(404);

  await page.goto(`/projects/${projectId}/improve`);
  await expect(page.getByRole("heading", { name: "Improve", level: 1 })).toBeVisible();
  await expect(page.getByRole("tab", { name: "Improvement loop" })).toHaveAttribute("aria-selected", "true");
  await expect(page.getByRole("tab", { name: "Try one change yourself" })).toBeVisible();
  const form = page.getByRole("form", { name: "Start an improvement loop" });
  await expect(form.getByText(/^Best .+ on cross-validation/)).toBeVisible(); // the goal, read-only
  await expect(form.getByText("AI off — $0")).toBeVisible();
  await expect(form.getByText(/A loop on a fresh split is not available yet/)).toBeVisible();
  await expect(form.getByText(/never looks at the final test set/)).toBeVisible();
  await expect(form.getByRole("checkbox")).toHaveCount(0); // no fresh-split switch
  await expect(page.getByText(/No loops yet/)).toBeVisible();

  // The budget is checked against the server's ceilings before anything is sent.
  const tries = form.getByLabel("Most tries");
  await tries.fill("21");
  await expect(form.getByText("Enter the most tries from 1 to 20.")).toBeVisible();
  await expect(form.getByRole("button", { name: "Start the loop" })).toBeDisabled();
  await tries.fill("2");
  await form.getByLabel("Most time, in minutes").fill("30");
  await form.getByLabel(/patience/).fill("3");
  await expect(form.getByText("The patience cannot be more than the most tries.")).toBeVisible();
  await form.getByLabel(/patience/).fill("2");
  await shot(page, "improve-loop-1-form");
  await form.getByRole("button", { name: "Start the loop" }).click();

  // Either the loop view opens, or the start is refused with a fixed sentence (most often: nothing left to try).
  const opened = await Promise.race([
    page.waitForURL(new RegExp(`/projects/${projectId}/improve/${UUID.source}$`), { timeout: 30_000 }).then(() => true),
    form.locator(".banner.crit").first().waitFor({ timeout: 30_000 }).then(() => false),
  ]);
  if (!opened) {
    await expect(form.getByText(/Nothing to try|cannot start a loop|Too many|already running|has been used enough|trust check blocks|needs a rule/).first()).toBeVisible();
    await honestText(page);
    test.info().annotations.push({ type: "note", description: "The start was refused (a fixed sentence); no loop to follow on this seeded run." });
    return;
  }
  const loopId = page.url().split("/improve/")[1];
  await expect(page.getByRole("heading", { name: "Improvement loop", level: 1 })).toBeVisible();
  await expect(page.getByRole("navigation", { name: "Loop progress" })).toBeVisible();
  await expect(page.getByText("AI off — $0")).toBeVisible();
  await expect(page.getByText(/of 2 tries/)).toBeVisible();

  // The page polls; wait for the loop to settle (a stop reason or waiting for you).
  await expect.poll(async () => (await readLoop(page, loopId)).status, { timeout: 12 * 60_000, intervals: [5_000] }).toMatch(new RegExp(`^(${SETTLED.join("|")})$`));
  await page.reload();
  const loop = await readLoop(page, loopId);
  await expect(page.getByRole("heading", { name: "Improvement loop", level: 1 })).toBeVisible(); // the reloaded page has read the loop
  const items = page.getByRole("list", { name: "Tries" }).getByRole("listitem");
  await expect(items).toHaveCount(loop.iterations.length);
  for (let i = 0; i < loop.iterations.length; i += 1) await expect(items.nth(i).getByText("Final test not used", { exact: true })).toBeVisible();
  await expect(page.getByText(/Most tries end within the noise/).first()).toBeVisible();
  await honestText(page);
  await shot(page, "improve-loop-2-settled");
  for (const width of [1280, 375]) {
    await page.setViewportSize({ width, height: 900 });
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= document.documentElement.clientWidth), `${width}px`).toBe(true);
    for (const scheme of ["light", "dark"] as const) {
      await page.emulateMedia({ colorScheme: scheme });
      expect(await axeViolations(page), `${width}px ${scheme}`).toEqual([]);
    }
    await page.emulateMedia({ colorScheme: "light" });
  }
  await page.setViewportSize({ width: 1280, height: 720 });
  // The start page with this loop in its list, at 1280 and 375 px, light and dark.
  await page.goto(`/projects/${projectId}/improve`);
  await expect(page.getByRole("heading", { name: "Loops of this project" })).toBeVisible();
  for (const width of [1280, 375]) {
    await page.setViewportSize({ width, height: 900 });
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= document.documentElement.clientWidth), `start ${width}px`).toBe(true);
    for (const scheme of ["light", "dark"] as const) {
      await page.emulateMedia({ colorScheme: scheme });
      expect(await axeViolations(page), `start ${width}px ${scheme}`).toEqual([]);
    }
    await page.emulateMedia({ colorScheme: "light" });
  }
  await page.setViewportSize({ width: 1280, height: 720 });
  await page.goto(`/projects/${projectId}/improve/${loopId}`);
  await expect(page.getByRole("heading", { name: "Improvement loop", level: 1 })).toBeVisible();

  // A viewer reads the loop but gets no Accept, Keep or Stop; the API refuses an accept too.
  const context = await browser.newContext();
  const viewer = await context.newPage();
  await viewer.goto("/login");
  await viewer.getByLabel("Email").fill(VIEWER_EMAIL);
  await viewer.getByLabel("Password").fill(VIEWER_PASSWORD);
  await viewer.getByRole("button", { name: "Sign in" }).click();
  await expect(viewer).not.toHaveURL(/\/login/);
  await viewer.goto(`/projects/${projectId}/improve/${loopId}`);
  await expect(viewer.getByRole("heading", { name: "Improvement loop", level: 1 })).toBeVisible();
  await expect(viewer.getByRole("button", { name: /Accept this try|Keep the current model|Stop the loop/ })).toHaveCount(0);
  if (loop.status === "waiting_user") await expect(viewer.getByText(/Your role can read this loop only/)).toBeVisible();
  await viewer.goto(`/projects/${projectId}/improve`);
  await expect(viewer.getByText(/Your role can read this page but not start a loop/)).toBeVisible();
  await expect(viewer.getByRole("button", { name: "Start the loop" })).toHaveCount(0);
  await shot(viewer, "improve-loop-3-viewer");
  await context.close();

  if (loop.status !== "waiting_user") {
    // A stop reason in plain words, and nothing to accept.
    // Visible text only: the glossary's hidden "trust checks" term must not satisfy this.
    await expect(page.getByText(/^Stopped: |No change beyond noise; current model kept\.|No try could be compared fairly|already meets the goal|trust check/).filter({ visible: true }).first()).toBeVisible();
    await expect(page.getByRole("button", { name: /Accept this try/ })).toHaveCount(0);
    return;
  }

  // Waiting for you: accept the try that beat the noise (one final run, one final-test number) or keep the current model.
  const decision = page.getByRole("form", { name: "Accept a try or keep the current model" });
  await expect(decision.getByText(/one final run, one final-test number/)).toBeVisible();
  if (loop.actions.accept) {
    await decision.getByRole("radio").first().check();
    await decision.getByRole("button", { name: /Accept this try/ }).click();
    await expect(decision.getByText("Say why you accept this try (it is saved with the decision).")).toBeVisible(); // a reason is required
    await decision.getByLabel(/Your reason/).fill("Beat the noise on every fold; worth one final run.");
    await decision.getByRole("button", { name: /Accept this try/ }).click();
    await expect(page.getByText("Final run in progress").first()).toBeVisible({ timeout: 60_000 });
    await expect.poll(async () => (await readLoop(page, loopId)).status, { timeout: 8 * 60_000, intervals: [5_000] }).toMatch(/^(completed|failed|waiting_user)$/);
    const after = await readLoop(page, loopId);
    if (after.status === "completed" && after.outcome === "improved" && after.sealed_set_consulted && !/after_look$/.test(after.stop_detail ?? "")) {
      // No reload: the page polls and shows the one number as soon as the loop settles.
      const metric = after.goal.plain?.metric ?? after.goal.objective?.maximize ?? "";
      const run = (await page.request.get(`/api/backend/v1/experiments/${after.final_experiment_id}`).then((r) => r.json())) as { metrics?: { holdout?: Record<string, number> } };
      const expected = run.metrics?.holdout?.[metric];
      expect(typeof expected).toBe("number");
      const final = page.locator("section", { has: page.getByRole("heading", { name: /^Final test \(scored once/ }) });
      await expect(final.getByText(/^Final test, .+:$/)).toHaveCount(1, { timeout: 30_000 }); // exactly one final-test number, labelled as such
      await expect(final.locator(".loop-final b")).toHaveText(fmtScore(expected as number));
      await expect(final.getByText(/never pick a model by it/)).toBeVisible();
    }
    await page.reload();
  } else {
    await decision.getByRole("button", { name: "Keep the current model" }).click();
    await expect.poll(async () => (await readLoop(page, loopId)).status).toBe("completed");
    await page.reload();
    await expect(page.getByText(/chose to keep the current model/)).toBeVisible();
  }
  await honestText(page);
  await shot(page, "improve-loop-4-decided");
});
