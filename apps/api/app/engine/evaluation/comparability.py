"""Reproducible final run and per-try comparability (P5.4-A1b; ADR 0007 §5, §6.2). Pure.

A run's CV evidence is reduced to ``RunEvidence``: the winner's candidate key and, for every
trained candidate, its fingerprint (which hashes ``library_version`` with the rest of the
scientific configuration), its scalar per-fold CV metrics keyed by ``fold_number``, the tuning
trials completed per fold and whether tuning was truncated; plus the candidates skipped for time.
The same shape is built from the runner's in-memory records (``evidence_from_records``) and from a
run's locked rows (``experiment_verdict_service.locked_run_evidence``), so:

- ``selection_mismatch(expected, actual)``: the final run pins its probe's evidence
  (``expected_selection``) and the runner aborts at ``SELECTION_LOCKED``, before the final test,
  when it does not reproduce within ``FOLD_TOLERANCE`` (``SelectionMismatchError``,
  ``nondeterministic_rerun``: no look is spent);
- ``comparability(incumbent, probe)``: a try is compared with the incumbent only when the probe is
  a strict superset (every incumbent candidate trained again with an identical fingerprint and
  identical per-fold metrics, plus at least one new trained candidate) and neither run skipped a
  candidate for time nor truncated its tuning; otherwise ``not_comparable`` with reason
  ``truncated`` | ``superset_violated`` | ``engine_or_data_drift``.

The time-budget helpers turn realised training seconds into the probe budget (§3) and the final
run's generous cap (§6.2); a binding cap skips a candidate, which both checks above catch.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any, Iterable, Mapping

from app.engine.search.generator import DUMMY_FAMILIES

FOLD_TOLERANCE = 1e-9  # ADR 0007 §5: shared per-fold metrics identical within 1e-9
NONDETERMINISTIC_RERUN = "nondeterministic_rerun"
REASON_TRUNCATED = "truncated"
REASON_SUPERSET_VIOLATED = "superset_violated"
REASON_DRIFT = "engine_or_data_drift"
COMPARABILITY_REASONS = (REASON_TRUNCATED, REASON_SUPERSET_VIOLATED, REASON_DRIFT)
# §3 probe budget: realised seconds of the start plus an estimate per new candidate, capped.
PROBE_BUDGET_MAX_SECONDS = 3600.0
REALISED_SLACK_FACTOR = 1.5  # headroom over the start's own realised time (a slower worker must not truncate)
NEW_CANDIDATE_ESTIMATE_FACTOR = 2.0  # x the start's mean seconds per trained learned candidate
NEW_CANDIDATE_MIN_SECONDS = 30.0
# §6.2 final run: a generous cap (3 x the probe's realised time) with a floor for tiny runs.
FINAL_RUN_BUDGET_FACTOR = 3.0
FINAL_RUN_BUDGET_FLOOR_SECONDS = 60.0


@dataclass(frozen=True)
class CandidateEvidence:
    fingerprint: str
    folds: Mapping[int, Mapping[str, float]]
    trials: Mapping[int, int] = field(default_factory=dict)  # tuned candidates only
    truncated: bool = False
    train_seconds: float | None = None
    family: str | None = None


@dataclass(frozen=True)
class RunEvidence:
    winner: str | None
    trained: Mapping[str, CandidateEvidence]
    skipped: frozenset[str] = frozenset()

    def to_dict(self) -> dict[str, Any]:
        """JSON-safe (NaN -> null, fold numbers as strings) for a service-only config record."""

        return {
            "winner": self.winner,
            "skipped": sorted(self.skipped),
            "trained": {
                key: {
                    "fingerprint": item.fingerprint,
                    "family": item.family,
                    "folds": {str(n): {m: _json_float(v) for m, v in sorted(metrics.items())}
                              for n, metrics in sorted(item.folds.items())},
                    "trials": {str(n): int(v) for n, v in sorted(item.trials.items())},
                    "truncated": bool(item.truncated),
                    "train_seconds": item.train_seconds,
                }
                for key, item in sorted(self.trained.items())
            },
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "RunEvidence":
        if not isinstance(payload, Mapping) or not isinstance(payload.get("trained"), Mapping):
            raise ValueError("expected_selection must carry a trained-candidate map")
        trained = {}
        for key, item in payload["trained"].items():
            if not isinstance(item, Mapping) or not isinstance(item.get("fingerprint"), str):
                raise ValueError(f"expected_selection candidate {key!r} is malformed")
            trained[str(key)] = CandidateEvidence(
                fingerprint=item["fingerprint"],
                family=item.get("family") if isinstance(item.get("family"), str) else None,
                folds={int(n): {str(m): (math.nan if v is None else float(v)) for m, v in dict(metrics).items()}
                       for n, metrics in dict(item.get("folds") or {}).items()},
                trials={int(n): int(v) for n, v in dict(item.get("trials") or {}).items()},
                truncated=bool(item.get("truncated")),
                train_seconds=_seconds(item.get("train_seconds")),
            )
        winner = payload.get("winner")
        return cls(winner=str(winner) if winner is not None else None, trained=trained,
                   skipped=frozenset(str(key) for key in payload.get("skipped") or []))


class SelectionMismatchError(RuntimeError):
    """The pinned re-run did not reproduce its probe at the winner lock: abort before the final test."""

    code = NONDETERMINISTIC_RERUN

    def __init__(self, detail: str) -> None:
        super().__init__(f"{NONDETERMINISTIC_RERUN}: {detail}")
        self.detail = detail


@dataclass(frozen=True)
class Comparability:
    comparable: bool
    reason: str | None = None
    detail: str | None = None
    new_candidates: tuple[str, ...] = ()


def _json_float(value: float) -> float | None:
    return None if not math.isfinite(value) else float(value)


def _seconds(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value < 0:
        return None
    return float(value)


def scalar_metrics(payload: Any) -> dict[str, float]:
    """The scalar fold metrics, exactly as ``evaluation_metrics`` ends up storing them: finite numbers
    only (the runner's JSON-safe result turns NaN/inf into null before persistence)."""

    if not isinstance(payload, Mapping):
        return {}
    return {str(name): float(raw) for name, raw in payload.items()
            if not isinstance(raw, bool) and isinstance(raw, (int, float)) and math.isfinite(raw)}


def is_time_skip(record: Mapping[str, Any]) -> bool:
    return str(record.get("status") or "").lower() == "skipped"


def candidate_from_payload(record: Mapping[str, Any], folds: Mapping[int, Mapping[str, float]] | None = None,
                           *, fingerprint: str | None = None) -> CandidateEvidence:
    """One trained candidate from a runner record (``folds`` overrides the record's fold metrics,
    e.g. with the locked ``cv_fold`` evaluation rows)."""

    trials: dict[int, int] = {}
    truncated = False
    own: dict[int, dict[str, float]] = {}
    for fold in record.get("folds") or []:
        if not isinstance(fold, Mapping):
            continue
        number = int(fold.get("fold_number") or fold.get("fold") or 0)
        if number < 1:
            continue
        own[number] = scalar_metrics(fold.get("metrics"))
        tuning = fold.get("tuning")
        if isinstance(tuning, Mapping):
            trials[number] = int(tuning.get("trials_completed") or 0)
            truncated = truncated or bool(tuning.get("truncated"))
    return CandidateEvidence(
        fingerprint=str(fingerprint if fingerprint is not None else record.get("fingerprint") or ""),
        folds=dict(folds) if folds is not None else own,
        trials=trials,
        truncated=truncated,
        train_seconds=_seconds(record.get("train_seconds")),
        family=str(record.get("model_family")) if record.get("model_family") else None,
    )


def evidence_from_records(records: Iterable[Mapping[str, Any]], winner: str | None) -> RunEvidence:
    """``RunEvidence`` of the runner's candidate records (trained and skipped-for-time ones)."""

    trained: dict[str, CandidateEvidence] = {}
    skipped: set[str] = set()
    fingerprints: set[str] = set()
    for record in records:
        key = str(record.get("candidate_id") or record.get("candidate") or "")
        if not key:
            continue
        fingerprint = str(record.get("fingerprint") or "")
        if fingerprint and fingerprint in fingerprints:
            continue  # persistence keeps one row per scientific configuration (candidate_modeling_service)
        fingerprints.add(fingerprint)
        if is_time_skip(record):
            skipped.add(key)
        elif str(record.get("status") or "").lower() == "trained":
            trained[key] = candidate_from_payload(record)
    return RunEvidence(winner=winner, trained=trained, skipped=frozenset(skipped))


def _same(a: float, b: float) -> bool:
    if math.isnan(a) or math.isnan(b):
        return math.isnan(a) and math.isnan(b)
    return abs(a - b) <= FOLD_TOLERANCE


def _candidate_difference(key: str, before: CandidateEvidence, after: CandidateEvidence) -> str | None:
    if before.fingerprint != after.fingerprint:
        return f"candidate {key}: fingerprint differs (configuration or library version)"
    if set(before.folds) != set(after.folds):
        return f"candidate {key}: different folds"
    for number in sorted(before.folds):
        left, right = before.folds[number], after.folds[number]
        if set(left) != set(right):
            return f"candidate {key}: fold {number} records different metrics"
        for name in sorted(left):
            if not _same(left[name], right[name]):
                return f"candidate {key}: fold {number} {name} differs beyond {FOLD_TOLERANCE:g}"
    if dict(before.trials) != dict(after.trials):
        return f"candidate {key}: tuning trials per fold differ"
    return None


def selection_mismatch(expected: RunEvidence, actual: RunEvidence) -> str | None:
    """Why ``actual`` does not reproduce ``expected`` (None when it does): the same winner, the
    same trained candidates, each with the same fingerprint, per-fold metrics (1e-9) and trials;
    no candidate skipped for time and no truncated tuning in the re-run."""

    if actual.winner != expected.winner:
        return f"winner {actual.winner!r} instead of {expected.winner!r}"
    if actual.skipped:
        return f"candidates skipped for time: {sorted(actual.skipped)}"
    if set(actual.trained) != set(expected.trained):
        missing, extra = set(expected.trained) - set(actual.trained), set(actual.trained) - set(expected.trained)
        return f"trained candidates differ (missing {sorted(missing)}, extra {sorted(extra)})"
    for key in sorted(expected.trained):
        if actual.trained[key].truncated:
            return f"candidate {key}: tuning truncated"
        difference = _candidate_difference(key, expected.trained[key], actual.trained[key])
        if difference:
            return difference
    return None


def comparability(incumbent: RunEvidence, probe: RunEvidence) -> Comparability:
    """ADR 0007 §5, per try and before any delta (precedence: truncated, superset, drift)."""

    for label, run in (("incumbent", incumbent), ("probe", probe)):
        if run.skipped:
            return Comparability(False, REASON_TRUNCATED, f"the {label} skipped candidates for time: "
                                                         f"{sorted(run.skipped)}")
        cut = sorted(key for key, item in run.trained.items() if item.truncated)
        if cut:
            return Comparability(False, REASON_TRUNCATED, f"the {label} truncated tuning of {cut}")
    missing = sorted(set(incumbent.trained) - set(probe.trained))
    if missing:
        return Comparability(False, REASON_SUPERSET_VIOLATED,
                             f"incumbent candidates not trained by the probe: {missing}")
    new = tuple(sorted(set(probe.trained) - set(incumbent.trained)))
    if not new:
        return Comparability(False, REASON_SUPERSET_VIOLATED, "no_new_trained_candidate")
    for key in sorted(incumbent.trained):
        difference = _candidate_difference(key, incumbent.trained[key], probe.trained[key])
        if difference:
            return Comparability(False, REASON_DRIFT, difference, new)
    return Comparability(True, None, None, new)


def realised_training_seconds(evidence: RunEvidence) -> float:
    """Seconds the run spent training its trained candidates (CV folds, tuning included)."""

    return float(sum(item.train_seconds or 0.0 for item in evidence.trained.values()))


def probe_budget_seconds(start: RunEvidence, new_candidates: int) -> float | None:
    """§3: 1.5 x the start's realised training time plus a code-owned estimate for ``new_candidates``
    (twice the start's mean seconds per trained learned candidate, at least 30 s each). None when that
    exceeds ``PROBE_BUDGET_MAX_SECONDS``: the try cannot fit (a clamped budget would truncate it for
    sure). A binding cap skips a candidate and the try is ``not_comparable`` (``truncated``)."""

    if new_candidates < 0:
        raise ValueError("new_candidates must be >= 0")
    learned = [item.train_seconds for key, item in start.trained.items()
               if item.train_seconds is not None and (item.family or key) not in DUMMY_FAMILIES]
    mean = sum(learned) / len(learned) if learned else 0.0
    estimate = max(NEW_CANDIDATE_MIN_SECONDS, NEW_CANDIDATE_ESTIMATE_FACTOR * mean)
    budget = REALISED_SLACK_FACTOR * realised_training_seconds(start) + new_candidates * estimate
    return budget if budget <= PROBE_BUDGET_MAX_SECONDS else None


def final_run_budget_seconds(probe: RunEvidence) -> float:
    """§6.2: 3 x the probe's realised training time (at least 60 s). Any binding cap skips a candidate
    or truncates tuning, so the pinned selection fails and the run aborts before the final test."""

    return max(FINAL_RUN_BUDGET_FLOOR_SECONDS, FINAL_RUN_BUDGET_FACTOR * realised_training_seconds(probe))
