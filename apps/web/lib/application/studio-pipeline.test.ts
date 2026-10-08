import assert from "node:assert/strict";
import test from "node:test";
import {
  answerSummary, canDownloadArtifact, canReplay, checkTotals, checksByStage, costLabel, decisionHref, decisionPointRows, durationLabel, orderStages, parseRecordParam,
  pointsForStage, provenanceRows, replayErrorText, replayView, runDurationMs, runsForExperiment, stageCounts, stageDigests, stageRecords, stageResult, stageTone, switcherRuns,
  type RecordLike, type StageLike,
} from "./studio-pipeline.ts";

const P = "11111111-1111-4111-8111-111111111111";
const E = "22222222-2222-4222-8222-222222222222";
const R = "33333333-3333-4333-8333-333333333333";
const D1 = "a".repeat(64);
const stage = (key: string, sequence: number, extra: Partial<StageLike> = {}): StageLike => ({ key, sequence, title: key, status: "completed", configuration: {}, ...extra });

test("stages keep the order the API returns (sequence, then key); missing stages stay missing", () => {
  const ordered = orderStages([stage("b", 2), stage("a", 1), stage("c", 2)]);
  assert.deepEqual(ordered.map((s) => s.key), ["a", "b", "c"]);
  assert.deepEqual(orderStages([]), []);
  assert.deepEqual(stageCounts([stage("a", 1), stage("b", 2, { status: "failed" }), stage("c", 3, { status: "running" }), stage("d", 4, { status: "pending" })]), { total: 4, completed: 1, failed: 1, running: 1 });
  assert.equal(stageTone("pending"), "gray");
  assert.equal(stageTone("Completed"), "ok");
});

test("durations", () => {
  assert.equal(durationLabel(null), "—");
  assert.equal(durationLabel(420), "420 ms");
  assert.equal(durationLabel(4200), "4.2 s");
  assert.equal(durationLabel(252000), "4 min 12 s");
  assert.equal(runDurationMs("2026-10-01T10:00:00Z", "2026-10-01T10:04:12Z"), 252000);
  assert.equal(runDurationMs("2026-10-01T10:00:00Z", null), null);
  assert.equal(runDurationMs("2026-10-01T10:04:00Z", "2026-10-01T10:00:00Z"), null);
});

test("digests: generated code, nested configuration digests, bounded and format checked", () => {
  const s = stage("artifact_reproducibility_persistence", 20, {
    generated_code: { digest: D1, spec_digest: "b".repeat(64) },
    configuration: {
      artifacts: [{ id: P, artifact_type: "model", content_digest: "c".repeat(64), size_bytes: 10 }, { content_digest: "not a digest <script>" }],
      code_snapshots: [{ code_digest: "d".repeat(64), dependency_lock_digest: "e".repeat(64) }],
      runtime_environments: [{ environment_digest: "f".repeat(64) }],
      winner: { selection_digest: "9".repeat(40) },
    },
  });
  const digests = stageDigests(s);
  assert.deepEqual(digests.map((d) => d.value), [D1, "b".repeat(64), "c".repeat(64), "d".repeat(64), "e".repeat(64), "f".repeat(64), "9".repeat(40)]);
  assert.ok(digests.every((d) => /^[A-Za-z0-9:._-]+$/.test(d.value)));
  assert.equal(digests[2].label, "artifacts 1 · content digest");
  const many = stage("x", 1, { configuration: { items: Array.from({ length: 30 }, (_, i) => ({ a_digest: String(i).padStart(10, "0") })) } });
  assert.ok(stageDigests(many).length <= 12);
  assert.deepEqual(stageDigests(stage("none", 1)), []);
});

test("stage result: scalars and lists as text, objects as counts, ids and digests skipped, final holdout locked", () => {
  const r = stageResult(stage("validation_plan", 8, { configuration: { strategy: "stratified_kfold", folds: 5, metrics: ["auc", "f1"], by_fold: [{ a: 1 }, { a: 2 }], nested: { a: 1, b: 2 }, plan_id: P, spec_digest: D1, empty: null } }));
  assert.deepEqual(r.rows.map((x) => [x.key, x.value]), [["strategy", "stratified_kfold"], ["folds", "5"], ["metrics", "auc, f1"], ["by_fold", "2 recorded (the experiment page lists them)"], ["nested", "2 fields recorded"]]);
  assert.equal(r.locked, false);
  const held = stageResult(stage("final_holdout", 19, { configuration: { evaluations: [{ metrics: { auc: 0.91 } }], auc: 0.91 } }));
  assert.deepEqual(held, { rows: [], more: 0, locked: true });
  const hidden = stageResult(stage("other", 1, { configuration: { holdout_auc: 0.9, test_accuracy: 0.8, y_true: [1], score_delta: 1, test_size: 0.2, fine: 1 } }));
  assert.deepEqual(hidden.rows.map((x) => x.key), ["test_size", "fine"]);
  const big = stageResult(stage("big", 1, { configuration: Object.fromEntries(Array.from({ length: 14 }, (_, i) => [`k${i}`, i])) }));
  assert.equal(big.rows.length, 10);
  assert.equal(big.more, 4);
});

test("checks: verification attempts on their stage, trust checks mapped to stages, the rest listed loose", () => {
  const stages = [
    stage("leakage_audit", 10),
    stage("deterministic_verification", 21, { configuration: { attempts: [{ id: R, audit_mode: "deterministic", deterministic_status: "VERIFIED" }, { id: P, audit_mode: "deep", deterministic_status: "VERIFIED_WITH_WARNINGS" }, { id: R, audit_mode: "x", deterministic_status: "NOT_VERIFIABLE" }, { id: R, audit_mode: "y", deterministic_status: "FAILED" }] } }),
  ];
  const findings = { investigated: true, checks: [
    { check: "target_leakage", status: "fail", severity: "critical", message: "leaky" },
    { check: "duplicate_rows", status: "pass", severity: "info", message: "none" },
    { check: "class_imbalance", status: "not_evaluated", severity: "info", message: "n/a" },
  ] };
  const { byStage, loose } = checksByStage(stages, findings);
  assert.deepEqual(byStage.get("leakage_audit")?.map((c) => c.status), ["fail"]);
  assert.deepEqual(byStage.get("deterministic_verification")?.map((c) => c.status), ["pass", "warn", "other", "fail"]);
  assert.ok(byStage.get("deterministic_verification")?.[0].text.includes("holdout"));
  assert.deepEqual(loose.map((c) => c.label), ["Duplicate rows", "Class imbalance"]);
  const totals = checkTotals([...byStage.values()].flat().concat(loose));
  assert.deepEqual(totals, { pass: 2, warn: 1, fail: 2, other: 2, total: 7 });
  assert.deepEqual(checksByStage([], undefined), { byStage: new Map(), loose: [] });
});

const record = (id: string, extra: Partial<RecordLike> & { details?: Record<string, unknown> }): RecordLike => ({
  id, decision_type: "decision_point_resolved", effective_state: "accepted", recorded_at: "2026-10-01T10:00:00Z",
  subject: { kind: "experiment", id: E }, actor: { kind: "rule", rule: "decision_point.x.v1" }, ...extra,
});

test("decision points: events join records; AI off rows show the rule only; levels and answers come from the record", () => {
  const events = [
    { event_type: "decision_point_resolved", sequence: 3, payload: { decision_point: "split.strategy", ai: "off", reason: "ai_disabled", level: 0, agreement: "off" } },
    { event_type: "decision_point_resolved", sequence: 5, payload: { decision_point: "column.semantic_role", ai: "on", level: 2, agreement: "disagree" } },
    { event_type: "stage_completed", sequence: 6, payload: { decision_point: "ignored" } },
  ];
  const records = [record(R, { details: { decision_point: "column.semantic_role", level: 2, agreement: "disagree", columns: [{ column: "region", rule: "numeric", ai: "categorical_code", used: "categorical_code", source: "ai" }], columns_total: 1 } })];
  const rows = decisionPointRows(events, records);
  assert.deepEqual(rows.map((r) => r.key), ["column.semantic_role", "split.strategy"]);
  const role = rows[0];
  assert.equal(role.level, 2);
  assert.equal(role.recordId, R);
  assert.equal(role.aiOff, false);
  assert.equal(answerSummary(role, "rule"), "numeric");
  assert.equal(answerSummary(role, "ai"), "categorical_code");
  assert.equal(role.stage, "feature_engineering");
  const truncated = { ...role, answersTotal: 30, answers: [{ column: "a", rule: "x", ai: "y", used: "y", source: "ai" }] };
  assert.match(answerSummary(truncated, "rule"), /first 1 of 30/);
  const split = rows[1];
  assert.equal(split.aiOff, true);
  assert.equal(split.recordId, null);
  assert.equal(split.reason, "ai_disabled");
  assert.equal(answerSummary(split, "ai"), "AI off: no answer");
  assert.deepEqual(pointsForStage(rows, "holdout_lock").map((r) => r.key), ["split.strategy"]);
  assert.deepEqual(decisionPointRows([], []), []);
});

test("a record without an event still gets a row; a superseded record loses to a live one", () => {
  const records = [
    record(R, { effective_state: "superseded", details: { decision_point: "target.column", level: 1 } }),
    record(P, { effective_state: "accepted", recorded_at: "2026-10-01T09:00:00Z", details: { decision_point: "target.column", level: 1 } }),
  ];
  const rows = decisionPointRows([], records);
  assert.equal(rows.length, 1);
  assert.equal(rows[0].recordId, P);
  assert.equal(rows[0].ai, "on");
});

test("stage records link engine-owned decisions; links are same-origin and UUID checked", () => {
  const w = record(R, { decision_type: "winner_locked" });
  assert.deepEqual(stageRecords("winner_lock", [w, record(P, {})]).map((r) => r.id), [R]);
  assert.deepEqual(stageRecords("ingestion", [w]), []);
  assert.equal(decisionHref(P, R), `/projects/${P}/decisions?record=${R}`);
  assert.equal(decisionHref("x", R), null);
  assert.equal(decisionHref(P, "../evil"), null);
  assert.equal(parseRecordParam(R), R);
  assert.equal(parseRecordParam("<script>"), null);
  assert.equal(parseRecordParam(null), null);
});

test("agent runs: specialist runs of this experiment only; cost and calls; replay once finished", () => {
  const run = (id: string, extra: Record<string, unknown> = {}) => ({ id, agent_key: "experiment_critic", kind: "specialist", status: "completed", cost_micros: 1500, currency: "USD", created_at: "2026-10-01T10:00:00Z", subject: { kind: "experiment", id: E }, ...extra });
  const runs = runsForExperiment([run(R), run(P, { kind: "lead" }), run("x", { subject: { kind: "experiment", id: P } }), run("y", { kind: "assistant" })], E);
  assert.deepEqual(runs.map((r) => r.id), [R]);
  assert.deepEqual(costLabel([]), { cost: "none recorded", calls: "no AI runs" });
  assert.deepEqual(costLabel(runs), { cost: "0.0015 USD", calls: "1 agent run" });
  assert.equal(costLabel([run(R), run(P, { currency: "EUR" })]).cost, "mixed currencies");
  assert.equal(canReplay(run(R)), true);
  assert.equal(canReplay(run(R, { status: "running" })), false);
});

test("replay results in plain words and typed errors", () => {
  const base = { equal: false, mismatches: [], tool_sequence: [{ tool: "read_findings", argument_digest: D1 }] };
  assert.equal(replayView({ ...base, equal: true }).tone, "ok");
  assert.match(replayView({ ...base, equal: true }).title, /matches/);
  assert.equal(replayView({ ...base, same_failure: true }).title, "Same failure on replay");
  assert.equal(replayView({ ...base, not_comparable: true }).title, "Not comparable");
  const bad = replayView({ ...base, mismatches: ["tool sequence differs at step 2"], incident_id: R });
  assert.equal(bad.tone, "crit");
  assert.deepEqual(bad.reasons, ["tool sequence differs at step 2"]);
  assert.match(bad.detail, /incident/);
  assert.equal(bad.tools[0].tool, "read_findings");
  const err = (status: number, code: string) => Object.assign(new Error("x"), { status, body: { error: { code } } });
  assert.match(replayErrorText(err(409, "not_replayable")).title, /cannot be replayed/);
  assert.match(replayErrorText(err(409, "run_not_finished")).title, /still running/);
  assert.match(replayErrorText(err(429, "rate_limited")).title, /Too many/);
  assert.match(replayErrorText(err(504, "replay_timeout")).title, /timed out/);
  assert.match(replayErrorText(err(403, "forbidden")).title, /cannot replay/);
  assert.match(replayErrorText(new Error("boom")).detail, /boom/);
});

test("downloads only for artifact types that carry no holdout rows or pickles", () => {
  assert.equal(canDownloadArtifact({ id: P, artifact_type: "source_code" }), true);
  for (const type of ["model", "preprocessor", "predictions", "report", "result_json", "dataset"]) assert.equal(canDownloadArtifact({ id: P, artifact_type: type }), false, type);
  assert.equal(canDownloadArtifact({ id: "nope", artifact_type: "source_code" }), false);
});

test("provenance shows only returned fields", () => {
  const stages = [stage("artifact_reproducibility_persistence", 20, { configuration: { runtime_environments: [{ environment_digest: "f".repeat(64), python_version: "3.12", os_name: "Linux", architecture: "arm64" }], code_snapshots: [{ code_digest: "d".repeat(64) }] } }), stage("holdout_lock", 6, { configuration: { seed: 42 } })];
  const rows = provenanceRows({ build: { pipeline_run_id: E, workspace_id: P, generator_version: "gen-1", reproduction_spec_digest: D1 }, stages, lineage: { source_dataset_id: R, split_plan_id: null }, eventCount: 12, formatWhen: (v) => v });
  const byKey = Object.fromEntries(rows.map((r) => [r.key, r.value]));
  assert.equal(byKey.generator, "gen-1");
  assert.equal(byKey.seed, "42");
  assert.equal(byKey.dataset, R);
  assert.equal(byKey["env-detail-0"], "Python 3.12, Linux, arm64");
  assert.equal(byKey["code-0"], "d".repeat(64));
  assert.equal(byKey.events, "12");
  assert.ok(!("split" in byKey) && !("locked" in byKey) && !("compat" in byKey));
  const bare = provenanceRows({ build: { pipeline_run_id: E, workspace_id: P }, stages: [], lineage: null, eventCount: 0, formatWhen: (v) => v });
  assert.ok(!bare.some((r) => r.key === "seed" || r.key === "generator"));
});

test("run switcher: newest first, capped, current run always listed", () => {
  const runs = Array.from({ length: 15 }, (_, i) => ({ id: `id${i}`, created_at: `2026-10-${String(i + 1).padStart(2, "0")}T00:00:00Z` }));
  const list = switcherRuns(runs, "id0");
  assert.equal(list.length, 13);
  assert.equal(list[0].id, "id14");
  assert.equal(list[12].id, "id0");
  assert.equal(switcherRuns(runs, "id14").length, 12);
});
