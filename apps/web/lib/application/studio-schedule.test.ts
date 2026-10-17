import assert from "node:assert/strict";
import test from "node:test";
import { isUuid } from "./command-search.ts";
import { whatWords } from "./studio-history.ts";
import { failureError, historyRows } from "./studio-scoring.ts";
import {
  ABOUT_LINES, REASON_MAX, UNKNOWN_OUTCOME, WEEKDAYS, confirmWords, ignoredUploadsText, isConflict, localHint, mapScheduleError, modelUsed, nextOccurrence, nextRunText, ownerWarning, reasonToSend,
  runRows, runSentence, savedText, scheduleAction, scheduleView, slotText, utcWhen, type ScheduleLike, type ScheduleStateLike,
} from "./studio-schedule.ts";
import { ActionKeys } from "./studio-wizard.ts";

const BANNED = /\b(slot|tick|release|ref|digest|champion|worker)s?\b/i;
const M1 = "11111111-1111-4111-8111-111111111111";
const P1 = "22222222-2222-4222-8222-222222222222";
const NOW = new Date("2026-10-13T12:00:00Z");
const sched = (extra: Partial<ScheduleLike> = {}): ScheduleLike => ({ weekday: 0, hour_utc: 9, enabled: true, next_planned_for: "2026-10-19T09:00:00Z", owner_can_score: true, ...extra });
const OUTCOMES = ["queued", "skipped_no_new_file", "skipped_no_model_in_use", "skipped_no_approval", "skipped_owner_not_allowed", "skipped_missed", "failed_to_queue", "__proto__", "constructor", "brand_new_code"];
const REASONS = [null, "run_limit", "worker_was_down", "owner_missing", "model_not_scoreable", "dataset_unavailable", "release_changed", "internal_error", "__proto__"];
const APPROVED = modelUsed({ inUseId: M1, inUseName: "Model v2", approvedId: M1, checked: true });
const WARNING = modelUsed({ inUseId: M1, inUseName: "Model v2", approvedId: null, checked: true });
const UNKNOWN = modelUsed({ inUseId: null, inUseName: null, approvedId: null, checked: false });
const state = (status: string, schedule: ScheduleLike | null): ScheduleStateLike => ({ status, schedule });
const view = (st: ScheduleStateLike | undefined, model = APPROVED, extra: { pending?: boolean; failed?: boolean } = {}) => scheduleView({ pending: false, failed: false, state: st, model, ...extra });

test("every state is distinct: loading, failed, not set up, on, on but idle, off, paused", () => {
  assert.equal(scheduleView({ pending: true, failed: false, state: undefined }).kind, "loading");
  assert.equal(scheduleView({ pending: false, failed: true, state: undefined }).kind, "error");
  assert.equal(view(state("off", null)).kind, "not_set_up");
  assert.equal(view(state("on", sched())).kind, "on");
  const off = view(state("off", sched({ enabled: false })));
  assert.equal(off.kind, "off");
  assert.match(off.kind === "off" ? off.sentence : "", /every Monday at 09:00 UTC/);
  const paused = view(state("paused_by_platform", sched()));
  assert.equal(paused.kind, "paused");
  assert.match(paused.kind === "paused" ? paused.sentence : "", /paused by the platform/);
  assert.equal(nextRunText(paused, sched(), NOW), "Next run: none while it is paused by the platform.");
  assert.equal(nextRunText(off, sched({ enabled: false }), NOW), null);
  assert.equal(scheduleView({ pending: false, failed: false, state: state("future_status", sched()), model: APPROVED }).kind, "on", "an unknown status string does not break the card");
});

test("on but it will score nothing: no approved model or an owner who can no longer score; never a plain 'on'", () => {
  const idle = view(state("on", sched()), WARNING);
  assert.equal(idle.kind, "on_idle");
  assert.match(idle.kind === "on_idle" ? idle.sentence : "", /^Weekly scoring is on, but it will score nothing until the model in use is approved for scoring/);
  const owner = view(state("on", sched({ owner_can_score: false })), APPROVED);
  assert.equal(owner.kind, "on_idle");
  assert.match(owner.kind === "on_idle" ? owner.sentence : "", /until someone who can run ML work in this workspace saves the schedule again/);
  assert.equal(view(state("on", sched()), UNKNOWN).kind, "on", "no claim of 'will score nothing' while the model could not be checked");
  assert.equal(scheduleView({ pending: false, failed: false, state: state("on", sched()), model: null }).kind, "on");
  // off with an owner who cannot score: no warning at all
  assert.equal(ownerWarning(sched({ enabled: false, owner_can_score: false })), null);
  assert.equal(view(state("off", sched({ enabled: false, owner_can_score: false }))).kind, "off");
  assert.match(ownerWarning(sched({ owner_can_score: false })) ?? "", /can no longer score/);
  assert.equal(ownerWarning(sched()), null);
});

test("slot text, next run (also overdue) and the local-time hint always say UTC", () => {
  assert.equal(slotText(0, 9), "Every Monday at 09:00 UTC");
  assert.equal(slotText(6, 23), "Every Sunday at 23:00 UTC");
  assert.equal(slotText(9, 30), "Every week at —:00 UTC");
  assert.equal(WEEKDAYS.length, 7);
  assert.equal(utcWhen("2026-10-19T09:00:00Z"), "Mon, 19 Oct 2026, 09:00 UTC");
  assert.equal(utcWhen("nope"), "—");
  const on = view(state("on", sched()));
  assert.equal(nextRunText(on, sched(), NOW), "Next run: Mon, 19 Oct 2026, 09:00 UTC.");
  // overdue: more than 5 minutes in the past is "was due", never "Next run"
  const late = nextRunText(on, sched({ next_planned_for: "2026-10-12T09:00:00Z" }), NOW);
  assert.equal(late, "Was due Mon, 12 Oct 2026, 09:00 UTC; it has not started. If it is not handled within 24 hours that week is skipped.");
  assert.match(nextRunText(on, sched({ next_planned_for: "2026-10-13T11:57:00Z" }), NOW) ?? "", /^Next run:/, "inside the grace period");
  assert.equal(nextRunText(on, sched({ next_planned_for: null }), NOW), null);
  assert.equal(nextOccurrence(0, 9, NOW).toISOString(), "2026-10-19T09:00:00.000Z");
  assert.equal(nextOccurrence(1, 13, NOW).toISOString(), "2026-10-13T13:00:00.000Z");
  assert.equal(nextOccurrence(1, 9, NOW).toISOString(), "2026-10-20T09:00:00.000Z");
  assert.equal(localHint(0, 9, NOW, (d) => d.toISOString()), "The next run is 2026-10-19T09:00:00.000Z where you are; local time shifts with daylight saving, UTC does not.");
  assert.equal(localHint(7, 9, NOW, () => "x"), null);
});

test("each run outcome reads as one plain sentence; only failed_to_queue is a failure; an unknown code has a fixed fallback", () => {
  assert.match(runSentence("queued"), /^Started scoring the newest file/);
  assert.doesNotMatch(runSentence("queued"), /^Scored/);
  assert.match(runSentence("skipped_no_new_file"), /no person uploaded a file for scoring after weekly scoring was first set up/);
  assert.match(runSentence("skipped_no_approval"), /not approved for scoring/);
  assert.match(runSentence("skipped_owner_not_allowed"), /can no longer score/);
  assert.match(runSentence("skipped_missed"), /did not get to it within 24 hours/);
  assert.match(runSentence("skipped_missed"), /never reached have no row/);
  assert.match(runSentence("failed_to_queue", "run_limit"), /^Failed: the scoring could not be started \(too many jobs were running\)/);
  assert.match(runSentence("failed_to_queue", "release_changed"), /model in use changed before the run started/);
  assert.match(runSentence("failed_to_queue", "__proto__"), /^Failed: the scoring could not be started\. Nothing was scored\.$/);
  assert.equal(runSentence("__proto__"), UNKNOWN_OUTCOME);
  assert.equal(runSentence("brand_new_code"), UNKNOWN_OUTCOME);
  for (const outcome of OUTCOMES) for (const reason of REASONS) {
    const text = runSentence(outcome, reason);
    assert.doesNotMatch(text, BANNED, `${outcome}/${reason}`);
    if (outcome !== "failed_to_queue") assert.doesNotMatch(text, /failed/i);
  }
});

test("a queued week is only 'started' until its prediction says more: scoring, done with the list link, or failed with the reason", () => {
  const runs = (id: string) => [{ id: "a", planned_for: "2026-10-12T09:00:00Z", outcome: "queued", batch_prediction_id: id }];
  const one = (pred?: Parameters<typeof runRows>[2] extends ReadonlyMap<string, infer V> | undefined ? V : never, id = P1) => runRows(runs(id), isUuid, pred ? new Map([[id, pred]]) : new Map())[0];
  assert.match(one().sentence, /^Started scoring/);
  assert.equal(one().predictionId, null, "no list link before it completed");
  assert.match(one({ status: "queued" }).sentence, /^Scoring the newest file/);
  assert.match(one({ status: "running" }).sentence, /^Scoring the newest file/);
  assert.equal(one({ status: "running" }).predictionId, null);
  const done = one({ status: "completed", rows_out: 120 });
  assert.equal(done.sentence, "Done: 120 rows scored.");
  assert.equal(done.predictionId, P1);
  const failed = one({ status: "failed", error_code: "schedule_owner_not_allowed", error_message: "server text" });
  assert.equal(failed.failed, true);
  assert.equal(failed.predictionId, null);
  assert.match(failed.sentence, /^Failed: This week was not scored\. The person who set up this schedule can no longer score, so this week was not scored/);
  assert.match(one({ status: "failed", error_code: "release_mismatch" }).sentence, /model in use changed before this scheduled run started/);
  assert.equal(one({ status: "done?" }).sentence, runSentence("queued"));
  assert.equal(one({ status: "completed" }, "not-a-uuid").predictionId, null);
  const rows = runRows([
    { id: "a", planned_for: "2026-10-12T09:00:00Z", outcome: "queued", batch_prediction_id: P1 },
    { id: "c", planned_for: "2026-09-28T09:00:00Z", outcome: "skipped_missed", batch_prediction_id: P1 },
  ], isUuid, new Map([[P1, { status: "completed", rows_out: 3 }]]));
  assert.deepEqual(rows.map((r) => r.predictionId), [P1, null]);
  assert.equal(rows[0].week, "Mon, 12 Oct 2026, 09:00 UTC");
  assert.equal(runRows(Array.from({ length: 30 }, (_, i) => ({ id: String(i), planned_for: "2026-10-12T09:00:00Z", outcome: "skipped_no_new_file" })), isUuid).length, 10);
});

test("the model it would use: approved, warning, or could not check (never 'no approved model' when unchecked)", () => {
  assert.deepEqual(APPROVED, { kind: "approved", text: "Model v2 — approved for scoring" });
  assert.match(WARNING.text, /No approved model is in use: nothing will be scored until one is approved/);
  assert.equal(modelUsed({ inUseId: null, inUseName: null, approvedId: M1, checked: true }).kind, "warning");
  assert.equal(modelUsed({ inUseId: M1, inUseName: "Model v2", approvedId: "other", checked: true }).kind, "warning");
  assert.deepEqual(UNKNOWN, { kind: "unknown", text: "Could not check which model would be used." });
  assert.equal(modelUsed({ inUseId: M1, inUseName: "x", approvedId: M1, checked: false }).kind, "unknown");
  assert.doesNotMatch(confirmWords({ enabled: true, weekday: 0, hour: 9 }, UNKNOWN).lines.join(" "), /No approved model/);
  assert.match(confirmWords({ enabled: true, weekday: 0, hour: 9 }, UNKNOWN).lines.join(" "), /Could not check which model would be used/);
});

test("buttons only for people who can run ML and not while a confirm step is open", () => {
  assert.equal(scheduleAction({ canWrite: false, confirming: false }), false);
  assert.equal(scheduleAction({ canWrite: true, confirming: true }), false);
  assert.equal(scheduleAction({ canWrite: true, confirming: false }), true);
});

test("the confirm step says which file, what it replaces, what is paused, and what will not happen", () => {
  const on = confirmWords({ enabled: true, weekday: 4, hour: 8 }, APPROVED, { current: sched() });
  const text = on.lines.join(" ");
  assert.match(text, /Every Friday at 08:00 UTC, DCLab will score the newest file uploaded for scoring after weekly scoring was first set up and after the last week it scored \(never the file the model learned from or one it already scored\)\. Only one file is scored per week; older new files in the same week are not scored\./);
  assert.match(text, /acting as you/);
  assert.match(text, /Right now that is Model v2\./);
  assert.match(text, /This replaces: every Monday at 09:00 UTC\./);
  assert.match(text, /does not retrain, does not fetch data and does not switch models/);
  assert.match(text, /no approved model in use, nothing is scored/);
  assert.match(text, /Only files uploaded by a person are scored; files a connected tool uploaded with a service token are ignored\. A file uploaded with a person's own sign-in or own API token counts as that person's\./);
  assert.doesNotMatch(text, /access token/);
  assert.match(text, /lose access to run ML work/);
  assert.match(text, /latest missed week within 24 hours/);
  assert.doesNotMatch(text, /paused/);
  assert.match(confirmWords({ enabled: true, weekday: 4, hour: 8 }, APPROVED, { current: sched({ enabled: false }) }).lines.join(" "), /This replaces: every Monday at 09:00 UTC \(currently off\)\./);
  assert.doesNotMatch(confirmWords({ enabled: true, weekday: 4, hour: 8 }, APPROVED).lines.join(" "), /This replaces/);
  const paused = confirmWords({ enabled: true, weekday: 4, hour: 8 }, APPROVED, { paused: true }).lines.join(" ");
  assert.match(paused, /paused by the platform: you can save it, but nothing is scored until the platform turns it on/);
  const off = confirmWords({ enabled: false, weekday: 0, hour: 9 }, WARNING);
  assert.equal(off.title, "Turn off weekly scoring?");
  assert.match(off.lines.join(" "), /A scoring that has already started still finishes/);
  for (const w of [on, off, confirmWords({ enabled: true, weekday: 2, hour: 0 }, WARNING, { paused: true }), confirmWords({ enabled: true, weekday: 2, hour: 0 }, UNKNOWN)]) {
    assert.doesNotMatch(`${w.title} ${w.button} ${w.lines.join(" ")}`, BANNED);
  }
});

test("the reason is omitted when none was typed (the API writes its own marked default); typed text is capped", () => {
  assert.equal(reasonToSend("  "), undefined);
  assert.equal(reasonToSend(""), undefined);
  assert.equal(reasonToSend(" because "), "because");
  assert.equal(reasonToSend(` ${"y".repeat(REASON_MAX + 5)} `)?.length, REASON_MAX);
});

test("the done message follows the state the save returned", () => {
  assert.equal(savedText({ enabled: true, weekday: 0, hour: 9 }, "on"), "Weekly scoring is saved: every Monday at 09:00 UTC. The change was saved in History.");
  assert.match(savedText({ enabled: true, weekday: 0, hour: 9 }, "paused_by_platform"), /saved \(every Monday at 09:00 UTC\), but it is paused by the platform: nothing is scored until the platform turns it on/);
  assert.match(savedText({ enabled: false, weekday: 0, hour: 9 }, "off"), /^Weekly scoring is off/);
  assert.equal(whatWords("scoring_schedule_changed"), "Changed weekly scoring");
});

test("one key per action: a failed retry of the same request keeps its key, a changed reason or time gets a new one", () => {
  let n = 0;
  const keys = new ActionKeys(() => `k${++n}`);
  const fp = (reason: string, hour = 9) => `true:0:${hour}:${reason}`;
  const first = keys.keyFor("save", fp(""));
  assert.equal(keys.keyFor("save", fp("")), first, "retry after a network failure");
  assert.notEqual(keys.keyFor("save", fp("because")), first, "a changed reason is a new action");
  const second = keys.keyFor("save", fp("because"));
  assert.notEqual(keys.keyFor("save", fp("because", 10)), second, "a changed hour is a new action");
  keys.done("save");
  assert.notEqual(keys.keyFor("save", fp("because", 10)), second, "after success the next save is new");
});

const env = (status: number, code: string, retryable = false) => ({ status, body: { error: { code, message: "server words: secret-ish internal text", retryable, details: {} } } });
test("save refusals are fixed plain sentences by code then status; a retryable conflict keeps the key", () => {
  const t = (e: unknown) => mapScheduleError(e).title;
  assert.equal(t(env(403, "human_session_required")), "Only a signed-in person can change the schedule");
  assert.equal(t(env(403, "service_token_not_permitted")), "Only a signed-in person can change the schedule");
  assert.equal(t(env(403, "forbidden")), "You do not have permission to change weekly scoring");
  assert.equal(t({ status: 403 }), "You do not have permission to change weekly scoring");
  assert.equal(t(env(404, "project_not_found")), "This project was not found");
  assert.equal(t(env(409, "anything")), "The schedule was changed by someone else");
  assert.equal(t(env(409, "concurrent_conflict", true)), "Another save was in progress");
  assert.equal(t(env(412, "precondition_failed")), "The schedule was changed by someone else");
  assert.equal(t(env(422, "validation_failed")), "The schedule could not be saved");
  assert.equal(t(env(400, "idempotency_key_required")), "The change could not be sent");
  assert.equal(t(env(400, "something_else")), "The request could not be understood");
  assert.equal(t(env(409, "idempotency_key_conflict")), "Your change may already be saved");
  assert.equal(t(env(500, "__proto__")), "The schedule was not saved");
  assert.equal(t(env(500, "constructor")), "The schedule was not saved");
  assert.equal(t(new Error("Failed to fetch")), "The schedule was not saved");
  assert.equal(isConflict(env(409, "x")), true);
  assert.equal(isConflict(env(412, "precondition_failed")), true);
  assert.equal(isConflict(env(409, "concurrent_conflict", true)), false, "same request, same key");
  assert.equal(isConflict(env(409, "other", true)), false);
  assert.equal(isConflict(env(500, "x")), false);
  for (const probe of [env(403, "forbidden"), env(409, "x"), env(409, "concurrent_conflict", true), env(422, "validation_failed"), env(400, "idempotency_key_required"), env(400, "x"), env(429, "r"), null, new Error("x")]) {
    const e = mapScheduleError(probe);
    assert.doesNotMatch(`${e.title} ${e.detail}`, /server words|secret-ish/);
    assert.doesNotMatch(`${e.title} ${e.detail}`, BANNED);
  }
});

test("no sentence of any state or the static paragraph uses a banned word", () => {
  const states = [
    state("off", null), state("off", sched({ enabled: false, owner_can_score: false })), state("on", sched()), state("on", sched({ owner_can_score: false })), state("paused_by_platform", sched()),
    state("on", sched({ next_planned_for: "2026-10-01T09:00:00Z" })),
  ];
  for (const st of states) for (const model of [APPROVED, WARNING, UNKNOWN, null]) {
    const v = scheduleView({ pending: false, failed: false, state: st, model });
    const text = [v.kind === "loading" || v.kind === "error" ? "" : v.sentence, nextRunText(v, st.schedule, NOW), ownerWarning(st.schedule), localHint(0, 9, NOW, () => "Monday 09:00 GMT")].join(" ");
    assert.doesNotMatch(text, BANNED, JSON.stringify(st));
  }
  for (const m of [APPROVED, WARNING, UNKNOWN]) assert.doesNotMatch(m.text, BANNED);
  assert.doesNotMatch(ABOUT_LINES.join(" "), BANNED);
  assert.match(ABOUT_LINES.join(" "), /Only one file is scored per week; older new files in the same week are not scored\./);
  assert.match(ABOUT_LINES.join(" "), /Only files uploaded by a person are scored; files a connected tool uploaded with a service token are ignored\. A file uploaded with a person's own sign-in or own API token counts as that person's\./);
  assert.doesNotMatch(ABOUT_LINES.join(" "), /access token/);
  for (const n of [1, 3]) assert.doesNotMatch(ignoredUploadsText(sched({ ignored_token_uploads: n })) ?? "", BANNED);
});

test("files from connected tools that the schedule ignored are counted in plain words, never invented", () => {
  assert.equal(ignoredUploadsText(sched({ ignored_token_uploads: 1 })), "1 file a connected tool uploaded with a service token was not scored. Only files uploaded by a person are scored.");
  assert.equal(ignoredUploadsText(sched({ ignored_token_uploads: 4 })), "4 files connected tools uploaded with a service token were not scored. Only files uploaded by a person are scored.");
  for (const bad of [0, -1, 1.5, undefined]) assert.equal(ignoredUploadsText(sched({ ignored_token_uploads: bad })), null);
  assert.equal(ignoredUploadsText(sched({ ignored_token_uploads: "2" as unknown as number })), null);
  assert.equal(ignoredUploadsText(null), null);
});

test("predictions: scheduled rows say so in UTC, name no fake file, and show why a scheduled scoring failed", () => {
  const owner = failureError("schedule_owner_not_allowed", "server text");
  assert.match(owner.detail, /person who set up this schedule can no longer score, so this week was not scored/);
  assert.match(failureError("release_mismatch", "x", true).detail, /model in use changed before this scheduled run started/);
  assert.match(failureError("release_mismatch", "x", false).detail, /Ask someone who can change this workspace/);
  const entries = [{ id: P1, fileName: "Chosen by the weekly schedule (file not named here)", modelVersionId: M1, scheduled: true }, { id: "33333333-3333-4333-8333-333333333333", fileName: "a.csv", modelVersionId: M1 }];
  const reads = new Map([
    [P1, { data: { id: P1, model_version_id: M1, status: "failed", created_at: "2026-10-12T09:00:05Z", scheduled: true, scheduled_for: "2026-10-12T09:00:00Z", error_code: "schedule_owner_not_allowed" } }],
    [entries[1].id, { data: { id: entries[1].id, model_version_id: M1, status: "completed", created_at: "2026-10-11T09:00:05Z", scheduled: false } }],
  ]);
  const rows = historyRows(entries, reads, () => "Model v1", () => null, { when: (iso) => `local-${iso}`, utc: utcWhen, inUseNow: () => true });
  assert.equal(rows[0].origin, "Scheduled run for Mon, 12 Oct 2026, 09:00 UTC");
  assert.match(rows[0].origin, /UTC$/);
  assert.equal(rows[0].by, "The weekly schedule");
  assert.equal(rows[0].file, "Chosen by the weekly schedule (file not named here)");
  assert.match(rows[0].failure ?? "", /This week was not scored\. The person who set up this schedule can no longer score/);
  assert.equal(rows[1].origin, "By hand");
  assert.equal(rows[1].by, "You, in this browser tab");
  assert.equal(rows[1].failure, null);
});
