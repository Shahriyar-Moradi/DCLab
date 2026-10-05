"""The Jev semantic port: ``GatewayService.decide()`` plus the deterministic policy.

Never imports the TypeSafe SDK (that lives in ``gateway/providers/typesafe_jev.py``).
Per ask: kill switches off → the deterministic port (zero calls); else subjects go in
batches of ≤ 50 questions with state ≤ 8 KB, 1 s timeout each, through the gateway
(policy, switches, redaction, limits and circuit breaker, budget, cache, ledger). Each
answer — including ``unavailable`` ones that have a ledger row — writes one
``semantic_decision_answers`` row on its own short session (durable like the ledger,
so the caller's session stays clean for the next gateway call); the first non-cached
row per digest is the cache entry the gateway serves later. A failed insert never
fails the decision, but it is counted per (workspace, purpose): past
``ANSWER_WRITE_FAILURE_THRESHOLD`` one open ``reconciliation`` incident records that
evaluation samples were lost (R3 samples must not drop silently). Rule value unless
the level and the agreement table say otherwise; nothing here changes product state.
"""

from __future__ import annotations

import logging
import threading
from collections import Counter
from typing import Any
from uuid import UUID

from pydantic import BaseModel
from sqlalchemy import null, select
from sqlalchemy.orm import Session

from app.agents.gateway import cache
from app.agents.gateway.contract import Refusal, SemanticDecisionRequest, SemanticQuestion
from app.agents.governance.decision_points import REGISTRY, answer_ceiling
from app.agents.governance.policy import effective_level
from app.agents.governance.switches import effective_switches
from app.agents.semantic import policy
from app.agents.semantic.deterministic import DeterministicSemanticPort
from app.agents.semantic.port import NO_KIND, Resolution, SemanticAsk, SemanticOutcome, Subject, Validator
from app.agents.semantic.releases import MAX_QUESTIONS, MAX_STATE_BYTES, TIMEOUT_MS, JevRelease, release_for
from app.db.models import AiIncident, PromptRelease, SemanticDecisionAnswer

logger = logging.getLogger(__name__)
BATCH_ESTIMATE_MICROS = 10_000  # worst case of one batch: ≤ 8 KB state at $0.042 / 1M input tokens
ANSWER_WRITE_FAILURE_THRESHOLD = 3  # failed answer-row inserts per (workspace, purpose) and process
WRITE_FAILURES: Counter[tuple[UUID, str]] = Counter()
_WRITE_FAILURES_LOCK = threading.Lock()


def _wire(value: Any) -> Any:
    """The gateway's wire form of a state value (``redaction.render_value``), for digests."""

    if isinstance(value, BaseModel):
        return value.model_dump(mode="json")
    if isinstance(value, dict):
        return {key: _wire(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_wire(item) for item in value]
    return str(value) if isinstance(value, UUID) else value


class _Batch:
    def __init__(self, ask: SemanticAsk, release: JevRelease, subjects: list[Subject]) -> None:
        self.subjects = subjects
        keys = {s.column_id: f"c{index}" for index, s in enumerate(subjects) if s.column_id}
        self.state = {**ask.context, **{keys[s.column_id]: s.fields for s in subjects if s.column_id}}
        self.column_keys = {**ask.context_columns, **{key: column for column, key in keys.items()}}
        choices = list(release.choices or ask.choices) if release.primitive == "choice" else []
        self.questions = [(s.question_key, release.primitive, choices, s.column_id) for s in subjects]

    def size(self) -> int:
        return len(cache.canonical_json(_wire(self.state)).encode())

    def digests(self, ask: SemanticAsk, release: JevRelease) -> list[str]:
        return cache.jev_question_keys(
            workspace_id=ask.workspace_id, purpose=release.purpose, release_version=str(release.version),
            model=release.model_id, data_class="metadata", state=_wire(self.state),
            column_keys={key: str(column) for key, column in self.column_keys.items()},
            user_text=[_wire(item) for item in ask.user_text],
            questions=[(key, primitive, choices, str(column) if column else None)
                       for key, primitive, choices, column in self.questions])


def batches(ask: SemanticAsk, release: JevRelease) -> list[_Batch]:
    out: list[_Batch] = []
    current: list[Subject] = []
    for subject in ask.subjects:
        if current and (len(current) >= MAX_QUESTIONS
                        or _Batch(ask, release, [*current, subject]).size() > MAX_STATE_BYTES):
            out.append(_Batch(ask, release, current))
            current = []
        current.append(subject)
    out.append(_Batch(ask, release, current))
    return out


class JevSemanticPort:
    def __init__(self, gateway: Any, *, settings: Any) -> None:
        self.gateway, self.settings = gateway, settings

    def resolve(self, db: Session, ask: SemanticAsk, *, validator: Validator | None = None) -> SemanticOutcome:
        release = release_for(ask.purpose)
        if release is None:
            raise ValueError(f"unknown Jev purpose {ask.purpose}")
        blocking = effective_switches(db, ask.workspace_id).blocking(
            ai_enabled=bool(getattr(self.settings, "ai_enabled", False)), provider="typesafe", purpose=ask.purpose)
        if blocking:  # "off is a switch, not a level": AI off, no call, no row (ADR 0008 §8)
            return DeterministicSemanticPort().resolve(db, ask)
        release_id = db.scalar(select(PromptRelease.id).where(
            PromptRelease.agent_key == release.agent_key, PromptRelease.version == release.version,
            PromptRelease.status == "released"))
        planned = batches(ask, release)
        reservation, reserved_here = ask.budget, False
        if reservation is None:
            held = self.gateway.reserve(db, workspace_id=ask.workspace_id, project_id=ask.project_id,
                                        agent_run_id=ask.agent_run_id, run_kind="jev",
                                        estimate_micros=BATCH_ESTIMATE_MICROS * len(planned))
            if isinstance(held, Refusal):
                return self._outcome(ask, [r for b in planned for r in self._unavailable(b, held.code)], 0)
            reservation, reserved_here = held, True
        resolutions: list[Resolution] = []
        try:
            for batch in planned:
                resolutions += self._ask(db, ask, release, release_id, batch, reservation, validator)
        finally:
            if reserved_here:
                self.gateway.release(db, reservation)
        return self._outcome(ask, resolutions, len(planned))

    @staticmethod
    def _outcome(ask: SemanticAsk, resolutions: list[Resolution], calls: int) -> SemanticOutcome:
        return SemanticOutcome(purpose=ask.purpose, ai="on", resolutions=tuple(resolutions), gateway_calls=calls)

    @staticmethod
    def _unavailable(batch: _Batch, code: str) -> list[Resolution]:
        return [Resolution(question_key=s.question_key, column_id=s.column_id, rule_answer=s.rule_answer,
                           value_used=s.rule_answer, agreement="unavailable", refusal=code) for s in batch.subjects]

    def _ask(self, db: Session, ask: SemanticAsk, release: JevRelease, release_id: UUID | None, batch: _Batch,
             reservation: Any, validator: Validator | None) -> list[Resolution]:
        try:
            request = SemanticDecisionRequest(
                purpose=release.purpose, release_version=str(release.version), decision_point_key=release.purpose,
                workspace_id=ask.workspace_id, project_id=ask.project_id, experiment_id=ask.experiment_id,
                dataset_id=ask.dataset_id, workflow_run_id=ask.workflow_run_id, agent_run_id=ask.agent_run_id,
                state=batch.state, column_keys=batch.column_keys, user_text=ask.user_text,
                questions=tuple(SemanticQuestion(question_key=key, primitive=primitive, choices=tuple(choices),
                                                 column_id=column)
                                for key, primitive, choices, column in batch.questions),
                source_datasets=ask.source_datasets,
                source_columns=tuple(dict.fromkeys([*ask.source_columns, *batch.column_keys.values()])),
                budget=reservation, timeout_ms=TIMEOUT_MS,
            )
        except ValueError:  # pydantic ValidationError: an illegal question never leaves the process
            logger.warning("semantic ask refused before the gateway", extra={"purpose": release.purpose})
            return self._unavailable(batch, "policy_denied")
        response = self.gateway.decide(db, request)
        answers = response.answers if response.ok else (None,) * len(batch.subjects)
        out, rows, levels = [], [], {}
        digests = list(response.question_digests)
        if len(digests) != len(batch.subjects):  # refused before the gateway keyed the questions
            digests = batch.digests(ask, release)
        for subject, answer, digest in zip(batch.subjects, answers, digests):
            confidence = answer.confidence if answer is not None else None
            banded = policy.band(release, answer.answer if answer is not None else None, confidence)
            agreement = "unavailable" if answer is None else policy.agreement(release, subject.rule_answer, banded)
            kind = policy.answer_kind(release, subject.rule_answer, banded.value)
            if kind not in levels and ask.levels is not None:  # snapshotted at job claim (P6.9-A)
                levels[kind] = min(release.max_level, answer_ceiling(release.purpose, kind),
                                   int(ask.levels.get(kind or NO_KIND, 0)))  # never above code caps
            elif kind not in levels:
                levels[kind] = min(release.max_level, effective_level(
                    db, ask.workspace_id, release.purpose, kind, prompt_release_id=release_id,
                    model_id=release.model_id))
            level = levels[kind]
            validator_ok = agreement == "disagree" and level >= 2 and (validator is not None and validator(subject, banded.value))
            applied = policy.apply(level, agreement, subject.rule_answer, banded.value, validator_ok=validator_ok)
            resolution = Resolution(
                question_key=subject.question_key, column_id=subject.column_id, rule_answer=subject.rule_answer,
                value_used=applied.value_used, agreement=agreement, level=level,
                policy_outcome=applied.policy_outcome, ai_answer=banded.value,
                raw_answer=dict(answer.answer) if answer is not None else None, confidence=confidence,
                in_acting_band=banded.in_acting_band, cache_hit=response.cache_hit,
                refusal=response.refusal.code if response.refusal else None, invocation_id=response.invocation_id)
            out.append(resolution)
            if response.invocation_id is not None:
                rows.append(self._row(ask, release, subject, answer, digest, resolution, response))
        return self._write(db, ask, release, out, rows)

    @staticmethod
    def _row(ask: SemanticAsk, release: JevRelease, subject: Subject, answer: Any, digest: str,
             resolution: Resolution, response: Any) -> SemanticDecisionAnswer:
        return SemanticDecisionAnswer(
            workspace_id=ask.workspace_id, project_id=ask.project_id, experiment_id=ask.experiment_id,
            dataset_id=ask.dataset_id, agent_run_id=ask.agent_run_id, llm_invocation_id=response.invocation_id,
            decision_point_key=release.purpose, purpose=release.purpose, release_version=str(release.version),
            model_id=release.model_id, question_key=subject.question_key[:200], question_digest=digest,
            data_class="metadata",
            evidence_partition=ask.evidence_partition or REGISTRY[release.purpose].evidence_partition,
            primitive=release.primitive, answer=dict(answer.answer) if answer is not None else {},
            probabilities=dict(answer.probabilities) if answer is not None and answer.probabilities else null(),
            confidence=round(resolution.confidence, 4) if resolution.confidence is not None else None,
            in_acting_band=resolution.in_acting_band, rule_answer={"value": subject.rule_answer},
            agreement=resolution.agreement, level=resolution.level, policy_outcome=resolution.policy_outcome,
            value_used={"value": resolution.value_used}, cache_hit=response.cache_hit,
            latency_ms=response.latency_ms,
        )

    @staticmethod
    def _write(db: Session, ask: SemanticAsk, release: JevRelease, out: list[Resolution],
               rows: list[SemanticDecisionAnswer]) -> list[Resolution]:
        if not rows:
            return out
        try:
            with Session(bind=db.get_bind(), expire_on_commit=False) as session:
                session.add_all(rows)
                session.commit()
        except Exception:  # the evaluation sample is lost, the decision is not: rule paths stay intact
            logger.exception("could not write semantic_decision_answers rows")
            count_answer_write_failure(db, ask.workspace_id, release.purpose, lost=len(rows))
            return out
        ids = iter(rows)
        return [_with_id(item, next(ids).id) if item.invocation_id is not None else item for item in out]


def count_answer_write_failure(db: Session, workspace_id: UUID, purpose: str, *, lost: int) -> int:
    """Count a failed answer insert; past the threshold keep one open ``reconciliation``
    incident for (workspace, purpose). Never raises (the decision already stands)."""

    with _WRITE_FAILURES_LOCK:
        WRITE_FAILURES[(workspace_id, purpose)] += 1
        count = WRITE_FAILURES[(workspace_id, purpose)]
    logger.warning("semantic answer rows lost", extra={"purpose": purpose, "answer_write_failures": count,
                                                       "rows_lost": lost})
    if count < ANSWER_WRITE_FAILURE_THRESHOLD:
        return count
    try:
        with Session(bind=db.get_bind()) as session:
            open_incident = session.scalar(select(AiIncident.id).where(
                AiIncident.workspace_id == workspace_id, AiIncident.kind == "reconciliation",
                AiIncident.status == "open", AiIncident.subject_kind == "purpose",
                AiIncident.subject_key == purpose).limit(1))
            if open_incident is None:  # the next incident needs another threshold of failures
                with _WRITE_FAILURES_LOCK:
                    WRITE_FAILURES[(workspace_id, purpose)] = 0
                session.add(AiIncident(
                    workspace_id=workspace_id, kind="reconciliation", subject_kind="purpose", subject_key=purpose,
                    action="none", status="open",
                    evidence={"table": "semantic_decision_answers", "failed_inserts": count, "rows_lost": lost}))
                session.commit()
    except Exception:  # noqa: BLE001 - the counter and the log line remain
        logger.exception("could not open the reconciliation incident")
    return count


def _with_id(resolution: Resolution, answer_id: UUID) -> Resolution:
    from dataclasses import replace

    return replace(resolution, answer_id=answer_id)
