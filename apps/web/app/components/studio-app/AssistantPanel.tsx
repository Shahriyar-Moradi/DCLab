"use client";

import Link from "next/link";
import { useEffect, useRef, useState, type FormEvent, type KeyboardEvent } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { Banner } from "@/components/studio/Banner";
import { Pill } from "@/components/studio/Pill";
import { AssistantProposalCard } from "@/app/components/studio-app/AssistantProposalCard";
import { formatWhen } from "@/app/components/studio-app/StudioParts";
import { plainText } from "@/lib/application/command-search";
import {
  BETA_LABEL, BETA_NOTE, EMPTY_TURN, STREAM_ENDED, assistantError, conversationCostText, foldTurns, isBetaOff, knownEvents, own, pageContextFor,
  replySegments, turnCostText, type AssistantError, type AssistantEvent, type TurnView,
} from "@/lib/application/studio-assistant";
import { createThread, streamMessage, useAssistantThread, useAssistantThreads, useGovernanceInvalidation } from "@/lib/application/studio-assistant-hooks";
import { ActionKeys } from "@/lib/application/studio-wizard";
import { workspaceQueryKey } from "@/lib/infrastructure/active-workspace";
import { newIdempotencyKey } from "@/lib/infrastructure/v1/client";

/** Live events kept while one answer streams (a longer turn is read back from the thread when it ends). */
const LIVE_MAX = 500;
const AUDIT_NOTE = "Only you can open this conversation here. It is kept in the workspace's AI record for audit.";

/** A reply as plain text; only `dclab://` references become internal links (no Markdown, no HTML, no linkify). */
function Reply({ text, projectId }: { text: string; projectId: string }) {
  return (
    <p style={{ whiteSpace: "pre-wrap", overflowWrap: "anywhere" }}>
      {replySegments(text, projectId).map((seg, i) =>
        seg.kind === "text" ? <span key={i}>{seg.text}</span> : seg.href ? <Link key={i} href={seg.href}>{seg.label}</Link> : <span key={i}>{seg.label}</span>,
      )}
    </p>
  );
}

function Turn({ turn, projectId, cost, live }: { turn: TurnView; projectId: string; cost: string | null; live?: boolean }) {
  return (
    <>
      {turn.userText !== null ? (
        <article className="msg user"><div className="from"><span>You</span></div><p style={{ whiteSpace: "pre-wrap", overflowWrap: "anywhere" }}>{turn.userText}</p></article>
      ) : null}
      {live || turn.replies.length || turn.steps.length || turn.notes.length || turn.proposalIds.length || turn.failed ? (
        <article className="msg agent">
          <div className="from"><span>Assistant <Pill tone="ai">{BETA_LABEL}</Pill></span>{cost ? <span>{cost}</span> : null}{live && !turn.done ? <span>Writing…</span> : null}</div>
          {turn.replies.map((reply, i) => <Reply key={i} text={reply.text} projectId={projectId} />)}
          {turn.steps.length ? <p className="small muted">Steps it ran: {turn.steps.join(" · ")}. It never sees raw rows or final-test numbers.</p> : null}
          {turn.notes.map((note) => <p key={note} className="small muted">{note}</p>)}
          {turn.proposalIds.map((id) => <AssistantProposalCard key={id} proposalId={id} projectId={projectId} />)}
          {turn.failed ? <p className="small">{turn.failed}</p> : null}
        </article>
      ) : null}
    </>
  );
}

/**
 * The assistant panel (D6b U1, Beta). Opened from the top bar only when AI is available and the workspace has the beta.
 * Conversations belong to one project and to the person who started them; page context is the screen and the ids in the URL.
 */
export function AssistantPanel({ pathname, onClose }: { pathname: string; onClose: () => void }) {
  const { projectId, context } = pageContextFor(pathname);
  const threads = useAssistantThreads(projectId, true);
  const [threadId, setThreadId] = useState<string | null>(null);
  const thread = useAssistantThread(threadId);
  const client = useQueryClient();
  const governanceChanged = useGovernanceInvalidation();
  const keys = useRef(new ActionKeys(newIdempotencyKey)).current;
  const abort = useRef<AbortController | null>(null);
  const flight = useRef(false);
  const root = useRef<HTMLDivElement>(null);
  const composer = useRef<HTMLTextAreaElement>(null);
  const refocus = useRef(false);
  const [text, setText] = useState("");
  const [pending, setPending] = useState<string | null>(null);
  const [live, setLive] = useState<AssistantEvent[]>([]);
  const [error, setError] = useState<AssistantError | null>(null);
  const [creating, setCreating] = useState(false);
  const [announce, setAnnounce] = useState("");

  // Focus moves into the panel when it opens (the frame returns it to the toggle on close).
  useEffect(() => root.current?.focus(), []);
  // Another project shows its own conversations; a live answer of the old one stops being read (the turn itself goes on).
  useEffect(() => {
    abort.current?.abort();
    setThreadId(null);
    setError(null);
    setPending(null);
    setLive([]);
  }, [projectId]);
  useEffect(() => () => abort.current?.abort(), []);
  useEffect(() => {
    if (!threadId && threads.data?.length) setThreadId(threads.data[0].id);
  }, [threadId, threads.data]);
  // The beta was switched off meanwhile: the gate re-reads and closes the panel.
  useEffect(() => {
    if (threads.error && isBetaOff(threads.error)) governanceChanged();
  }, [threads.error]); // eslint-disable-line react-hooks/exhaustive-deps

  // A control disabled while busy would drop focus to the page (and Escape would stop closing the panel): focus holds on the
  // panel meanwhile and returns to the message box afterwards, unless the person moved it.
  const holdFocus = () => {
    if (!root.current?.contains(document.activeElement)) return;
    root.current.focus();
    refocus.current = true;
  };

  const fail = (caught: unknown) => {
    setError(assistantError(caught));
    if (isBetaOff(caught)) governanceChanged();
  };

  const newConversation = async () => {
    if (!projectId || flight.current) return;
    flight.current = true;
    holdFocus();
    setCreating(true);
    setError(null);
    try {
      const created = await createThread({ projectId, key: keys.keyFor("create", projectId) });
      keys.done("create");
      setThreadId(created.id);
      void client.invalidateQueries({ queryKey: workspaceQueryKey("v1", "assistant-threads", projectId) });
    } catch (caught) {
      fail(caught);
    } finally {
      flight.current = false;
      setCreating(false);
    }
  };

  const send = async (message: string) => {
    const body = message.trim();
    if (!threadId || !body || flight.current) return;
    flight.current = true;
    holdFocus();
    setError(null);
    setAnnounce("");
    setPending(body);
    setLive([]);
    const controller = new AbortController();
    abort.current = controller;
    try {
      // The same text on the same screen reuses its key: a retry after a dropped connection replays the stored turn.
      const key = keys.keyFor("send", `${threadId}:${body}:${JSON.stringify(context)}`);
      const result = await streamMessage({
        threadId, text: body, pageContext: context, key, signal: controller.signal,
        onEvent: (event) => setLive((prev) => (prev.length >= LIVE_MAX ? prev : [...prev, event])),
      });
      if (controller.signal.aborted) return;
      if (result.finished) {
        keys.done("send");
        setText("");
        setAnnounce("Answer ready.");
      } else {
        setError(STREAM_ENDED);
      }
      await thread.refetch();
    } catch (caught) {
      if ((caught as { name?: string } | null)?.name !== "AbortError" && !controller.signal.aborted) fail(caught);
    } finally {
      if (abort.current === controller) {
        setPending(null);
        setLive([]);
      }
      flight.current = false;
    }
  };

  const submit = (event: FormEvent) => {
    event.preventDefault();
    void send(text);
  };
  const onKeyDown = (event: KeyboardEvent<HTMLDivElement>) => {
    if (event.key === "Escape") {
      event.stopPropagation();
      onClose();
    }
  };

  const detail = thread.data;
  // Links and suggestion cards use the conversation's own project, never the route's.
  const threadProject = detail?.project_id ?? null;
  const history = detail ? foldTurns(knownEvents(detail.events)) : [];
  const liveTurn = pending !== null ? { ...(foldTurns(live)[0] ?? EMPTY_TURN), userText: pending } : null;
  const upper = !!detail?.cost.price_is_upper_bound;
  const turnCost = (turn: TurnView) => {
    if (!detail || !turn.replies.length) return null;
    const micros = own(detail.cost.by_turn_micros, turn.turnId);
    return turnCostText(typeof micros === "number" ? micros : null, turn.replies.every((r) => r.templated), upper);
  };
  const busy = pending !== null || creating;
  useEffect(() => {
    if (busy || !refocus.current) return;
    refocus.current = false;
    if (document.activeElement === root.current) composer.current?.focus();
  }, [busy]);

  return (
    <div className="asst" data-testid="assistant-panel" ref={root} tabIndex={-1} onKeyDown={onKeyDown} role="region" aria-label="Assistant (Beta)">
      <div className="asst-head">
        <h2>Assistant <Pill tone="ai">{BETA_LABEL}</Pill></h2>
        <button type="button" className="btn" onClick={onClose}>Close<span className="sr-only"> the assistant</span></button>
      </div>
      <p className="small muted">{BETA_NOTE}</p>

      {!projectId ? <div className="empty">Open a project to talk with the assistant about it.</div> : null}
      {projectId && threads.isPending ? <p role="status">Loading your conversations…</p> : null}
      {projectId && threads.isError ? <Banner tone={isBetaOff(threads.error) ? "info" : "crit"}><b>{assistantError(threads.error).title}.</b> {assistantError(threads.error).detail}</Banner> : null}

      {projectId && threads.data ? (
        <div className="toolbar">
          {threads.data.length ? (
            <label className="field asst-pick">
              <span>Conversation</span>
              <select value={threadId ?? ""} disabled={busy} onChange={(e) => { setThreadId(e.target.value || null); setError(null); }}>
                {threads.data.map((t) => <option key={t.id} value={t.id}>{t.title ? plainText(t.title, 60) : `Started ${formatWhen(t.created_at)}`}</option>)}
              </select>
            </label>
          ) : <p className="muted">No conversations in this project yet.</p>}
          <button type="button" className="btn" disabled={busy} onClick={() => void newConversation()}>{creating ? "Starting…" : "New conversation"}</button>
        </div>
      ) : null}

      {threadId && thread.isPending ? <p role="status">Loading the conversation…</p> : null}
      {threadId && thread.isError ? <Banner tone="crit"><b>{assistantError(thread.error).title}.</b> {assistantError(thread.error).detail}</Banner> : null}

      {detail && threadProject ? (
        <>
          {!detail.ai_available ? (
            <Banner tone="info">
              AI is off for this conversation, so answers are fixed templates built by DCLab, with no AI. Try a quick action:
              <span className="toolbar">{detail.quick_actions.map((q) => <button key={q} type="button" className="btn" disabled={busy} onClick={() => void send(q)}>{q}</button>)}</span>
            </Banner>
          ) : null}
          {/* Announced only while an answer streams (aria-busy until it is complete), never on the first load. */}
          <div role="log" aria-label="Conversation with the assistant" className="chat asst-log" aria-live={pending !== null ? "polite" : "off"} aria-busy={pending !== null}>
            {history.length === 0 && !liveTurn ? <p className="muted">Ask about this project. Anything that changes it comes back as a suggestion for you to accept.</p> : null}
            {history.map((turn) => <Turn key={turn.turnId} turn={turn} projectId={threadProject} cost={turnCost(turn)} />)}
            {liveTurn ? <Turn turn={liveTurn} projectId={threadProject} cost={null} live /> : null}
          </div>
          <p className="sr-only" role="status">{announce}</p>
          <p className="small muted" data-testid="assistant-cost">{conversationCostText(detail.cost.conversation_micros, upper, own(detail.limits, "cost_micros"))}</p>
        </>
      ) : null}

      {error ? <Banner tone="crit"><b>{error.title}.</b> {error.detail}</Banner> : null}

      {detail && threadProject ? (
        <form className="composer" onSubmit={submit}>
          <textarea ref={composer} aria-label="Message the assistant" maxLength={4000} value={text} disabled={busy} placeholder="Ask about this project…" onChange={(e) => setText(e.target.value)} />
          <div className="acts"><button type="submit" className="btn primary" disabled={busy || !text.trim()}>{pending !== null ? "Sending…" : "Send"}</button></div>
        </form>
      ) : null}
      {detail ? (
        <p className="small muted" data-testid="assistant-notice">
          {detail.ai_available ? plainText(detail.provider_notice, 600) : "AI is off: nothing you type is sent to an AI provider."} {AUDIT_NOTE}
        </p>
      ) : null}
    </div>
  );
}
