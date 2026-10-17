"use client";

import { useRef, useState, type ReactNode } from "react";
import { Banner } from "@/components/studio/Banner";
import { Card } from "@/components/studio/Card";
import { Level } from "@/components/studio/Level";
import { PageGuide } from "@/components/studio/PageGuide";
import { PageHead } from "@/components/studio/PageHead";
import { Pill } from "@/components/studio/Pill";
import { formatWhen } from "@/app/components/studio-app/StudioParts";
import { useSession } from "@/lib/application";
import { plainText } from "@/lib/application/command-search";
import {
  LEVELS_LEGEND, LEVELS_NOTE, actorWords, aiStatus, automaticCell, changeKindWords, changeLine, dataWords, modelWords, parseDollars, pointName, reportTitle,
  settingOverride, settingsError, shownLevel, spendView, switchSubject, testedCell, withMonthlyBudget,
} from "@/lib/application/studio-ai-settings";
import { BETA_LABEL, money } from "@/lib/application/studio-assistant";
import { acceptPolicy, changeAiSwitch, proposePolicy, resolveReport, useGovernanceInvalidation, type StudioPolicyChange } from "@/lib/application/studio-assistant-hooks";
import { useGovernance, type StudioGovernance } from "@/lib/application/studio-home-hooks";
import { answerLines } from "@/lib/application/studio-inbox";
import { ActionKeys } from "@/lib/application/studio-wizard";
import { ApiError } from "@/lib/infrastructure/api-client";
import { newIdempotencyKey } from "@/lib/infrastructure/v1/client";

/** One write at a time per form, with the action's Idempotency-Key reused on retry and a fixed error sentence. */
function useCommand() {
  const keys = useRef(new ActionKeys(newIdempotencyKey)).current;
  const flight = useRef(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const invalidate = useGovernanceInvalidation();
  const run = async <T,>(action: string, fingerprint: string, call: (key: string) => Promise<T>): Promise<T | null> => {
    if (flight.current) return null;
    flight.current = true;
    setBusy(true);
    setError(null);
    try {
      const result = await call(keys.keyFor(action, fingerprint));
      keys.done(action);
      invalidate();
      return result;
    } catch (caught) {
      setError(settingsError(caught));
      return null;
    } finally {
      flight.current = false;
      setBusy(false);
    }
  };
  return { busy, error, run };
}

/** A reason box + confirm button for one command (the reason is recorded as plain text). */
function ReasonForm({ label, confirm, tone, onSubmit, onCancel, busy, error, field = "Reason (recorded)" }: {
  label: string; confirm: string; tone?: "danger"; field?: string; busy: boolean; error: string | null; onSubmit: (reason: string) => void; onCancel: () => void;
}) {
  const [reason, setReason] = useState("");
  return (
    <form className="form" aria-label={label} onSubmit={(e) => { e.preventDefault(); if (reason.trim()) onSubmit(reason.trim()); }}>
      <label className="field"><span>{field}</span><textarea rows={2} maxLength={2000} required value={reason} disabled={busy} onChange={(e) => setReason(e.target.value)} /></label>
      <div className="toolbar">
        <button type="button" className="btn" disabled={busy} onClick={onCancel}>Back</button>
        <button type="submit" className={`btn ${tone === "danger" ? "danger" : "primary"}`} disabled={busy || !reason.trim()}>{busy ? "Saving…" : confirm}</button>
      </div>
      {error ? <Banner tone="crit">{error}</Banner> : null}
    </form>
  );
}

function OnOffCard({ g }: { g: StudioGovernance }) {
  const status = aiStatus(g);
  const viewer = g.viewer;
  const [mode, setMode] = useState<"off" | "on" | null>(null);
  const cmd = useCommand();
  const canOff = !!viewer?.can_switch_off && !status.workspaceOff;
  const canOn = !!viewer?.can_approve && status.workspaceOff && !status.held;
  return (
    <Card title="AI on or off" aside={<Pill tone={status.on ? "ok" : "gray"}>{status.on ? "AI: on" : "AI: off"}</Pill>}>
      <p data-testid="ai-status">{status.sentence}</p>
      <p>
        <b>Assistant beta:</b>{" "}
        {g.assistant_beta ? "on for this workspace. DCLab switched it on; it shows only while AI is on." : "off. DCLab offers the beta to chosen workspaces; everything works without it."}
      </p>
      <p className="small muted">Any owner or administrator can turn AI off at once. Turning it back on needs an approver, and DCLab must also have AI switched on.</p>
      {mode ? (
        <ReasonForm
          label={mode === "off" ? "Turn AI off now" : "Turn AI back on"} confirm={mode === "off" ? "Turn AI off now" : "Turn AI back on"} tone={mode === "off" ? "danger" : undefined}
          busy={cmd.busy} error={cmd.error} onCancel={() => setMode(null)}
          onSubmit={(reason) => void cmd.run(mode, `${mode}:${reason}`, (key) => changeAiSwitch({ state: mode, reason, key })).then((ok) => { if (ok) setMode(null); })}
        />
      ) : (
        <div className="toolbar">
          {canOff ? <button type="button" className="btn danger" onClick={() => setMode("off")}>Turn AI off now</button> : null}
          {canOn ? <button type="button" className="btn" onClick={() => setMode("on")}>Turn AI back on</button> : null}
        </div>
      )}
      {status.held ? <p className="small" role="note">An open problem report keeps AI off. Resolve it below first; resolving does not turn AI back on by itself.</p> : null}
    </Card>
  );
}

/** True when the API would refuse this viewer accepting the change: they proposed it and another approver exists. */
function ownProposal(proposedBy: string | null | undefined, myId: string | null, sole: boolean | undefined): boolean {
  return !!myId && proposedBy === `user:${myId}` && sole !== true;
}

function BudgetForm({ g, myId }: { g: StudioGovernance; myId: string | null }) {
  const [amount, setAmount] = useState("");
  const [reason, setReason] = useState("");
  const [proposed, setProposed] = useState<(StudioPolicyChange & { micros: number }) | null>(null);
  const [inputError, setInputError] = useState<string | null>(null);
  const [done, setDone] = useState<string | null>(null);
  const cmd = useCommand();
  const propose = () => {
    const parsed = parseDollars(amount);
    if ("error" in parsed) return setInputError(parsed.error);
    // The base is the workspace's own accepted rules (or the platform's when it has none), never the merged view.
    const base = g.policy_head ?? (g.policy && g.policy.workspace_version == null ? g.policy.document : null);
    const policy = withMonthlyBudget(base, parsed.micros);
    if (!policy) return setInputError("The AI rules could not be read, so the budget cannot be changed now.");
    if (!reason.trim()) return setInputError("A reason is required.");
    setInputError(null);
    void cmd.run("propose", `${parsed.micros}:${reason.trim()}`, (key) => proposePolicy({ policy, rationale: reason.trim(), key })).then((change) => { if (change) setProposed({ ...change, micros: parsed.micros }); });
  };
  const accept = () => {
    if (!proposed) return;
    void cmd.run("accept", proposed.id, (key) => acceptPolicy({ proposalId: proposed.id, digest: proposed.policy_digest, key })).then((ok) => {
      if (ok) { setProposed(null); setAmount(""); setReason(""); setDone("Saved. The new monthly budget applies now and is recorded in the change history."); }
    });
  };
  if (proposed) {
    // The server's diff against the workspace's rules (re-read after proposing); otherwise the one field this form changes.
    const fresh = g.policy_changes?.find((c) => c.id === proposed.id);
    const diff = fresh?.diff_vs_head ?? proposed.diff_vs_head;
    const lines = diff ? diff.map(changeLine) : [`Monthly AI budget: ${money(proposed.micros)} (new)`];
    const waiting = ownProposal(proposed.proposed_by ?? (myId ? `user:${myId}` : null), myId, g.sole_approver);
    return (
      <div className="form" aria-label="Review the budget change">
        <h3>Review the change</h3>
        {lines.length ? <ul className="plain-list">{lines.map((line) => <li key={line}>{line}</li>)}</ul> : <p>Nothing changes compared with the rules in use.</p>}
        <div className="toolbar">
          <button type="button" className="btn" disabled={cmd.busy} onClick={() => setProposed(null)}>{waiting ? "Done" : "Back"}</button>
          {waiting ? null : <button type="button" className="btn primary" disabled={cmd.busy} onClick={accept}>{cmd.busy ? "Saving…" : "Accept this change"}</button>}
        </div>
        {waiting ? <p role="status">Proposed. Waiting for another owner or admin to accept it; it is in the change history below.</p> : null}
        {cmd.error ? <Banner tone="crit">{cmd.error}</Banner> : null}
      </div>
    );
  }
  return (
    <form className="form" aria-label="Set the monthly AI budget" onSubmit={(e) => { e.preventDefault(); propose(); }}>
      <label className="field"><span>New monthly AI budget (US dollars)</span><input inputMode="decimal" value={amount} disabled={cmd.busy} onChange={(e) => setAmount(e.target.value)} /></label>
      <label className="field"><span>Reason (recorded)</span><textarea rows={2} maxLength={4000} value={reason} disabled={cmd.busy} onChange={(e) => setReason(e.target.value)} /></label>
      <div className="toolbar"><button type="submit" className="btn" disabled={cmd.busy || !amount.trim()}>{cmd.busy ? "Saving…" : "Review the change"}</button></div>
      <p className="small muted">DCLab sets the most a workspace may spend; here you can only set a budget at or below it. You review the change first; {g.sole_approver ? "as the only owner or admin, you then accept it yourself." : "another owner or admin then accepts it."}</p>
      {inputError ? <Banner tone="warn">{inputError}</Banner> : null}
      {cmd.error ? <Banner tone="crit">{cmd.error}</Banner> : null}
      {done ? <p role="status">{done}</p> : null}
    </form>
  );
}

function SpendCard({ g, myId }: { g: StudioGovernance; myId: string | null }) {
  const spend = spendView(g);
  const model = modelWords(g.model_in_use, aiStatus(g).on && g.assistant_beta === true);
  return (
    <div className="grid cols-2">
      <Card title="Spend this month">
        {spend ? (
          <>
            <p data-testid="ai-spend"><b className="num">{spend.spent}</b>{spend.limit ? <> of {spend.limit}</> : null} ({spend.qualifier}), {spend.calls} AI calls.</p>
            {spend.percent !== null ? <span className="meter" role="progressbar" aria-label="AI spend this month" aria-valuemin={0} aria-valuemax={100} aria-valuenow={spend.percent}><i style={{ width: `${spend.percent}%` }} /></span> : null}
            {spend.turnLimit || spend.threadLimit ? <p className="small muted">Each assistant answer is limited to {spend.turnLimit ?? "—"} and each conversation to {spend.threadLimit ?? "—"}.</p> : null}
          </>
        ) : <p className="muted">Spend is not available in this read.</p>}
        {g.viewer?.can_approve ? <BudgetForm g={g} myId={myId} /> : <p className="small muted">An owner or admin can change the monthly budget.</p>}
      </Card>
      <Card title="AI model">
        {model ? (
          <>
            <p data-testid="ai-model"><b>{model.name}</b>{model.provider ? <> by {model.provider}</> : null}</p>
            <p className="small muted">{model.verb}, as chosen by DCLab&apos;s rules. {model.price}</p>
          </>
        ) : <p className="muted">No model is in use: the AI rules could not be read.</p>}
      </Card>
    </div>
  );
}

function LevelsCard({ g }: { g: StudioGovernance }) {
  const levels = g.levels ?? [];
  return (
    <Card title="What the assistant may do, per kind of decision" aside={<Pill tone="gray">Read-only during the beta</Pill>}>
      <p className="small">{LEVELS_NOTE}</p>
      <dl className="kv small" aria-label="What the settings mean">{LEVELS_LEGEND.map(([term, meaning]) => <div key={term}><dt>{term}</dt><dd>{meaning}</dd></div>)}</dl>
      {levels.length === 0 ? <div className="empty">No kinds of decision were returned.</div> : (
        <div className="tbl" tabIndex={0} role="region" aria-label="Kinds of decision (scrolls sideways on small screens)">
          <table data-testid="ai-levels">
            <caption className="sr-only">What the assistant may do per kind of decision</caption>
            <thead><tr><th scope="col">Kind of decision</th><th scope="col">Setting now</th><th scope="col">Automatic</th><th scope="col">How it was earned</th></tr></thead>
            <tbody>
              {levels.map((row) => {
                const auto = automaticCell(row.cap, row.effective_level);
                const setting = settingOverride(row.key);
                const open = Object.values(row.open_incidents ?? {}).reduce((sum, n) => sum + (Number.isFinite(n) ? n : 0), 0);
                return (
                  <tr key={row.key}>
                    <th scope="row">{pointName(row.key)}</th>
                    <td>{setting ?? <Level level={shownLevel(row.effective_level)} />}</td>
                    <td>{auto.locked ? <span><span aria-hidden="true">🔒 </span>{auto.text}</span> : auto.text}</td>
                    <td>{testedCell(row.tested)}{open > 0 ? ` ${open} open problem report${open === 1 ? "" : "s"}.` : ""}</td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}
    </Card>
  );
}

function ReportItem({ report, canResolve, myId }: { report: NonNullable<StudioGovernance["incidents"]>[number]; canResolve: boolean; myId: string | null }) {
  const [open, setOpen] = useState(false);
  const cmd = useCommand();
  const isOpen = report.status === "open";
  const evidence = answerLines(report.evidence ?? null);
  return (
    <li className="card flat">
      <h3>{reportTitle(report.kind)} <span className="sp"><Pill tone={isOpen ? "warn" : "gray"}>{isOpen ? "open" : "resolved"}</Pill></span></h3>
      <p className="small muted">Opened {formatWhen(report.opened_at)}{report.resolved_at ? ` · resolved ${formatWhen(report.resolved_at)} by ${actorWords(report.resolved_by, myId).replace(/^You$/, "you")}` : ""}</p>
      {evidence.length ? <dl className="kv">{evidence.map((line, i) => <div key={`${i}:${line.key}`}><dt>{line.key}</dt><dd>{line.value}</dd></div>)}</dl> : null}
      {report.resolution ? <p className="small"><span className="muted">How it was resolved (written by a person):</span> {plainText(report.resolution, 600)}</p> : null}
      {isOpen && canResolve ? (open ? (
        <ReasonForm label={`Resolve: ${reportTitle(report.kind)}`} confirm="Resolve" field="What was done (recorded)" busy={cmd.busy} error={cmd.error} onCancel={() => setOpen(false)}
          onSubmit={(resolution) => void cmd.run("resolve", `${report.id}:${resolution}`, (key) => resolveReport({ incidentId: report.id, resolution, key })).then((ok) => { if (ok) setOpen(false); })} />
      ) : <div className="toolbar"><button type="button" className="btn" onClick={() => setOpen(true)}>Resolve<span className="sr-only"> {reportTitle(report.kind)}</span></button></div>) : null}
    </li>
  );
}

function ReportsCard({ g, myId }: { g: StudioGovernance; myId: string | null }) {
  const reports = g.incidents ?? g.open_incidents;
  return (
    <Card title="Problem reports">
      {reports.length === 0 ? <div className="empty">No problem reports have been opened in this workspace.</div> : (
        <ul className="plain-list">{reports.map((r) => <ReportItem key={r.id} report={r} canResolve={!!g.viewer?.can_approve} myId={myId} />)}</ul>
      )}
      <p className="small muted">Resolving a report never turns AI back on by itself; that stays a separate step.</p>
    </Card>
  );
}

function OpenChange({ change, canApprove, myId, sole }: { change: NonNullable<StudioGovernance["policy_changes"]>[number]; canApprove: boolean; myId: string | null; sole?: boolean }) {
  const cmd = useCommand();
  const lines = (change.diff_vs_head ?? change.diff_vs_effective ?? []).map(changeLine);
  const mine = ownProposal(change.proposed_by, myId, sole);
  return (
    <li>
      <b>Waiting: change to the AI rules</b> <span className="small muted">proposed {formatWhen(change.created_at)} by {actorWords(change.proposed_by, myId)}</span>
      {change.rationale ? <p className="small">Reason: {plainText(change.rationale, 400)}</p> : null}
      {lines.length ? <ul className="plain-list small">{lines.map((line) => <li key={line}>{line}</li>)}</ul> : null}
      {canApprove && change.consent_change ? <p className="small muted">This change also changes what this workspace shares for testing the AI, so it is not accepted on this page.</p> : null}
      {canApprove && mine && !change.consent_change ? <p className="small muted">Waiting for another owner or admin: you proposed it.</p> : null}
      {canApprove && !mine && !change.consent_change ? (
        <div className="toolbar"><button type="button" className="btn" disabled={cmd.busy} onClick={() => void cmd.run("accept", change.id, (key) => acceptPolicy({ proposalId: change.id, digest: change.policy_digest, key }))}>{cmd.busy ? "Saving…" : "Accept this change"}</button></div>
      ) : null}
      {cmd.error ? <Banner tone="crit">{cmd.error}</Banner> : null}
    </li>
  );
}

function HistoryCard({ g, myId }: { g: StudioGovernance; myId: string | null }) {
  const open = (g.policy_changes ?? []).filter((c) => c.open);
  const recent = g.recent_changes ?? [];
  return (
    <Card title="Change history">
      {open.length ? <ul className="plain-list">{open.map((c) => <OpenChange key={c.id} change={c} canApprove={!!g.viewer?.can_approve} myId={myId} sole={g.sole_approver} />)}</ul> : null}
      {recent.length === 0 ? <div className="empty">No changes yet.</div> : (
        <div className="tbl" tabIndex={0} role="region" aria-label="Change history (scrolls sideways on small screens)">
          <table>
            <caption className="sr-only">Recent changes to the AI settings</caption>
            <thead><tr><th scope="col">When</th><th scope="col">What</th><th scope="col">State</th><th scope="col">By</th><th scope="col">Reason</th></tr></thead>
            <tbody>
              {recent.map((c) => (
                <tr key={`${c.kind}:${c.id}`}>
                  <td>{formatWhen(c.at)}</td>
                  <th scope="row">{changeKindWords(c.kind)}{c.kind === "switch" ? `: ${switchSubject(c.subject)}` : c.kind === "level" ? `: ${pointName(c.subject)}` : ""}</th>
                  <td>{plainText(c.state.replaceAll("_", " "), 30)}</td>
                  <td>{actorWords(c.actor, myId)}</td>
                  <td>{c.rationale ? `${c.actor?.startsWith("user:") ? "" : "System note: "}${plainText(c.rationale, 300)}` : "—"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </Card>
  );
}

export default function AiSettingsPage() {
  const session = useSession();
  const governance = useGovernance(session.loaded && !!session.user);
  const g = governance.data;
  const refused = governance.error instanceof ApiError && governance.error.status === 403;
  let body: ReactNode = null;
  if (governance.isPending) body = <p role="status">Loading the AI settings…</p>;
  else if (refused) body = <Card title="AI settings"><Banner tone="info">Your role cannot read the AI settings. Ask a data scientist, an owner or an admin.</Banner></Card>;
  else if (governance.isError || !g) body = <Banner tone="crit">The AI settings could not be loaded. Reload the page to try again.</Banner>;
  else body = (
    <>
      <OnOffCard g={g} />
      <SpendCard g={g} myId={session.user?.id ?? null} />
      <Card title="What AI is allowed to see">
        <p><b>Raw rows are never sent to an AI model.</b> From your data, the most it may see: {dataWords(g.data_classes)}</p>
        <p className="small">It also reads what DCLab records about the project: run results (cross-validation scores, never final-test numbers), goals and test designs, trust-check findings, and history text.</p>
        <p className="small muted" data-testid="ai-provider-notice">{plainText(g.provider_notice || "What you type to the assistant is sent to the AI provider to answer it.", 400)} Every AI call is logged with its cost.</p>
      </Card>
      <LevelsCard g={g} />
      <ReportsCard g={g} myId={session.user?.id ?? null} />
      <HistoryCard g={g} myId={session.user?.id ?? null} />
    </>
  );
  return (
    <>
      <PageHead eyebrow="Admin" title="AI settings" badges={<Pill tone="ai">{BETA_LABEL}</Pill>} subtitle="Whether AI is on for this workspace, which model the assistant beta uses, what it costs, what it may see and what it may do. Everything works with AI off." />
      <PageGuide
        purpose="See and control how AI is used in this workspace during the beta."
        howTo="Read the on/off state first. Owners and admins can turn AI off at once, set the monthly budget and resolve problem reports. The table of kinds of decision is read-only during the beta."
        youGet="The real model in use, this month's spend (an estimate, or an upper bound while the price is not confirmed), what AI may see, and every change with who made it."
        attention={g && aiStatus(g).held ? "An open problem report keeps AI off." : undefined}
      />
      {body}
    </>
  );
}
