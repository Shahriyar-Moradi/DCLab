import { expect, test } from "@playwright/test";
import { axeViolations, login, shot, trainedProject } from "./support/studio-fixture";

/** P5.2-UI: the Threshold card on the experiment page; choosing a point records a decision; the model card shows it. */
const VIEWER_EMAIL = process.env.DCLAB_E2E_VIEWER_EMAIL ?? "viewer-a@verification.invalid";
const VIEWER_PASSWORD = process.env.DCLAB_E2E_STUDIO_PASSWORD ?? "VerificationOnly123!";

type Points = {
  status: string;
  locked: { threshold: number } | null;
  chosen: { threshold: number; rationale: string; decision_id: string; method: string } | null;
  pareto: Array<{ threshold: number }>;
  points: Array<{ threshold: number }>;
};

test("Operating points: chart + table, choose a point with a reason, model card shows it, viewer cannot choose", async ({ page, browser }) => {
  await login(page);
  const { projectId, experimentId } = await trainedProject(page, "Operating points");
  const read = async () => (await (await page.request.get(`/api/backend/v1/experiments/${experimentId}/operating-points`)).json()) as Points;
  const api = await read();
  expect(api.status).toBe("available");
  expect(api.chosen).toBeNull();

  await page.goto(`/projects/${projectId}/experiments/${experimentId}`);
  const card = page.locator("#operating-points");
  await expect(card.getByRole("heading", { name: "Threshold: how many rows get flagged" })).toBeVisible();
  await expect(card.getByText("measured on the training folds").first()).toBeVisible();
  await expect(card.getByRole("img", { name: /Precision and recall by threshold across \d+ candidate thresholds measured on the training folds/ })).toBeVisible();
  const table = card.getByRole("table", { name: /^Thresholds to choose from:/ });
  await expect(table).toBeVisible();
  await expect(table.getByRole("row")).toHaveCount(new Set([...api.pareto.map((p) => p.threshold), ...(api.locked ? [api.locked.threshold] : [])]).size + 1);
  await expect(card.getByText(/Scoring still uses the model's locked threshold/)).toBeVisible();
  await expect(card.getByText("Locked point.", { exact: true })).toBeVisible();
  await shot(page, "operating-1-before");

  // Choose a Pareto point with a reason (untrusted text stays text).
  const target = api.pareto[0].threshold;
  const reason = "Prefer catching churners <b>first</b>";
  await table.getByRole("button", { name: new RegExp(`threshold ${String(Number(target.toPrecision(4))).replace(".", "\\.")}$`) }).click();
  await expect(card.getByText("Selected point.").or(card.getByText("Locked point.", { exact: true })).first()).toBeVisible();
  const submit = card.getByRole("button", { name: "Record this threshold choice" });
  await expect(submit).toBeDisabled(); // reason required
  await card.getByLabel("Why (recorded with the decision)").fill(reason);
  await submit.click();
  await expect(card.getByText(/Decision recorded: threshold .* is now the chosen threshold\. Scoring still uses the locked threshold/)).toBeVisible();
  const after = await read();
  expect(after.chosen?.threshold).toBe(target);
  expect(after.chosen?.rationale).toBe(reason);
  await expect(card.getByText(/Chosen point: threshold/)).toContainText("Prefer catching churners <b>first</b>");
  expect(await card.locator("b", { hasText: "first" }).count()).toBe(0);
  await expect(card.getByText("Chosen point.", { exact: true })).toBeVisible();
  await shot(page, "operating-2-chosen");

  // An objective solved on the stored curve supersedes the choice.
  await card.getByLabel("A goal, solved on the stored curve").check();
  await card.getByLabel("Why (recorded with the decision)").fill("Balance precision and recall");
  await card.getByRole("button", { name: "Record this threshold choice" }).click();
  await expect.poll(async () => (await read()).chosen?.method).toBe("objective");

  // Model card shows the chosen point, the reason and that scoring is unchanged.
  const refs = (await (await page.request.get(`/api/backend/v1/projects/${projectId}/refs`)).json()) as { items: Array<{ ref_kind: string; target: { id: string } }> };
  const modelId = refs.items.find((r) => r.ref_kind === "champion_model")!.target.id;
  await page.goto(`/projects/${projectId}/models/${modelId}?tab=card`);
  const mc = page.locator(".model-card-print");
  await expect(mc.getByRole("heading", { name: "Chosen threshold" })).toBeVisible();
  await expect(mc.getByText("Balance precision and recall")).toBeVisible();
  await expect(mc.getByText(/still uses the locked threshold/)).toBeVisible();
  await shot(page, "operating-3-model-card");

  // The same threshold card is on the model page, in plain words; it keeps the protective note.
  await page.goto(`/projects/${projectId}/models/${modelId}?tab=threshold`);
  const mt = page.locator("#operating-points");
  await expect(mt.getByRole("heading", { name: "Threshold: how many rows get flagged" })).toBeVisible();
  await expect(mt.getByText(/Chosen point: threshold/)).toBeVisible();
  await expect(mt.getByText(/Scoring still uses the model's locked threshold/)).toBeVisible();
  await expect(page.getByRole("region", { name: "About this screen" })).toContainText("applying your choice needs a new model version");
  await expect(page.getByRole("tabpanel", { name: "Threshold" })).not.toContainText(/out-of-fold|Pareto|holdout|operating point/i);
  expect(await axeViolations(page)).toEqual([]);
  await page.emulateMedia({ colorScheme: "dark" });
  expect(await axeViolations(page)).toEqual([]);
  await page.emulateMedia({ colorScheme: "light" });
  await shot(page, "operating-3b-model-threshold");
  expect(await axeViolations(page)).toEqual([]);

  // axe on the experiment page, light and dark.
  await page.goto(`/projects/${projectId}/experiments/${experimentId}`);
  await expect(page.locator("#operating-points").getByRole("table", { name: /^Thresholds to choose from:/ })).toBeVisible();
  expect(await axeViolations(page)).toEqual([]);
  await page.emulateMedia({ colorScheme: "dark" });
  expect(await axeViolations(page)).toEqual([]);
  await shot(page, "operating-4-dark");
  await page.emulateMedia({ colorScheme: "light" });

  // A viewer sees the curve but cannot choose; the API refuses too.
  const context = await browser.newContext();
  const viewer = await context.newPage();
  await viewer.goto("/login");
  await viewer.getByLabel("Email").fill(VIEWER_EMAIL);
  await viewer.getByLabel("Password").fill(VIEWER_PASSWORD);
  await viewer.getByRole("button", { name: "Sign in" }).click();
  await expect(viewer).not.toHaveURL(/\/login/);
  await viewer.goto(`/projects/${projectId}/experiments/${experimentId}`);
  const vcard = viewer.locator("#operating-points");
  await expect(vcard.getByRole("table", { name: /^Thresholds to choose from:/ })).toBeVisible();
  await expect(vcard.getByText(/Your role can read thresholds but not choose one/)).toBeVisible();
  await expect(vcard.getByRole("button", { name: "Record this threshold choice" })).toHaveCount(0);
  const csrf = (await context.cookies()).find((c) => c.name === "dclab_csrf")?.value ?? "";
  const denied = await viewer.request.post(`/api/backend/v1/experiments/${experimentId}/operating-point`, {
    headers: { "Idempotency-Key": `e2e-viewer-${Date.now()}`, "X-CSRF-Token": csrf, Origin: new URL(viewer.url()).origin },
    data: { threshold: target, reason: "viewer attempt" },
  });
  expect(denied.status()).toBe(403);
  expect(((await denied.json()) as { error: { code: string } }).error.code).toBe("forbidden");
  // The model page Threshold tab gates the same way.
  await viewer.goto(`/projects/${projectId}/models/${modelId}?tab=threshold`);
  const vmodel = viewer.locator("#operating-points");
  await expect(vmodel.getByRole("table", { name: /^Thresholds to choose from:/ })).toBeVisible();
  await expect(vmodel.getByText(/Your role can read thresholds but not choose one/)).toBeVisible();
  await expect(vmodel.getByRole("button", { name: "Record this threshold choice" })).toHaveCount(0);
  await shot(viewer, "operating-5-viewer");
  await context.close();
});
