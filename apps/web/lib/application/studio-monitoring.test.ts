import assert from "node:assert/strict";
import test from "node:test";
import { utcWhen } from "./format-time.ts";
import {
  DATES_LATER, DATES_NOT_JUDGED, MAY_DIFFER, NOTHING_AUTOMATIC, NOT_THE_MODEL, PAUSED_NOTE, REAL_NOT_TEST, REFUSED_SET, TABLE_NOTE, THRESHOLDS_READ_ONLY,
  THRESHOLDS_UNAVAILABLE, UNCONFIRMED, WEEK_DEFINITION, canAct, dateNote, dateSentence, driftBars, featureList, featureSentence, featureStatusWord, goBackOffer,
  headline, idCaveat, isLinkConflict, linkConfirmWords, linkedRows, looksLikeId, mapLinkError, mapUploadError, metricLabel, numberPair, outcomeCell, outcomeView,
  REAL_TREND_NOTE, floorWords, ownSteps, trendCell, weekLines, psiTrend, realTrend, realTrendEmpty, seriesColumns, shortWeek, weekTicks,
  plainNumber, psiDefinition, scopeText, scoreSentences, statusWords, thresholdLines, weekKey, weekLabel, weekRows, weekSentence,
  type FeatureLike, type OverviewLike, type Thresholds, type WeekSummaryLike,
} from "./studio-monitoring.ts";
import { utcWhen as scheduleUtcWhen } from "./studio-schedule.ts";

const BANNED = /\b(release|champion|ref|digest|holdout|manifest|package|slot|tick|revision|window)s?\b/i;
const T: Thresholds = { psi_watch: 0.1, psi_alert: 0.2, significance_level: 0.01, flagged_share_relative_watch: 0.3, flagged_share_absolute_watch: 0.02, min_rows: 200, min_labels: 100, min_per_class: 10 };
const MON = "2026-10-05T00:00:00Z";
const M_USE = "11111111-1111-4111-8111-111111111111";
const week = (extra: Partial<WeekSummaryLike> = {}): WeekSummaryLike => ({
  window_start: MON, status: "ok", summary_code: "stable", n_rows: 1234, n_batch_predictions: 2, max_psi: 0.04, prediction_psi: 0.02,
  outcomes_status: "not_linked", model_version_id: M_USE, ...extra,
});
const overview = (extra: Partial<OverviewLike> = {}): OverviewLike => ({ enabled: true, status: "ok", model_version: "v3", latest: week(), windows: [week()], thresholds: T, ...extra });
const err = (status: number, code: string, retryable = false) => ({ status, body: { error: { code, message: "SERVER INTERNAL WORDS", retryable, details: {} } } });
const sentences: string[] = [];
const keep = (s: string) => { sentences.push(s); return s; };

test("weeks are Mondays in UTC; a bad date is never a week; one shared UTC formatter", () => {
  assert.equal(weekLabel(MON), "week of Mon, 5 Oct 2026 (UTC)");
  assert.equal(weekKey(MON), "2026-10-05");
  assert.equal(weekKey("not a date"), null);
  assert.equal(weekLabel(null), "—");
  assert.equal(scheduleUtcWhen, utcWhen, "studio-schedule re-exports the shared formatter");
  keep(WEEK_DEFINITION);
});

test("B1: numbers keep their zeros, group thousands, avoid exponents, and two different values never print the same", () => {
  assert.equal(plainNumber(5000), "5,000");
  assert.equal(plainNumber(123456), "123,500");
  assert.equal(plainNumber(0.0001234), "0.0001234");
  assert.equal(plainNumber(-42.125), "-42.13");
  assert.equal(plainNumber(1200.5), "1,201");
  assert.deepEqual(numberPair(1500, 1499.5), ["1,500", "1,499.5"]);
  assert.deepEqual(numberPair(1200.5, 1000), ["1,201", "1,000"]);
  assert.deepEqual(numberPair(5000, 5000), ["5,000", "5,000"]);
  assert.equal(featureSentence({ name: "income", kind: "numeric", psi: 0.05, status: "ok", mean: 123456, mean_reference: 5000 }), "income moved little (PSI 0.05): the average is 123,500 vs 5,000 in training.");
  assert.equal(featureSentence({ name: "delta", kind: "numeric", psi: 0.05, status: "ok", mean: -0.0001234, mean_reference: -12.5 }), "delta moved little (PSI 0.05): the average is -0.0001234 vs -12.5 in training.");
  for (const v of [5000, 123456, 0.0001234, -3.5]) assert.doesNotMatch(plainNumber(v), /e[+-]/);
});

test("B3: thresholds name the statistical gate; no hardcoded lines while they are unavailable", () => {
  const def = keep(psiDefinition(T));
  assert.match(def, /watch at 0\.10 or more and alert at 0\.20 or more, but only when PSI passes the line AND a statistical test says the difference is not chance/);
  assert.match(def, /p below 0\.01 divided by the number of columns compared/);
  const none = keep(psiDefinition(null));
  assert.doesNotMatch(none, /0\.10|0\.20/);
  assert.match(none, /unavailable until the status loads/);
  keep(THRESHOLDS_UNAVAILABLE);
  const lines = thresholdLines(T);
  assert.match(lines[0], /marked only when PSI passes the line AND a statistical test says it is not chance/);
  assert.match(lines[1], /30% or more .* AND by at least 2 percentage points AND the test says it is not chance/);
  assert.match(lines[2], /rows compared/);
  assert.ok(lines.includes(DATES_NOT_JUDGED));
  lines.forEach(keep);
  keep(THRESHOLDS_READ_ONLY);
  // Older reads without the optional fields still describe the rule without inventing numbers.
  assert.doesNotMatch(thresholdLines({ ...T, significance_level: null, flagged_share_absolute_watch: null }).join(" "), /p below|percentage points/);
});

test("B3: an ok week above a line says it is within chance, never 'look like training' next to an alarming number", () => {
  const s = keep(weekSentence(week({ max_psi: 0.35 }), T));
  assert.match(s, /the columns compared look like training \(1,234 rows scored\)\. The largest input change \(PSI\) is 0\.35, above the line but within what chance allows for this many rows and columns/);
  assert.match(s, /a change counts only when a statistical test says it is not chance/);
  assert.equal(weekSentence(week(), T, 0), "In the week of Mon, 5 Oct 2026 (UTC), no column could be compared with training, so nothing is judged.");
  const rows = weekRows([week({ max_psi: 0.35, prediction_psi: 0.12 })], T);
  assert.equal(rows[0].inputs, "0.35 (within chance)");
  assert.equal(rows[0].scores, "0.12 (within chance)");
  assert.equal(weekRows([week({ status: "alert", max_psi: 0.35 })], T)[0].inputs, "0.35");
  keep(TABLE_NOTE);
  assert.equal(featureStatusWord({ status: "ok", psi: 0.3 }, T), "Within chance");
  assert.equal(featureStatusWord({ status: "ok", psi: 0.02 }, T), "Little change");
  assert.equal(keep(featureSentence({ name: "plan", kind: "categorical", psi: 0.31, status: "ok" }, T)), "plan changed (PSI 0.31), above the line but within what chance allows for this many rows and columns.");
  assert.match(keep(scoreSentences({ available: true, psi: 0.25, status: "ok" }, T)[0]), /changed \(PSI 0\.25\), above the line but within what chance allows/);
});

test("B3: a watch driven only by the flagged share points to the scores, not to a small input number", () => {
  const s = keep(weekSentence(week({ status: "watch", max_psi: 0.03, prediction_psi: 0.04 }), T));
  assert.match(s, /^Watch: in the week of .*, the share of rows the model flagged moved \(see Scores in the week's details\)\. The inputs and scores stay below the watch line \(largest input change 0\.03\)\./);
  assert.doesNotMatch(s, /moved somewhat\.|watch line is 0\.10/);
});

test("every headline state is distinct; paused keeps the last status and tone with a note", () => {
  assert.equal(headline({ pending: true, failed: false, overview: undefined, model: "Model v3" }).kind, "loading");
  assert.equal(headline({ pending: false, failed: true, overview: undefined, model: "Model v3" }).kind, "error");
  const cases: Array<[OverviewLike, string, RegExp]> = [
    [overview({ status: "no_model_in_use", latest: null, windows: [], model_version: null }), "no_model", /No model is in use yet/],
    [overview({ status: "no_windows", latest: null, windows: [] }), "no_weeks", /no week has been checked yet/],
    [overview({ enabled: false, status: "no_windows", latest: null, windows: [] }), "paused", /monitoring is switched off on this server/],
    [overview(), "ok", /the columns compared look like training/],
    [overview({ status: "watch", latest: week({ status: "watch", max_psi: 0.14 }) }), "watch", /^Model v3: Watch: .*moved somewhat\..*0\.14/],
    [overview({ status: "alert", latest: week({ status: "alert", max_psi: 0.31 }) }), "alert", /Alert: .*moved a lot\..*0\.31.*It does not mean the model is wrong.*Nothing switches the model in use on its own/],
    [overview({ status: "not_enough_data", latest: week({ status: "not_enough_data", summary_code: "too_few_rows", n_rows: 12 }) }), "not_enough_data", /Fewer than 200 rows could be compared with training .* \(12 scored\)/],
    [overview({ status: "brand_new", latest: week({ status: "brand_new" }) }), "unknown", /does not recognise/],
    [overview({ status: "__proto__", latest: week({ status: "__proto__" }) }), "unknown", /does not recognise/],
  ];
  for (const [o, kind, text] of cases) {
    const h = headline({ pending: false, failed: false, overview: o, model: "Model v3" });
    assert.equal(h.kind, kind, kind);
    assert.ok("sentence" in h);
    assert.match(keep(h.sentence), text, kind);
  }
  const paused = headline({ pending: false, failed: false, overview: overview({ enabled: false, status: "alert", latest: week({ status: "alert", max_psi: 0.3 }) }), model: "Model v3" });
  assert.equal(paused.kind, "alert");
  if (paused.kind === "alert") {
    assert.equal(paused.tone, "crit");
    assert.equal(paused.paused, true);
    assert.equal(paused.label, "Alert · monitoring paused");
    assert.ok(paused.sentence.endsWith(PAUSED_NOTE));
  }
});

test("not-enough-data reasons, by summary code; an unknown or prototype code is a fixed sentence", () => {
  for (const code of ["too_few_rows", "no_reference", "inputs_unavailable", "__proto__", "constructor", "new_code"]) {
    const s = keep(weekSentence(week({ status: "not_enough_data", summary_code: code, n_rows: 5 }), T));
    assert.doesNotMatch(s, /undefined|function|\[object/);
  }
  assert.match(weekSentence(week({ status: "not_enough_data", summary_code: "no_reference" }), T), /saved before monitoring existed/);
});

test("S2: drift never reads as a broken model or a cause", () => {
  assert.equal(NOT_THE_MODEL, "Drift means the inputs look different from training. It does not mean the model is wrong.");
  for (const status of ["watch", "alert"]) {
    const s = weekSentence(week({ status, max_psi: 0.25 }), T);
    assert.ok(s.includes(NOT_THE_MODEL));
    assert.doesNotMatch(s.replace(NOT_THE_MODEL, ""), /model is (wrong|broken|failing)|because|caused/i);
  }
  assert.ok(weekSentence(week({ status: "alert", max_psi: 0.3 }), T).includes(NOTHING_AUTOMATIC));
});

test("the weeks table: newest first, at most 12, bad dates dropped, short real-world cells", () => {
  const many = Array.from({ length: 15 }, (_, i) => week({ window_start: new Date(Date.UTC(2026, 6, 6 + 7 * i)).toISOString() }));
  const rows = weekRows([...many, week({ window_start: "nope" })], T);
  assert.equal(rows.length, 12);
  assert.ok(rows[0].key > rows[11].key);
  assert.equal(weekRows([week({ max_psi: null })], T)[0].inputs, "—");
  assert.equal(outcomeCell(week({ outcomes_status: "measured", outcome_metric: "pr_auc", outcome_metric_value: 0.612, n_labelled: 140 })), "PR AUC 0.61 on 140 rows");
  for (const s of ["not_linked", "no_key_in_scores", "key_not_unique", "not_enough_labels", "measured", "", "__proto__"]) keep(outcomeCell(week({ outcomes_status: s, n_labelled: 3 })));
  assert.equal(outcomeCell(week({ outcomes_status: "__proto__" })), "—");
  assert.deepEqual(statusWords("__proto__"), { label: "Unknown", tone: "gray" });
  assert.equal(statusWords("ok").label, "Compared columns look like training");
});

test("S9: null or broken feature entries are dropped, never thrown on", () => {
  assert.deepEqual(featureList({ features: [null, 3, "x", [], { name: "a", psi: 0.1 }] }), [{ name: "a", psi: 0.1 }]);
  assert.deepEqual(featureList(null), []);
  assert.deepEqual(featureList({ features: "nope" }), []);
  assert.doesNotThrow(() => driftBars([null as unknown as FeatureLike, { name: "a", psi: 0.1, status: "ok" }], T));
  assert.doesNotThrow(() => idCaveat(featureList({ features: [null] })));
});

test("B2 + S8: chart data leaves dates and uncounted columns out of bars and scale; caps the axis; status word on each bar", () => {
  const features: FeatureLike[] = Array.from({ length: 14 }, (_, i) => ({ name: `col_${i}`, kind: "numeric", psi: i / 100, status: i / 100 >= 0.1 ? "watch" : "ok" }));
  features.push(
    { name: "x".repeat(200), kind: "categorical", psi: 0.5, status: "alert" },
    { name: "signup_date", kind: "date", psi: 8.3, status: "ok", counted: false },
    { name: "hidden", kind: "numeric", psi: 2, status: "ok", counted: false },
    { name: "bad", psi: "0.9" }, { name: "neg", psi: -1 },
  );
  const chart = driftBars(features, T);
  assert.equal(chart.bars.length, 10);
  assert.ok(!chart.bars.some((b) => ["signup_date", "hidden", "bad", "neg"].includes(b.name)));
  assert.equal(chart.bars[0].psi, 0.5);
  assert.ok(chart.bars[0].name.length <= 60);
  assert.equal(chart.max, 0.5, "the scale ignores the date column's 8.3");
  assert.equal(chart.bars[0].word, "Alert");
  assert.match(keep(chart.summary), /^Input change \(PSI\) of the 10 judged columns that moved most, largest first: x+… 0\.50 \(alert\), col_13 0\.13 \(watch\)/);
  const huge = driftBars([{ name: "a", psi: 2.4, status: "alert" }, { name: "b", psi: 0.1, status: "ok" }], T);
  assert.ok(Math.abs(huge.max - 0.6) < 1e-9, "capped at 3x the alert line");
  assert.equal(huge.bars[0].offScale, true);
  assert.equal(huge.bars[0].width, 100);
  assert.equal(huge.bars[1].word, "Within chance");
  assert.equal(huge.bars[1].offScale, false);
  assert.equal(huge.stackLabels, false);
  assert.equal(driftBars([{ name: "a", psi: 0.5, status: "alert" }], { psi_watch: 0.18, psi_alert: 0.2 }).stackLabels, true, "labels stack when the lines are close");
  assert.equal(driftBars([{ name: "a", psi: 0.01, status: "ok" }], T).max, 0.25, "the alert line stays on the scale");
  assert.equal(keep(driftBars([], T).summary), "No judged column could be compared for this week.");
});

test("B2: date columns show their date ranges only, never a PSI; the note comes from kind and later_than_training", () => {
  const d: FeatureLike = { name: "signup_date", kind: "date", psi: 8.3, status: "ok", counted: false, range: ["2026-09-01", "2026-10-04"], range_reference: ["2024-01-01", "2026-06-30"], later_than_training: true };
  assert.equal(keep(featureSentence(d, T)), "signup_date: dates from 2026-09-01 to 2026-10-04 (training: 2024-01-01 to 2026-06-30).");
  assert.doesNotMatch(featureSentence(d, T), /PSI|moved/);
  assert.equal(dateSentence({ name: "d", kind: "date", range: ["2026-09-01", "<b>"], range_reference: ["x", "y"] }), "d: no dates could be read this week.");
  assert.equal(dateSentence({ name: "d", kind: "date", range: ["2026-09-01", "2026-09-02"], range_reference: null }), "d: dates from 2026-09-01 to 2026-09-02.");
  assert.equal(keep(dateNote([d]) ?? ""), `${DATES_LATER} ${DATES_NOT_JUDGED}`);
  assert.equal(dateNote([{ ...d, later_than_training: false }]), DATES_NOT_JUDGED);
  assert.equal(dateNote([{ name: "created_at", kind: "numeric" }]), null, "a date-like name is not a date: only kind decides");
});

test("column sentences come from numbers; untrusted names and values are plain text; a null category value", () => {
  assert.equal(keep(featureSentence({ name: "monthly_spend", kind: "numeric", psi: 0.23, status: "alert", mean: 72.5, mean_reference: 61.25, missing_share: 0.02, missing_share_reference: 0.01 }, T)),
    "monthly_spend moved a lot (PSI 0.23): the average is 72.5 vs 61.25 in training.");
  assert.equal(keep(featureSentence({ name: "plan", kind: "categorical", psi: 0.12, status: "watch", top_change: { bucket: "category", value: "family_5g", share: 0.06, share_reference: 0.01 } }, T)),
    "plan moved somewhat (PSI 0.12): the value 'family_5g' is 6% of rows vs 1% in training.");
  assert.equal(featureSentence({ name: "plan", kind: "categorical", psi: 0.12, status: "watch", top_change: { bucket: "category", value: null, share: 0.06, share_reference: 0.01 } }, T),
    "plan moved somewhat (PSI 0.12): an unnamed value is 6% of rows vs 1% in training.");
  assert.match(keep(featureSentence({ name: "region", kind: "categorical", psi: 0.02, status: "ok", top_change: { bucket: "other", share: 0.1, share_reference: 0.05 }, missing_share: 0.3, missing_share_reference: 0.05 }, T)),
    /region moved little .*values that were rare or unseen in training are 10% .*; 30% of values are missing vs 5% in training\.$/);
  assert.equal(featureSentence({ name: "a‮b\nc", psi: 0.01, status: "ok" }), "a b c moved little (PSI 0.01).");
});

test("S1: a tight ID guess from the name, worded as a guess; dates are never guessed from names", () => {
  for (const name of ["id", "customer_id", "CustomerID", "customerId", "uuid", "order_no", "account_number", "customer_code", "user_id"]) assert.ok(looksLikeId(name), name);
  for (const name of ["total_day_minutes", "total_day_calls", "number_customer_service_calls", "number_vmail_messages", "time_on_site", "phone_number", "key_account", "Region_Code", "signup_date", "created_at", "idle", "paid"]) {
    assert.ok(!looksLikeId(name), name);
  }
  const caveat = idCaveat([{ name: "customer_code", kind: "categorical", status: "watch" }, { name: "signup_date", kind: "date", status: "watch", counted: false }, { name: "plan", status: "alert" }]);
  assert.equal(keep(caveat ?? ""), "The name of customer_code suggests an ID. ID-like columns can look different simply because new customers appear in new files; a change here does not by itself mean anything is wrong.");
  assert.equal(idCaveat([{ name: "customer_id", status: "ok" }]), null);
});

test("S3: the scope of the comparison is stated", () => {
  assert.equal(keep(scopeText({ features_compared: 12, features_total: 15, features_not_monitored: 2, dates_compared: 1 }) ?? ""),
    "12 of 15 columns compared with training. 2 columns are not monitored. 1 date column is shown but not judged.");
  assert.equal(scopeText({ features_compared: 4 }), "4 columns compared with training.");
  assert.equal(scopeText({}), null);
});

test("score movement and its unavailable reasons", () => {
  const lines = scoreSentences({ available: true, psi: 0.15, status: "watch", flagged_share: 0.18, flagged_share_reference: 0.12, flagged_status: "watch" }, T);
  assert.match(keep(lines[0]), /scores moved somewhat \(PSI 0\.15\)/);
  assert.equal(keep(lines[1]), "The share of rows flagged is 18% this week vs 12% during training, a change big enough to watch.");
  assert.equal(keep(scoreSentences({ available: false, reason_code: "too_few_scores", n_scores: 40 })[0]), "Too few scores this week to compare (40).");
  for (const r of ["no_reference", "scores_missing", "__proto__", undefined]) keep(scoreSentences({ available: false, reason_code: r })[0]);
  assert.match(scoreSentences(null)[0], /could not be compared/);
});

test("S4: real-world results — coverage of rows with an ID, left-out values, spellings, a skipped file, comparability", () => {
  const measured = outcomeView({
    status: "measured", n_labelled: 140, n_keyed: 400, coverage: 0.35, metric: "pr_auc", metric_value: 0.6,
    metrics: { precision: 0.5, recall: 0.8, f1: null }, cv_reference: { metric: "pr_auc", value: 0.7 }, unrecognised_outcome_values: 3,
    outcome_spellings: "training_labels", refused_sets: [{ outcome_set_id: "x", reason_code: "key_not_unique" }], label_positive_rate: 0.2, positive_rate_reference: 0.15,
  }, T);
  assert.equal(measured.kind, "measured");
  if (measured.kind === "measured") {
    assert.equal(keep(measured.lead), "Measured on 140 of 400 scored rows with an ID (35%).");
    assert.deepEqual(measured.metrics, [{ label: "Precision", value: "0.500" }, { label: "Recall", value: "0.800" }]);
    assert.deepEqual(measured.notes, [
      MAY_DIFFER, "20% of them had the outcome, vs 15% of the training rows.", "PR AUC on these real outcomes is 0.600; during training (cross-validation) it was 0.700.",
      "3 outcome values did not match the training outcome labels and were left out.", "Only the training outcome's own labels count as outcomes.", REFUSED_SET,
    ]);
    measured.notes.forEach(keep);
  }
  const noCv = outcomeView({ status: "measured", n_labelled: 100, n_keyed: 100, coverage: 1, metric: "precision", metric_value: 0.5, metrics: {} }, T);
  assert.ok(noCv.notes.includes("Precision on these real outcomes is 0.500. No cross-validation figure is shown for it: it would not be directly comparable."));
  assert.ok(!noCv.notes.includes(MAY_DIFFER), "full coverage has no caveat");
  const few = outcomeView({ status: "not_enough_labels", n_labelled: 40, unrecognised_outcome_values: 1, outcome_spellings: "yes_no_tokens" }, T);
  assert.match(keep(few.lead), /Not enough labelled rows yet: 40 .* recognised real outcome; at least 100 .* at least 10 of each outcome/);
  assert.deepEqual(few.notes, ["1 outcome value did not match the training outcome labels and was left out.", "This model was saved before its outcome labels were recorded, so only yes/no style values count as outcomes."]);
  few.notes.forEach(keep);
  const reasons: Array<[Record<string, unknown> | null, RegExp]> = [
    [{ status: "not_linked" }, /No outcomes file is linked yet/],
    [{ status: "no_key_in_scores" }, /no ID column/],
    [{ status: "key_not_unique", side: "outcomes", refused_sets: [{}] }, /IDs repeat in the linked outcomes files/],
    [{ status: "key_not_unique", side: "scores" }, /IDs repeat in this week's scored rows/],
    [{ status: "not_enough_labels", n_labelled: 0, outcome_sets_tried: 2 }, /could not be matched to this week's scored rows yet/],
    [{ status: "__proto__" }, /not available for this week/],
    [null, /not available for this week/],
  ];
  for (const [o, text] of reasons) {
    const v = outcomeView(o, T);
    assert.equal(v.kind, "reason");
    assert.match(keep(v.lead), text);
  }
  assert.deepEqual(outcomeView({ status: "key_not_unique", side: "outcomes", refused_sets: [{}] }, T).notes, [], "the skipped-file note is not repeated under the same reason");
  keep(REAL_NOT_TEST);
  assert.equal(metricLabel("__proto__"), "proto");
  assert.equal(metricLabel("ROC_AUC"), "ROC AUC");
});

test("role gating: only people who can run ML work, never while a confirm step is open", () => {
  assert.equal(canAct({ canWrite: true, confirming: false }), true);
  assert.equal(canAct({ canWrite: true, confirming: true }), false);
  assert.equal(canAct({ canWrite: false, confirming: false }), false);
});

test("S7 + S2: go back only on an alert of the model in use, to the newest earlier approved model; none means no button", () => {
  const rows = [{ goBack: false, modelVersionId: "m3", model: "Model v3" }, { goBack: true, modelVersionId: "m2", model: "Model v2" }, { goBack: true, modelVersionId: "m1", model: "Model v1" }];
  const inUse = { weekModelId: "m3", inUseId: "m3" };
  for (const status of ["ok", "watch", "not_enough_data", "no_windows", "__proto__"]) assert.equal(goBackOffer(status, rows, inUse).kind, "hidden");
  assert.equal(goBackOffer("alert", rows, { weekModelId: "m2", inUseId: "m3" }).kind, "hidden", "an alert of another model offers nothing");
  assert.equal(goBackOffer("alert", rows, { weekModelId: null, inUseId: null }).kind, "hidden");
  const offer = goBackOffer("alert", rows, inUse);
  assert.equal(offer.kind, "offer");
  if (offer.kind === "offer") {
    assert.equal(offer.modelVersionId, "m2");
    assert.equal(keep(offer.text), "If these differences mean the model in use should not be used for now, you can go back to the previous approved model, Model v2. Check whether the data source or the population scored has changed first.");
    assert.match(keep(offer.viewerText), /^The previous approved model is Model v2\. A person who can change this workspace can go back to it/);
  }
  const none = goBackOffer("alert", rows.slice(0, 1), inUse);
  assert.equal(none.kind, "none");
  if (none.kind === "none") keep(none.text);
  assert.doesNotMatch(JSON.stringify([offer, none]), /retrain/i);
});

test("S6: link confirm says exactly what happens, what is rechecked, and that a link cannot be removed", () => {
  const words = linkConfirmWords({ datasetId: "d", file: "outcomes · q3‮.csv", key: "customer_code", outcome: "churned", rows: 1200, uploadedAt: "2026-10-12T09:30:00Z" }, T, "alert");
  assert.equal(words.title, "Link outcomes · q3 .csv as real outcomes?");
  assert.equal(words.lines[0], "File: outcomes · q3 .csv · 1,200 rows · uploaded Mon, 12 Oct 2026, 09:30 UTC.");
  assert.match(words.lines[1], /joins the outcomes in churned to the rows the model in use scored, by matching customer_code/);
  assert.ok(words.lines.includes("The recent weeks of the model in use are checked again in the background; reload in a few minutes to see the result."));
  assert.ok(words.lines.includes("A link cannot be removed; the newest linked file that shares IDs with a week is the one used."));
  assert.ok(words.lines.some((l) => /never used to train or to choose a model, and linking them never switches the model in use/.test(l)));
  [words.title, words.button, ...words.lines].forEach(keep);
  const noWeeks = linkConfirmWords({ datasetId: "d", file: "f", key: "k", outcome: "o" }, T, "no_windows");
  assert.ok(noWeeks.lines.some((l) => /nothing is checked again now/.test(l)));
  assert.ok(!noWeeks.lines.some((l) => /reload in a few minutes/.test(l)));
  assert.ok(!noWeeks.lines.some((l) => /^File:/.test(l)), "no file facts when none are known");
  keep(linkConfirmWords({ datasetId: "d", file: "f", key: "k", outcome: "o" }, T, "no_model_in_use").lines[2]);
});

test("linked files: newest first, who linked them without ids, plain text names, UTC, file facts when known", () => {
  const files = new Map([["d1", { name: "old.csv", rows: 50, uploadedAt: "2026-09-30T08:00:00Z" }]]);
  const rows = linkedRows([
    { id: "a", dataset_id: "d1", dataset_name: "old.csv", key_column: "id", outcome_column: "y", linked_at: "2026-10-01T10:00:00Z", linked_by_user_id: "u1" },
    { id: "b", dataset_name: null, key_column: "k\u0000", outcome_column: "y", linked_at: "2026-10-02T10:00:00Z", linked_by_user_id: "u2" },
    { id: "c", dataset_name: "token.csv", key_column: "id", outcome_column: "y", linked_at: "2026-09-01T10:00:00Z", linked_by_user_id: null },
  ], "u1", files);
  assert.deepEqual(rows.map((r) => r.id), ["b", "a", "c"]);
  assert.equal(rows[0].file, "A file of this project");
  assert.equal(rows[0].key, "k");
  assert.equal(rows[0].line, "Linked Fri, 2 Oct 2026, 10:00 UTC by a person.");
  assert.equal(rows[1].line, "Linked Thu, 1 Oct 2026, 10:00 UTC by you. File: 50 rows · uploaded Wed, 30 Sept 2026, 08:00 UTC.".replace("Sept", rows[1].line.includes("Sept") ? "Sept" : "Sep"));
  assert.match(rows[2].line, /^Linked Tue, 1 Sept? 2026, 10:00 UTC\.$/);
  rows.forEach((r) => keep(r.line));
});

test("S5: link refusals are fixed sentences by code, then status; never the server's message; unconfirmed failures claim nothing", () => {
  const codes = ["outcomes_from_training_data", "key_column_unknown", "outcome_column_unknown", "columns_must_differ", "already_linked", "rate_limited", "monitoring_disabled",
    "dataset_not_found", "dataset_not_in_project", "dataset_not_uploaded", "dataset_not_published", "human_session_required", "service_token_not_permitted", "capability_denied",
    "idempotency_key_conflict", "concurrent_conflict", "validation_failed", "__proto__", "constructor"];
  for (const code of codes) {
    const e = mapLinkError(err(409, code));
    assert.ok(e.title && e.detail, code);
    assert.doesNotMatch(`${e.title} ${e.detail}`, /SERVER INTERNAL WORDS/);
    keep(`${e.title}. ${e.detail}`);
  }
  assert.equal(mapLinkError(err(409, "already_linked")).title, "This file is already linked with these columns");
  assert.equal(mapLinkError(err(409, "already_linked")).detail, "Check the list of linked files.");
  assert.match(mapLinkError(err(429, "rate_limited")).detail, /more than 10 files linked to this project in the last hour/);
  assert.match(mapLinkError(err(429, "")).detail, /more than 10 files/);
  assert.match(mapLinkError(err(409, "dataset_not_published")).title, /not ready yet/);
  assert.deepEqual(mapLinkError(err(500, "internal_error")), UNCONFIRMED);
  assert.deepEqual(mapLinkError(new TypeError("Failed to fetch")), UNCONFIRMED);
  assert.doesNotMatch(`${UNCONFIRMED.title} ${UNCONFIRMED.detail}`, /Nothing changed|was not linked/);
  for (const status of [400, 403, 404, 409, 412, 422, 428, 429, 500]) keep(mapLinkError(err(status, "")).title);
  assert.equal(isLinkConflict(err(409, "already_linked")), true);
  assert.equal(isLinkConflict(err(422, "key_column_unknown")), true);
  assert.equal(isLinkConflict(err(409, "concurrent_conflict", true)), false);
  assert.equal(isLinkConflict(err(500, "internal_error")), false);
  for (const status of [413, 403, 500]) keep(mapUploadError(err(status, "")).title);
  assert.match(mapUploadError(err(422, "upload_rejected")).title, /could not be read as a table/);
  assert.equal(mapUploadError(err(500, "__proto__")).title, "Could not confirm the upload");
});

test("UI-E trends: weeks oldest first and once each, at most 12; a missing week is a gap, never a zero", () => {
  const ws = ["2026-09-21", "2026-10-05", "2026-09-28", "2026-10-05", "bad"].map((d, i) => week({ window_start: d === "bad" ? "not a date" : `${d}T00:00:00Z`, max_psi: 0.05 + i / 100 }));
  assert.deepEqual(weekTicks(ws).map((w) => w.key), ["2026-09-21", "2026-09-28", "2026-10-05"]);
  assert.equal(shortWeek("2026-10-05T00:00:00Z"), "5 Oct");
  assert.equal(shortWeek("nope"), "—");
  const many = Array.from({ length: 15 }, (_, i) => week({ window_start: new Date(Date.UTC(2026, 0, 5 + 7 * i)).toISOString() }));
  assert.equal(weekTicks(many).length, 12);
  assert.equal(weekTicks(many)[11].key, "2026-04-13");
  const series = [{ name: "plan", kind: "categorical", points: [{ window_start: "2026-09-21T00:00:00Z", psi: 0.31, status: "alert" }, { window_start: "2026-09-28T00:00:00Z", psi: null }] }];
  const chart = psiTrend(ws, series, null, T);
  assert.deepEqual(chart.lines.map((l) => l.id), ["column", "largest"]);
  assert.deepEqual(chart.lines[0].values, [0.31, null, null]);
  assert.deepEqual(chart.lines[1].values.map((v) => v?.toFixed(2)), ["0.05", "0.07", "0.06"]); // the first entry of a repeated week wins (newest-first reads)
  assert.deepEqual(chart.refs.map((r) => r.value), [0.1, 0.2]);
  assert.deepEqual(chart.rows[1], ["28 Sep", "—", "0.07"]);
  assert.match(keep(chart.summary), /^Input change \(PSI\) by week, oldest first\. plan: 21 Sep 0\.31, 28 Sep —, 5 Oct —\. Largest change of any column: /);
  assert.match(keep(chart.summary), /Watch line 0\.10, alert line 0\.20; crossing a line counts only when the statistical test agrees\.$/);
  assert.equal(chart.note, null);
});

test("UI-E trends: the axis stops at 3x the alert line and says so; one week has no trend; no series draws only the largest", () => {
  const big = psiTrend([week({ max_psi: 2.5 })], [], null, T);
  assert.equal(big.max, 0.6000000000000001);
  assert.equal(big.lines.length, 1);
  assert.match(keep(big.note ?? ""), /Only one week has been checked so far/);
  assert.match(big.note ?? "", /No column has a weekly series yet/);
  assert.match(big.note ?? "", /drawn at the top; the table has the exact number/);
  assert.equal(psiTrend([], undefined, null, T).summary, keep("No week has been checked yet."));
});

test("UI-E trends: column names are untrusted plain text, capped; dates and broken entries are not columns; a bad choice falls back", () => {
  const series = [
    { name: "a\u202enasty\nname" + "x".repeat(90), kind: "numeric", points: [] },
    { name: "signup_date", kind: "date", points: [] },
    null as unknown as { name: string; points: [] },
    { name: "__proto__", kind: "numeric", points: [{ window_start: MON, psi: -1 }] },
  ];
  const cols = seriesColumns(series);
  assert.deepEqual(cols.map((c) => c.index), [0, 3]);
  assert.ok(cols[0].name.length <= 60 && !/[\u202e\n]/.test(cols[0].name));
  assert.equal(cols[1].name, "__proto__");
  const chart = psiTrend([week()], series, 3, T);
  assert.equal(chart.lines[0].label, "__proto__");
  assert.deepEqual(chart.lines[0].values, [null]); // a negative PSI is not drawn
  assert.equal(psiTrend([week()], series, 1, T).lines[0].label, cols[0].name); // a date column cannot be picked
});

test("UI-E real precision and recall: values only in 0..1, gaps for missing weeks, each empty reason distinct", () => {
  const ws = [
    { ...week({ window_start: "2026-10-05T00:00:00Z", outcomes_status: "not_enough_labels", n_labelled: 20 }) },
    { ...week({ window_start: "2026-09-28T00:00:00Z", outcomes_status: "measured", n_labelled: 1400 }), outcome_precision: 0.48, outcome_recall: 0.8 },
    { ...week({ window_start: "2026-09-21T00:00:00Z", outcomes_status: "measured", n_labelled: 900 }), outcome_precision: 1.4, outcome_recall: 0.79 },
  ];
  assert.equal(realTrendEmpty(ws), null);
  const chart = realTrend(ws);
  assert.deepEqual(chart.lines.map((l) => l.values), [[null, 0.48, null], [0.79, 0.8, null]]);
  assert.deepEqual(chart.rows[1], ["28 Sep", "0.48", "0.80", "1,400"]);
  assert.match(keep(chart.note ?? ""), /^2 of 3 weeks have no result yet/);
  assert.match(realTrend(ws.slice(1)).note ?? "", /^1 of 2 weeks has no result yet; it is left as a gap|^1 of 2 weeks has no result yet/);
  keep(chart.summary);
  keep(REAL_TREND_NOTE);
  const empties = [
    realTrendEmpty([week({ outcomes_status: "measured" })]),
    realTrendEmpty([week({ outcomes_status: "not_linked" })]),
    realTrendEmpty([week({ outcomes_status: "not_enough_labels" })]),
    realTrendEmpty([]),
  ];
  assert.equal(new Set(empties.slice(0, 3)).size, 3);
  assert.match(empties[0] ?? "", /only for yes\/no goals/);
  assert.match(empties[1] ?? "", /No outcomes file is linked yet/);
  empties.forEach((e) => keep(e ?? ""));
  assert.doesNotMatch(REAL_TREND_NOTE, /final test/i);
});

test("UI-E + D5c steps: weekly checks and the alert rule show their real state; the retrain steps are passed in; going back is a person's", () => {
  const offer = { kind: "offer" as const, modelVersionId: M_USE, model: "Model v2", text: "", viewerText: "" };
  const extra = [{ key: "retrain", title: "Retrain on newest data", who: "Off for this project", state: "off" as const, pill: "Off", tone: "gray" as const, text: "x" }];
  const alert = ownSteps(overview({ status: "alert", latest: week({ status: "alert" }) }), offer, extra);
  assert.deepEqual(alert.map((s) => s.key), ["check", "flag", "retrain", "back"]);
  assert.deepEqual(alert.map((s) => s.state), ["on", "on", "off", "yours"]);
  assert.ok(!alert.some((s) => s.state === "planned"), "nothing is Planned: every step is built");
  assert.match(alert[0].text, /latest check was the week of Mon, 5 Oct 2026 \(UTC\)\. A week is checked only after the model in use scores a file in it\./);
  assert.equal(alert[0].title, "Check each week the model in use scores a file");
  // S1: the alert rule's pill follows the status tone (never green on alert / watch / not enough data).
  assert.equal(alert[1].tone, "crit");
  assert.equal(ownSteps(overview({ status: "watch", latest: week({ status: "watch" }) }), { kind: "hidden" })[1].tone, "warn");
  assert.equal(ownSteps(overview({ status: "not_enough_data", latest: week({ status: "not_enough_data" }) }), { kind: "hidden" })[1].tone, "gray");
  assert.equal(ownSteps(overview(), { kind: "hidden" })[1].tone, "ok");
  assert.match(ownSteps(overview(), { kind: "hidden" })[2].text, /When an earlier approved model exists/);
  assert.match(alert[1].text, /^Alert in the week of .*look different from training\. It is also listed in the Inbox\.$/);
  assert.match(alert[3].text, /Model v2/);
  // D5c: a week that alerts because real outcomes fell below a floor is never called drift.
  const floor = ownSteps(overview({ status: "alert", latest: week({ status: "alert", summary_code: "below_floor" }) }), { kind: "hidden" });
  assert.equal(floor[1].pill, "Alert: below a floor");
  assert.match(floor[1].text, /real outcomes fell below a floor set for this project\. This is not drift\./);
  assert.doesNotMatch(floor[1].text.replace("This is not drift.", ""), /drift/i);
  const paused = ownSteps(overview({ enabled: false }), { kind: "hidden" });
  assert.equal(paused[0].pill, "Paused");
  const none = ownSteps(overview({ status: "no_model_in_use", latest: null, windows: [] }), { kind: "none", text: "" });
  assert.equal(none[0].pill, "Waiting");
  assert.equal(none[1].state, "off");
  assert.match(none[2].text, /no earlier approved model/);
  const fresh = ownSteps(overview({ status: "no_windows", latest: null, windows: [] }), { kind: "hidden" });
  assert.match(fresh[0].text, /first week is checked/);
  assert.equal(ownSteps(undefined, { kind: "hidden" })[0].pill, "Unknown");
  const odd = ownSteps(overview({ status: "__proto__", latest: week({ status: "__proto__" }) }), { kind: "hidden" });
  assert.match(odd[1].text, /does not recognise/);
  for (const status of ["ok", "watch", "not_enough_data"]) {
    const steps = ownSteps(overview({ status, latest: week({ status }) }), { kind: "hidden" });
    assert.notEqual(steps[1].text, alert[1].text);
    steps.forEach((s) => { keep(s.text); keep(s.title); keep(s.pill); keep(s.who); });
  }
  [...alert, ...floor, ...paused, ...none, ...fresh, ...odd].forEach((s) => { keep(s.text); keep(s.title); keep(s.pill); keep(s.who); });
  for (const s of [...alert, ...none]) assert.doesNotMatch(s.text, /\bautomatic(ally)? (retrain|switch)/i);
});

test("D5c: the trend judges each week with its own lines and says when the drawn lines differ", () => {
  const used = { version: 2, psi_watch: 0.3, psi_alert: 0.6 };
  const ws = [week({ window_start: "2026-09-28T00:00:00Z", status: "ok", max_psi: 0.25, thresholds_used: used }), week({ window_start: "2026-10-05T00:00:00Z", status: "ok", max_psi: 0.25 })];
  const chart = psiTrend(ws, [], null, T, { psi_watch: 0.1, psi_alert: 0.2 });
  assert.deepEqual(chart.lines[0].marks, ["judged", "chance"]); // 0.25 is below its own watch line 0.3, above the default 0.1
  assert.match(keep(chart.note ?? ""), /some weeks were judged with other lines \(the weekly table shows the values each week used\)/);
  assert.doesNotMatch(psiTrend([week({ max_psi: 0.25 })], [], null, T).note ?? "", /other lines/);
});

test("D5c: a floor alert week reads as real outcomes below a floor, never as drift; rows use the week's own lines and floor verdict", () => {
  const s = weekSentence(week({ status: "alert", summary_code: "below_floor", max_psi: 0.04 }), T);
  assert.match(keep(s), /real outcomes of the model in use fell below a precision or recall floor/);
  assert.doesNotMatch(s.replace("This is not drift", ""), /drift|look different/i);
  const used = { version: 2, psi_watch: 0.3, psi_alert: 0.6, precision_floor: 0.5, recall_floor: null };
  const [row] = weekRows([week({ status: "ok", max_psi: 0.25, thresholds_used: used, floor_status: "not_judged" })], T);
  assert.equal(row.inputs, "0.25"); // below this week's own watch line 0.3, so not "within chance"
  assert.deepEqual([row.floor, row.floorTone], ["Not judged", "gray"]);
  const [legacy] = weekRows([week({ status: "ok", max_psi: 0.25, thresholds_used: null })], T, { psi_watch: 0.1, psi_alert: 0.2 });
  assert.equal(legacy.inputs, "0.25 (within chance)");
  assert.equal(legacy.floor, "No floor");
  assert.equal(weekRows([week({ status: "alert", summary_code: "below_floor", floor_status: "below" })], T)[0].label, "Alert: below a floor");
  assert.deepEqual(floorWords("below"), { label: "Below a floor", tone: "crit" });
  assert.deepEqual(floorWords("__proto__"), { label: "Unknown", tone: "gray" });
  assert.deepEqual(weekLines({ thresholds_used: used }, T), { ...T, psi_watch: 0.3, psi_alert: 0.6 });
  assert.equal(weekLines({ thresholds_used: null }, T), T);
});

test("UI-E B1: the trend follows the statistical gate: an ok week or point above a line is within chance; a not-enough-data week is not judged", () => {
  const ws = [
    week({ window_start: "2026-09-14T00:00:00Z", status: "ok", max_psi: 0.35 }), // above the alert line, but the test said chance
    week({ window_start: "2026-09-21T00:00:00Z", status: "not_enough_data", summary_code: "too_few_rows", max_psi: 0.9, n_rows: 12 }),
    week({ window_start: "2026-09-28T00:00:00Z", status: "watch", max_psi: 0.14 }),
    week({ window_start: "2026-10-05T00:00:00Z", status: "alert", max_psi: 0.31 }),
  ];
  const pts = (statuses: Array<[number | null, string | null]>) => ["2026-09-14", "2026-09-21", "2026-09-28", "2026-10-05"].map((d, i) => ({ window_start: `${d}T00:00:00Z`, psi: statuses[i][0], status: statuses[i][1] }));
  const series = [{ name: "plan", kind: "categorical", points: pts([[0.15, "ok"], [0.9, null], [0.14, "watch"], [0.31, "alert"]]) }];
  const chart = psiTrend(ws, series, null, T);
  const [col, largest] = chart.lines;
  assert.deepEqual(col.marks, ["chance", "not_judged", "judged", "judged"]);
  assert.deepEqual(largest.marks, ["chance", "not_judged", "judged", "judged"]);
  assert.deepEqual(largest.values, [0.35, null, 0.14, 0.31]); // the not-judged week is left off the line
  assert.equal(trendCell(largest, 0), "0.35 (within chance)");
  assert.equal(trendCell(largest, 1), "0.90 (not judged: too little data)");
  assert.equal(trendCell(col, 0), "0.15 (within chance)");
  assert.equal(trendCell(col, 3), "0.31");
  assert.deepEqual(chart.rows[0], ["14 Sep", "0.15 (within chance)", "0.35 (within chance)"]);
  assert.match(chart.summary, /14 Sep 0\.35 \(within chance\)/);
  assert.match(chart.summary, /21 Sep 0\.90 \(not judged: too little data\)/);
  assert.match(keep(chart.note ?? ""), /hollow dot is above a line but within what chance allows/);
  assert.match(chart.note ?? "", /too little data is not judged and is left off the lines; the table has its number/);
  // An ok point below the watch line is plain; a point without a status in a judged week has no mark.
  const quiet = psiTrend([week({ status: "ok", max_psi: 0.04 })], [{ name: "x", points: [{ window_start: MON, psi: 0.04, status: null }] }], null, T);
  assert.deepEqual(quiet.lines[0].marks, [null]);
  assert.deepEqual(quiet.lines[1].marks, ["judged"]);
  assert.equal(quiet.note, "Only one week has been checked so far, so there is no trend yet.");
});

test("UI-E S3: outcomes that cannot be joined say so, distinct from not linked and not enough rows", () => {
  const msg = realTrendEmpty([week({ outcomes_status: "key_not_unique" }), week({ outcomes_status: "not_linked" }), week({ outcomes_status: "no_key_in_scores" })]);
  assert.match(keep(msg ?? ""), /could not be joined to the scored rows/);
  assert.notEqual(msg, realTrendEmpty([week({ outcomes_status: "not_linked" })]));
  assert.notEqual(msg, realTrendEmpty([week({ outcomes_status: "key_not_unique" }), week({ outcomes_status: "not_enough_labels" })]));
});

test("banned words: no sentence template or aria label uses internal words", () => {
  const labels = ["Show: week of Mon, 5 Oct 2026 (UTC)", "Shown below: week of Mon, 5 Oct 2026 (UTC)", "Go back to the previous model: Model v2", "Judged columns in plain words, largest change first",
    "Date columns, shown but not judged", "Linked outcomes files, newest first", "Weekly checks of the model in use, newest first", "Outcomes file", "Watch and alert lines (read-only)",
    "Real-world results", "Largest input change (PSI)", "Score change (PSI)", "Link real outcomes", "Link this file", "Upload a new outcomes file (optional)",
    "Only people who can run ML work in this workspace can link an outcomes file.",
    "Input change by week", "Real precision and recall by week", "What the system does on its own", "and where it stops for you", "Lines in the chart", "The numbers as a table", "Column",
    "Largest change of any column", "Nothing on this screen changes the model in use by itself. Switching models, or going back, is always done by a person and saved in History."];
  assert.ok(sentences.length > 80, `collected ${sentences.length}`);
  for (const s of [...sentences, ...labels]) assert.doesNotMatch(s, BANNED, s);
});
