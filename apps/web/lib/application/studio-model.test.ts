import assert from "node:assert/strict";
import test from "node:test";
import { modelHeader, constraintWords, experimentOfModel, plainWords, goalWords, modelSentence, runRef, usedAsWords } from "./studio-model.ts";

test("run reference: a number when the whole list is loaded, the short id otherwise", () => {
  const ordinals = new Map([["aaaaaaaa-1", 3]]);
  assert.equal(runRef(ordinals, false, "aaaaaaaa-1"), "Run 3");
  assert.equal(runRef(ordinals, true, "aaaaaaaa-1"), "Run aaaaaaaa");
  assert.equal(runRef(ordinals, false, "bbbbbbbb-2"), "Run bbbbbbbb");
  assert.equal(runRef(ordinals, false, null), null);
});

test("the run behind a model comes only from a produced_by edge", () => {
  const edge = (relation: string, kind = "experiment") => ({ from: { kind: "model_version", id: "m" }, to: { kind, id: "e" }, relation });
  assert.equal(experimentOfModel([edge("produced_by")], "m"), "e");
  assert.equal(experimentOfModel([edge("uses")], "m"), null);
  assert.equal(experimentOfModel([edge("produced_by", "dataset_version")], "m"), null);
  assert.equal(experimentOfModel([edge("produced_by")], "other"), null);
});

test("used as: in use is named in words, other kinds are cleaned, nothing means not in use", () => {
  assert.equal(usedAsWords(["champion_model"], true), "the model in use");
  assert.equal(usedAsWords([], false), "Not in use");
  assert.equal(usedAsWords(undefined, false), "Not in use");
  assert.equal(usedAsWords(["shadow_model"], false), "shadow model");
  assert.equal(usedAsWords([], true), "the model in use");
  assert.equal(usedAsWords(["__proto__", "constructor"], false), "proto, constructor");
});

test("model sentence says only what is known and never claims 'in use' without the flag", () => {
  const base = { family: "lightgbm", algorithm: null, run: "Run 3", data: "churn.csv · 12 Jun 2026", designKind: "time", folds: 5, trained: "12 Jun 2026", inUse: true };
  assert.equal(modelSentence(base), "LightGBM, built by Run 3 from churn.csv · 12 Jun 2026, checked with 5 time-ordered folds of cross-validation. Trained 12 Jun 2026. It is the model in use.");
  const bare = modelSentence({ family: null, algorithm: null, run: null, data: null, designKind: null, folds: null, trained: null, inUse: false });
  assert.equal(bare, "A model. It is not the model in use.");
  assert.match(modelSentence({ ...base, designKind: "__proto__" }), /5 folds of cross-validation/);
  assert.doesNotMatch(modelSentence({ ...base, inUse: false }), /is the model in use/);
});

test("goal and constraint words: own keys only", () => {
  assert.match(goalWords("f1"), /balance of precision and recall/);
  assert.equal(goalWords("expected_cost"), "the lowest expected cost");
  assert.equal(goalWords("constructor"), "constructor");
  assert.equal(goalWords("__proto__"), "proto");
  assert.equal(constraintWords("flagged_share"), "share of rows flagged");
  assert.equal(constraintWords("constructor"), "constructor");
});

test("plain words: whole server words only, case kept, user values untouched", () => {
  assert.equal(plainWords("Scored on the holdout set once."), "Scored on the final test set once.");
  assert.equal(plainWords("Holdout metrics were not used."), "Final test set metrics were not used.");
  assert.equal(plainWords("hold-out data and holdout rows"), "final test set and final test rows");
  assert.equal(plainWords("The final evaluation set and final-evaluation rows."), "The final test set and final test rows.");
  assert.equal(plainWords("Measured on out-of-fold training predictions."), "Measured on predictions made on the training folds.");
  assert.equal(plainWords("never moves an operating point; operating points differ"), "never moves a threshold; thresholds differ");
  assert.equal(plainWords("nothing to change"), "nothing to change");
  // Column names, labels and sentences that name them are never rewritten.
  for (const keep of ["holdout_score, tenure", "No holdout_group value appears", "in Holdout_2 the weakest", "my-holdout", "holdouts", "xholdout", "out-of-fold_rate", "operating_point"]) {
    assert.equal(plainWords(keep), keep);
  }
});

test("model header: no name or in-use badge for another project, unknown ids or while loading", () => {
  const name = (v: string) => `Model v${v}`;
  const data = { version: "3", project_id: "A", is_champion: true };
  assert.deepEqual(modelHeader({ validId: true, isError: false, data }, "A", name), { title: "Model v3", pill: { text: "★ in use", tone: "ok" } });
  assert.deepEqual(modelHeader({ validId: true, isError: false, data: { ...data, is_champion: false } }, "A", name).pill, { text: "not in use", tone: "gray" });
  assert.deepEqual(modelHeader({ validId: true, isError: false, data }, "B", name), { title: "Model", pill: { text: "other project", tone: "warn" } });
  assert.deepEqual(modelHeader({ validId: false, isError: false, data: null }, "A", name), { title: "Model not found", pill: { text: "not found", tone: "warn" } });
  assert.equal(modelHeader({ validId: true, isError: true }, "A", name).pill.text, "status unavailable");
  assert.equal(modelHeader({ validId: true, isError: false, data: null }, "A", name).pill.text, "loading");
});
