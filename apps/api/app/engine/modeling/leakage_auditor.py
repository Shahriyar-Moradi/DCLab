"""Train-only prediction-time leakage auditor and ModelDevelopmentPlan.

Does not redesign ProblemProfile, ValidationPlan, or MetricPlan. Never inspects
the locked holdout. Name tokens or high correlation alone never exclude a column.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from difflib import SequenceMatcher
from typing import Any, Literal

import numpy as np
import pandas as pd
from sklearn.metrics import roc_auc_score

from app.engine.lab.evidence import LeakageReviewEvidence
from app.engine.lab.schema_inference import identifier_likelihood, looks_like_identifier, normalize_name
from app.engine.modeling.metric_planner import MetricPlan, plan_metrics
from app.engine.modeling.problem_profile import ProblemProfile, build_problem_profile
from app.engine.modeling.validation_planner import ValidationPlan, plan_validation
from app.engine.validation.splits import SOURCE_ROW_COLUMN

PLAN_VERSION = "dclab.model_development_plan.v1"
LEAKAGE_AUDIT_VERSION = "dclab.leakage_audit.v1"
AvailabilityStatus = Literal[
    "known_before_prediction",
    "known_at_prediction",
    "known_after_prediction",
    "unknown",
]
AvailabilitySource = Literal["deterministic", "llm", "explicit_configuration"]
RiskLevel = Literal["NONE", "LOW", "MEDIUM", "HIGH", "CRITICAL"]
LeakageAction = Literal["keep", "keep_with_warning", "requires_review", "exclude"]

SUSPICIOUS_PREFIXES = (
    "final_",
    "actual_",
    "realized_",
    "completed_",
    "resolved_",
    "post_",
    "after_",
    "future_",
    "result_",
    "label_",
    "target_",
)
SUSPICIOUS_TOKENS = {
    "final",
    "actual",
    "realized",
    "completed",
    "resolved",
    "post",
    "after",
    "future",
    "result",
    "label",
    "target",
    "outcome",
    "oracle",
}
FUTURE_TOKENS = {
    "final",
    "actual",
    "realized",
    "completed",
    "resolved",
    "post",
    "after",
    "future",
    "result",
}

EXACT_MATCH_THRESHOLD = 0.999
AFTER_FRACTION_THRESHOLD = 0.05
STRONG_AUC = 0.97
STRONG_R2 = 0.95
STRONG_PURITY = 0.99
MODERATE_AUC = 0.80
MODERATE_R2 = 0.80
MODERATE_PURITY = 0.90
NAME_SIMILARITY_THRESHOLD = 0.85
RELATED_NAME_LIMIT = 40

LeakageReviewer = Callable[[LeakageReviewEvidence], Any]
PlanningEventCallback = Callable[[str, dict[str, Any]], None]

_EVENT_NAME_CAP = 40
_EVENT_ITEM_CAP = 20


def _native(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): _native(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_native(item) for item in value]
    if isinstance(value, np.generic):
        value = value.item()
    if isinstance(value, float) and not np.isfinite(value):
        return None
    return value


@dataclass
class FeatureAvailabilityAssessment:
    column: str
    status: AvailabilityStatus
    confidence: float
    reason: str
    evidence: dict[str, Any] = field(default_factory=dict)
    source: AvailabilitySource = "deterministic"

    def to_dict(self) -> dict[str, Any]:
        return _native(asdict(self))


@dataclass
class LeakageRisk:
    column: str
    risk: RiskLevel
    action: LeakageAction
    reasons: list[str]
    evidence: dict[str, Any] = field(default_factory=dict)
    availability: FeatureAvailabilityAssessment | None = None
    llm_consulted: bool = False
    llm_accepted: bool = False
    llm_validator_reason: str = ""

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        if self.availability is not None:
            payload["availability"] = self.availability.to_dict()
        return _native(payload)


@dataclass
class LeakageAuditResult:
    assessments: list[FeatureAvailabilityAssessment]
    risks: list[LeakageRisk]
    partition: str = "train"
    version: str = LEAKAGE_AUDIT_VERSION

    def to_dict(self) -> dict[str, Any]:
        return {
            "assessments": [item.to_dict() for item in self.assessments],
            "risks": [item.to_dict() for item in self.risks],
            "partition": self.partition,
            "version": self.version,
        }

    def risk_for(self, column: str) -> LeakageRisk | None:
        return next((item for item in self.risks if item.column == column), None)


@dataclass
class ModelDevelopmentPlan:
    problem_profile: dict[str, Any]
    validation_plan: dict[str, Any]
    metric_plan: dict[str, Any]
    feature_availability: list[dict[str, Any]]
    leakage_assessment: dict[str, Any]
    allowed_features: list[str]
    excluded_features: list[dict[str, Any]]
    group_column: str | None
    time_column: str | None
    recommended_model_family_hints: list[str]
    plan_version: str = PLAN_VERSION

    def to_dict(self) -> dict[str, Any]:
        return _native(asdict(self))

    @classmethod
    def from_dict(cls, payload: Any) -> ModelDevelopmentPlan:
        from app.engine.modeling.coerce import from_mapping

        plan = from_mapping(cls, payload)
        if plan is None:
            raise ValueError("ModelDevelopmentPlan evidence is missing.")
        return plan


@dataclass
class _ColumnSignals:
    column: str
    dtype: str
    cardinality: int
    unique_ratio: float
    missing_fraction: float
    identifier: bool
    identifier_likelihood: float
    suspicious_tokens: list[str]
    target_name_similarity: float
    exact_match_fraction: float
    complement_match_fraction: float
    single_feature_score: float | None
    single_feature_score_kind: str | None
    datetime_after_fraction: float | None
    class_purity: float | None


def _tokens(name: str) -> set[str]:
    return set(normalize_name(name).split("_")) - {""}


def _suspicious_tokens(name: str) -> list[str]:
    key = normalize_name(name)
    found: list[str] = []
    for prefix in SUSPICIOUS_PREFIXES:
        if key.startswith(prefix) or f"_{prefix}" in f"_{key}_":
            token = prefix.rstrip("_")
            if token not in found:
                found.append(token)
    for token in sorted(_tokens(name) & SUSPICIOUS_TOKENS):
        if token not in found:
            found.append(token)
    return found


def _name_similarity(column: str, target: str) -> float:
    left = normalize_name(column)
    right = normalize_name(target)
    if not left or not right:
        return 0.0
    if left == right:
        return 1.0
    ratio = SequenceMatcher(None, left, right).ratio()
    left_tokens = _tokens(column)
    right_tokens = _tokens(target)
    overlap = len(left_tokens & right_tokens) / max(len(left_tokens | right_tokens), 1)
    return float(max(ratio, overlap))


def _parse_datetime(series: pd.Series) -> pd.Series:
    parsed = pd.to_datetime(series, errors="coerce", utc=True)
    if getattr(parsed.dt, "tz", None) is not None:
        return parsed.dt.tz_convert(None)
    return parsed


def _exact_match_fraction(series: pd.Series, target: pd.Series) -> float:
    mask = series.notna() & target.notna()
    if int(mask.sum()) == 0:
        return 0.0
    left = series[mask]
    right = target[mask]
    if pd.api.types.is_numeric_dtype(left) and pd.api.types.is_numeric_dtype(right):
        a = pd.to_numeric(left, errors="coerce")
        b = pd.to_numeric(right, errors="coerce")
        valid = a.notna() & b.notna()
        if not bool(valid.any()):
            return 0.0
        return float(np.isclose(a[valid].to_numpy(dtype=float), b[valid].to_numpy(dtype=float)).mean())
    return float((left.astype(str) == right.astype(str)).mean())


def _complement_match_fraction(series: pd.Series, target: pd.Series) -> float:
    y = pd.to_numeric(target, errors="coerce")
    x = pd.to_numeric(series, errors="coerce")
    mask = x.notna() & y.notna()
    if int(mask.sum()) < 2:
        return 0.0
    yv = y[mask]
    xv = x[mask]
    if set(pd.unique(yv)) - {0, 1} or set(pd.unique(xv)) - {0, 1}:
        return 0.0
    return float((xv == (1 - yv)).mean())


def _class_purity(series: pd.Series, target: pd.Series) -> float | None:
    frame = pd.DataFrame({"x": series, "y": target}).dropna()
    if len(frame) < 20 or int(frame["x"].nunique()) < 2 or int(frame["y"].nunique()) < 2:
        return None
    majority = frame.groupby("x")["y"].agg(lambda values: float(values.value_counts(normalize=True).iloc[0]))
    weights = frame.groupby("x").size()
    aligned = majority.reindex(weights.index)
    return float((aligned * weights).sum() / max(float(weights.sum()), 1.0))


def _single_feature_score(
    series: pd.Series,
    target: pd.Series,
    task_type: str,
) -> tuple[float | None, str | None]:
    mask = series.notna() & target.notna()
    if int(mask.sum()) < 20:
        return None, None
    y = target[mask]
    if task_type in {"binary", "multiclass"}:
        numeric = pd.to_numeric(series[mask], errors="coerce")
        if numeric.notna().sum() >= 20 and int(numeric.nunique()) >= 2 and int(y.nunique()) >= 2:
            try:
                auc = float(roc_auc_score(y[numeric.notna()], numeric[numeric.notna()]))
                return max(auc, 1.0 - auc), "auc"
            except ValueError:
                pass
        codes = pd.Series(pd.Categorical(series[mask]).codes, index=series[mask].index)
        if int(codes.nunique()) >= 2 and int(y.nunique()) == 2:
            try:
                auc = float(roc_auc_score(y, codes))
                return max(auc, 1.0 - auc), "auc"
            except ValueError:
                pass
        purity = _class_purity(series, target)
        return (purity, "purity") if purity is not None else (None, None)

    numeric = pd.to_numeric(series, errors="coerce")
    y_num = pd.to_numeric(target, errors="coerce")
    aligned = pd.concat([numeric, y_num], axis=1).dropna()
    if len(aligned) < 20:
        return None, None
    if int(aligned.iloc[:, 0].nunique()) < 2 or int(aligned.iloc[:, 1].nunique()) < 2:
        return None, None
    corr = aligned.iloc[:, 0].corr(aligned.iloc[:, 1])
    if corr is None or not np.isfinite(corr):
        return None, None
    return float(corr**2), "r2"


def _looks_like_datetime(series: pd.Series, name: str) -> bool:
    if pd.api.types.is_datetime64_any_dtype(series):
        return True
    key = normalize_name(name)
    tokens = _tokens(name)
    named = bool(
        tokens
        & {
            "date",
            "time",
            "datetime",
            "timestamp",
            "asof",
            "completed",
            "resolved",
            "closed",
            "created",
            "updated",
        }
    ) or any(needle in key for needle in ("date", "time", "_at", "as_of", "asof"))
    if not named:
        return False
    parsed = _parse_datetime(series)
    return float(parsed.notna().mean()) >= 0.8


def _datetime_after_fraction(
    series: pd.Series,
    time_series: pd.Series | None,
    *,
    column: str,
) -> float | None:
    if time_series is None or not _looks_like_datetime(series, column):
        return None
    feature = _parse_datetime(series)
    cutoff = _parse_datetime(time_series)
    valid = feature.notna() & cutoff.notna()
    if int(valid.sum()) < 5:
        return None
    return float((feature[valid] > cutoff[valid]).mean())


def _strong_stats(signals: _ColumnSignals) -> bool:
    score = signals.single_feature_score
    if score is None:
        return False
    if signals.single_feature_score_kind == "auc":
        return score >= STRONG_AUC
    if signals.single_feature_score_kind == "r2":
        return score >= STRONG_R2
    if signals.single_feature_score_kind == "purity":
        return score >= STRONG_PURITY
    return False


def _moderate_stats(signals: _ColumnSignals) -> bool:
    score = signals.single_feature_score
    if score is None:
        return False
    if signals.single_feature_score_kind == "auc":
        return score >= MODERATE_AUC
    if signals.single_feature_score_kind == "r2":
        return score >= MODERATE_R2
    if signals.single_feature_score_kind == "purity":
        return score >= MODERATE_PURITY
    return False


def _collect_signals(
    train: pd.DataFrame,
    column: str,
    *,
    target: str,
    task_type: str,
    time_column: str | None,
    identifier_columns: set[str],
) -> _ColumnSignals:
    series = train[column]
    y = train[target]
    n = max(len(train), 1)
    observed = series.dropna()
    cardinality = int(observed.nunique())
    likelihood = float(identifier_likelihood(column, series, n))
    datetime_like = _looks_like_datetime(series, column)
    identifier = (column in identifier_columns or looks_like_identifier(column, series, n)) and not datetime_like
    time_series = train[time_column] if time_column and time_column in train.columns else None
    score, kind = _single_feature_score(series, y, task_type)
    return _ColumnSignals(
        column=column,
        dtype=str(series.dtype),
        cardinality=cardinality,
        unique_ratio=float(cardinality / n),
        missing_fraction=float(series.isna().mean()),
        identifier=identifier,
        identifier_likelihood=likelihood,
        suspicious_tokens=_suspicious_tokens(column),
        target_name_similarity=_name_similarity(column, target),
        exact_match_fraction=_exact_match_fraction(series, y),
        complement_match_fraction=_complement_match_fraction(series, y),
        single_feature_score=score,
        single_feature_score_kind=kind,
        datetime_after_fraction=_datetime_after_fraction(series, time_series, column=column),
        class_purity=_class_purity(series, y) if task_type in {"binary", "multiclass"} else None,
    )


def _availability_from_signals(
    signals: _ColumnSignals,
    *,
    time_column: str | None,
    explicit_status: AvailabilityStatus | None,
) -> FeatureAvailabilityAssessment:
    evidence = {
        "datetime_after_fraction": signals.datetime_after_fraction,
        "suspicious_name_tokens": list(signals.suspicious_tokens),
        "identifier": signals.identifier,
    }
    if explicit_status is not None:
        return FeatureAvailabilityAssessment(
            column=signals.column,
            status=explicit_status,
            confidence=1.0,
            reason="Availability was supplied by explicit configuration.",
            evidence=evidence,
            source="explicit_configuration",
        )
    after = signals.datetime_after_fraction
    if after is not None and after >= AFTER_FRACTION_THRESHOLD:
        return FeatureAvailabilityAssessment(
            column=signals.column,
            status="known_after_prediction",
            confidence=0.92,
            reason="Train-only timestamps occur after the prediction-time column.",
            evidence=evidence,
            source="deterministic",
        )
    if time_column and signals.column == time_column:
        return FeatureAvailabilityAssessment(
            column=signals.column,
            status="known_at_prediction",
            confidence=0.9,
            reason="This column is the prediction-time / as-of timestamp.",
            evidence=evidence,
            source="deterministic",
        )
    if signals.identifier:
        return FeatureAvailabilityAssessment(
            column=signals.column,
            status="known_before_prediction",
            confidence=0.85,
            reason="Identifier-like columns exist before prediction and are not estimators.",
            evidence=evidence,
            source="deterministic",
        )
    if signals.suspicious_tokens and not _strong_stats(signals) and (after is None or after < AFTER_FRACTION_THRESHOLD):
        return FeatureAvailabilityAssessment(
            column=signals.column,
            status="unknown",
            confidence=0.45,
            reason="The name looks post-outcome but train-only statistics are not conclusive.",
            evidence=evidence,
            source="deterministic",
        )
    return FeatureAvailabilityAssessment(
        column=signals.column,
        status="known_before_prediction",
        confidence=0.7,
        reason="No train-only evidence that the feature is created after prediction time.",
        evidence=evidence,
        source="deterministic",
    )


def _direct_duplicate(signals: _ColumnSignals) -> bool:
    return (
        signals.exact_match_fraction >= EXACT_MATCH_THRESHOLD
        or signals.complement_match_fraction >= EXACT_MATCH_THRESHOLD
    )


def _score_risk(
    signals: _ColumnSignals,
    availability: FeatureAvailabilityAssessment,
) -> tuple[RiskLevel, LeakageAction, list[str]]:
    reasons: list[str] = []
    if _direct_duplicate(signals):
        reasons.append("direct_target_duplicate")
        return "CRITICAL", "exclude", reasons

    after = signals.datetime_after_fraction
    after_prediction = availability.status == "known_after_prediction" or (
        after is not None and after >= AFTER_FRACTION_THRESHOLD
    )
    strong_after = after is not None and after >= AFTER_FRACTION_THRESHOLD
    combined_proxy = bool(signals.suspicious_tokens) and _strong_stats(signals)
    similar_proxy = signals.target_name_similarity >= NAME_SIMILARITY_THRESHOLD and _strong_stats(signals)
    if strong_after:
        reasons.append("post_outcome_datetime")
        if signals.suspicious_tokens:
            reasons.append("suspicious_name")
        return "HIGH", "exclude", reasons
    if after_prediction and (_strong_stats(signals) or combined_proxy):
        reasons.append("known_after_prediction")
        if _strong_stats(signals):
            reasons.append("strong_single_feature_score")
        return "HIGH", "exclude", reasons
    if combined_proxy or similar_proxy:
        reasons.append("target_proxy")
        if signals.suspicious_tokens:
            reasons.append("suspicious_name")
        if similar_proxy:
            reasons.append("target_name_similarity")
        reasons.append("strong_single_feature_score")
        return "HIGH", "exclude", reasons

    if signals.identifier:
        reasons.append("identifier_not_a_predictor")
        return "NONE", "exclude", reasons

    ambiguous = (bool(signals.suspicious_tokens) and _moderate_stats(signals) and not _strong_stats(signals)) or (
        (not signals.suspicious_tokens) and _strong_stats(signals)
    )
    if after_prediction and not strong_after:
        reasons.append("ambiguous_after_prediction")
        return "MEDIUM", "requires_review", reasons
    if ambiguous:
        if signals.suspicious_tokens:
            reasons.append("suspicious_name")
        if _strong_stats(signals):
            reasons.append("high_correlation_alone")
        elif _moderate_stats(signals):
            reasons.append("moderate_single_feature_score")
        return "MEDIUM", "requires_review", reasons
    if signals.suspicious_tokens or signals.target_name_similarity >= NAME_SIMILARITY_THRESHOLD:
        reasons.append("suspicious_name" if signals.suspicious_tokens else "target_name_similarity")
        return "LOW", "keep_with_warning", reasons
    return "NONE", "keep", reasons


def _signals_evidence(signals: _ColumnSignals) -> dict[str, Any]:
    return {
        "dtype": signals.dtype,
        "cardinality": signals.cardinality,
        "unique_ratio": signals.unique_ratio,
        "missing_fraction": signals.missing_fraction,
        "identifier": signals.identifier,
        "identifier_likelihood": signals.identifier_likelihood,
        "suspicious_name_tokens": list(signals.suspicious_tokens),
        "target_name_similarity": signals.target_name_similarity,
        "exact_target_match_fraction": signals.exact_match_fraction,
        "complement_match_fraction": signals.complement_match_fraction,
        "single_feature_score": signals.single_feature_score,
        "single_feature_score_kind": signals.single_feature_score_kind,
        "datetime_after_fraction": signals.datetime_after_fraction,
        "class_purity": signals.class_purity,
        "partition": "train",
    }


def _related_column_names(train: pd.DataFrame, *, target: str, column: str) -> list[str]:
    names = [name for name in train.columns if name not in {SOURCE_ROW_COLUMN, column}]
    names = [target] + [name for name in names if name != target]
    return names[:RELATED_NAME_LIMIT]


def build_leakage_review_evidence(
    signals: _ColumnSignals,
    availability: FeatureAvailabilityAssessment,
    *,
    target: str,
    task_type: str,
    related_column_names: list[str],
) -> LeakageReviewEvidence:
    return LeakageReviewEvidence(
        column=signals.column,
        target=target,
        task=task_type,
        dtype=signals.dtype,
        cardinality=signals.cardinality,
        related_column_names=list(related_column_names),
        exact_target_match_fraction=signals.exact_match_fraction,
        single_feature_score=signals.single_feature_score,
        single_feature_score_kind=signals.single_feature_score_kind,
        suspicious_name_tokens=list(signals.suspicious_tokens),
        target_name_similarity=signals.target_name_similarity,
        datetime_after_fraction=signals.datetime_after_fraction,
        identifier_likelihood=signals.identifier_likelihood,
        unique_ratio=signals.unique_ratio,
        missing_fraction=signals.missing_fraction,
        availability_status=availability.status,
        availability_reason=availability.reason,
    )


def _is_ambiguous(
    signals: _ColumnSignals,
    risk: RiskLevel,
    action: LeakageAction,
    availability: FeatureAvailabilityAssessment,
) -> bool:
    """LLM is only for semantic/stat disagreement, never a keep/exclude switch."""
    if _direct_duplicate(signals) or risk in {"HIGH", "CRITICAL"}:
        return False
    if signals.identifier and action == "exclude" and risk in {"NONE", "LOW"}:
        return False
    name = bool(signals.suspicious_tokens)
    if name and not _strong_stats(signals):
        return True
    return availability.status == "unknown"


def _apply_reviewer(
    signals: _ColumnSignals,
    availability: FeatureAvailabilityAssessment,
    *,
    target: str,
    task_type: str,
    related_column_names: list[str],
    reviewer: LeakageReviewer | None,
) -> tuple[FeatureAvailabilityAssessment, bool, bool, str]:
    if reviewer is None:
        return availability, False, False, ""
    evidence = build_leakage_review_evidence(
        signals,
        availability,
        target=target,
        task_type=task_type,
        related_column_names=related_column_names,
    )
    try:
        decision = reviewer(evidence)
    except Exception:
        return availability, True, False, "leakage reviewer failed closed"
    if decision is None:
        return availability, True, False, "leakage reviewer unavailable"
    from app.engine.lab.decision_validator import validate_leakage_review_decision

    check = validate_leakage_review_decision(evidence, decision)
    if check.verdict != "accept":
        return availability, True, False, check.reason
    status = getattr(decision, "availability_status", availability.status)
    if status not in {
        "known_before_prediction",
        "known_at_prediction",
        "known_after_prediction",
        "unknown",
    }:
        return availability, True, False, "accepted recommendation had an invalid availability status"
    updated = FeatureAvailabilityAssessment(
        column=signals.column,
        status=status,
        confidence=float(getattr(decision, "confidence", availability.confidence)),
        reason=str(getattr(decision, "rationale", availability.reason)),
        evidence={**availability.evidence, "llm_risk_level": getattr(decision, "risk_level", None)},
        source="llm",
    )
    return updated, True, True, ""


def audit_leakage(
    train: pd.DataFrame,
    *,
    target: str,
    task_type: str,
    time_column: str | None = None,
    entity_column: str | None = None,
    identifier_columns: list[str] | None = None,
    reviewer: LeakageReviewer | None = None,
    availability_overrides: dict[str, AvailabilityStatus] | None = None,
) -> LeakageAuditResult:
    """Score prediction-time leakage using the locked TRAIN partition only."""
    if target not in train.columns:
        raise ValueError(f"training partition is missing target {target!r}")
    skip = {target, SOURCE_ROW_COLUMN}
    identifiers = set(identifier_columns or [])
    if entity_column:
        identifiers.add(entity_column)
    assessments: list[FeatureAvailabilityAssessment] = []
    risks: list[LeakageRisk] = []
    for column in train.columns:
        if column in skip:
            continue
        signals = _collect_signals(
            train,
            column,
            target=target,
            task_type=task_type,
            time_column=time_column,
            identifier_columns=identifiers,
        )
        availability = _availability_from_signals(
            signals,
            time_column=time_column,
            explicit_status=(availability_overrides or {}).get(column),
        )
        risk, action, reasons = _score_risk(signals, availability)
        llm_consulted = False
        llm_accepted = False
        llm_reason = ""
        if _is_ambiguous(signals, risk, action, availability):
            availability, llm_consulted, llm_accepted, llm_reason = _apply_reviewer(
                signals,
                availability,
                target=target,
                task_type=task_type,
                related_column_names=_related_column_names(train, target=target, column=column),
                reviewer=reviewer,
            )
            risk, action, reasons = _score_risk(signals, availability)
        assessments.append(availability)
        risks.append(
            LeakageRisk(
                column=column,
                risk=risk,
                action=action,
                reasons=reasons,
                evidence=_signals_evidence(signals),
                availability=availability,
                llm_consulted=llm_consulted,
                llm_accepted=llm_accepted,
                llm_validator_reason=llm_reason,
            )
        )
    return LeakageAuditResult(assessments=assessments, risks=risks)


def leakage_report_from_audit(audit: LeakageAuditResult) -> dict[str, Any]:
    findings = [item.to_dict() for item in audit.risks if item.risk != "NONE" or item.action != "keep"]
    high_columns = [item.column for item in audit.risks if item.risk in {"HIGH", "CRITICAL"}]
    overall = "LOW"
    if any(item.risk == "CRITICAL" for item in audit.risks):
        overall = "CRITICAL"
    elif any(item.risk == "HIGH" for item in audit.risks):
        overall = "HIGH"
    elif any(item.risk == "MEDIUM" for item in audit.risks):
        overall = "MEDIUM"
    elif any(item.risk == "LOW" for item in audit.risks):
        overall = "LOW"
    return {
        "risk": overall,
        "findings": findings,
        "high_risk_columns": high_columns,
        "source": "leakage_auditor",
        "partition": "train",
        "version": audit.version,
    }


def recommend_model_family_hints(profile: ProblemProfile) -> list[str]:
    n_num = len(profile.numeric_columns)
    n_cat = len(profile.categorical_columns) + len(profile.boolean_columns)
    n_feat = max(int(profile.feature_count), 1)
    hints: list[str] = []
    if n_feat <= 4 and n_cat <= 1:
        hints.append("logistic/linear models")
    if n_cat >= 3 or (n_cat / n_feat >= 0.4 and n_cat >= 2):
        hints.append("catboost/lightgbm")
    if n_num / n_feat >= 0.5 and n_feat >= 3:
        hints.append("xgboost/lightgbm/tree ensembles")
    if not hints:
        hints.append("xgboost/lightgbm/tree ensembles")
    return hints


def build_model_development_plan(
    *,
    problem_profile: ProblemProfile,
    validation_plan: ValidationPlan,
    metric_plan: MetricPlan,
    audit: LeakageAuditResult,
    conservative_auto_train: bool = True,
) -> ModelDevelopmentPlan:
    reserved = {
        problem_profile.target,
        SOURCE_ROW_COLUMN,
        validation_plan.group_column,
    }
    reserved.discard(None)
    allowed: list[str] = []
    excluded: list[dict[str, Any]] = []
    keep_actions = {"keep", "keep_with_warning"}
    if not conservative_auto_train:
        keep_actions = {"keep", "keep_with_warning", "requires_review"}
    for item in audit.risks:
        availability = item.availability.to_dict() if item.availability is not None else {}
        record = {
            "column": item.column,
            "risk": item.risk,
            "action": item.action,
            "reason": "; ".join(item.reasons) if item.reasons else item.action,
            "reasons": list(item.reasons),
            "availability": availability,
        }
        identifier = bool((item.evidence or {}).get("identifier"))
        if item.column in reserved or identifier or item.action == "exclude":
            excluded.append(record)
            continue
        if item.risk in {"HIGH", "CRITICAL"}:
            excluded.append(record)
            continue
        if item.action in keep_actions:
            allowed.append(item.column)
        else:
            excluded.append(record)
    return ModelDevelopmentPlan(
        problem_profile=problem_profile.to_dict(),
        validation_plan=validation_plan.to_dict(),
        metric_plan=metric_plan.to_dict(),
        feature_availability=[item.to_dict() for item in audit.assessments],
        leakage_assessment=leakage_report_from_audit(audit),
        allowed_features=allowed,
        excluded_features=excluded,
        group_column=validation_plan.group_column,
        time_column=validation_plan.time_column,
        recommended_model_family_hints=recommend_model_family_hints(problem_profile),
        plan_version=PLAN_VERSION,
    )


def _cap_list(items: list[Any], limit: int = _EVENT_NAME_CAP) -> list[Any]:
    return list(items)[:limit]


def _emit_planning(
    on_event: PlanningEventCallback | None,
    event_type: str,
    *,
    stage: str,
    status: str = "completed",
    **payload: Any,
) -> None:
    if on_event is None:
        return
    on_event(event_type, {"stage": stage, "status": status, **payload})


def _availability_status(item: LeakageRisk) -> str:
    if item.availability is None:
        return "unknown"
    return str(item.availability.status)


def plan_model_development(
    train: pd.DataFrame,
    *,
    target: str,
    task_type: str,
    requested_folds: int = 5,
    random_state: int = 42,
    time_column: str | None = None,
    entity_column: str | None = None,
    reviewer: LeakageReviewer | None = None,
    conservative_auto_train: bool = True,
    availability_overrides: dict[str, AvailabilityStatus] | None = None,
    on_event: PlanningEventCallback | None = None,
) -> tuple[ProblemProfile, ValidationPlan, MetricPlan, LeakageAuditResult, ModelDevelopmentPlan]:
    """Profile → validate → metrics → leakage → one ModelDevelopmentPlan (train only)."""
    feature_columns = [name for name in train.columns if name not in {target, SOURCE_ROW_COLUMN}]
    _emit_planning(
        on_event,
        "problem_profile_started",
        stage="problem_profile",
        status="started",
        target=target,
        task_type=task_type,
        row_count=int(len(train)),
        feature_count=len(feature_columns),
    )
    problem_profile = build_problem_profile(
        train,
        target=target,
        task_type=task_type,
        feature_columns=feature_columns,
    )
    _emit_planning(
        on_event,
        "problem_profile_completed",
        stage="problem_profile",
        task_type=problem_profile.task_type,
        row_count=problem_profile.row_count,
        feature_count=problem_profile.feature_count,
        identifier_columns=_cap_list(problem_profile.identifier_columns),
        datetime_columns=_cap_list(problem_profile.datetime_columns),
        class_distribution=problem_profile.class_distribution,
        minority_class_fraction=problem_profile.minority_class_fraction,
        imbalance_ratio=problem_profile.imbalance_ratio,
        version=problem_profile.version,
    )
    validation_plan = plan_validation(
        problem_profile,
        y=train[target],
        frame=train,
        requested_folds=requested_folds,
        random_state=random_state,
    )
    _emit_planning(
        on_event,
        "validation_plan_selected",
        stage="validation_plan",
        strategy=validation_plan.strategy,
        group_column=validation_plan.group_column,
        time_column=validation_plan.time_column,
        requested_folds=validation_plan.requested_folds,
        actual_folds=validation_plan.actual_folds,
        stratified=validation_plan.stratified,
        shuffle=validation_plan.shuffle,
        reason=validation_plan.reason,
        fallback_reason=validation_plan.fallback_reason,
        version=validation_plan.version,
    )
    metric_plan = plan_metrics(problem_profile)
    _emit_planning(
        on_event,
        "metric_plan_selected",
        stage="metric_plan",
        primary_metric=metric_plan.primary_metric,
        secondary_metrics=_cap_list(metric_plan.secondary_metrics),
        reason=metric_plan.reason,
        version=metric_plan.version,
    )
    _emit_planning(
        on_event,
        "leakage_audit_started",
        stage="leakage_audit",
        status="started",
        column_count=len(feature_columns),
        partition="train",
    )
    audit = audit_leakage(
        train,
        target=target,
        task_type=task_type,
        time_column=validation_plan.time_column or time_column,
        entity_column=validation_plan.group_column or entity_column,
        identifier_columns=problem_profile.identifier_columns,
        reviewer=reviewer,
        availability_overrides=availability_overrides,
    )
    plan = build_model_development_plan(
        problem_profile=problem_profile,
        validation_plan=validation_plan,
        metric_plan=metric_plan,
        audit=audit,
        conservative_auto_train=conservative_auto_train,
    )
    allowed = set(plan.allowed_features)
    warning_count = 0
    for item in audit.risks:
        if item.column not in allowed:
            continue
        if item.action != "keep_with_warning" and item.risk not in {"LOW", "MEDIUM"}:
            continue
        if item.action == "keep" and item.risk == "NONE":
            continue
        if warning_count >= _EVENT_ITEM_CAP:
            break
        _emit_planning(
            on_event,
            "feature_leakage_warning",
            stage="leakage_audit",
            column=item.column,
            risk=item.risk,
            action=item.action,
            reasons=_cap_list(item.reasons, 8),
            availability_status=_availability_status(item),
        )
        warning_count += 1
    for record in plan.excluded_features[:_EVENT_ITEM_CAP]:
        availability = record.get("availability") if isinstance(record.get("availability"), dict) else {}
        _emit_planning(
            on_event,
            "feature_excluded_for_leakage",
            stage="leakage_audit",
            column=record.get("column"),
            risk=record.get("risk"),
            action=record.get("action"),
            reasons=_cap_list(list(record.get("reasons") or []), 8),
            availability_status=availability.get("status") or "unknown",
        )
    report = leakage_report_from_audit(audit)
    _emit_planning(
        on_event,
        "leakage_audit_completed",
        stage="leakage_audit",
        overall_risk=report.get("risk"),
        finding_count=len(report.get("findings") or []),
        excluded_count=len(plan.excluded_features),
        warning_count=warning_count,
        partition="train",
        version=audit.version,
    )
    _emit_planning(
        on_event,
        "model_development_plan_locked",
        stage="model_development_plan",
        allowed_features=_cap_list(plan.allowed_features),
        excluded_count=len(plan.excluded_features),
        group_column=plan.group_column,
        time_column=plan.time_column,
        primary_metric=metric_plan.primary_metric,
        strategy=validation_plan.strategy,
        plan_version=plan.plan_version,
        recommended_model_family_hints=_cap_list(plan.recommended_model_family_hints),
    )
    return problem_profile, validation_plan, metric_plan, audit, plan


def consult_leakage_llm(evidence: LeakageReviewEvidence) -> Any:
    """Optional semantic review. Fail closed; never drop a feature itself."""
    from app.engine.lab.llm_client import DecisionAgentUnavailable, request_leakage_review
    from app.engine.lab.prompts.leakage_review_v1 import PROMPT_VERSION

    try:
        return request_leakage_review(evidence, PROMPT_VERSION)
    except DecisionAgentUnavailable:
        return None
