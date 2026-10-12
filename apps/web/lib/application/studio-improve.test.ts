import assert from "node:assert/strict";
import test from "node:test";

import {
  NOTHING_UNATTENDED, SAME_FOLDS_NOTE, STANDING_BANNER, comparisonsText, countsSentence, fmtScore, fmtSigned, foldViews, differenceText, familyChoices, fmtNum, lookText, maxLooks, maxPlanRuns, newModelsText, notComparableText, shownVerdict, triesCaveat, triesOf, tryBody,
  verdictBadge, verdictCounts, verdictKey, verdictSentence, type VerdictLike,
} from "./studio-improve.ts";

const ID = "11111111-1111-4111-8111-111111111111";
const v = (patch: Partial<VerdictLike>): VerdictLike => ({ experiment_id: ID, verdict: "within_the_noise", metric: "pr_auc", direction: "higher_is_better", folds: 5, mean_difference: 0.01, margin: 0.05, ...patch });

test("the four verdicts have plain labels; anything else is 'Not recognised', never a raw code", () => {
  assert.equal(verdictBadge("beats_the_noise").label, "Beats the noise");
  assert.equal(verdictBadge("within_the_noise").label, "Within the noise");
  assert.equal(verdictBadge("worse").label, "Worse beyond the noise");
  assert.equal(verdictBadge("not_comparable").label, "Can't compare");
  for (const odd of ["__proto__", "constructor", "toString", "hasOwnProperty", "", "BEATS_THE_NOISE", null, undefined]) {
    assert.equal(verdictBadge(odd).label, "Not recognised", String(odd));
    assert.equal(verdictKey(odd), "unknown", String(odd));
  }
});

test("a not-comparable reason is told in words; an unknown or prototype key gets the generic sentence", () => {
  assert.match(notComparableText("different_split_plan"), /different test designs/);
  assert.match(notComparableText("too_few_folds"), /fewer than 5/);
  for (const odd of ["__proto__", "constructor", "nope", null, undefined]) assert.equal(notComparableText(odd), "These two runs cannot be compared.");
  assert.doesNotMatch(verdictSentence(v({ verdict: "not_comparable", reason: "__proto__" })), /__proto__/);
});

test("differences are in the score's own units and say which way is better (error scores included)", () => {
  assert.equal(differenceText(v({ mean_difference: 0.012 })), "0.012 higher (better)");
  assert.equal(differenceText(v({ mean_difference: -0.012 })), "0.012 lower (worse)");
  const rmse = v({ metric: "rmse", direction: "lower_is_better" });
  assert.equal(differenceText({ ...rmse, mean_difference: -0.5 }), "0.5 lower (better)");
  assert.equal(differenceText({ ...rmse, mean_difference: 0.5 }), "0.5 higher (worse)");
  // No direction in the read: the known error scores are lower-is-better.
  assert.equal(differenceText({ ...rmse, direction: null, mean_difference: -2 }), "2 lower (better)");
  assert.equal(differenceText(v({ mean_difference: 0 })), "no difference");
  assert.equal(differenceText(v({ mean_difference: null })), null);
  assert.equal(differenceText(v({ mean_difference: undefined })), null);
});

test("the sentences say what was measured, claim no more than the margin allows, and never print a negated stored score", () => {
  const beats = verdictSentence(v({ verdict: "beats_the_noise", mean_difference: 0.08, margin: 0.05 }));
  assert.match(beats, /Across the same 5 folds, PR-AUC was 0\.08 higher \(better\) on average/);
  assert.match(beats, /more than the noise margin of 0\.05/);
  assert.match(beats, /evidence, not proof/);
  assert.match(beats, /this one comparison/);
  const within = verdictSentence(v({ reason: "inside_margin" }));
  assert.match(within, /inside the noise margin of 0\.05, so it could be luck/);
  assert.match(within, /Keep the current model/);
  assert.match(verdictSentence(v({ reason: "below_minimum_effect" })), /too small a change to matter/);
  assert.match(verdictSentence(v({ reason: "no_change" })), /same on every one of the 5 folds/);
  assert.match(verdictSentence(v({ verdict: "worse", mean_difference: -0.09, margin: 0.05 })), /0\.09 lower \(worse\).*larger than the noise margin of 0\.05/);
  const rmse = verdictSentence(v({ verdict: "beats_the_noise", metric: "rmse", direction: "lower_is_better", mean_difference: -1.2, margin: 0.4 }));
  assert.match(rmse, /RMSE was 1\.2 lower \(better\)/);
  assert.doesNotMatch(rmse, /-1\.2/);
});

test("missing numbers and unknown verdicts draw no conclusion", () => {
  assert.match(verdictSentence(v({ margin: null })), /not returned, so no conclusion/);
  assert.match(verdictSentence(v({ verdict: "dazzling" })), /not recognised, so no conclusion/);
  assert.match(verdictSentence(v({ verdict: "__proto__" })), /not recognised/);
});

test("look text: a number only when the API gave both numbers; otherwise what is known", () => {
  assert.equal(lookText({ looks_on_plan: 4, experiment_look: 3 }), "Look 3 of 4");
  assert.equal(lookText({ looks_on_plan: 1, experiment_look: null }), "1 look so far, place of this run not recorded");
  assert.equal(lookText({ looks_on_plan: 2 }), "2 looks so far, place of this run not recorded");
  assert.equal(lookText({}), "Not counted");
  assert.equal(lookText(null), "Not counted");
  assert.equal(lookText(undefined), "Not counted");
});

test("the tries caveat appears only with several runs and does not overstate", () => {
  const three = triesCaveat(3)!;
  assert.match(three, /^3 runs on this test design have chosen, or are choosing, a best model \(the run in use and runs still training included\)/);
  assert.match(three, /higher than 5%.*easier it is to be fooled/);
  assert.doesNotMatch(three, /× 5%/);
  assert.equal(triesCaveat(2), null);
  assert.equal(triesCaveat(1), null);
  assert.equal(triesCaveat(0), null);
  assert.equal(triesCaveat(null), null);
  assert.equal(triesCaveat(undefined), null);
});

test("counts: every verdict is counted once; unknown and unread are 'not read', not 'within the noise'", () => {
  const counts = verdictCounts([v({ verdict: "beats_the_noise" }), v({}), v({ verdict: "worse" }), v({ verdict: "not_comparable" }), null, v({ verdict: "zzz" })]);
  assert.deepEqual(counts, { beats: 1, within: 1, worse: 1, cannot: 1, unread: 2 });
  assert.equal(countsSentence(counts), "Of the finished tries: 1 beats the noise, 1 is within the noise, 1 is worse, 1 can't be compared, 2 not read.");
  assert.equal(countsSentence(verdictCounts([])), null);
  assert.equal(maxPlanRuns([v({ comparisons_on_plan: 2 }), v({ comparisons_on_plan: 5 }), null]), 5);
  assert.equal(maxPlanRuns([null]), null);
  assert.equal(maxLooks([v({ test_set_looks: { looks_on_plan: 3 } }), v({ test_set_looks: { looks_on_plan: 4 } })]), 4);
  assert.equal(maxLooks([v({})]), null);
});

test("tries are the runs based on the run in use, newest first; a run with no parent is never one", () => {
  const runs = [
    { id: "a", status: "completed", created_at: "2026-01-01T00:00:00Z", parent_experiment_id: ID },
    { id: "b", status: "running", created_at: "2026-01-03T00:00:00Z", parent_experiment_id: ID },
    { id: "c", status: "completed", created_at: "2026-01-02T00:00:00Z", parent_experiment_id: "other" },
    { id: "d", status: "completed", created_at: "2026-01-04T00:00:00Z" },
  ];
  assert.deepEqual(triesOf(runs, ID).map((r) => r.id), ["b", "a"]);
  assert.deepEqual(triesOf(runs, null), []);
  assert.deepEqual(triesOf(runs, undefined), []);
});

test("the families to offer follow the kind of answer, leave out baselines and what the run already trained; an unknown kind offers none", () => {
  const binary = familyChoices("binary").map((f) => f.value);
  assert.ok(binary.includes("random_forest") && binary.includes("lightgbm"));
  assert.ok(!binary.includes("majority") && !binary.includes("ridge"));
  assert.ok(!familyChoices("binary", ["random_forest", " lightgbm "]).some((f) => f.value === "random_forest" || f.value === "lightgbm"));
  const regression = familyChoices("regression").map((f) => f.value);
  assert.ok(regression.includes("ridge") && !regression.includes("logistic_regression") && !regression.includes("mean"));
  for (const odd of [null, undefined, "", "__proto__", "constructor", "time_series"]) assert.deepEqual(familyChoices(odd), [], String(odd));
});

test("the try request is one family_include change with the person's own reason, unchanged", () => {
  assert.deepEqual(tryBody("random_forest", "  keep <b>this</b> text  "), { intent: "keep <b>this</b> text", changes: [{ kind: "family_include", family: "random_forest" }] });
  assert.equal(typeof tryBody("", "why"), "string");
  assert.equal(typeof tryBody("ridge", "   "), "string");
});

test("a suspicious or time-ordered result is never 'Beats the noise'; unknown reasons stay generic; the server sentence is shown capped", () => {
  const sus = v({ verdict: "beats_the_noise", reason: "suspicious_result" });
  assert.equal(shownVerdict(sus), "not_comparable");
  assert.equal(verdictBadge(shownVerdict(sus)).label, "Can't compare");
  assert.match(verdictSentence(sus), /too good to be true/);
  assert.match(verdictSentence(v({ verdict: "not_comparable", reason: "time_ordered_folds" })), /ordered by time/);
  assert.equal(verdictCounts([sus]).beats, 0);
  assert.equal(verdictCounts([sus]).cannot, 1);
  assert.equal(verdictSentence(v({ verdict: "not_comparable", reason: "brand_new_code" })), "These two runs cannot be compared.");
  // The server's `message` is never shown, whatever it says (old caveat, raw metric keys, its own words).
  const message = "Best of 3 tries on the same folds: the cross-validation number is a little optimistic; the final test set is the honest estimate. roc_auc is higher by 0.04 across experiment candidates.";
  for (const verdict of ["beats_the_noise", "within_the_noise", "worse"]) {
    const said = verdictSentence(v({ verdict, message, metric: "roc_auc", mean_difference: 0.04, margin: 0.02 }));
    assert.doesNotMatch(said, /honest estimate|roc_auc|experiment|candidates|optimistic/i, verdict);
    assert.match(said, /ROC-AUC/);
  }
  assert.doesNotMatch(verdictSentence(v({ verdict: "dazzling", message })), /honest estimate|roc_auc/);
  assert.doesNotMatch(verdictSentence(v({ verdict: "not_comparable", reason: "weird", message })), /honest estimate|roc_auc/);
  assert.match(newModelsText(2)!, /^2 new models\. The more models a try adds, the larger the gain must be\.$/);
  assert.match(newModelsText(1)!, /^1 new model\./);
  assert.equal(newModelsText(null), null);
  assert.equal(newModelsText(0), "This try added no new models: the same models were trained again.");
});

test("the fold table keeps every sign and says the direction in words", () => {
  const folds = [
    { fold_number: 1, experiment: 0.31, against: 0.3, difference: 0.01 },
    { fold_number: 2, experiment: 0.28, against: 0.3, difference: -0.02 },
    { fold_number: 3, experiment: 0.3, against: 0.3, difference: 0 },
  ];
  const up = foldViews({ metric: "pr_auc", direction: "higher_is_better", fold_scores: folds });
  assert.deepEqual(up.map((r) => r.difference), ["+0.01", "\u22120.02", "0"]);
  assert.deepEqual(up.map((r) => r.words), ["higher, better", "lower, worse", "same"]);
  // An error score: natural (positive) units, lower is better.
  const err = foldViews({ metric: "rmse", direction: "lower_is_better", fold_scores: [{ fold_number: 1, experiment: 2.5, against: 3, difference: -0.5 }, { fold_number: 2, experiment: 3.2, against: 3, difference: 0.2 }] });
  assert.deepEqual(err.map((r) => [r.current, r.tried, r.difference, r.words]), [["3", "2.5", "\u22120.5", "lower, better"], ["3", "3.2", "+0.2", "higher, worse"]]);
  // A negative R² fold keeps its minus.
  const r2 = foldViews({ metric: "r2", direction: "higher_is_better", fold_scores: [{ fold_number: 1, experiment: -0.12, against: -0.3, difference: 0.18 }] });
  assert.deepEqual([r2[0].current, r2[0].tried, r2[0].difference], ["\u22120.3", "\u22120.12", "+0.18"]);
  assert.deepEqual(foldViews({ fold_scores: null }), []);
});

test("tiny numbers read 'about 0', never '0.'", () => {
  assert.equal(fmtNum(1e-7), "about 0");
  assert.equal(fmtScore(-1e-7), "about 0");
  assert.equal(fmtSigned(1e-7), "about 0");
  assert.equal(comparisonsText(3), "3 comparisons");
  assert.equal(comparisonsText(1), "1 comparison");
  assert.equal(comparisonsText(null), null);
});

test("the page copy never claims who starts a try or that only a person can", () => {
  assert.match(NOTHING_UNATTENDED, /never starts a try by itself/);
  assert.match(NOTHING_UNATTENDED, /from code or by a connected tool/);
  assert.doesNotMatch(`${NOTHING_UNATTENDED} ${SAME_FOLDS_NOTE}`, /you press|human|only a person|by you/i);
  assert.match(SAME_FOLDS_NOTE, /that number is not used to compare/);
});

test("numbers print without noise", () => {
  assert.equal(fmtNum(0.0123456), "0.0123");
  assert.equal(fmtNum(-12.3456), "12.3");
  assert.equal(fmtNum(0), "0");
  assert.equal(fmtNum(0.00000123), "about 0");
  assert.equal(fmtNum(0.0000123), "0.0000123");
});

test("the standing banner says what is true and nothing about picking by the final number being fine", () => {
  assert.equal(STANDING_BANNER, "Every try is scored on the final test set once. Do not pick a model by its final-test number.");
});
