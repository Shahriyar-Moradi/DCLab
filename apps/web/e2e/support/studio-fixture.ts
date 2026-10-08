import { expect, type Page } from "@playwright/test";
import path from "node:path";

/** Shared steps for the Studio Home and Inbox specs (P4.15-UI / P4.16-UI): sign in, train one small run, propose a decision. */
export const EMAIL = process.env.DCLAB_E2E_STUDIO_EMAIL ?? "business-admin-a@verification.invalid";
export const PASSWORD = process.env.DCLAB_E2E_STUDIO_PASSWORD ?? "VerificationOnly123!";
const AXE = path.resolve(__dirname, "../../node_modules/axe-core/axe.min.js");

export function churnCsv(): string {
  const rows = ["tenure,monthly_spend,customer_code,plan,churn"];
  for (let i = 0; i < 300; i += 1) {
    const tenure = 1 + ((i * 13) % 72);
    const churn = tenure < 12 && i % 5 !== 0 ? "yes" : "no";
    rows.push(`${tenure},${i % 20 === 0 ? "" : (10 + ((i * 17) % 110)).toFixed(2)},C${1000 + i},${["basic", "plus", "pro"][i % 3]},${churn}`);
  }
  return `${rows.join("\n")}\n`;
}

export async function login(page: Page): Promise<void> {
  await page.goto("/login");
  await page.getByLabel("Email").fill(EMAIL);
  await page.getByLabel("Password").fill(PASSWORD);
  await page.getByRole("button", { name: "Sign in" }).click();
  await expect(page).not.toHaveURL(/\/login/);
}

export async function axeViolations(page: Page): Promise<string[]> {
  await page.addScriptTag({ path: AXE });
  return page.evaluate(async () => {
    const axe = (window as unknown as { axe: { run: (ctx: Document, opts: object) => Promise<{ violations: Array<{ id: string; impact?: string; nodes: unknown[] }> }> } }).axe;
    const result = await axe.run(document, { runOnly: { type: "tag", values: ["wcag2a", "wcag2aa"] } });
    return result.violations.filter((v) => v.impact === "serious" || v.impact === "critical").map((v) => `${v.id} (${v.nodes.length})`);
  });
}

export async function shot(page: Page, name: string): Promise<void> {
  const dir = process.env.DCLAB_E2E_SHOTS;
  if (dir) await page.screenshot({ path: path.join(dir, `${name}.png`), fullPage: true });
}

/** Upload churn data, train one run to completion; returns the project and run ids. */
export async function trainedProject(page: Page, label: string): Promise<{ projectId: string; experimentId: string; name: string }> {
  await page.goto("/projects/new");
  const name = `${label} ${Date.now()}`;
  await page.getByLabel("Project name").fill(name);
  await page.getByLabel("Data file").setInputFiles({ name: "churn.csv", mimeType: "text/csv", buffer: Buffer.from(churnCsv()) });
  await page.getByRole("button", { name: "Upload and continue" }).click();
  await expect(page.getByRole("table", { name: "Columns of the uploaded file" })).toContainText("customer_code");
  const projects = (await page.request.get("/api/backend/v1/projects").then((r) => r.json())) as Array<{ id: string; name: string }>;
  const projectId = projects.find((p) => p.name === name)?.id;
  expect(projectId).toBeTruthy();
  await page.goto(`/projects/${projectId}/experiments/new`);
  await expect(page.getByLabel("Dataset in this project")).not.toHaveValue("");
  await page.getByRole("button", { name: "Continue" }).click();
  await page.getByLabel("Task").selectOption("binary");
  await page.getByLabel("Target column").selectOption("churn");
  await page.getByRole("button", { name: "Continue" }).click();
  await page.getByRole("button", { name: "Continue" }).click();
  await page.getByRole("button", { name: "Train" }).click();
  await expect(page).toHaveURL(/\/experiments\/[0-9a-f-]{36}$/);
  const experimentId = page.url().split("/experiments/")[1];
  await expect.poll(async () => {
    const run = await page.request.get(`/api/backend/v1/experiments/${experimentId}`);
    return ((await run.json()) as { status: string }).status;
  }, { timeout: 170_000, intervals: [2_000] }).toBe("completed");
  return { projectId: projectId!, experimentId, name };
}

/** A person's proposed decision (the only decision a person can propose is experiment accepted / rejected). */
export async function proposeDecision(page: Page, projectId: string, experimentId: string, reason: string): Promise<string> {
  const csrf = (await page.context().cookies()).find((c) => c.name === "dclab_csrf")?.value ?? "";
  const r = await page.request.post(`/api/backend/v1/projects/${projectId}/decisions`, {
    headers: { "Idempotency-Key": `e2e-${reason.replace(/[^A-Za-z0-9]/g, "-")}-${Date.now()}`, "X-CSRF-Token": csrf, Origin: new URL(page.url()).origin },
    data: { action: "propose", decision_type: "experiment_accepted", subject: { kind: "experiment", id: experimentId }, rationale: reason, evidence_refs: [{ kind: "experiment", id: experimentId }] },
  });
  expect(r.status(), await r.text()).toBe(201);
  return ((await r.json()) as { id: string }).id;
}
