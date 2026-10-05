"""The AI-off semantic port (ADR 0008 §8): the rule's answer, no gateway call, no row."""

from __future__ import annotations

from sqlalchemy.orm import Session

from app.agents.semantic.port import Resolution, SemanticAsk, SemanticOutcome, Validator


class DeterministicSemanticPort:
    def resolve(self, db: Session, ask: SemanticAsk, *, validator: Validator | None = None) -> SemanticOutcome:
        return SemanticOutcome(purpose=ask.purpose, ai="off", resolutions=tuple(
            Resolution(question_key=s.question_key, column_id=s.column_id, rule_answer=s.rule_answer,
                       value_used=s.rule_answer, agreement="off")
            for s in ask.subjects
        ))
