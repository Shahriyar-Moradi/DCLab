import assert from "node:assert/strict";
import test from "node:test";

import {
  USAGE_FORBIDDEN_TEXT,
  USAGE_HONEST_NOTES,
  aiSpendView,
  dailySeries,
  formatDuration,
  formatGb,
  formatMoney,
  formatUtcDate,
  freshnessText,
  noteTexts,
  NO_AI_BUDGET_TEXT,
  NO_ALLOWANCE_TEXT,
  quotaBar,
  quotaBars,
  usageScreenState,
  type UsageQuota,
} from "./studio-usage.ts";

const BANNED = /\b(holdout|champion|digest|refs?|stale|L[0-3]|critic|verifier|proposer|jev|governance|decision record|experiment|candidate|fold|cross-validation)\b/i;
const q = (over: Partial<UsageQuota>): UsageQuota => ({ key: "runs_per_day", limit: 10, over: false, percent: 0, period: "day", resets_at: null, used: 0, warning: false, enforced: true, unit: "runs", ...over });

test("screen states: loading, forbidden, error, empty, ready; a failed read is never empty", () => {
  const data = { quotas: [q({})], daily: [] } as never;
  const base = { loaded: true, pending: false, status: null, failed: false, data };
  assert.equal(usageScreenState({ ...base, loaded: false }), "loading");
  assert.equal(usageScreenState({ ...base, pending: true }), "loading");
  assert.equal(usageScreenState({ ...base, status: 403, failed: true, data: undefined }), "forbidden");
  assert.equal(usageScreenState({ ...base, status: 500, failed: true, data: undefined }), "error");
  assert.equal(usageScreenState({ ...base, data: undefined }), "error");
  assert.equal(usageScreenState({ ...base, data: { quotas: [], daily: [] } as never }), "empty");
  assert.equal(usageScreenState(base), "ready");
});

test("duration in plain words incl. 0, singular, rollover and huge values", () => {
  assert.equal(formatDuration(0), "0 seconds");
  assert.equal(formatDuration(1), "1 second");
  assert.equal(formatDuration(59), "59 seconds");
  assert.equal(formatDuration(60), "1 minute");
  assert.equal(formatDuration(3600), "1 hour");
  assert.equal(formatDuration(3600 + 90), "1 hour 2 min");
  assert.equal(formatDuration(3600 + 1790 + 10), "1 hour 30 min");
  assert.equal(formatDuration(3599.9), "1 hour");
  assert.equal(formatDuration(3570), "1 hour");
  assert.equal(formatDuration(3540), "59 minutes");
  assert.equal(formatDuration(1e12), "277,777,777 hours 47 min");
  for (const bad of [null, undefined, NaN, -5, "x", Infinity]) assert.equal(formatDuration(bad), "unavailable");
});

test("bytes in GB, money in whole units", () => {
  assert.equal(formatGb(0), "0.0 GB");
  assert.equal(formatGb(50 * 1024 ** 3), "50.0 GB");
  assert.equal(formatGb(1.5 * 1024 ** 3), "1.5 GB");
  assert.equal(formatGb(null), "unavailable");
  assert.equal(formatGb(5 * 1024 ** 2), "5.0 MB");
  assert.equal(formatGb(1e18), "931,322,574.6 GB");
  assert.equal(formatMoney(1_234_500, "USD"), "1.23 USD");
  assert.equal(formatMoney(0, ""), "0.00 (billing currency)");
  assert.equal(formatMoney(NaN, "USD"), "unavailable");
  assert.equal(formatUtcDate("2026-11-01T00:00:00Z"), "Nov 1, 2026 (UTC)");
  assert.equal(formatUtcDate("nope"), "date unknown");
});

test("quota bar: ok, warning at 80, over, unlimited (null limit), no invented percent", () => {
  const ok = quotaBar(q({ used: 2, percent: 20 }));
  assert.equal(ok.state, "ok");
  assert.equal(ok.percentText, "20%");
  assert.equal(ok.usedText, "2 runs");
  assert.equal(ok.stateText, "Within the limit");
  const warn = quotaBar(q({ used: 8, percent: 80, warning: true }));
  assert.equal(warn.state, "warning");
  assert.match(warn.stateText, /80%/);
  const over = quotaBar(q({ used: 10, percent: 100, over: true }));
  assert.equal(over.state, "over");
  assert.equal(over.stateText, "Limit reached");
  const huge = quotaBar(q({ used: 30, percent: 300, over: true }));
  assert.equal(huge.width, 100);
  assert.equal(huge.percentText, "300%");
  const free = quotaBar(q({ limit: null, percent: null, used: 5 }));
  assert.equal(free.state, "unlimited");
  assert.equal(free.limitText, "no limit");
  assert.equal(free.percentText, "");
  assert.equal(free.width, 0);
  const zero = quotaBar(q({ used: 0, percent: 0 }));
  assert.equal(zero.usedText, "0 runs");
  assert.equal(zero.width, 0);
  assert.match(quotaBar(q({ enforced: false })).enforcedNote ?? "", /does not refuse/);
});

test("quota bars: only the three shown limits, in a fixed order, with units in words and reset dates", () => {
  const bars = quotaBars([
    q({ key: "members", enforced: false }),
    q({ key: "storage_bytes", used: 10 * 1024 ** 3, limit: 50 * 1024 ** 3, percent: 20, period: "none" }),
    q({ key: "compute_seconds_month", used: 7200, limit: 36000, percent: 20, period: "month", resets_at: "2026-11-01T00:00:00Z" }),
    q({ key: "runs_per_day" }),
  ]);
  assert.deepEqual(bars.map((b) => b.key), ["compute_seconds_month", "storage_bytes", "runs_per_day"]);
  assert.equal(bars[0].usedText, "2 hours");
  assert.equal(bars[0].limitText, "10 hours");
  assert.equal(bars[0].resetText, "Starts again Nov 1, 2026 (UTC)");
  assert.equal(bars[1].usedText, "10.0 GB");
  assert.deepEqual(quotaBars([]), []);
  assert.equal(quotaBar(q({ key: "__proto__" })).label, "__proto__");
  assert.equal(quotaBar(q({ key: "constructor" })).label, "constructor");
});

test("limit 0 is 'no allowance', not 0%; members shown only when enforced; AI budget 0 is 'no budget'", () => {
  const zero = quotaBar(q({ key: "compute_seconds_month", limit: 0, used: 0, percent: null, over: true }));
  assert.equal(zero.state, "over");
  assert.equal(zero.stateText, "Limit reached");
  assert.equal(zero.limitText, NO_ALLOWANCE_TEXT);
  assert.equal(zero.percentText, "");
  assert.equal(zero.noAllowance, true);
  assert.equal(zero.width, 100);
  assert.equal(quotaBars([q({ key: "members", enforced: false }), q({ key: "members", enforced: true, limit: 5 })]).length, 1);
  const none = aiSpendView({ currency: "USD", hard_stop: true, limit_micros: 0, over: false, percent: null, period_start: "x", reserved_micros: 0, spent_micros: 0, warning: false })!;
  assert.equal(none.state, "none");
  assert.equal(none.stateText, NO_AI_BUDGET_TEXT);
  assert.equal(none.percentText, "");
  assert.equal(none.width, 0);
  assert.match(freshnessText("2026-10-16T01:05:00Z"), /last updated Oct 16, 2026.*01:05.*\(UTC\)/);
  assert.equal(freshnessText(null), "Daily totals have not been recorded yet.");
  assert.deepEqual(noteTexts([USAGE_HONEST_NOTES[0], "Server only", "Server only"]), ["Server only"]);
  assert.equal(usageScreenState({ loaded: true, hasWorkspace: false, pending: true, status: null, failed: false, data: undefined }), "no_workspace");
});

test("AI spend: null stays null; warning and over states", () => {
  assert.equal(aiSpendView(null), null);
  const spend = { currency: "USD", hard_stop: true, limit_micros: 10_000_000, over: false, percent: 85.04, period_start: "2026-10-01", reserved_micros: 0, spent_micros: 8_504_000, warning: true };
  const warn = aiSpendView(spend)!;
  assert.equal(warn.state, "warning");
  assert.equal(warn.spentText, "8.50 USD");
  assert.equal(warn.percentText, "85%");
  assert.match(warn.hardStopText, /refused/);
  assert.equal(warn.stateText, "Warning: close to the AI budget");
  const over = aiSpendView({ ...spend, over: true, warning: false, percent: 140, hard_stop: false })!;
  assert.equal(over.state, "over");
  assert.equal(over.width, 100);
  assert.match(over.hardStopText, /flagged/);
});

test("daily series: scaled to its maximum; all zero draws nothing; text is cleaned", () => {
  const s = dailySeries([{ day: "2026-10-01", compute_seconds: 0 }, { day: "2026-10-02", compute_seconds: 3600 }, { day: "2026-10-03", compute_seconds: 1 }]);
  assert.equal(s.max, 3600);
  assert.deepEqual(s.bars.map((b) => b.height > 0), [false, true, true]);
  assert.equal(s.bars[1].height, 100);
  assert.equal(s.bars[2].height, 2);
  assert.match(s.summary, /last 3 days \(UTC\)/);
  const zero = dailySeries([{ day: "2026-10-01", compute_seconds: 0 }]);
  assert.equal(zero.allZero, true);
  assert.equal(zero.bars[0].height, 0);
  assert.equal(dailySeries([]).summary, "No days to show.");
  assert.equal(dailySeries([{ day: "__proto__", compute_seconds: -4 }]).bars[0].seconds, 0);
});

test("notes are plain text; fixed texts contain no ML vocabulary", () => {
  assert.deepEqual(noteTexts(["a\n<b>b</b>", ""]), ["a <b>b</b>"]);
  assert.deepEqual(noteTexts("x"), []);
  const texts = [USAGE_FORBIDDEN_TEXT, ...USAGE_HONEST_NOTES];
  const bars = quotaBars([q({ key: "compute_seconds_month", over: true }), q({ key: "storage_bytes", warning: true }), q({ limit: null })]);
  for (const bar of bars) texts.push(bar.label, bar.stateText, bar.usedText, bar.limitText, bar.resetText, bar.enforcedNote ?? "");
  const ai = aiSpendView({ currency: "USD", hard_stop: true, limit_micros: 1, over: true, percent: 1, period_start: "x", reserved_micros: 0, spent_micros: 1, warning: false })!;
  texts.push(ai.stateText, ai.hardStopText);
  for (const text of texts) assert.doesNotMatch(text, BANNED, text);
});
