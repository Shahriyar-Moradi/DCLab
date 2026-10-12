import { expect, test } from "@playwright/test";
import { axeViolations, login, shot, trainedProject } from "./support/studio-fixture";

/** V7-B4: the guided Improve screen. A try is an ordinary new run that adds one model family; the verdict and the look come from the verdict read. */
const VIEWER_EMAIL = process.env.DCLAB_E2E_VIEWER_EMAIL ?? "viewer-a@verification.invalid";
const VIEWER_PASSWORD = process.env.DCLAB_E2E_STUDIO_PASSWORD ?? "VerificationOnly123!";
const BANNER = "Every try is scored on the final test set once. Do not pick a model by its final-test number.";
const LABEL: Record<string, string> = { beats_the_noise: "Beats the noise", within_the_noise: "Within the noise", worse: "Worse beyond the noise", not_comparable: "Can't compare" };
const BANNED = /holdout|hold-out|\bloop\b|\bengine\b|champion|proposer|digest|out-of-fold|operating point|\bL[12]\b|autopilot/i;

type Verdict = { verdict: string; folds?: number | null; mean_difference?: number | null; margin?: number | null; test_set_looks?: { looks_on_plan?: number | null; experiment_look?: number | null } | null };
type Run = { id: string; status: string; parent_experiment_id?: string | null };

test("Improve: goal, threshold card, start a try, verdict and look label, banner, viewer cannot try, axe, 375px", async ({ page, browser }) => {
  await login(page);
  const { projectId, experimentId } = await trainedProject(page, "Improve");

  // A project id that is not a UUID is not found; the sidebar lists Improve inside a project.
  const missing = await page.goto("/projects/not-a-uuid/improve");
  expect(missing?.status()).toBe(404);

  await page.goto(`/projects/${projectId}/improve`);
  await expect(page.getByRole("heading", { name: "Improve", level: 1 })).toBeVisible();
  await expect(page.getByRole("link", { name: "Improve", exact: true }).first()).toBeVisible();
  const main = page.locator("main");
  await expect(main.getByText(BANNER).first()).toBeVisible();
  await expect(main.getByText(/never starts a try by itself/).first()).toBeVisible();

  // Goal and the model in use, in natural units from the run read.
  const goal = main.locator("section", { has: page.getByRole("heading", { name: "Your goal and the model in use" }) });
  await expect(goal.getByText("Model v1")).toBeVisible();
  await expect(goal.getByText("churn", { exact: true })).toBeVisible();
  await expect(goal.getByText(/cross-validation/i).first()).toBeVisible();
  await expect(goal.getByText(/Look \d+ of \d+|looks? so far|Not counted|Look count could not be read/)).toBeVisible(); // the run in use has its own place among the looks

  // Step 1: the existing threshold card, with its protective note.
  const step1 = page.locator("#operating-points");
  await expect(step1.getByRole("heading", { name: "Step 1 · Set the threshold" })).toBeVisible();
  await expect(step1.getByRole("table", { name: /^Thresholds to choose from:/ })).toBeVisible();
  await expect(step1.getByText(/scoring keeps the model's locked threshold/)).toBeVisible();
  await expect(step1.getByText(/needs a new model version/).first()).toBeVisible();

  // Step 3 is empty before any try.
  const step3 = page.locator("#verdicts");
  await expect(step3.getByText(/No tries yet/)).toBeVisible();
  await shot(page, "improve-1-before");

  // Step 2: start a try. The app refuses a family the run already trained (with its own message); take the first it accepts.
  const step2 = page.locator("#try");
  await expect(step2.getByRole("heading", { name: "Step 2 · Try another model family" })).toBeVisible();
  await expect(step2.getByText(/same folds as the run in use/)).toBeVisible();
  await expect(step2.getByText(/Reading the (run in use|families)/)).toHaveCount(0); // the list settles once the run in use has been read
  const select = step2.getByLabel("Model family to add");
  const values = await select.locator("option").evaluateAll((os) => os.map((o) => (o as HTMLOptionElement).value).filter(Boolean));
  expect(values.length).toBeGreaterThan(0);
  const start = step2.getByRole("button", { name: /^Start this try/ });
  await expect(start).toBeDisabled(); // no family chosen yet
  let started = false;
  let refused = 0;
  for (const value of values) {
    await select.selectOption(value);
    await expect(step2.getByLabel(/^Why are you trying it/)).not.toHaveValue("");
    await start.click();
    const ok = step2.getByText(/A new run was started/);
    const no = step2.getByRole("alert");
    await expect(ok.or(no)).toBeVisible();
    if (await ok.isVisible()) { started = true; break; }
    refused += 1;
    await expect(no).not.toContainText(/holdout|Traceback|internal/i);
  }
  expect(started, `refused ${refused} of ${values.length} families`).toBe(true);
  await expect(start).toBeDisabled(); // form reset after a start

  // The try is a child of the run in use; wait for it to finish.
  const runs = async () => ((await (await page.request.get(`/api/backend/v1/experiments?project_id=${projectId}&limit=100`)).json()) as { items: Run[] }).items;
  let tryId = "";
  await expect.poll(async () => {
    const child = (await runs()).find((r) => r.parent_experiment_id === experimentId);
    tryId = child?.id ?? "";
    return child?.status ?? "";
  }, { timeout: 170_000, intervals: [2_000] }).toBe("completed");

  // The screen shows the verdict the API gives, the look label and the standing banner.
  const api = (await (await page.request.get(`/api/backend/v1/experiments/${tryId}/verdict`)).json()) as Verdict;
  expect(Object.keys(LABEL)).toContain(api.verdict);
  await page.goto(`/projects/${projectId}/improve`);
  const item = step3.getByRole("listitem").filter({ has: page.getByRole("heading", { level: 3 }) }).first();
  await expect(item.getByText(LABEL[api.verdict], { exact: true })).toBeVisible();
  const others = Object.values(LABEL).filter((l) => l !== LABEL[api.verdict]);
  for (const other of others) await expect(item.getByText(other, { exact: true })).toHaveCount(0);
  const looks = api.test_set_looks;
  if (looks?.experiment_look != null && looks.looks_on_plan != null) {
    await expect(item.getByText(`Look ${looks.experiment_look} of ${looks.looks_on_plan}`)).toBeVisible();
  } else {
    await expect(item.getByText(/looks? so far|Not counted/)).toBeVisible();
  }
  if (api.verdict !== "not_comparable") {
    await expect(item.getByText("Noise margin", { exact: true })).toBeVisible();
    await expect(item.getByText(/(higher|lower) \((better|worse)\)|no difference/).first()).toBeVisible();
    await expect(item.getByText(new RegExp(`Folds compared \\(K\\)`))).toBeVisible();
    await expect(item.getByText("Models this try added", { exact: true })).toBeVisible();
    await expect(item.getByText("Comparisons the margin covers", { exact: true })).toBeVisible();
    await expect(item.getByText(/Each try is judged at 5%|up to \d+ × 5%/)).toHaveCount(0); // the multiplicity caveat is on the step, not repeated per try
    if (api.folds != null) await expect(item.getByText(String(api.folds), { exact: true }).first()).toBeVisible();
  }
  await expect(main.getByText(BANNER).first()).toBeVisible();
  await expect(step3.getByText(/Most tries end within the noise/)).toBeVisible();
  await expect(main.getByText(/Nothing is recorded when you keep it/)).toBeVisible();
  // The try links to its run, which is where a model is put in use; no new write path is on this page.
  await expect(item.getByRole("link").first()).toHaveAttribute("href", new RegExp(`/projects/${projectId}/experiments/${tryId}$`));
  await expect(main.getByRole("button", { name: /put .* in use|make .* champion|accept/i })).toHaveCount(0);
  // Honest copy: nothing banned, no raw codes, no stored-sign scores.
  const text = await main.innerText();
  expect(text).not.toMatch(BANNED);
  expect(text).not.toMatch(/undefined|NaN|\[object|honest estimate|you press|human-only|really helped|is the gain real/i);
  // No raw code or metric key reaches the page: nothing that looks like snake_case (verdicts, reasons, scores such as roc_auc).
  expect(text.match(/\b[a-z]+_[a-z_]+\b/g) ?? []).toEqual([]);
  // Scores are in natural units: no leading minus on a cross-validation score of a score where lower is not a loss of sign (PR-AUC here).
  for (const dd of await item.locator("dt:has-text('Cross-validation score') + dd").allInnerTexts()) expect(dd).not.toMatch(/^\s*[-\u2212]/);
  for (const dd of await goal.locator("dt:has-text('score ±') + dd").allInnerTexts()) expect(dd).not.toMatch(/^\s*[-\u2212]/);
  // The fold table keeps signs: a difference is printed with + or a minus, or is 0.
  if (api.verdict !== "not_comparable") {
    await item.getByText("Fold by fold", { exact: true }).click();
    const diffs = await item.getByRole("table").locator("tbody tr td:nth-child(4)").allInnerTexts();
    expect(diffs.length).toBeGreaterThan(0);
    for (const d of diffs) expect(d.trim()).toMatch(/^([+\u2212]\d|0$|about 0)/);
  }
  await shot(page, "improve-2-after");

  // axe, light and dark; 375px has no horizontal scroll.
  expect(await axeViolations(page)).toEqual([]);
  await page.emulateMedia({ colorScheme: "dark" });
  expect(await axeViolations(page)).toEqual([]);
  await shot(page, "improve-3-dark");
  await page.emulateMedia({ colorScheme: "light" });
  await page.setViewportSize({ width: 375, height: 812 });
  await page.goto(`/projects/${projectId}/improve`);
  await expect(step3.getByText(LABEL[api.verdict], { exact: true }).first()).toBeVisible();
  const overflow = await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);
  expect(overflow).toBeLessThanOrEqual(0);
  await shot(page, "improve-4-mobile");
  await page.setViewportSize({ width: 1280, height: 720 });

  // A viewer reads the page but gets the explanation instead of the form; the API refuses a try too.
  const context = await browser.newContext();
  const viewer = await context.newPage();
  await viewer.goto("/login");
  await viewer.getByLabel("Email").fill(VIEWER_EMAIL);
  await viewer.getByLabel("Password").fill(VIEWER_PASSWORD);
  await viewer.getByRole("button", { name: "Sign in" }).click();
  await expect(viewer).not.toHaveURL(/\/login/);
  await viewer.goto(`/projects/${projectId}/improve`);
  const vstep2 = viewer.locator("#try");
  await expect(vstep2.getByText(/Your role can read this page but not start a try/)).toBeVisible();
  await expect(vstep2.getByRole("button", { name: /^Start this try/ })).toHaveCount(0);
  await expect(vstep2.getByLabel("Model family to add")).toHaveCount(0);
  await expect(viewer.locator("#verdicts").getByRole("listitem").filter({ has: viewer.getByRole("heading", { level: 3 }) }).first().getByText(LABEL[api.verdict], { exact: true })).toBeVisible();
  const csrf = (await context.cookies()).find((c) => c.name === "dclab_csrf")?.value ?? "";
  const denied = await viewer.request.post(`/api/backend/v1/experiments/${experimentId}/branches`, {
    headers: { "Idempotency-Key": `e2e-viewer-try-${Date.now()}`, "X-CSRF-Token": csrf, Origin: new URL(viewer.url()).origin },
    data: { intent: "viewer attempt", changes: [{ kind: "family_include", family: values[0] }] },
  });
  expect(denied.status()).toBe(403);
  await shot(viewer, "improve-5-viewer");
  await context.close();
});

test("Improve: a project with no model in use says so and offers no try", async ({ page }) => {
  await login(page);
  await page.goto("/projects/new");
  const name = `Improve empty ${Date.now()}`;
  await page.getByLabel("Project name").fill(name);
  await page.getByLabel("Data file").setInputFiles({ name: "tiny.csv", mimeType: "text/csv", buffer: Buffer.from("a,b\n1,2\n3,4\n") });
  await page.getByRole("button", { name: "Upload and continue" }).click();
  await expect(page.getByRole("table", { name: "Columns of the uploaded file" })).toBeVisible();
  const projects = (await page.request.get("/api/backend/v1/projects").then((r) => r.json())) as Array<{ id: string; name: string }>;
  const projectId = projects.find((p) => p.name === name)!.id;
  await page.goto(`/projects/${projectId}/improve`);
  await expect(page.getByText(/No model is in use yet/)).toBeVisible();
  await expect(page.getByRole("button", { name: /^Start this try/ })).toHaveCount(0);
  await expect(page.locator("main").getByText(BANNER).first()).toBeVisible();
  expect(await axeViolations(page)).toEqual([]);
});
