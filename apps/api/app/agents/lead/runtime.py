"""``lead_loop``: the lead agent's bounded tool loop (ADR 0009 §7.1, §7.3; ADR 0008 §6).

Per turn (``spec.turn``: the user's message and the bounded transcript) each step is one
``session.complete`` returning an ``AssistantStep``, checked by ``session.check`` (the
harness's output validator: schema, holdout, citations of the run's project, then this
runtime's checks below). A rejected step, or an output that did not validate, is retried
once with the reasons as a note; a second rejection ends the turn with the honest
"could not verify" template. ``tool_calls`` go through ``session.call_tool`` (registry,
capability, validator, consumer-mode service, shaping; a write tool returns its pending
L1 proposal id) and their tagged results join the transcript. Bounds are the harness's:
steps, tokens and wall time before every model call (typed ``budget_exhausted`` event,
run ``over_budget`` / ``timed_out``), cost by the turn's budget hold, tool calls (six per
step by the schema; the run's per-turn limit ends the turn with the limit template).
Other provider refusals end the turn with the "unavailable" template. Templates carry no
number and no citation. No generated code runs; the runtime never sees a database
session, a storage client or a provider key.

``validate_output`` (on the NFKC-normalised message without zero-width characters): shape
per kind, an answer (or a ``done`` with a message) cites at least one node (templates exempt),
every tool on the run's catalog surface, no holdout wording ("unseen" too, except the
preprocessing phrase "unseen categories": categorical levels absent from training), and the Markdown subset by allowlist: the only link is
``[label](dclab://<kind>/<id>)`` to a cited node; once those are reduced to their labels, any
other link construct (``](``, ``]:``, ``][``, ``<…``, ``![``, ``//``, HTML entities,
``www.``, link schemes with or without ``//``, host names, e-mail addresses) rejects the
step. ``check_output``: every decimal (also ``.93``, ``0,93``, ``7e-1``) and every
percentage / per-mille in the message, link labels included, is a CV metric, selected score
or CV delta of a cited experiment or model version, read in agent consumer mode
(holdout-free, at most ``MAX_CITED_READS`` reads), so a final-holdout number never passes.
Identical write calls within a turn reuse the first proposal.
"""

from __future__ import annotations

import json
import re
import unicodedata
from collections.abc import Iterable
from typing import Any

from app.agents.contracts import (
    SYSTEM_SOURCE,
    AgentRunSpec,
    AssistantStep,
    Citation,
    ContextField,
    LeadTurn,
    TranscriptItem,
    Untrusted,
)
from app.agents.runtime.base import OutputCheck, RuntimeOutput, RuntimeRefused, ToolOutcome
from app.agents.tools.catalog import FORBIDDEN_OPERATIONS, ToolError, catalog
from app.agents.tools.shaping import HOLDOUT_KEY
from app.domain.agent_records import KEY_PATTERN

VERSION = "lead_loop==1"
AGENT_KEY = "lead"
PURPOSE = "assistant.turn"
PROMPT_VERSION = 1  # app/agents/prompts/lead/v1.md
MAX_OUTPUT_TOKENS = 2000
TRANSCRIPT_MAX = 60
NOT_VERIFIED = ("I could not verify an answer against this project's evidence, so I am not giving one. "
                "Ask about a specific experiment or model, or use the forms.")
UNAVAILABLE = "The assistant could not finish this answer right now. Use the forms or the quick actions instead."
AT_LIMIT = "This turn reached its limit. Use the forms to continue, or ask a narrower question."
MAX_CITED_READS = 8
_LIMIT_REFUSALS = frozenset({"budget_exhausted", "timeout"})
_KEY = re.compile(KEY_PATTERN)
_NODE_LINK = re.compile(r"\[([^\[\]\n]{0,200})\]\(\s*dclab://([a-z_]+)/([0-9a-f-]{36})\s*\)")
_HTML = re.compile(r"<\s*/?\s*[A-Za-z!?]|!\[")
_LINKISH = re.compile(
    r"(?i)\]\s*[(:\[]|//|&#?[a-z0-9]+;|\bwww\.|[\w.+-]+@[\w-]+\.[a-z]"
    r"|\b(?:https?|ftps?|wss?|javascript|data|vbscript|mailto|tel|sms|file|blob|about|xmpp|irc)\s*:"
    r"|\b[a-z0-9-]+(?:\.[a-z0-9-]+)+/"  # host/path
    r"|\b[a-z0-9-]+(?:\.[a-z0-9-]+)*\.(?:com|net|org|io|co|ai|app|dev|info|biz|xyz|me|ly|sh|to|gg|link|site"
    r"|online|top|click|ru|cn|uk|de|us|tk)\b")  # a host a linkifier would turn into a link
_HOLDOUT_WORDS = re.compile(r"(?i)\b(?:hold[\s_-]?out|held[\s_-]?(?:out|back)|final[\s_-]?(?:test|evaluation)"
                            r"|test[\s_-]?(?:set|split)|unseen(?![\s_-]+categor(?:y|ies)\b))\b")
_EXP = r"(?:e[+-]?\d+(?![a-z0-9]))?"  # an exponent, never the start of a hex token
_NUMBER = re.compile(rf"(?i)(?<![\d.,])(\d*[.,]\d+{_EXP}|\d+{_EXP})(\s*(?:%|‰|per\s*cent\b|percent(?:age)?\b|pct\b))?")
_UUID = re.compile(r"(?i)\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b")
_THOUSANDS = re.compile(r"[1-9]\d{0,2},\d{3}")
_INVISIBLE = re.compile("[\u00ad\u180e\u200b-\u200f\u202a-\u202e\u2060-\u2064\ufeff]")
CV_NUMBER_KEYS = ("selected_score", "cv", "baseline_comparison", "diff_vs_parent")  # never label constants


def normalized(message: str) -> str:
    return _INVISIBLE.sub("", unicodedata.normalize("NFKC", message))
_CV_READS = {"experiment": ("get_experiment", "experiment_id"), "model_version": ("get_model", "model_version_id")}


def markdown_reasons(message: str, citations: Iterable[Citation]) -> list[str]:
    """Allowlist: cited node links are reduced to their labels, then anything link-like left
    (any other inline / reference / auto link, HTML, image, entity, scheme) rejects."""

    message = normalized(message)
    cited = {(item.kind, str(item.id)) for item in citations}
    if any((node[2], node[3]) not in cited for node in _NODE_LINK.finditer(message)):
        return ["link_not_cited"]
    rest = _NODE_LINK.sub(lambda node: node[1], message)
    if _HTML.search(rest):
        return ["markdown_not_allowed"]
    return ["external_link"] if _LINKISH.search(rest) else []


def stated_numbers(message: str) -> list[tuple[float, int, float]]:
    """(value, decimal places, scale) of every decimal, scientific number, percentage or
    per-mille the message states, link labels included (only a node link's target is
    dropped); plain integers (counts, versions) and node ids are not metric values."""

    found = []
    text = _UUID.sub(" ", _NODE_LINK.sub(lambda node: f" {node[1]} ", normalized(message)))
    for match in _NUMBER.finditer(text):
        raw, unit = match[1].lower(), (match[2] or "").strip().lower()
        raw = raw.replace(",", "") if _THOUSANDS.fullmatch(raw) else raw.replace(",", ".")
        mantissa, _, exponent = raw.partition("e")
        if "." in mantissa or exponent or unit:
            places = (len(mantissa.split(".", 1)[1]) if "." in mantissa else 0) - int(exponent or 0)
            found.append((float(raw), max(0, places), 1000.0 if unit == "‰" else 100.0 if unit else 1.0))
    return found


def uncited(message: str, values: Iterable[float]) -> list[float]:
    cited = list(values)
    return [number for number, places, scale in stated_numbers(message)
            if not any(abs(value * scale - number) <= 0.5 * 10 ** -places + 1e-12 for value in cited)]


def _numbers(value: Any) -> list[float]:
    if isinstance(value, bool):
        return []
    if isinstance(value, (int, float)):
        return [float(value)]
    if isinstance(value, dict):
        return [n for item in value.values() for n in _numbers(item)]
    if isinstance(value, (list, tuple)):
        return [n for item in value for n in _numbers(item)]
    return []


def cited_cv_values(ctx: Any, citations: Iterable[Citation]) -> list[float]:
    """The CV numbers of the cited experiments / model versions, through the catalog's read
    tools (the acting human's authorization, holdout-free shaping)."""

    values: list[float] = []
    for item in list(dict.fromkeys(c for c in citations if c.kind in _CV_READS))[:MAX_CITED_READS]:
        read = _CV_READS[item.kind]
        try:
            shaped = catalog()[read[0]].read(ctx, {read[1]: str(item.id)})
        except ToolError:
            ctx.db.rollback()
            continue
        node = next(iter(shaped.payload.values()), None)  # {"experiment": {...}} / {"model_version": {...}}
        if isinstance(node, dict):
            metrics = node.get("metrics") if isinstance(node.get("metrics"), dict) else {}
            values += _numbers([metrics.get(key) for key in CV_NUMBER_KEYS] + [node.get("diff_vs_parent")])
    return values


def _output(step: AssistantStep) -> RuntimeOutput:
    return RuntimeOutput(output=step, citations=step.citations, message=step.message)


def _template(message: str, code: str) -> RuntimeOutput:
    return RuntimeOutput(output=AssistantStep(kind="answer", message=message), message=message,
                         meta={"refusal": code})


def _result_item(outcome: ToolOutcome) -> TranscriptItem:
    """A tool outcome as a tagged transcript item: its status (and pending proposal id) plus
    the read result's fields, which the harness already bounded to the run's class and scope."""

    status: dict[str, Any] = {"status": {outcome.status: True}}
    if outcome.code:
        safe = _KEY.fullmatch(outcome.code) and not HOLDOUT_KEY.search(outcome.code)
        status["code"] = {outcome.code if safe else "rejected": True}
    if outcome.proposal_id is not None:
        status |= {"proposal_id": outcome.proposal_id, "pending_confirmation": outcome.status == "proposed"}
    field = ContextField(key="tool_outcome", value=status, data_class="metadata", outcome_scope="none",
                         sources=(SYSTEM_SOURCE,))
    return TranscriptItem(kind="tool_result", tool_name=outcome.tool if _KEY.fullmatch(outcome.tool) else None,
                          fields=(field, *outcome.fields)[:200])


class LeadRuntime:
    name, version, output_schema = "lead_loop", VERSION, AssistantStep

    def __init__(self, turn: LeadTurn) -> None:
        self.turn, self.tools, self._used = turn, (), False

    def run(self, session: Any) -> RuntimeOutput:
        if self._used:
            raise RuntimeRefused("runtime_reused")  # one instance per run
        self._used = True
        self.tools = tuple(session.tools)
        if not self.tools or "accept_proposal" in self.tools or any(
                operation in name for name in self.tools for operation in FORBIDDEN_OPERATIONS):
            raise RuntimeRefused("tool_surface_invalid")
        check = getattr(session, "check", None) or self.validate_output
        transcript, notes, retried = list(self.turn.transcript), (), False
        proposed: dict[str, ToolOutcome] = {}  # identical write calls in one turn: one proposal
        while True:  # ends on an answer, a template, or the harness's step / token / wall limit
            response = session.complete(output_schema=AssistantStep, max_output_tokens=MAX_OUTPUT_TOKENS,
                                        transcript=tuple(transcript[-TRANSCRIPT_MAX:]),
                                        user_text=(self.turn.user_text, *notes))
            refusal = None if response.ok and response.output is not None else (
                response.refusal.code if response.refusal else "provider_error")
            if refusal is not None and refusal != "invalid_output":
                return _template(AT_LIMIT if refusal in _LIMIT_REFUSALS else UNAVAILABLE, refusal)
            output = _output(response.output) if refusal is None else None
            reasons = check(output) if output is not None else ["invalid_output"]
            if "wall_limit" in reasons:  # the harness stopped the run while checking
                return _template(AT_LIMIT, "wall_limit")
            if reasons:
                if retried:
                    return _template(NOT_VERIFIED, "step_rejected")
                retried = True
                notes = (Untrusted(untrusted_text=f"[validator] the previous step was rejected "
                                                  f"({', '.join(reasons)}); return a corrected step."),)
                continue
            retried, notes = False, ()
            step = output.output
            if step.kind != "tool_calls":
                return output
            for call in step.tool_calls:
                same = json.dumps([call.tool, call.arguments], sort_keys=True, default=str)
                outcome = proposed.get(same) or session.call_tool(call.tool, call.arguments, reason=call.reason)
                if outcome.status == "proposed":
                    proposed[same] = outcome
                transcript.append(_result_item(outcome))
                if outcome.code in ("tool_call_limit", "run_stopped"):
                    return _template(AT_LIMIT, outcome.code)

    def validate_output(self, output: RuntimeOutput) -> list[str]:
        step = output.output
        if not isinstance(step, AssistantStep):
            return ["output_schema_invalid"]
        if (step.kind == "tool_calls") != bool(step.tool_calls) or (
                step.kind in ("answer", "clarify") and not (step.message or "").strip()):
            return ["step_invalid"]
        if any(call.tool not in self.tools for call in step.tool_calls):
            return ["unknown_tool"]
        if step.kind in ("answer", "done") and (step.message or "").strip() and not step.citations \
                and not output.meta.get("refusal"):
            return ["uncited_answer"]  # every claim rests on a node; only DCLab's templates cite none
        if _HOLDOUT_WORDS.search(normalized(step.message or "")):
            return ["holdout_in_output"]
        return markdown_reasons(step.message or "", step.citations)

    def check_output(self, check: OutputCheck, output: RuntimeOutput) -> list[str]:
        step = output.output
        message = step.message if isinstance(step, AssistantStep) else None
        if not message or not stated_numbers(message):
            return []
        if check.tool_ctx is None:
            return ["number_unverifiable"]
        return ["uncited_number"] if uncited(message, cited_cv_values(check.tool_ctx, step.citations)) else []


def factory(spec: AgentRunSpec) -> LeadRuntime:
    """The registered ``lead_loop`` factory: a ``lead`` run of a signed-in human (ADR 0009
    §7.2: never a service token) on the assistant surface. The turn's user text is stored as
    the run's first event (``user_message``, P6.3-B2) but the turn still runs only in the API
    process: never queued (``submit`` refuses), and not replayed yet (see ``replay``)."""

    if spec.turn is None:
        raise RuntimeRefused("turn_input_missing")
    if (spec.agent_key != AGENT_KEY or spec.kind != "lead" or spec.tool_surface != "assistant"
            or spec.service_token_id is not None or spec.user_id is None or spec.project_id is None
            or spec.purpose != PURPOSE or spec.decision_point_key is not None):
        raise RuntimeRefused("lead_spec_invalid")
    return LeadRuntime(spec.turn)
