import assert from "node:assert/strict";
import test from "node:test";
import {
  CAPS_READ_ONLY, NO_CUSTOMER_ROWS, OPERATOR_TABS, TIMES_UTC, ageText, capsItems, checkLabel, checkName, checkSentence, checkTone, codeText,
  failedJobRows, failureSentence, failuresView, jobStatusLabel, jobTypeLabel, jobsPivot, pageSentence, queueSentence, readFailure, readinessView,
  nextPageAction, requiredText, signInHref, statusTag, tabFromQuery, workersView, workspaceKind, workspaceRows, type CapsLike, type CheckLike,
} from "./operator.ts";
import { OPERATOR_FEATURES, operatorNavigationForUser, studioNavigationForUser } from "./studio-navigation.ts";

const BANNED = /\b(release|champion|ref|digest|manifest|package|slot|tick|revision)s?\b/i;
const W1 = "11111111-1111-4111-8111-111111111111";
const J1 = "22222222-2222-4222-8222-222222222222";
const sentences: string[] = [];
const keep = (s: string) => { sentences.push(s); return s; };

const CAPS: CapsLike = {
  ai_enabled: false, max_data_class: "aggregates", sample_values_per_column: 0, user_text_to_jev: false, auto_release: false,
  model_allowlist: ["gpt-6-luna", "gpt-6.1-sol", "jev-1.13.0"],
  budgets: { workspace_month_micros: 25_000_000, project_month_micros: 12_000_000, alert_fraction: 0.8, hard_stop: true, new_budget_micros: 5 },
};

test("tabs: three working tabs, three planned ones that name what is missing; unknown ?tab= opens Overview", () => {
  assert.deepEqual(OPERATOR_TABS.map((t) => [t.id, t.status]), [
    ["overview", "working"], ["workspaces", "working"], ["failed-jobs", "working"], ["quarantine", "planned"], ["caps", "planned"], ["benchmarks", "planned"],
  ]);
  assert.match(OPERATOR_TABS[3].planned ?? "", /data safety/);
  // No internal plan id (such as P8.7 or R1) reaches the screen.
  for (const tab of OPERATOR_TABS) assert.doesNotMatch(tab.planned ?? "", /\b(P\d+\.\d+|R\d)\b/);
  for (const t of OPERATOR_TABS.filter((t) => t.status === "planned")) keep(t.planned ?? "");
  assert.deepEqual(statusTag("working"), { label: "Working today", tone: "ok" });
  assert.deepEqual(statusTag("planned"), { label: "Planned", tone: "gray" });
  assert.equal(tabFromQuery("workspaces"), "workspaces");
  for (const bad of [null, undefined, "", "Overview", "__proto__", "constructor", "../x", "failed_jobs"]) assert.equal(tabFromQuery(bad), "overview", String(bad));
});

test("ages, queue and worker sentences come from the fields", () => {
  assert.equal(ageText(4.2), "4 s");
  assert.equal(ageText(185), "3 min");
  assert.equal(ageText(2 * 3600 + 5 * 60), "2 h 5 min");
  assert.equal(ageText(3 * 3600), "3 h");
  assert.equal(ageText(3 * 86400 + 5), "3 days");
  for (const bad of [null, undefined, -1, Number.NaN, Number.POSITIVE_INFINITY]) assert.equal(ageText(bad as number | null), "—");
  assert.equal(keep(queueSentence({ depth: 0, oldest_age_seconds: null })), "No job is waiting in the queue.");
  assert.equal(keep(queueSentence({ depth: 1, oldest_age_seconds: 75 })), "1 job is waiting in the queue. The oldest has waited 1 min.");
  assert.equal(keep(queueSentence({ depth: 1200, oldest_age_seconds: null })), "1,200 jobs are waiting in the queue.");

  const two = workersView({ dispatcher: "postgres", count: 2, heartbeat_age_seconds: [3, 61], visible: true });
  assert.match(keep(two.headline), /^2 workers are connected/);
  assert.deepEqual(two.lines, ["Worker 1: last seen 3 s ago.", "Worker 2: last seen 1 min ago."]);
  const hidden = workersView({ dispatcher: "postgres", count: 0, heartbeat_age_seconds: [], visible: false });
  assert.match(keep(hidden.headline), /^Unknown: .*This does not mean no worker runs\.$/);
  assert.equal(hidden.label, "Unknown");
  const none = workersView({ dispatcher: "postgres", count: 0, heartbeat_age_seconds: [], visible: true });
  assert.match(keep(none.headline), /No worker process is connected/);
  assert.equal(none.tone, "crit");
  const thread = workersView({ dispatcher: "thread", count: 0, heartbeat_age_seconds: [], visible: true });
  assert.match(keep(thread.headline), /inside the API process/);
  keep(workersView({ dispatcher: "postgres", count: 1, heartbeat_age_seconds: [5], visible: true }).headline);
});

test("jobs of the last 24 h pivot by type; unknown statuses add up in Other; codes are own keys only", () => {
  const pivot = jobsPivot([
    { job_type: "auto_train", status: "completed", count: 3 }, { job_type: "auto_train", status: "failed", count: 1 },
    { job_type: "batch_predict", status: "queued", count: 7 }, { job_type: "batch_predict", status: "paused_new", count: 2 },
    { job_type: "__proto__", status: "constructor", count: 1 },
  ]);
  assert.equal(pivot.total, 14);
  assert.deepEqual(pivot.rows.map((r) => [r.type, r.label, r.total]), [["batch_predict", "Scoring", 9], ["auto_train", "Training run", 4], ["__proto__", "Other job type", 1]]);
  assert.equal(pivot.rows[0].counts.queued, 7);
  assert.equal(pivot.rows[0].other, 2);
  assert.equal(pivot.rows[2].other, 1);
  assert.equal(keep(pivot.sentence), "14 jobs were queued in the last 24 hours, of 3 types.");
  assert.equal(keep(jobsPivot([]).sentence), "No job was queued in the last 24 hours.");
  assert.equal(keep(jobsPivot([{ job_type: "monitoring", status: "running", count: 1 }]).sentence), "1 job was queued in the last 24 hours, of 1 type.");
  for (const code of ["auto_train", "batch_predict", "monitoring", "agent_run", "auth_cleanup", "__proto__", "toString", "new_type"]) keep(jobTypeLabel(code));
  assert.equal(jobTypeLabel("toString"), "Other job type");
  for (const s of ["queued", "running", "completed", "failed", "cancelled", "__proto__", "x"]) keep(jobStatusLabel(s));
  assert.equal(jobStatusLabel("__proto__"), "Other");
  assert.equal(codeText("models.batch_predict"), "models.batch_predict");
  assert.equal(codeText("bad‮code\nx"), "bad code x");
  assert.equal(codeText(""), "—");
});

test("failure codes become fixed sentences; the truncated flag is stated as a lower bound", () => {
  const codes = ["feature_contract_failed", "unsupported_transform", "unsupported_model", "scoring_failed", "dataset_unavailable", "release_mismatch",
    "schedule_owner_not_allowed", "heartbeat_expired", "unknown_handler", "auto_train_failed", "see_logs"];
  for (const code of codes) assert.doesNotMatch(keep(failureSentence(code)), /does not recognise/, code);
  for (const code of ["__proto__", "constructor", "hasOwnProperty", "brand_new_code", ""]) assert.match(keep(failureSentence(code)), /does not recognise/, code);
  assert.match(failureSentence("see_logs"), /free text that can hold customer values/);

  const full = failuresView([{ code: "see_logs", count: 2 }, { code: "scoring_failed", count: 5 }], false);
  assert.equal(full.total, 7);
  assert.equal(keep(full.sentence), "7 jobs failed in the last 24 hours.");
  assert.equal(keep(full.scope), "These counts cover every job that failed in the last 24 hours.");
  assert.deepEqual(full.items.map((i) => i.count), ["5", "2"]);
  const cut = failuresView([{ code: "see_logs", count: 5000 }], true);
  assert.equal(keep(cut.sentence), "At least 5,000 jobs failed in the last 24 hours.");
  assert.match(keep(cut.scope), /lower bound/);
  assert.equal(keep(failuresView([], false).sentence), "No job failed in the last 24 hours.");
  assert.equal(keep(failuresView([], true).sentence), "At least 0 jobs failed in the last 24 hours.");
  keep(failuresView([{ code: "x", count: 1 }], false).sentence);
});

test("readiness: every check is a named status with its reason code as a sentence; unknown values read as not recognised", () => {
  const cases: Array<[CheckLike, RegExp]> = [
    [{ name: "database", status: "ok", required: true, code: null }, /reaches the database/],
    [{ name: "database", status: "failing", required: true, code: "unreachable" }, /cannot reach the database/],
    [{ name: "migrations", status: "failing", required: true, code: "not_at_head" }, /run the migrations/],
    [{ name: "migrations", status: "unknown", required: true, code: "not_versioned" }, /no migration record/],
    [{ name: "migrations", status: "unknown", required: true, code: "scripts_unavailable" }, /cannot read its migration scripts/],
    [{ name: "migrations", status: "unknown", required: true, code: "database_unreachable" }, /Not checked: the database is unreachable/],
    [{ name: "object_storage", status: "failing", required: true, code: "unreachable" }, /cannot reach file storage/],
    [{ name: "object_storage", status: "ok", required: true }, /reaches file storage/],
    [{ name: "worker", status: "skipped", required: false, code: "in_process_dispatcher" }, /inside the API process/],
    [{ name: "worker", status: "unknown", required: false, code: "activity_not_visible" }, /does not show worker sessions/],
    [{ name: "worker", status: "stale", required: false, code: "no_worker_seen" }, /No worker process/],
    [{ name: "worker", status: "stale", required: false, code: "heartbeat_old" }, /too long ago/],
    [{ name: "worker", status: "ok", required: false, code: null }, /seen recently/],
    [{ name: "worker", status: "failing", required: false, code: "__proto__" }, /reason code this page does not recognise/],
    [{ name: "__proto__", status: "ok", required: false, code: null }, /^Working\.$/],
    [{ name: "database", status: "__proto__", required: true, code: null }, /does not recognise this check's status/],
  ];
  for (const [check, expected] of cases) assert.match(keep(checkSentence(check)), expected, JSON.stringify(check));
  assert.equal(checkName("object_storage"), "File storage");
  assert.equal(checkName("__proto__"), "Other check");
  assert.equal(checkTone("failing"), "crit");
  assert.equal(checkTone("__proto__"), "gray");
  assert.equal(checkLabel("skipped"), "Not checked");
  assert.equal(checkLabel("constructor"), "Unknown");
  assert.equal(requiredText(true), "Required");
  assert.equal(requiredText(false), "Not required");
  for (const s of ["ready", "degraded", "not_ready", "__proto__", "x"]) keep(readinessView(s).sentence);
  assert.equal(readinessView("not_ready").tone, "crit");
  assert.match(readinessView("not_ready").sentence, /503/);
  // The backend answers degraded whenever no required check fails and any check is not ok/skipped (a required one that is unknown included).
  assert.equal(readinessView("degraded").sentence, "No required check is failing, but at least one check is stale or unknown (see below).");
  assert.equal(readinessView("ready").sentence, "No check is failing, stale or unknown; checks marked Not checked do not apply on this server.");
  assert.doesNotMatch(readinessView("degraded").sentence, /other check|required checks pass/);
  assert.equal(readinessView("__proto__").label, "Unknown");
});

test("platform AI caps: plain words, read-only, unknown budget keys counted but never named", () => {
  const caps = capsItems(CAPS);
  const byKey = Object.fromEntries(caps.items.map((i) => [i.key, i]));
  assert.equal(byKey.ai.value, "Off");
  assert.match(byKey.data.value, /never rows/);
  assert.equal(byKey.samples.value, "0");
  assert.equal(byKey.auto.value, "No: a person always approves");
  assert.equal(byKey.models.value, "gpt-6-luna, gpt-6.1-sol, jev-1.13.0");
  assert.equal(byKey["budget:workspace_month_micros"].value, "25.00 (billing currency)");
  assert.equal(byKey["budget:alert_fraction"].value, "80% of a budget");
  assert.equal(byKey["budget:hard_stop"].value, "Yes");
  assert.equal(caps.unknownBudgets, 1);
  assert.ok(!caps.items.some((i) => i.key.includes("new_budget")));
  const odd = capsItems({ ...CAPS, max_data_class: "__proto__", ai_enabled: true, auto_release: true, model_allowlist: [], budgets: JSON.parse('{"__proto__": 1, "constructor": 2}') as Record<string, number> });
  const oddKey = Object.fromEntries(odd.items.map((i) => [i.key, i]));
  assert.match(oddKey.data.value, /does not recognise/);
  assert.equal(oddKey.ai.value, "On");
  assert.equal(oddKey.models.value, "None");
  assert.equal(odd.unknownBudgets, 2);
  for (const item of [...caps.items, ...odd.items]) { keep(item.label); keep(item.value); }
  keep(CAPS_READ_ONLY);
});

test("workspace rows: names are capped plain text, ids only when UUIDs; kinds are own keys", () => {
  const long = "Acme <script>alert(1)</script>‮" + "x".repeat(200);
  const rows = workspaceRows([
    { id: W1, name: long, kind: "business", created_at: "2026-10-05T09:00:00Z", member_count: 3, project_count: 2, jobs_last_30d: 9, failed_jobs_last_24h: 1, active_last_7d: true },
    { id: "not-a-uuid", name: "   ", kind: "__proto__", created_at: "nope", member_count: 0, project_count: 0, jobs_last_30d: 0, failed_jobs_last_24h: 0, active_last_7d: false },
  ]);
  assert.equal(rows[0].name.length, 80);
  assert.ok(rows[0].name.startsWith("Acme <script>alert(1)</script> x"));
  assert.ok(!rows[0].name.includes("‮"));
  assert.equal(rows[0].id, W1);
  assert.equal(rows[0].shortId, "11111111");
  assert.equal(rows[0].created, "Mon, 5 Oct 2026, 09:00 UTC");
  assert.equal(rows[0].kind, "Business");
  assert.equal(rows[0].active, "Yes");
  assert.equal(rows[1].id, null);
  assert.equal(rows[1].shortId, "—");
  assert.equal(rows[1].name, "(no name)");
  assert.equal(rows[1].kind, "Other kind");
  assert.equal(rows[1].created, "—");
  assert.equal(workspaceKind("personal"), "Personal");
  assert.equal(workspaceKind("constructor"), "Other kind");
});

test("failed job rows: ids only when UUIDs, reason sentence from the code, UTC time", () => {
  const rows = failedJobRows([
    { id: J1, workspace_id: W1, job_type: "batch_predict", handler_key: "models.batch_predict", code: "release_mismatch", attempts: 2, failed_at: "2026-10-12T14:30:00Z" },
    { id: "x", workspace_id: "javascript:alert(1)", job_type: "__proto__", handler_key: "h", code: "__proto__", attempts: 1, failed_at: "" },
  ]);
  assert.deepEqual([rows[0].id, rows[0].workspaceId, rows[0].shortId, rows[0].workspaceShort], [J1, W1, "22222222", "11111111"]);
  assert.equal(rows[0].type, "Scoring");
  assert.equal(rows[0].handler, "models.batch_predict");
  assert.equal(rows[0].sentence, "The saved model no longer matches the one approved for scoring.");
  assert.equal(rows[0].when, "Mon, 12 Oct 2026, 14:30 UTC");
  assert.deepEqual([rows[1].id, rows[1].workspaceId, rows[1].type, rows[1].when], [null, null, "Other job type", "—"]);
  assert.match(rows[1].sentence, /does not recognise/);
  for (const r of rows) keep(r.sentence);
});

test("paging sentences and read failures use fixed words, never the server message", () => {
  assert.equal(keep(pageSentence(50, true, ["workspace", "workspaces"])), "Showing the first 50 workspaces. More are available.");
  assert.equal(keep(pageSentence(1, false, ["failed job", "failed jobs"])), "Showing all 1 failed job.");
  const err = (status: number, code: string) => ({ status, body: { error: { code, message: "SERVER INTERNAL WORDS release", retryable: false } } });
  assert.equal(readFailure(err(403, "capability_denied"), "overview").kind, "forbidden");
  assert.equal(readFailure({ status: 403, body: { detail: { code: "human_session_required" } } }, "overview").kind, "forbidden");
  assert.equal(readFailure(err(401, "x"), "overview").kind, "signed_out");
  assert.equal(readFailure(err(400, "invalid_cursor"), "workspaces").kind, "invalid_cursor");
  assert.match(readFailure(err(400, "invalid_cursor"), "workspaces").text, /Start again from the first page\.$/);
  assert.equal(readFailure(err(400, "validation_failed"), "workspaces").kind, "failed");
  assert.equal(readFailure(err(500, "__proto__"), "failed jobs").kind, "failed");
  assert.equal(readFailure(null, "overview").kind, "failed");
  assert.equal(readFailure({ status: 400, body: "nope" }, "overview").kind, "failed");
  for (const e of [err(403, "x"), err(401, "x"), err(400, "invalid_cursor"), err(500, "x")]) {
    const text = keep(readFailure(e, "workspaces").text);
    assert.doesNotMatch(text, /SERVER INTERNAL WORDS/);
  }
});

test("a failed next page: a refused cursor starts over from the first page; anything else retries the same page", () => {
  const err = (status: number, code: string) => ({ status, body: { error: { code, message: "x" } } });
  assert.deepEqual(nextPageAction(err(400, "invalid_cursor")), { label: "Start again from the first page", restart: true });
  for (const e of [err(500, "internal"), err(400, "validation_failed"), err(0, ""), null]) assert.deepEqual(nextPageAction(e), { label: "Try again", restart: false });
  for (const e of [err(400, "invalid_cursor"), err(500, "x")]) keep(nextPageAction(e).label);
  assert.equal(signInHref("overview"), "/login?next=%2Foperator");
  assert.equal(signInHref("workspaces"), "/login?next=%2Foperator%3Ftab%3Dworkspaces");
  assert.equal(signInHref("__proto__"), "/login?next=%2Foperator");
});

test("navigation: the console sidebar is for platform administrators only; Studio keeps one link for them", () => {
  const admin = { capabilities: { platform_read: true, platform_write: true } };
  const developer = { capabilities: { platform_read: true } };
  const owner = { capabilities: { development_access: true, workspace_execute_ml: true, workspace_manage_members: true } };
  const items = operatorNavigationForUser(admin).flatMap((g) => g.items);
  assert.deepEqual(items.map((i) => [i.label, i.href]), [["Operator console", "/operator"], ["Workspaces", "/operator?tab=workspaces"], ["Failed jobs", "/operator?tab=failed-jobs"]]);
  for (const who of [developer, owner, { capabilities: { platform_write: true } }, null]) assert.deepEqual(operatorNavigationForUser(who), []);
  assert.equal(OPERATOR_FEATURES.operator_quarantine, false);
  const studio = (who: { capabilities: Record<string, boolean> }) => studioNavigationForUser(who).flatMap((g) => g.items.map((i) => i.href));
  assert.ok(studio({ capabilities: { ...admin.capabilities, development_access: true } }).includes("/operator"));
  assert.ok(!studio({ capabilities: { ...developer.capabilities, development_access: true } }).includes("/operator"));
  assert.ok(!studio(owner).includes("/operator"));
  for (const i of items) keep(i.label);
});

test("banned words: no sentence template or aria label uses internal words", () => {
  const labels = [
    NO_CUSTOMER_ROWS, TIMES_UTC, CAPS_READ_ONLY, ...OPERATOR_TABS.map((t) => t.label),
    "Operator console sections", "Readiness checks", "Workers, freshest first", "Failed jobs by reason, most first",
    "Jobs queued in the last 24 hours by type and status", "Workspaces, oldest first", "Failed jobs, newest first",
    "Copy the id of workspace Acme", "Copy the job id 22222222", "Copy the workspace id of job 22222222", "Show more workspaces", "Show more failed jobs",
    "Platform admin pages", "Counts as of", "Plan and usage per workspace are not in this read yet, so they are not shown here. Each workspace's owners see their own usage in Studio settings.",
  ];
  assert.ok(sentences.length > 60, String(sentences.length));
  for (const s of [...sentences, ...labels]) assert.doesNotMatch(s, BANNED, s);
});
