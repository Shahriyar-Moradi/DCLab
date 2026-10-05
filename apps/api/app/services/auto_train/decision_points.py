"""Hybrid decision points inside auto-train (P6.9-A step A1; ADR 0008 §2, §2c, §5, §7, §8).

The Jev-backed cross-check points: ``feature.leakage_suspect`` in ``run_train_only_decisions``
and ``column.is_identifier`` / ``column.semantic_role`` in ``run_column_roles``. Per point:
the rule answer is computed first (it is today's value); with AI on, the semantic port asks
Jev with band evidence computed on the **locked training partition only** (``locked_train``
/ ``engineered_train``, never ``inp.profile``; no sample values) and applies the level
snapshotted at job claim; ``decision_point_service.resolve`` applies the precedence
(branch/human override > AI at L2, validator-accepted > rule). Only ``column.semantic_role``
can change a value, and only numeric ↔ categorical for a column the rule already models;
every exclusion answer stays ≤ L1 (a review item). New AI decisions happen on root runs
only (a branch run is rule + its change set, ``ai: "off"``; inherited values are step A2).

AI off (the default) asks nothing and writes nothing: each point emits only its
``decision_point_resolved`` event with ``ai: "off"``. A failing AI path never fails the
run: every error is the rule value with ``agreement: unavailable`` (reads on the run's
session go through savepoints). An AI value is applied only when its record can be
written (the run's experiment has a project), and a branch of a run whose AI values
changed the modeled roles fails closed until step A2 inherits them.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
from collections.abc import Callable
from typing import Any
from uuid import UUID

import pandas as pd
from sqlalchemy import select

from app.agents.contracts import Untrusted
from app.agents.governance.snapshot import PolicySnapshot, take_snapshot
from app.agents.semantic.deterministic import DeterministicSemanticPort
from app.agents.semantic.port import Resolution, SemanticAsk, SemanticOutcome, Subject, semantic_port
from app.agents.semantic.releases import cardinality_band, name_tokens, ratio_band
from app.db.models import Dataset, DatasetColumn, Experiment
from app.engine.lab.auto_prepare import MAX_CATEGORICAL_CARDINALITY
from app.engine.lab.schema_inference import IDENTIFIER_UNIQUE_RATIO
from app.domain.errors import InvalidChangeSetError
from app.engine.validation.splits import SOURCE_ROW_COLUMN
from app.services.decision_point_service import (
    EVENT_STAGE,
    EVENT_TYPE,
    PointResolution,
    idempotency_key,
    record_details,
    resolve,
    write_record,
)
from app.services.decision_record_service import existing_record

logger = logging.getLogger("app.services.auto_train_service")

LEAKAGE, IDENTIFIER, ROLE = "feature.leakage_suspect", "column.is_identifier", "column.semantic_role"
JEV_POINTS = (IDENTIFIER, ROLE, LEAKAGE)
EVIDENCE_PARTITION = "train"
MAX_SUBJECTS_PER_ASK = 512
_MODELED_ROLES = ("numeric", "categorical_code")
_TASKS = {"binary": "binary_classification", "multiclass": "multiclass_classification", "regression": "regression"}
_AVAILABILITY = {"known_before_prediction": "before_prediction", "known_at_prediction": "before_prediction",
                 "known_after_prediction": "after_outcome"}
_DATE_LIKE = re.compile(r"^\d{4}-\d{1,2}-\d{1,2}|^\d{1,2}[/.-]\d{1,2}[/.-]\d{2,4}")


class RunDecisionPoints:
    """The run's snapshot and its resolved points (``RunContext.decisions``)."""

    def __init__(self, snapshot: PolicySnapshot) -> None:
        self.snapshot = snapshot
        self.resolved: dict[str, PointResolution] = {}
        self.record_ids: dict[str, UUID] = {}
        self._column_ids: dict[str, UUID] | None = None
        self._target: tuple[UUID, UUID, UUID] | None | object = _UNSET

    @property
    def ai_on(self) -> bool:
        return self.snapshot.ai == "on"

    @property
    def fingerprint_digest(self) -> str | None:
        """The snapshot's fingerprint digest, plus the values AI actually applied (two L2
        runs under one policy can apply different values: their candidates differ)."""

        digest = self.snapshot.fingerprint_digest
        applied = {key: {a.question_key: a.used for a in item.applied()} for key, item in self.resolved.items()}
        applied = {key: values for key, values in applied.items() if values}
        if digest is None or not applied:
            return digest
        return hashlib.sha256(json.dumps({"policy_digest": digest, "applied": applied}, sort_keys=True,
                                         default=str).encode()).hexdigest()

    def target(self, ctx: Any) -> tuple[UUID, UUID, UUID] | None:
        """(workspace, project, experiment) the records are written against: the upload's
        pipeline-run experiment, which ``run_preprocessing_setup`` binds. None when it has no
        project or workflow run (then no record can be written, so no AI value is applied)."""

        if self._target is _UNSET:
            self._target = _guarded(ctx.db, lambda: _record_target(ctx), None)
        return self._target  # type: ignore[return-value]

    def levels(self, ctx: Any, key: str) -> dict[str, int]:
        """The snapshot's levels; at most L1 when the record's experiment cannot take a record."""

        levels = self.snapshot.levels_for(key)
        return levels if self.target(ctx) is not None else {kind: min(level, 1) for kind, level in levels.items()}

    def keep(self, ctx: Any, resolution: PointResolution) -> PointResolution:
        """Record first, then use: with AI on the point's record is written now, before any
        applied value reaches the Pipeline; if it cannot be written, applied AI values fall
        back to the rule value (no run trains on an unrecorded AI value)."""

        if resolution.ai == "on" and resolution.answers and not self._record(ctx, resolution) and resolution.applied():
            resolution = resolution.rule_fallback("record_write_failed")
        self.resolved[resolution.key] = resolution
        ctx.emit_event(EVENT_STAGE, EVENT_TYPE, "completed", resolution.event_payload())
        return resolution

    def _record(self, ctx: Any, resolution: PointResolution) -> bool:
        target = self.target(ctx)
        if target is None:
            return False
        workspace_id, project_id, experiment_id = target
        db = ctx.db
        try:
            existing = existing_record(db, workspace_id=workspace_id,
                                       idempotency_key=idempotency_key(experiment_id, resolution.key))
            if existing is not None:  # a re-run of this upload: it records these values only if equal
                if resolution.applied() and (existing.details or {}).get("revert") != record_details(resolution)["revert"]:
                    return False
                self.record_ids[resolution.key] = existing.id
                return True
            with db.begin_nested():
                row = write_record(db, resolution, workspace_id=workspace_id, project_id=project_id,
                                   experiment_id=experiment_id)
                db.flush()
            db.commit()
        except Exception:  # noqa: BLE001 - the audit row is lost; applied values fall back
            logger.exception("could not write the %s record for %s", resolution.key, experiment_id)
            return False
        if row is None:
            return False
        self.record_ids[resolution.key] = row.id
        return True

    def column_ids(self, ctx: Any) -> dict[str, UUID]:
        """Source columns of the upload's dataset (the gateway re-checks names and labels)."""

        if self._column_ids is None:
            upload = ctx.upload
            self._column_ids = {} if upload is None or upload.dataset_id is None else _guarded(
                ctx.db, lambda: dict(ctx.db.execute(select(DatasetColumn.name, DatasetColumn.id).where(
                    DatasetColumn.dataset_id == upload.dataset_id,
                    DatasetColumn.workspace_id == upload.workspace_id)).all()), {})
        return self._column_ids

    def evidence(self) -> dict[str, Any] | None:
        """Run evidence (ADR 0008 §2c); None when AI is off, so AI-off results are unchanged."""

        if not self.ai_on:
            return None
        return {
            "policy_digest": self.snapshot.digest,
            "fingerprint_digest": self.fingerprint_digest,
            "points": {
                key: {"level": item.level, "agreement": item.agreement(), "columns_total": len(item.answers),
                      "ai_applied": len(item.applied()), "review_items": len(item.reviews()),
                      "evidence_partition": item.evidence_partition,
                      "record_id": str(self.record_ids[key]) if key in self.record_ids else None}
                for key, item in self.resolved.items()
            },
        }



_UNSET = object()


def _record_target(ctx: Any) -> tuple[UUID, UUID, UUID] | None:
    upload = ctx.upload
    shell = ctx.db.get(Experiment, upload.experiment_id) if upload.experiment_id is not None else None
    if shell is None or shell.project_id is None or shell.workflow_run_id is None:
        return None
    return shell.workspace_id, shell.project_id, shell.id


def _guarded(db: Any, read: Callable[[], Any], default: Any) -> Any:
    """A read on the run's session inside a savepoint: a database error never poisons the run."""

    try:
        with db.begin_nested():
            return read()
    except Exception:  # noqa: BLE001 - AI can never fail a run
        logger.exception("decision point read failed; the rule value stands")
        return default


def _parent_applied_ai_roles(db: Any, branch: Any) -> bool:
    parent = db.get(Experiment, branch.parent_id) if getattr(branch, "parent_id", None) else None
    points = ((parent.result or {}).get("decision_points") or {}).get("points") or {} if parent is not None else {}
    return any(int((item or {}).get("ai_applied") or 0) > 0 for item in points.values())


def start_decision_points(ctx: Any) -> RunDecisionPoints:
    """Snapshot the policy at job claim (root runs only)."""

    if ctx.branch is not None:
        if _parent_applied_ai_roles(ctx.db, ctx.branch):  # A2 inherits them as ai_inherited overrides
            raise InvalidChangeSetError(
                "ai_values_not_inherited", "the parent applied AI values; branching it needs inherited overrides",
                path="parent")
        return RunDecisionPoints(PolicySnapshot.off("branch_run"))
    try:
        port = semantic_port()
    except Exception:  # noqa: BLE001 - AI can never fail a run
        logger.exception("semantic port unavailable; decision points run rule-only")
        return RunDecisionPoints(PolicySnapshot.off("snapshot_unavailable"))
    if isinstance(port, DeterministicSemanticPort):  # AI off: no read, no savepoint
        return RunDecisionPoints(PolicySnapshot.off("ai_disabled", port))
    snapshot = _guarded(ctx.db, lambda: take_snapshot(ctx.db, ctx.upload.workspace_id, JEV_POINTS, port=port), None)
    return RunDecisionPoints(snapshot or PolicySnapshot.off("snapshot_unavailable"))


def decision_points(ctx: Any) -> RunDecisionPoints:
    if getattr(ctx, "decisions", None) is None:
        ctx.decisions = RunDecisionPoints(PolicySnapshot.off("not_started"))
    return ctx.decisions


# --- evidence bands (train partition; values never leave) --------------------------------


def _dtype(series: pd.Series) -> str:
    if pd.api.types.is_bool_dtype(series):
        return "boolean"
    if pd.api.types.is_integer_dtype(series):
        return "integer"
    if pd.api.types.is_float_dtype(series):
        return "float"
    if pd.api.types.is_datetime64_any_dtype(series):
        return "datetime"
    if isinstance(series.dtype, pd.CategoricalDtype):
        return "categorical"
    if pd.api.types.is_object_dtype(series) or pd.api.types.is_string_dtype(series):
        return "string"
    return "other"


def _value_pattern(series: pd.Series) -> str:
    if _dtype(series) != "string":
        return "not_text"
    sample = series.dropna().astype(str).head(200)
    if sample.empty:
        return "short_text"
    if float(sample.str.fullmatch(r"\d+").mean()) >= 0.9:
        return "digits"
    if float(sample.map(lambda value: bool(_DATE_LIKE.match(value))).mean()) >= 0.8:
        return "date_like"
    if float(sample.str.len().mean()) > 40 or float(sample.str.count(r"\s").mean()) >= 3:
        return "long_text"
    if float(sample.str.contains(r"\d").mean()) >= 0.5 and float(sample.str.contains(r"[A-Za-z]").mean()) >= 0.5:
        return "mixed"
    return "short_text"


def _unique_ratio(series: pd.Series) -> float:
    return float(series.nunique(dropna=True)) / max(len(series), 1)


def identifier_fields(series: pd.Series, name: str) -> dict[str, Any]:
    return {"dtype": _dtype(series), "name_tokens": [Untrusted(untrusted_text=t) for t in name_tokens(name)],
            "uniqueness": ratio_band(_unique_ratio(series)), "nulls": ratio_band(float(series.isna().mean()))}


def role_fields(series: pd.Series) -> dict[str, Any]:
    return {"dtype": _dtype(series), "cardinality": cardinality_band(int(series.nunique(dropna=True))),
            "value_pattern": _value_pattern(series), "nulls": ratio_band(float(series.isna().mean()))}


# --- asking ----------------------------------------------------------------------------


def _off(key: str, rules: dict[str, Any]) -> SemanticOutcome:
    return SemanticOutcome(purpose=key, ai="off", resolutions=tuple(
        Resolution(question_key=name, column_id=None, rule_answer=rule, value_used=rule, agreement="off")
        for name, rule in rules.items()))


def _unavailable(name: str, rule: Any, refusal: str) -> Resolution:
    return Resolution(question_key=name, column_id=None, rule_answer=rule, value_used=rule,
                      agreement="unavailable", refusal=refusal)


def _attribution(ctx: Any) -> dict[str, Any]:
    """Ids the gateway attributes the call to; only ids consistent with the run's project
    (the answer rows carry composite project FKs)."""

    db, upload = ctx.db, ctx.upload
    shell = db.get(Experiment, upload.experiment_id) if upload.experiment_id is not None else None
    project_id = shell.project_id if shell is not None else None
    dataset = db.get(Dataset, upload.dataset_id) if upload.dataset_id is not None else None
    return {
        "project_id": project_id,
        "experiment_id": shell.id if shell is not None and project_id is not None else None,
        "workflow_run_id": shell.workflow_run_id if shell is not None else None,
        "dataset_id": dataset.id if dataset is not None and project_id is not None
        and dataset.project_id == project_id else None,
    }


def _ask(ctx: Any, points: RunDecisionPoints, key: str, rules: dict[str, Any],
         fields: Callable[[str], dict[str, Any]], *, context: dict[str, Any] | None = None,
         context_columns: dict[str, UUID] | None = None, validator: Any = None) -> tuple[SemanticOutcome, str | None]:
    """The port's outcome for every rule question (unmapped columns: ``unavailable``)."""

    ids = points.column_ids(ctx)
    asked: dict[str, Resolution] = {}
    names = [n for n in rules if n in ids and len(n) <= 200]
    try:
        with ctx.db.begin_nested():  # the port reads on the run's session: never poison it
            base = {"purpose": key, "workspace_id": ctx.upload.workspace_id, **_attribution(ctx),
                    "context": dict(context or {}), "context_columns": dict(context_columns or {}),
                    "source_datasets": (ctx.upload.dataset_id,), "levels": points.levels(ctx, key),
                    "evidence_partition": EVIDENCE_PARTITION}
            for start in range(0, len(names), MAX_SUBJECTS_PER_ASK):
                subjects = tuple(Subject(question_key=n, column_id=ids[n], rule_answer=rules[n],
                                         fields={"column": Untrusted(untrusted_text=n), **fields(n)})
                                 for n in names[start:start + MAX_SUBJECTS_PER_ASK])
                outcome = points.snapshot.port.resolve(ctx.db, SemanticAsk(**base, subjects=subjects),
                                                       validator=validator)
                if outcome.ai == "off":  # a kill switch is off for the purpose: AI off, no row
                    return _off(key, rules), "kill_switch"
                asked.update({r.question_key: r for r in outcome.resolutions})
    except Exception:  # noqa: BLE001 - the rule value stands
        logger.exception("decision point %s fell back to the rule", key)
        asked = {n: _unavailable(n, rules[n], "hook_error") for n in rules}
    return SemanticOutcome(purpose=key, ai="on", resolutions=tuple(
        asked.get(n) or _unavailable(n, rules[n], "column_unmapped") for n in rules)), None


def _resolve(ctx: Any, key: str, rules: dict[str, Any], fields: Callable[[str], dict[str, Any]], *,
             validator: Any = None, reasons: dict[str, list[str]] | None = None, askable: bool = True,
             legacy: dict[str, Any] | None = None, **ask: Any) -> PointResolution:
    """``legacy``: columns the pre-Phase-6 decision agent already changed (value it applied);
    they are not asked, their rule answer stays the rule's and they are never a revert target."""

    points = decision_points(ctx)
    snapshot = points.snapshot
    reason = snapshot.reason
    legacy = {n: v for n, v in (legacy or {}).items() if n in rules}
    asked = {n: r for n, r in rules.items() if n not in legacy}
    if not points.ai_on:
        outcome = _off(key, rules)
    elif not askable:  # e.g. the leakage context's target name has no source column
        outcome = SemanticOutcome(purpose=key, ai="on", resolutions=tuple(
            _unavailable(n, r, "column_unmapped") for n, r in rules.items()))
    elif asked:
        outcome, reason = _ask(ctx, points, key, asked, fields, validator=validator, **ask)
    else:
        outcome = SemanticOutcome(purpose=key, ai="on", resolutions=())
    if outcome.ai == "off":
        outcome = _off(key, rules)
    elif legacy:
        by_name = {r.question_key: r for r in outcome.resolutions}
        outcome = SemanticOutcome(purpose=key, ai="on", resolutions=tuple(
            by_name.get(n) or _unavailable(n, rules[n], "legacy_override") for n in rules))
    on = outcome.ai == "on"
    return points.keep(ctx, resolve(
        key, outcome, evidence_partition=EVIDENCE_PARTITION, validator_reasons=reasons if on else None,
        legacy=legacy, policy_digest=snapshot.digest if on else None,
        release=(snapshot.releases.get(key) or {}) if on else None, reason=None if on else reason))


# --- the points --------------------------------------------------------------------------


def resolve_leakage_point(ctx: Any, *, locked_train: pd.DataFrame, target: Any, audit: Any,
                          development_plan: Any) -> PointResolution:
    """``feature.leakage_suspect`` (cap L1: the AI may only add a review flag; the rule's
    exclusions are unchanged). Rule: excluded by the plan → ``exclude``; kept with a warning
    → ``review_flag``; else ``clear``. Identifier and reserved columns are not leakage questions."""

    excluded = {item["column"] for item in development_plan.excluded_features}
    reserved = {target.column, SOURCE_ROW_COLUMN, development_plan.group_column}
    rules: dict[str, Any] = {}
    availability: dict[str, str] = {}
    for risk in audit.risks:
        if risk.column in reserved or (risk.evidence or {}).get("identifier") or risk.column not in locked_train:
            continue
        rules[risk.column] = ("exclude" if risk.column in excluded
                              else "review_flag" if risk.action in ("keep_with_warning", "requires_review")
                              else "clear")
        status = risk.availability.status if risk.availability is not None else "unknown"
        availability[risk.column] = _AVAILABILITY.get(str(status), "unknown")
    points = decision_points(ctx)
    target_id = points.column_ids(ctx).get(target.column) if points.ai_on else None

    def fields(name: str) -> dict[str, Any]:
        return {"dtype": _dtype(locked_train[name]), "availability": availability[name]}

    return _resolve(ctx, LEAKAGE, rules, fields, askable=target_id is not None,
                    context={"target": Untrusted(untrusted_text=target.column),
                             "task": _TASKS.get(str(target.task_type), "regression")},
                    context_columns={"target": target_id} if target_id else None)


class RoleValidator:
    """Deterministic validator of an L2 ``column.semantic_role`` value (ADR 0008 §1b, §2):
    numeric ↔ categorical only, for a column the rule already models; never the validation
    group / entity column; never a very-high-uniqueness column; categorical within the
    engine's one-hot cardinality cap; numeric only for a numeric (non-boolean) column; and
    consistent with the column's missing-value action (else the rule stands for both)."""

    def __init__(self, frame: pd.DataFrame, num_cols: list[str], cat_cols: list[str], *, protected: set[str],
                 missing_actions: dict[str, str]) -> None:
        self.frame, self.num, self.cat = frame, set(num_cols), set(cat_cols)
        self.protected, self.missing_actions = {p for p in protected if p}, missing_actions
        self.reasons: dict[str, list[str]] = {}

    def check(self, column: str, value: Any) -> list[str]:
        rule = "numeric" if column in self.num else "categorical_code" if column in self.cat else None
        reasons = []
        if rule is None:
            reasons.append("not_modeled_by_rule")
        if value not in _MODELED_ROLES or value == rule:
            reasons.append("not_a_numeric_categorical_change")
        if column in self.protected:
            reasons.append("group_or_entity_column")
        if column not in self.frame.columns:
            return [*reasons, "unknown_column"]
        series = self.frame[column]
        if _unique_ratio(series) > IDENTIFIER_UNIQUE_RATIO:
            reasons.append("very_high_uniqueness")
        if value == "numeric" and not (pd.api.types.is_numeric_dtype(series) and not pd.api.types.is_bool_dtype(series)):
            reasons.append("not_numeric")
        if value == "categorical_code" and int(series.nunique(dropna=True)) > MAX_CATEGORICAL_CARDINALITY:
            reasons.append("above_one_hot_cardinality_cap")
        action = self.missing_actions.get(column)
        needed = "impute_median" if value == "numeric" else "impute_most_frequent"
        if action in ("impute_median", "impute_most_frequent") and action != needed:
            reasons.append("conflicts_with_missing_value_action")
        return reasons

    def __call__(self, subject: Subject, value: Any) -> bool:
        self.reasons[subject.question_key] = self.check(subject.question_key, value)
        return not self.reasons[subject.question_key]


def resolve_column_points(
    ctx: Any, *, engineered_train: pd.DataFrame, kept_columns: list[str], rule_numeric: list[str],
    rule_categorical: list[str], rule_identifiers: list[str], legacy_numeric: list[str],
    legacy_categorical: list[str], num_cols: list[str], cat_cols: list[str], identifier_cols: list[str],
    ignored: list[str], transformed_datetime: set[str], protected: set[str], missing_actions: dict[str, str],
    leakage_excluded: set[str] | None = None,
) -> tuple[list[str], list[str], set[str]]:
    """``column.is_identifier`` (cap L1) then ``column.semantic_role`` (L2 for numeric ↔
    categorical among modeled columns). Rule answers come from ``split_column_roles`` /
    the identifier rule **before** the legacy column-type writer (``rule_*``); a column that
    writer changed (``legacy_*``, ``identifier_cols``) is not asked and keeps its legacy
    value. Returns the modeled (numeric, categorical) lists with any applied L2 values and
    the re-typed columns; branch overrides apply afterwards (they take precedence)."""

    names = [c for c in kept_columns if c in engineered_train.columns]
    rule_ids, used_ids = set(rule_identifiers), set(identifier_cols)
    _resolve(ctx, IDENTIFIER, {c: c in rule_ids for c in names}, lambda c: identifier_fields(engineered_train[c], c),
             legacy={c: c in used_ids for c in names if (c in rule_ids) != (c in used_ids)})

    def role_of(column: str, numeric: list[str], categorical: list[str], identifiers: set[str]) -> str:
        if column in numeric:
            return "datetime" if column in transformed_datetime else "numeric"
        if column in categorical:
            return "categorical_code"
        if column in identifiers:
            return "identifier"
        return "free_text" if column in ignored else "other"

    validator = RoleValidator(engineered_train, num_cols, cat_cols, protected=protected,
                              missing_actions=missing_actions)
    # Columns the leakage plan excluded are not role questions (their role is moot).
    role_names = [c for c in names if c in rule_ids or c not in (leakage_excluded or set())]
    rules = {c: role_of(c, rule_numeric, rule_categorical, rule_ids) for c in role_names}
    used = {c: role_of(c, legacy_numeric, legacy_categorical, used_ids) for c in role_names}
    resolution = _resolve(ctx, ROLE, rules, lambda c: role_fields(engineered_train[c]), validator=validator,
                          reasons=validator.reasons, legacy={c: v for c, v in used.items() if v != rules[c]})
    num, cat, applied = list(num_cols), list(cat_cols), set()
    for answer in resolution.applied():  # defence in depth: re-check before changing the Pipeline's roles
        column = answer.question_key
        if answer.used not in _MODELED_ROLES or validator.check(column, answer.used):
            continue
        num = [c for c in num if c != column]
        cat = [c for c in cat if c != column]
        (num if answer.used == "numeric" else cat).append(column)
        applied.add(column)
    return num, cat, applied


__all__ = ["JEV_POINTS", "RoleValidator", "RunDecisionPoints", "decision_points", "resolve_column_points",
           "resolve_leakage_point", "start_decision_points"]
