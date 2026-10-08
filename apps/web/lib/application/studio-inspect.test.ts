import assert from "node:assert/strict";
import test from "node:test";
import {
  candidateRows, experimentUsing, featureRows, foldRows, foldSizes, inspectorPath, investigationView, metricNames, modelVersionOf,
  proposalsFor, reviewView, safeFilename, splitFacts, transformSnippet, preprocessingSteps, stepsFor, type BuildLike, type ProposalLike,
} from "./studio-inspect.ts";

const U = (n: number) => `00000000-0000-4000-8000-${n.toString(16).padStart(12, "0")}`;
const build: BuildLike = {
  stages: [
    { key: "final_holdout_plan", reason: null, configuration: { strategy: "stratified", test_size: 0.2 } },
    { key: "holdout_lock", reason: "Fixed before modelling.", configuration: { locked: true, locked_at: "2026-10-01T00:00:00Z", test_size: 0.2, group_column: null, time_column: "ts" } },
    { key: "feature_engineering", reason: "Kept 3 columns.", configuration: { features: [
      { id: "f1", name: "tenure", feature_type: "numeric", origin: "original", decision: "accepted", definition: "tenure", transformations: [{ sequence: 2, transformation_type: "scale", transformer_class: "StandardScaler" }], sources: [{ column_name: "tenure" }] },
      { id: "f2", name: "spend_ratio", feature_type: "derived", origin: "generated", decision: "rejected", transformations: [], sources: [] },
    ] } },
    { key: "preprocessing", configuration: { steps: [
      { sequence: 2, column_scope: "numerical", transformer_type: "scale", transformer_class: "sklearn.preprocessing.StandardScaler", fit_scope: "fold_train" },
      { sequence: 1, column_scope: "numerical", transformer_type: "impute", transformer_class: "sklearn.impute.SimpleImputer", fit_scope: "fold_train" },
      { sequence: 3, column_scope: "categorical", transformer_type: "encode", transformer_class: "sklearn.preprocessing.OneHotEncoder", fit_scope: "fold_train" },
    ] } },
    { key: "candidate_generation", configuration: { candidates: [
      { id: "c1", algorithm: "Ridge", model_family: "linear", status: "completed", fingerprint: "abc", hyperparameters: { alpha: 1 } },
      { id: "c2", algorithm: "Forest", model_family: "tree", status: "completed", fingerprint: "def", hyperparameters: {} },
    ] } },
    { key: "cv_training", configuration: { folds: [
      { id: "k2", candidate_id: "c1", fold_number: 1, train_row_count: 80, validation_row_count: 20, metrics: { auc: 0.8 } },
      { id: "k1", candidate_id: "c1", fold_number: 0, train_row_count: 80, validation_row_count: 20, metrics: { auc: 0.6 } },
      { id: "k3", candidate_id: "c2", fold_number: 0, train_row_count: 80, validation_row_count: 20, metrics: { auc: 0.9, f1: 0.5 } },
    ] } },
    { key: "candidate_comparison", configuration: { candidate_scores: [{ candidate_id: "c2", metrics: { auc: 0.91 } }] } },
    { key: "winner_lock", configuration: { selected_candidate_id: "c2", runner_up_candidate_id: "c1" } },
    { key: "final_holdout", configuration: { evaluations: [{ id: "e", metrics: { auc: 0.123456 } }] } },
  ],
};

test("candidates: winner first, aggregate CV beats fold means, fold means fill the rest", () => {
  const rows = candidateRows(build);
  assert.deepEqual(rows.map((r) => [r.algorithm, r.selected, r.runnerUp, r.folds]), [["Forest", true, false, 1], ["Ridge", false, true, 2]]);
  assert.equal(rows[0].cv.auc, 0.91);
  assert.ok(Math.abs(rows[1].cv.auc - 0.7) < 1e-9);
  assert.deepEqual(rows[1].hyperparameters, [["alpha", "1"]]);
  assert.deepEqual(metricNames(rows, "f1"), ["auc"]);
  assert.deepEqual(metricNames(foldRows(build), "f1"), ["f1", "auc"]);
  assert.deepEqual(candidateRows(undefined), []);
});

test("folds are sorted, named by candidate and carry counts only", () => {
  const rows = foldRows(build);
  assert.deepEqual(rows.map((r) => [r.candidate, r.fold, r.trainRows, r.validationRows]), [["Forest", 0, 80, 20], ["Ridge", 0, 80, 20], ["Ridge", 1, 80, 20]]);
  assert.deepEqual(foldSizes(build), [{ fold: 0, trainRows: 80, validationRows: 20 }]);
});

test("nothing from the holdout evaluation stage reaches a view model", () => {
  const dump = JSON.stringify([candidateRows(build), foldRows(build), featureRows(build), splitFacts(build)]);
  assert.equal(dump.includes("0.123456"), false);
});

test("split facts merge plan and lock; features carry formula, transforms and a snippet", () => {
  assert.deepEqual(splitFacts(build), { strategy: "stratified", testSize: 0.2, groupColumn: null, timeColumn: "ts", locked: true, lockedAt: "2026-10-01T00:00:00Z", reason: "Fixed before modelling." });
  assert.equal(splitFacts(undefined).locked, false);
  const features = featureRows(build);
  assert.equal(features[0].transforms[0].transformer, "StandardScaler");
  const steps = preprocessingSteps(build);
  assert.deepEqual(steps.map((s) => s.type), ["impute", "scale", "encode"]);
  assert.match(transformSnippet(features[0], steps), /\("step_1", StandardScaler\(\)\)/);
  assert.match(transformSnippet({ ...features[1], kind: "numeric" }, steps), /Pipeline\(\[[\s\S]*SimpleImputer/);
  assert.deepEqual(stepsFor({ ...features[1], kind: "categorical" }, steps), ["sklearn.preprocessing.OneHotEncoder"]);
  assert.match(transformSnippet({ ...features[1], kind: "text" }, steps), /passes through/);
});

test("graph helpers: newest in-window run per plan, model version by produced_by, UUID-checked paths", () => {
  const node = (n: number, created: string, outside = false) => ({ kind: "experiment", id: U(n), created_at: created, outside_window: outside });
  const edge = (from: { kind: string; id: string }, to: { kind: string; id: string }, relation: string) => ({ from, to, relation });
  const split = { kind: "split_plan", id: U(9) };
  const nodes = [node(1, "2026-10-01"), node(2, "2026-10-03", true), node(3, "2026-10-02")];
  const edges = nodes.map((n) => edge(n, split, "uses_split_plan"));
  assert.equal(experimentUsing(nodes, edges, "split_plan", U(9)), U(3));
  assert.equal(experimentUsing(nodes, edges, "feature_recipe", U(9)), null);
  const recipe = { kind: "feature_recipe", id: U(8) };
  assert.equal(experimentUsing(nodes, [edge(recipe, nodes[0], "produced_by"), edge(recipe, nodes[2], "produced_by")], "feature_recipe", U(8)), U(3));
  assert.equal(modelVersionOf([edge({ kind: "model_version", id: U(7) }, { kind: "experiment", id: U(3) }, "produced_by")], U(3)), U(7));
  assert.equal(inspectorPath(U(1), "split_plan", U(9)), `/projects/${U(1)}/splits/${U(9)}`);
  assert.equal(inspectorPath(U(1), "problem_spec", U(9)), null);
  assert.equal(inspectorPath(U(1), "split_plan", "../x"), null);
  assert.equal(inspectorPath("x", "experiment", U(9)), null);
});

test("download names are plain basenames", () => {
  assert.equal(safeFilename("../../etc/passwd", "f.py"), "passwd");
  assert.equal(safeFilename("a\r\nb.py", "f.py"), "a_b.py");
  assert.equal(safeFilename("..", "f.py"), "f.py");
  assert.equal(safeFilename(null, "f.py"), "f.py");
});

const proposal = (over: Partial<ProposalLike>): ProposalLike => ({
  id: "p1", proposal_type: "ExperimentReviewProposal", status: "proposed", level_at_proposal: 1, proposed_by: "agent", created_at: "2026-10-05T10:00:00Z",
  subject: { kind: "experiment", id: U(3) }, payload: {}, validator_verdict: "accepted", ...over,
});

test("Critic review and investigation payloads read as typed fields; other subjects and types are filtered out", () => {
  const items = [
    proposal({ id: "a", payload: { verdict: "needs_work", summary: "CV auc 0.7 is close to the baseline", confidence: 0.6, cv_metrics: [{ metric: "auc", value: 0.7 }, { metric: 1, value: "x" }], findings: [{ check: "leakage", status: "warning", note: "tenure" }] }, rule_answer: { verdict: "promote" } }),
    proposal({ id: "b", created_at: "2026-10-06T10:00:00Z" }),
    proposal({ id: "c", subject: { kind: "experiment", id: U(4) } }),
    proposal({ id: "d", proposal_type: "DatasetInvestigationProposal" }),
  ];
  assert.deepEqual(proposalsFor(items, "ExperimentReviewProposal", "experiment", U(3)).map((p) => p.id), ["b", "a"]);
  const review = reviewView(items[0]);
  assert.deepEqual([review.verdict, review.level, review.metrics, review.findings.length, review.ruleAnswer], ["needs_work", 1, [{ metric: "auc", value: 0.7 }], 1, '{"verdict":"promote"}']);
  assert.equal(reviewView(items[1]).ruleAnswer, null);
  const inv = investigationView(proposal({ level_at_proposal: 0, payload: { target_candidates: [{ column: "b", rank: 2, reason: "r" }, { column: "a", rank: 1, reason: "r" }], questions: ["Which?", 4] } }));
  assert.deepEqual([inv.level, inv.targets.map((t) => t.column), inv.questions], [0, ["a", "b"], ["Which?"]]);
  assert.equal(investigationView(proposal({ level_at_proposal: 9 })).level, null);
});
