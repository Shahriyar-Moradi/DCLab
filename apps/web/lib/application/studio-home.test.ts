import assert from "node:assert/strict";
import test from "node:test";
import { actorLabel, activityHref, aiHealth, aiHealthUnavailable, championMetric, formatMicros, recentProjects, runsInProgress, spendSummary } from "./studio-home.ts";

const PROJECT = "11111111-1111-4111-8111-111111111111";
const RECORD = "22222222-2222-4222-8222-222222222222";
const base = { ai_enabled_setting: true, policy_unavailable: null, spend: null, open_incidents: [], switches: { platform_ai_blocking: null, workspace: [] } };

test("AI health says AI is off explicitly, for every way it can be off", () => {
  assert.equal(aiHealth({ ...base, ai_enabled_setting: false }).label, "AI is off");
  assert.equal(aiHealth({ ...base, policy_unavailable: "policy_missing" }).state, "off");
  assert.equal(aiHealth({ ...base, switches: { platform_ai_blocking: "global_ai", workspace: [] } }).state, "off");
  assert.equal(aiHealth(base).state, "on");
  assert.match(aiHealth({ ...base, open_incidents: [{}, {}] }).label, /2 open incidents/);
  assert.equal(aiHealth({ ...base, switches: { platform_ai_blocking: null, workspace: [{ state: "off" }] } }).state, "attention");
});

test("spend comes from the monthly workspace counter and never invents a budget", () => {
  assert.equal(spendSummary(base), null);
  const period = { currency: "USD", limit_micros: 50_000_000, period: "month", scope: "workspace", spent_micros: 12_500_000, hard_stop: true };
  const s = spendSummary({ ...base, spend: { currency: "USD", workspace: [{ ...period, period: "day", spent_micros: 1 }, period] } })!;
  assert.equal(s.value, "$12.50");
  assert.match(s.hint, /of \$50\.00 month \(hard stop\)/);
  assert.equal(s.fraction, 0.25);
  assert.match(spendSummary({ ...base, spend: { currency: "USD", workspace: [{ ...period, limit_micros: 0 }] } })!.hint, /no budget limit/);
  assert.equal(formatMicros(1_000_000, "NOT A CURRENCY"), "1.00 NOT A CURRENCY");
});

test("activity links need UUID ids and only point at decisions and runs", () => {
  assert.equal(activityHref({ kind: "decision", project_id: PROJECT, link: { kind: "decision_record", id: RECORD } }), `/projects/${PROJECT}/decisions?record=${RECORD}`);
  assert.equal(activityHref({ kind: "run_queued", project_id: PROJECT, link: { kind: "experiment", id: RECORD } }), `/projects/${PROJECT}/experiments/${RECORD}`);
  assert.equal(activityHref({ kind: "agent_run_started", project_id: PROJECT, link: { kind: "agent_run", id: RECORD } }), null);
  assert.equal(activityHref({ kind: "decision", project_id: "../x", link: { kind: "decision_record", id: RECORD } }), null);
  assert.equal(activityHref({ kind: "decision", project_id: PROJECT, link: { kind: "decision_record", id: "javascript:1" } }), null);
  assert.equal(activityHref({ kind: "decision", project_id: null, link: { kind: "decision_record", id: RECORD } }), null);
});

test("actor kinds map to rule / agent / person (you)", () => {
  assert.deepEqual(actorLabel({ kind: "rule" }), { text: "rule", tone: "det" });
  assert.deepEqual(actorLabel({ kind: "agent" }), { text: "agent", tone: "ai" });
  assert.equal(actorLabel({ kind: "person", is_you: true }).text, "you");
  assert.equal(actorLabel({ kind: "person" }).text, "person");
});

test("champion metric prefers the selection metric and is empty until evidence is locked", () => {
  const p = (champion: unknown) => ({ id: PROJECT, name: "x", updated_at: "2026-01-01", summary: { champion: champion && { metric_scope: "cv_aggregate", ...(champion as object) } } }) as Parameters<typeof championMetric>[0];
  assert.equal(championMetric(p(null)), null);
  assert.equal(championMetric(p({ version: "v1", cv_metrics: {} })), null);
  assert.deepEqual(championMetric(p({ version: "v1", selection_metric: "roc_auc", cv_metrics: { accuracy: 0.9, roc_auc: 0.8 } })), { name: "roc_auc", value: 0.8 });
  assert.equal(championMetric(p({ version: "v1", selection_metric: "missing", cv_metrics: { b: 2, a: 1 } }))!.name, "a");
});

test("runs in progress count the latest run of each project; recent sorts newest first", () => {
  const mk = (status: string | null, updated_at: string) => ({ id: PROJECT, name: "n", updated_at, summary: { latest_run: status ? { status } : null } });
  const list = [mk("running", "2026-01-02"), mk("completed", "2026-01-03"), mk("queued", "2026-01-01"), mk(null, "2026-01-04")];
  assert.equal(runsInProgress(list), 2);
  assert.deepEqual(recentProjects(list, 2).map((x) => x.updated_at), ["2026-01-04", "2026-01-03"]);
});

test("a failed governance read is 'unavailable', never 'AI is off'", () => {
  for (const error of [new Error("network"), { status: 500 }, { status: 502 }]) {
    const h = aiHealthUnavailable(error);
    assert.equal(h.state, "unavailable");
    assert.equal(h.detail, "AI health could not be loaded.");
    assert.doesNotMatch(`${h.label} ${h.detail}`, /AI is off/);
  }
  assert.match(aiHealthUnavailable({ status: 403 }).detail, /Your role cannot read governance/);
  assert.equal(aiHealth({ ...base, ai_enabled_setting: false }).state, "off");
  assert.equal(aiHealth(base).state, "on");
});

test("champion metric is shown only for a cross-validation aggregate", () => {
  const p = (metric_scope?: string) => ({ id: PROJECT, name: "x", updated_at: "2026", summary: { champion: { version: "v1", metric_scope, cv_metrics: { auc: 0.7 } } } }) as Parameters<typeof championMetric>[0];
  assert.deepEqual(championMetric(p("cv_aggregate")), { name: "auc", value: 0.7 });
  assert.equal(championMetric(p("holdout")), null);
  assert.equal(championMetric(p(undefined)), null);
});
