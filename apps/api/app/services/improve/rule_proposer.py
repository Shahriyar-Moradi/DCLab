"""``rule_proposer.v1`` (ADR 0007 §1b): the pre-registered try sequence of one loop. Pure.

Input: the start's trained candidate ids, the families F+ may add, the pure generator's candidate ids
for ``start ⊕ family_include(f)`` (``probe_ids``), the goal kind, ``max_iterations`` and the start's
stored findings -- read only through ``PROPOSER_READABLE_CHECKS`` (the five test-partition checks are
never an input, so their values cannot change a plan). Output: OP first for a constrained binary goal
(iteration 0, no retrain, not in T), then one F+ per eligible family with its ``k_i`` = the
generator's new candidate ids against the start's trained ids (a branch lifts the candidate cap, so an
include can also restore capped ``__balanced`` variants). An include that would drop or move a trained
candidate (e.g. the ``__tuned`` variant) is skipped: every probe must stay a strict superset. F+ tries
beyond ``max_iterations`` are cut. T = Σ k_i. Later evidence only removes tries, never adds one.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from app.domain.findings import FINDING_CHECKS
from app.domain.improve import IT_FAILED, KIND_FAMILY_INCLUDE, KIND_OPERATING_POINT
from app.engine.investigate.checks import TEST_PARTITION_CHECKS
from app.engine.search.generator import DUMMY_FAMILIES

PROPOSER_VERSION = "rule_proposer.v1"
PROPOSER_READABLE_CHECKS = frozenset(FINDING_CHECKS) - TEST_PARTITION_CHECKS  # ten checks (§1b)
BLOCKING_CHECKS = ("target_leakage", "implausible_score")  # a `fail` blocks (§4 rule 3)
MAX_FAILURES_PER_TRY = 2  # "the same try failed twice" -> stop failed (§4 rule 2)


@dataclass(frozen=True)
class PlannedTry:
    index: int
    kind: str
    family: str | None = None
    new_candidate_ids: tuple[str, ...] = ()

    @property
    def k(self) -> int:
        return len(self.new_candidate_ids)

    @property
    def change(self) -> dict[str, Any] | None:
        return {"kind": KIND_FAMILY_INCLUDE, "family": self.family} if self.kind == KIND_FAMILY_INCLUDE else None

    def to_dict(self) -> dict[str, Any]:
        return {"index": self.index, "kind": self.kind, "family": self.family,
                "new_candidate_ids": list(self.new_candidate_ids), "k": self.k}


@dataclass(frozen=True)
class Plan:
    tries: tuple[PlannedTry, ...]
    skipped: tuple[tuple[str, str], ...] = ()  # (family, reason)
    blocked: bool = False
    unstable: bool = False

    @property
    def planned_comparisons(self) -> int:
        return sum(item.k for item in self.tries if item.kind == KIND_FAMILY_INCLUDE)

    @property
    def retraining_tries(self) -> tuple[PlannedTry, ...]:
        return tuple(item for item in self.tries if item.kind == KIND_FAMILY_INCLUDE)

    def to_dict(self) -> dict[str, Any]:
        return {"version": PROPOSER_VERSION, "tries": [item.to_dict() for item in self.tries],
                "skipped": [{"family": family, "reason": reason} for family, reason in self.skipped],
                "blocked": self.blocked, "unstable": self.unstable,
                "planned_comparisons": self.planned_comparisons}

    def to_json(self) -> bytes:
        return json.dumps(self.to_dict(), sort_keys=True, separators=(",", ":")).encode("utf-8")


def readable_findings(findings: Iterable[Mapping[str, Any]]) -> tuple[tuple[str, str, bool], ...]:
    """(check, status, worst_fold_at_baseline) of the readable checks only, in a canonical order."""

    rows = []
    for item in findings:
        check = str(item.get("check") or "")
        if check not in PROPOSER_READABLE_CHECKS:
            continue
        evidence = item.get("evidence") if isinstance(item.get("evidence"), Mapping) else {}
        rows.append((check, str(item.get("status") or ""), evidence.get("worst_fold_at_baseline") is True))
    return tuple(sorted(rows))


def is_blocking(findings: Iterable[Mapping[str, Any]]) -> bool:
    return any(check in BLOCKING_CHECKS and status == "fail" for check, status, _ in readable_findings(findings))


def is_unstable(findings: Iterable[Mapping[str, Any]]) -> bool:
    """§4 rule 6: a ``fold_instability`` warning whose worst fold sits at the chance baseline."""

    return any(check == "fold_instability" and status == "warning" and at_baseline
               for check, status, at_baseline in readable_findings(findings))


def plan_tries(
    *,
    task_type: str,
    constrained: bool,
    families: Sequence[str],
    start_trained_ids: Iterable[str],
    probe_ids: Callable[[str], Sequence[str] | None],
    max_iterations: int,
    findings: Iterable[Mapping[str, Any]] = (),
) -> Plan:
    findings = list(findings)
    trained = set(start_trained_ids)
    tries: list[PlannedTry] = []
    skipped: list[tuple[str, str]] = []
    if constrained and task_type == "binary":
        tries.append(PlannedTry(index=0, kind=KIND_OPERATING_POINT))
    retraining = 0
    for family in families:
        ids = probe_ids(family)
        if ids is None:
            skipped.append((family, "refused_by_validator"))
            continue
        if not trained <= set(ids):
            skipped.append((family, "would_drop_or_move_a_trained_candidate"))
            continue
        new = tuple(sorted(set(ids) - trained - DUMMY_FAMILIES))
        if not new:
            skipped.append((family, "adds_no_candidate"))
            continue
        if retraining >= max_iterations:
            skipped.append((family, "beyond_max_iterations"))
            continue
        tries.append(PlannedTry(index=len(tries), kind=KIND_FAMILY_INCLUDE, family=family, new_candidate_ids=new))
        retraining += 1
    return Plan(tries=tuple(tries), skipped=tuple(skipped), blocked=is_blocking(findings),
                unstable=is_unstable(findings))


def plan_from_dict(payload: Sequence[Mapping[str, Any]]) -> tuple[PlannedTry, ...]:
    return tuple(PlannedTry(index=int(item["index"]), kind=str(item["kind"]), family=item.get("family"),
                            new_candidate_ids=tuple(item.get("new_candidate_ids") or ())) for item in payload)


def next_try(tries: Sequence[PlannedTry], history: Sequence[tuple[int, str]]) -> PlannedTry | str | None:
    """The next planned try given ``history`` = (plan index, final status) of the loop's iterations in
    order: a try that failed once is tried again; twice -> ``"failed_twice"``; None when every planned
    try was used (``proposer_exhausted``)."""

    failures: dict[int, int] = {}
    done: set[int] = set()
    for index, status in history:
        if status == IT_FAILED:
            failures[index] = failures.get(index, 0) + 1
        else:
            done.add(index)
    for item in tries:
        if item.index in done:
            continue
        if failures.get(item.index, 0) >= MAX_FAILURES_PER_TRY:
            return "failed_twice"
        return item
    return None
