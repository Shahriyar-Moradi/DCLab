"""Generic, domain-independent schema inference for arbitrary tabular uploads."""

from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, field
from typing import Any

import numpy as np
import pandas as pd

TARGET_CONFIDENCE_THRESHOLD = 0.65
TARGET_MARGIN_THRESHOLD = 0.08
MAX_MULTICLASS_CARDINALITY = 20
MIN_TRAIN_ROWS = 40

_BINARY_TOKENS = {"0", "1", "true", "false", "yes", "no", "y", "n"}
_TARGET_ROLE_TOKENS = {
    "target",
    "label",
    "outcome",
    "response",
    "result",
    "output",
    "class",
    "groundtruth",
    "ground_truth",
}


def normalize_name(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", str(name).strip().lower()).strip("_")


def identifier_likelihood(name: str, series: pd.Series, row_count: int | None = None) -> float:
    """Return a conservative identifier likelihood without business-name aliases."""
    key = normalize_name(name)
    tokens = set(key.split("_"))
    if key in {"id", "uuid", "guid"} or key.endswith(("_id", "_uuid", "_guid")):
        return 1.0
    if tokens & {"uuid", "guid", "identifier"}:
        return 0.98

    n = max(int(row_count if row_count is not None else len(series)), 1)
    observed = series.dropna()
    ratio = float(observed.nunique()) / n
    if ratio <= 0.95:
        return 0.0

    # Compact near-unique strings are commonly opaque keys. Natural-language
    # text (whitespace-rich values) is handled separately as ignored/free text.
    # Numeric measurements are not rejected merely for being unique.
    if not pd.api.types.is_numeric_dtype(series):
        samples = observed.astype(str).head(50)
        compact_ratio = float(samples.str.fullmatch(r"\S{1,64}").mean()) if len(samples) else 0.0
        return 0.9 if compact_ratio >= 0.9 else 0.0
    if pd.api.types.is_integer_dtype(series) and len(observed) >= 3:
        values = pd.to_numeric(observed, errors="coerce").dropna().to_numpy(dtype=float)
        if len(values) >= 3:
            ordered = np.sort(np.unique(values))
            steps = np.diff(ordered)
            if len(steps) and np.allclose(steps, steps[0]) and abs(float(steps[0])) == 1.0:
                return 0.85
    return 0.0


def looks_like_identifier(name: str, series: pd.Series, row_count: int | None = None) -> bool:
    return identifier_likelihood(name, series, row_count) >= 0.8


def infer_entity_column(
    frame: pd.DataFrame,
    columns: list[str],
    *,
    explicit: str | None = None,
) -> str | None:
    """Use an explicit or strongly inferred identifier; otherwise return None."""
    if explicit is not None:
        return explicit if explicit in frame.columns else None
    ranked = [
        (identifier_likelihood(name, frame[name], len(frame)), normalize_name(name), name)
        for name in columns
        if name in frame.columns
    ]
    eligible = [item for item in ranked if item[0] >= 0.8]
    if not eligible:
        return None
    eligible.sort(key=lambda item: (-item[0], item[1]))
    return eligible[0][2]


@dataclass(frozen=True)
class TargetCandidate:
    column: str
    probable_task_type: str
    confidence: float
    evidence: dict[str, Any]
    reason: str


@dataclass
class TargetChoice:
    column: str | None
    reason: str
    task_type: str | None = None
    evaluation_metric: str | None = None
    confidence: float = 0.0
    source: str = "fallback"
    evidence: dict[str, Any] = field(default_factory=dict)
    candidates: list[TargetCandidate] = field(default_factory=list)
    raw_llm_output: dict[str, Any] | None = None
    validator_verdict: str = "not_run"

    def audit_dict(self) -> dict[str, Any]:
        return {
            "column": self.column,
            "task_type": self.task_type,
            "evaluation_metric": self.evaluation_metric,
            "confidence": self.confidence,
            "source": self.source,
            "reason": self.reason,
            "evidence": self.evidence,
            "candidates": [asdict(item) for item in self.candidates],
            "raw_llm_output": self.raw_llm_output,
            "validator_verdict": self.validator_verdict,
        }


def _sample_values(series: pd.Series, limit: int = 10) -> list[Any]:
    values: list[Any] = []
    for raw in series.dropna().drop_duplicates().head(limit).tolist():
        if isinstance(raw, np.generic):
            raw = raw.item()
        if isinstance(raw, (pd.Timestamp, np.datetime64)):
            raw = str(raw)
        if isinstance(raw, float) and not np.isfinite(raw):
            raw = None
        try:
            json.dumps(raw)
        except (TypeError, ValueError):
            raw = str(raw)
        values.append(raw)
    return values


def _probable_task_type(series: pd.Series, unique: int, unique_ratio: float) -> str:
    if unique == 2:
        return "binary"
    if pd.api.types.is_bool_dtype(series):
        return "binary" if unique == 2 else "unusable"
    if pd.api.types.is_datetime64_any_dtype(series):
        return "unusable"
    if pd.api.types.is_numeric_dtype(series):
        if (
            pd.api.types.is_integer_dtype(series)
            and 2 < unique <= MAX_MULTICLASS_CARDINALITY
            and unique_ratio <= 0.2
        ):
            return "multiclass"
        return "regression"
    if 2 < unique <= MAX_MULTICLASS_CARDINALITY and unique_ratio <= 0.5:
        return "multiclass"
    return "unusable"


def _name_role_score(name: str) -> tuple[float, list[str]]:
    key = normalize_name(name)
    tokens = set(key.split("_"))
    reasons: list[str] = []
    score = 0.0
    if key in _TARGET_ROLE_TOKENS or tokens & _TARGET_ROLE_TOKENS:
        score += 0.30
        reasons.append("name contains a generic outcome-role token")
    if key.startswith(("is_", "has_")) or key.endswith(("_flag", "_label", "_target")):
        score += 0.20
        reasons.append("name has a generic boolean/label form")
    if key.endswith("ed") and len(key) > 4:
        score += 0.08
        reasons.append("name has an outcome-like past-participle form")
    return min(score, 0.35), reasons


def generate_target_candidates(frame: pd.DataFrame, columns: list[str]) -> list[TargetCandidate]:
    """Build ranked candidates from data evidence; never uses domain templates."""
    n = max(len(frame), 1)
    provisional: list[dict[str, Any]] = []
    for name in columns:
        if name not in frame.columns:
            continue
        series = frame[name]
        unique = int(series.nunique(dropna=True))
        missing_ratio = float(series.isna().sum()) / n
        unique_ratio = unique / n
        id_score = identifier_likelihood(name, series, n)
        constant = unique <= 1
        task_type = _probable_task_type(series, unique, unique_ratio)
        if constant or missing_ratio > 0.8 or id_score >= 0.8 or task_type == "unusable":
            continue
        distinct = set(series.dropna().astype(str).str.strip().str.lower().unique())
        provisional.append(
            {
                "column": name,
                "series": series,
                "task_type": task_type,
                "unique": unique,
                "unique_ratio": unique_ratio,
                "missing_ratio": missing_ratio,
                "identifier_likelihood": id_score,
                "binary_tokens": task_type == "binary" and distinct <= _BINARY_TOKENS,
            }
        )

    binary_count = sum(item["task_type"] == "binary" for item in provisional)
    regression_count = sum(item["task_type"] == "regression" for item in provisional)
    candidates: list[TargetCandidate] = []
    for item in provisional:
        name = item["column"]
        task_type = item["task_type"]
        score = 0.25
        reasons = [f"usable {task_type} target shape"]
        if task_type == "binary":
            score += 0.25
            if item["binary_tokens"]:
                score += 0.15
                reasons.append("values form a recognized binary label set")
            if binary_count == 1:
                score += 0.12
                reasons.append("only binary-shaped candidate")
        elif task_type == "regression":
            score += 0.20
            if item["unique_ratio"] >= 0.2:
                score += 0.05
                reasons.append("continuous numeric distribution")
            if regression_count == 1:
                score += 0.05
        else:
            score += 0.12

        name_score, name_reasons = _name_role_score(name)
        score += name_score
        reasons.extend(name_reasons)
        score *= max(0.7, 1.0 - item["missing_ratio"])
        score = round(min(score, 0.99), 4)
        evidence = {
            "column": name,
            "dtype": str(item["series"].dtype),
            "unique_count": item["unique"],
            "unique_ratio": item["unique_ratio"],
            "missing_ratio": item["missing_ratio"],
            "identifier_likelihood": item["identifier_likelihood"],
            "constant": False,
            "sample_values": _sample_values(item["series"]),
        }
        candidates.append(
            TargetCandidate(
                column=name,
                probable_task_type=task_type,
                confidence=score,
                evidence=evidence,
                reason="; ".join(reasons),
            )
        )
    return sorted(candidates, key=lambda item: (-item.confidence, normalize_name(item.column)))


def metric_for_task(task_type: str) -> str:
    if task_type == "binary":
        return "pr_auc"
    if task_type == "regression":
        return "mae"
    return "accuracy"


def choose_target_deterministically(
    frame: pd.DataFrame,
    columns: list[str],
    *,
    explicit_target: str | None = None,
) -> TargetChoice:
    candidates = generate_target_candidates(frame, columns)
    by_column = {item.column: item for item in candidates}
    if explicit_target is not None:
        if explicit_target not in frame.columns:
            return TargetChoice(
                column=None,
                reason=f"explicit target {explicit_target!r} is not present in the dataset",
                source="explicit",
                candidates=candidates,
            )
        candidate = by_column.get(explicit_target)
        if candidate is None:
            return TargetChoice(
                column=None,
                reason=f"explicit target {explicit_target!r} is constant, identifier-like, or unusable",
                source="explicit",
                candidates=candidates,
            )
        return TargetChoice(
            column=candidate.column,
            reason="explicit target supplied by user/admin",
            task_type=candidate.probable_task_type,
            evaluation_metric=metric_for_task(candidate.probable_task_type),
            confidence=1.0,
            source="explicit",
            evidence=candidate.evidence,
            candidates=candidates,
        )

    if not candidates:
        return TargetChoice(
            column=None,
            reason="no usable target candidates remain after identifier, constant, and type checks",
            candidates=[],
        )

    best = candidates[0]
    runner_up = candidates[1].confidence if len(candidates) > 1 else 0.0
    margin = best.confidence - runner_up
    if best.confidence >= TARGET_CONFIDENCE_THRESHOLD and margin >= TARGET_MARGIN_THRESHOLD:
        return TargetChoice(
            column=best.column,
            reason=f"strong deterministic candidate: {best.reason}",
            task_type=best.probable_task_type,
            evaluation_metric=metric_for_task(best.probable_task_type),
            confidence=best.confidence,
            source="rule",
            evidence={**best.evidence, "runner_up_margin": round(margin, 4)},
            candidates=candidates,
        )
    return TargetChoice(
        column=None,
        reason=(
            f"target selection is ambiguous: best candidate {best.column!r} has confidence "
            f"{best.confidence:.2f} and margin {margin:.2f}"
        ),
        confidence=best.confidence,
        source="fallback",
        evidence={"runner_up_margin": round(margin, 4)},
        candidates=candidates,
    )
