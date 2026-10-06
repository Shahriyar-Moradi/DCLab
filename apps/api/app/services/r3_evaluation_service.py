"""R3 evaluation harness and trust-level rule engine (P6.8-A; ADR 0008 §3, §4, §8).

Corpus. The platform corpus is built from the R1 benchmark datasets
(``benchmarks/harness/tasks.py``; the ``quick`` suite is bundled with scikit-learn, so
the runner works offline) with **planted** columns whose correct answer is known —
an identifier, a datetime, a free-text column, a categorical code and a copy of the
target (leakage) — plus an obfuscated-name variant of every dataset. Every task is in
the ``sealed`` or the ``development`` partition by a hash of (task id, release
version), so the partition rotates per prompt release (§4). Labels are train-side
ground truth; no holdout is ever built, read or scored here. Labeled user decisions
are tenant data: ``workspace_evidence`` returns per-workspace aggregates only, to an
approver of that workspace or a platform admin, never rows.

Answerers. The AI side is any ``Answerer`` (``(release, cases) -> answers``):
``ScriptedAnswerer`` (deterministic fake, CI), ``ai_off`` (every answer unavailable:
the ADR 0008 §8 ablation, which must equal the rule baseline) or a gateway-backed
port (``--live`` only, never in CI). The agreement table of ``semantic/policy.py`` is
applied to every answer, so the measured policy is the deployed one.

Statistics (§4). Paired (AI policy − rule) gap with a cluster bootstrap by dataset
(≥ 20 clusters) or a t interval on cluster means (fewer); Wilson bounds for in-band
precision (Kish effective n under inverse-probability weights); ECE with equal-mass
bins is reported, never gated; Holm correction across the gate p-values of one run.
``promotion_check`` / ``demotion_check`` encode the §4 tables; a level can only be
proposed (``propose_promotion``) when the verdict allows it, and only a human decides.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import math
import random
import secrets
import statistics
from collections import Counter, defaultdict
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any, Callable
from uuid import UUID, uuid4

import numpy as np
import pandas as pd
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.agents.governance.decision_points import REGISTRY, DecisionPoint
from app.agents.governance.incidents import open_incident_counts
from app.agents.governance.policy import (
    GovernanceNotFound,
    GovernanceNotPermitted,
    _level_head,
    _level_row,
    is_platform_admin,
    propose_level,
)
from app.agents.semantic import policy as agreement
from app.agents.semantic.releases import JEV_MODEL_ID, RELEASES, JevRelease
from app.db.models import (
    AgentProposal,
    AiIncident,
    DecisionPointPolicy,
    LlmInvocation,
    PromptRelease,
    R3Run,
    SemanticDecisionAnswer,
    User,
)
from app.services.authorization_service import can_approve_ai_policy

R3_RULE = "r3.release.v1"
SCHEMA_VERSION = 1
JEV_POINTS = ("column.is_identifier", "column.semantic_role", "feature.leakage_suspect")
YES_NO_POINTS = frozenset({"column.is_identifier", "feature.leakage_suspect"})
L2_POINTS = frozenset({"column.semantic_role", "column.missing_value_action", "training.families_budget"})
# ADR 0008 §1 ``budget`` column: p95 latency (ms) and cost (micros) per 1 000 decisions.
BUDGETS = {"jev": (1000, 50_000), "agent": (60_000, 100_000_000)}
GATES: dict[int, dict[str, Any]] = {
    1: {"sealed_min": 100, "blind_in_band_min": 30, "margin": 0.05, "precision": 0.90,
        "validator_max": 0.05, "validator_min_proposals": 50, "citation_min": 0.99},
    2: {"sealed_min": 300, "sealed_datasets_min": 20, "blind_min": 100, "margin": 0.01, "precision": 0.98,
        "validator_max": 0.02, "validator_min_proposals": 100},
    3: {"weeks_at_l2": 4, "applied_min": 100, "revert_max": 0.02},
}
DEMOTION_BARS = {1: (0.05, 0.90), 2: (0.01, 0.98)}  # (gap margin, precision bar) per current level
ALPHA = 0.05
TARGET = "target"
_ROW_LIMIT = 3000  # seeded subsample per benchmark task (bands need no more)


# --- corpus ----------------------------------------------------------------------------


@dataclass(frozen=True)
class Case:
    dataset: str  # task id; the bootstrap cluster (both name variants of a task share it)
    variant: str  # plain | obfuscated
    partition: str  # sealed | development
    purpose: str
    column: str
    fields: dict[str, Any]
    context: dict[str, Any]
    rule: Any
    label: Any
    weight: float = 1.0  # inverse-probability weight (blind strata); 1 for benchmarks
    source: str = ""  # the dataset name the evaluation workspace registers the frame under
    accepts: tuple[str, ...] = ("numeric", "categorical_code")  # role values the deployed RoleValidator accepts


# Tasks ever used for prompt / threshold development stay development forever (one-way
# rotation, ADR 0008 §4 "sealed part never used for either"); add, never remove.
DEVELOPMENT_TASKS = frozenset({"sk-iris", "sk-wine"})
SEALED_SHARE = 2  # 1 in SEALED_SHARE of the remaining tasks is development for a purpose release


def partition_for(task_id: str, purpose: str, version: int | None = None) -> str:
    """Per purpose: a task is development when pinned, or when it was drawn as development under
    ANY release version 1..v of that purpose (one-way: a task once used for development never
    becomes sealed; a bump only moves sealed tasks into development, never the reverse)."""

    if task_id in DEVELOPMENT_TASKS:
        return "development"
    release = RELEASES.get(purpose)
    latest = version if version is not None else (release.version if release is not None else 1)
    for v in range(1, max(1, latest) + 1):
        digest = hashlib.sha256(f"{task_id}:{purpose}@v{v}".encode()).hexdigest()
        if int(digest[:8], 16) % SEALED_SHARE == 0:
            return "development"
    return "sealed"


def plant(frame: pd.DataFrame, task_type: str, seed: int) -> tuple[pd.DataFrame, dict[str, dict[str, Any]]]:
    """Shuffle (an ordered benchmark file would make the planted id a target proxy), drop constant
    columns (the deployed role map never models them), add columns with known answers; natural
    columns are numeric (binary ones categorical codes, as the deployed boolean role), no id, no leak."""

    rng = np.random.default_rng(seed)
    frame = frame.sample(frac=1.0, random_state=seed).reset_index(drop=True)
    frame = frame[[c for c in frame.columns if c == TARGET or frame[c].nunique(dropna=True) > 1]]
    n = len(frame)
    out = frame.copy()
    def natural_role(series: pd.Series) -> str:  # the deployed role map: bool -> code, numeric -> numeric
        if pd.api.types.is_bool_dtype(series):
            return "categorical_code"
        return "numeric" if pd.api.types.is_numeric_dtype(series) else "categorical_code"

    labels = {c: {"role": natural_role(frame[c]), "identifier": False, "leak": False,
                  "availability": "before_prediction"} for c in frame.columns if c != TARGET}
    out["row_id"] = np.arange(100_000, 100_000 + n)
    labels["row_id"] = {"role": "identifier", "identifier": True, "leak": False, "availability": "before_prediction"}
    days = rng.integers(0, 3650, n)
    out["signup_date"] = [(pd.Timestamp("2015-01-01") + pd.Timedelta(days=int(d))).strftime("%Y-%m-%d") for d in days]
    labels["signup_date"] = {"role": "datetime", "identifier": False, "leak": False, "availability": "before_prediction"}
    words = ["alpha", "delta", "orbit", "ledger", "quartz", "maple", "signal", "harbor"]
    out["notes"] = [" ".join(rng.choice(words, size=int(rng.integers(8, 14)))) for _ in range(n)]
    labels["notes"] = {"role": "free_text", "identifier": False, "leak": False, "availability": "before_prediction"}
    out["segment_code"] = rng.choice(["A", "B", "C"], size=n)
    labels["segment_code"] = {"role": "categorical_code", "identifier": False, "leak": False,
                              "availability": "before_prediction"}
    out["outcome_flag"] = frame[TARGET].values
    labels["outcome_flag"] = {"role": "numeric" if task_type == "regression" else "categorical_code",
                              "identifier": False, "leak": True, "availability": "unknown"}
    return out, labels


_LEAK_ACTIONS = ("exclude", "keep_with_warning", "requires_review")  # the deployed rule's flag / exclude actions


def _rule_answers(frame: pd.DataFrame, columns: list[str], task_type: str) -> dict[str, dict[str, Any]]:
    """The deployed rules, not a rewrite: ``infer_column_roles`` (the role map auto-train uses,
    incl. datetime / identifier / free text), the ``schema_inference`` identifier rule, the
    leakage auditor on the frame as the locked training partition (availability from the
    auditor, never from the labels) and the deployed ``RoleValidator`` (which role values an L2
    override may take for each column)."""

    from app.engine.features.encode import encode_datetime_columns
    from app.engine.lab.auto_prepare import infer_column_roles
    from app.engine.modeling.leakage_auditor import audit_leakage
    from app.services.auto_train.decision_points import _AVAILABILITY, RoleValidator

    encoded, converted = encode_datetime_columns(frame, columns)  # structural cleaning: dates → epoch
    roles = infer_column_roles(encoded, columns)
    role_of = {**{c: "numeric" for c in roles.numerical}, **{c: "categorical_code" for c in roles.categorical},
               **{c: "categorical_code" for c in roles.boolean}, **{c: "identifier" for c in roles.identifier},
               **{c: "free_text" for c in roles.ignored_free_text}, **{c: "datetime" for c in [*roles.datetime, *converted]}}
    audit = audit_leakage(encoded, target=TARGET, task_type=task_type, identifier_columns=list(roles.identifier))
    risks = {risk.column: risk for risk in audit.risks}
    validator = RoleValidator(encoded, roles.numerical, [*roles.categorical, *roles.boolean], protected=set(),
                              missing_actions={})
    out: dict[str, dict[str, Any]] = {}
    for name in columns:
        risk = risks.get(name)
        status = risk.availability.status if risk is not None and risk.availability is not None else "unknown"
        out[name] = {"identifier": name in roles.identifier,  # the deployed rule: the engineered role map
                     "role": role_of.get(name, "other"),
                     "leak": risk is not None and risk.action in _LEAK_ACTIONS,
                     # the deployed point never asks about identifier columns (decision_points.py)
                     "leak_asked": risk is not None and not (risk.evidence or {}).get("identifier"),
                     "availability": _AVAILABILITY.get(str(status), "unknown"),
                     "accepts": tuple(v for v in ("numeric", "categorical_code") if not validator.check(name, v))}
    return out


def cases_for_frame(frame: pd.DataFrame, labels: dict[str, dict[str, Any]], *, dataset: str, variant: str,
                    task_type: str) -> list[Case]:
    from app.services.auto_train.decision_points import identifier_fields, role_fields

    columns = [c for c in frame.columns if c != TARGET]
    rules = _rule_answers(frame, columns, task_type)
    task = {"binary": "binary_classification", "multiclass": "multiclass_classification",
            "regression": "regression"}[task_type]
    source = dataset if variant == "plain" else f"{dataset}#obf"
    out: list[Case] = []
    for name in columns:
        series, label, rule = frame[name], labels[name], rules[name]
        ident = {k: (v if k != "name_tokens" else [t.untrusted_text for t in v])
                 for k, v in identifier_fields(series, name).items()}

        def case(purpose: str, **values: Any) -> Case:
            return Case(dataset=dataset, variant=variant, partition=partition_for(dataset, purpose), purpose=purpose,
                        column=name, source=source, accepts=rule["accepts"], **values)

        out.append(case("column.is_identifier", fields=ident, context={}, rule=rule["identifier"],
                        label=label["identifier"]))
        out.append(case("column.semantic_role", fields=role_fields(series), context={}, rule=rule["role"],
                        label=label["role"]))
        if rule["leak_asked"]:
            out.append(case("feature.leakage_suspect", fields={"dtype": ident["dtype"], "availability": rule["availability"]},
                            context={"target": TARGET, "task": task}, rule=rule["leak"], label=label["leak"]))
    return out


def benchmark_corpus(suite: str = "quick", *, tasks: list[str] | None = None, seed: int = 42) -> list[Case]:
    """Cases from the R1 tasks (both name variants). ``full`` needs network (OpenML); never in CI."""

    try:
        from benchmarks.harness.run import load_task_frame
        from benchmarks.harness.tasks import SUITES, find
    except ModuleNotFoundError:  # the CLI runs with apps/api on the path; the R1 harness lives at the repo root
        import sys
        from pathlib import Path

        sys.path.append(str(Path(__file__).resolve().parents[4]))
        from benchmarks.harness.run import load_task_frame
        from benchmarks.harness.tasks import SUITES, find

    selected = [find(t) for t in tasks] if tasks else list(SUITES[suite])
    corpus: list[Case] = []
    for task in selected:
        frame = load_task_frame(task, seed=seed)
        if len(frame) > _ROW_LIMIT:
            frame = frame.sample(n=_ROW_LIMIT, random_state=seed).reset_index(drop=True)
        planted, labels = plant(frame, task.task_type, seed)
        corpus += cases_for_frame(planted, labels, dataset=task.id, variant="plain", task_type=task.task_type)
        names = {c: (TARGET if c == TARGET else f"f{i:03d}") for i, c in enumerate(planted.columns)}
        hidden = planted.rename(columns=names)
        # The obfuscated copy shares the task's rows: same cluster (``dataset``), not a second dataset.
        corpus += cases_for_frame(hidden, {names[c]: v for c, v in labels.items()}, dataset=task.id,
                                  variant="obfuscated", task_type=task.task_type)
    return corpus


def release_pair() -> dict[str, str]:
    """The (prompt release, model) pair the Jev evidence is keyed by (ADR 0008 §3)."""

    versions = sorted({f"{r.purpose}@v{r.version}" for r in RELEASES.values()})
    return {"release": "+".join(versions), "model_id": JEV_MODEL_ID}


# --- answerers -------------------------------------------------------------------------

Answer = tuple[dict[str, Any], float | None] | None  # (answer, confidence) or unavailable
Answerer = Callable[[JevRelease, list[Case]], list[Answer]]


def ai_off(_release: JevRelease, cases: list[Case]) -> list[Answer]:
    return [None] * len(cases)


class ScriptedAnswerer:
    """Deterministic fake: right with probability ``accuracy`` (seeded), abstains sometimes,
    confidence higher when right. Proves the pipeline and the rules, never model quality."""

    def __init__(self, *, seed: int = 7, accuracy: float = 0.9, abstain: float = 0.1) -> None:
        self.seed, self.accuracy, self.abstain = seed, accuracy, abstain

    def __call__(self, release: JevRelease, cases: list[Case]) -> list[Answer]:
        rng = random.Random(f"{self.seed}:{release.purpose}")
        out: list[Answer] = []
        for case in cases:
            right = rng.random() < self.accuracy
            value = case.label if right else _wrong(release, case.label, rng)
            if release.primitive == "noul":
                p = rng.uniform(0.92, 0.995) if rng.random() >= self.abstain else rng.uniform(0.3, 0.7)
                out.append(({"value": p if value else 1 - p}, None))
            else:
                confidence = rng.uniform(0.3, 0.79) if rng.random() < self.abstain else (
                    rng.uniform(0.86, 0.99) if right else rng.uniform(0.80, 0.9))
                out.append(({"value": value}, round(confidence, 4)))
        return out


def port_answerer(db: Session, port: Any, workspace_id: UUID, *, settings: Any, dataset_id: UUID | None = None,
                  project_id: UUID | None = None) -> Answerer:
    """Ask a ``SemanticPort`` (the gateway's Jev port, or a test port over the fake provider)
    inside the **designated platform evaluation workspace** (``settings.r3_eval_workspace_id``;
    any other workspace is refused: an R3 run never spends a tenant's budget, seeds its cache
    or lands in its evaluation samples). A case reaches the provider only when its column is a
    registered ``DatasetColumn`` there (the gateway binds names to stored columns); the rest
    are ``unavailable``. Asks bypass the answer cache (ADR 0008 §4). Live use is
    ``dclab r3 run --live`` only, never CI."""

    from app.agents.contracts import Untrusted
    from app.agents.semantic.port import SemanticAsk, Subject
    from app.db.models import Dataset, DatasetColumn

    if not platform_evaluation_workspace_ok(db, workspace_id, settings):
        raise GovernanceNotPermitted("r3_workspace_not_designated",
                                     "live R3 asks run only inside the platform evaluation workspace")
    query = (select(Dataset.name, DatasetColumn.name, DatasetColumn.id, DatasetColumn.dataset_id, Dataset.project_id)
             .join(Dataset, Dataset.id == DatasetColumn.dataset_id)
             .where(DatasetColumn.workspace_id == workspace_id, Dataset.workspace_id == workspace_id))
    if dataset_id is not None:
        query = query.where(DatasetColumn.dataset_id == dataset_id)
    # keyed by (registered dataset name, column): planted names repeat across tasks
    columns = {(source, name): (cid, did, pid) for source, name, cid, did, pid in db.execute(query).all()}

    def wrap(fields: dict[str, Any]) -> dict[str, Any]:
        return {k: ([Untrusted(untrusted_text=t) for t in v] if k == "name_tokens" else v) for k, v in fields.items()}

    def answer(release: JevRelease, cases: list[Case]) -> list[Answer]:
        out: list[Answer] = [None] * len(cases)
        groups: dict[tuple, list[tuple[int, Case]]] = defaultdict(list)  # one ask per (dataset, context)
        for i, c in enumerate(cases):
            if (c.source, c.column) in columns:
                groups[(c.source, json.dumps(c.context, sort_keys=True))].append((i, c))
            else:
                answer.unregistered += 1
        for (source, _), asked in groups.items():
            raw_context = asked[0][1].context
            target = raw_context.get("target")
            if target is not None and (source, target) not in columns:
                answer.unregistered += len(asked)
                continue  # the leakage context needs the target column registered in the same dataset
            context = {k: (Untrusted(untrusted_text=v) if k == "target" else v) for k, v in raw_context.items()}
            context_columns = {"target": columns[(source, target)][0]} if target is not None else {}
            _, did, project = columns[(source, asked[0][1].column)]  # the ledger row is attributed to the dataset's project
            for start in range(0, len(asked), 50):
                chunk = asked[start:start + 50]
                subjects = tuple(Subject(question_key=c.column, column_id=columns[(source, c.column)][0],
                                         rule_answer=c.rule,
                                         fields={"column": Untrusted(untrusted_text=c.column), **wrap(c.fields)})
                                 for _, c in chunk)
                ask = SemanticAsk(purpose=release.purpose, workspace_id=workspace_id, project_id=project or project_id,
                                  dataset_id=did if project is not None else None,  # answer rows: dataset ⊂ project
                                  context=context, context_columns=context_columns, subjects=subjects,
                                  source_datasets=(did,), levels={"*": 0}, cache=False)
                outcome = port.resolve(db, ask)
                for (i, c), res in zip(chunk, outcome.resolutions):
                    answer.resolutions.append((c, res))  # the port's own value_used, for the ablation check
                    if res.raw_answer is not None:
                        out[i] = (dict(res.raw_answer), res.confidence)
        return out

    answer.resolutions, answer.unregistered = [], 0  # type: ignore[attr-defined]
    answer.live = True  # type: ignore[attr-defined]  # derived from the answerer, never from a string
    answer.evaluation_workspace_id = workspace_id  # type: ignore[attr-defined]
    return answer


def _wrong(release: JevRelease, label: Any, rng: random.Random) -> Any:
    if release.primitive == "noul":
        return not label
    return rng.choice([c for c in release.choices if c != label])


# --- scoring ---------------------------------------------------------------------------


@dataclass
class Scored:
    case: Case
    agreement: str  # agree | disagree | abstain | unavailable
    ai_value: Any
    confidence: float | None
    rule_correct: bool
    policy_correct: bool  # the deployed cross-check policy: AI value in band, else the rule
    ai_correct: bool | None  # None unless in band
    override_eligible: bool  # §1b-eligible in-band disagreement (deployed-policy precision)
    raw_correct: bool | None = None  # the model's own answer, band or not (calibration)


def score(release: JevRelease, cases: list[Case], answers: list[Answer]) -> list[Scored]:
    out: list[Scored] = []
    for case, answer in zip(cases, answers):
        banded = agreement.band(release, answer[0] if answer else None, answer[1] if answer else None)
        agree = "unavailable" if answer is None else agreement.agreement(release, case.rule, banded)
        value = banded.value if banded.in_acting_band else None
        in_band = banded.in_acting_band and value is not None
        ai_ok = (value == case.label) if in_band else None
        kind = agreement.answer_kind(release, case.rule, value)
        # §1b-eligible and, as in production, accepted by the deployed RoleValidator on the frame
        eligible = agree == "disagree" and (release.purpose != "column.semantic_role"
                                            or (kind == "role_numeric_categorical" and value in case.accepts))
        conf = raw_ok = None
        if answer is not None:
            raw = answer[0]["value"]
            if release.primitive == "noul":
                conf, raw_ok = max(raw, 1 - raw), (raw >= 0.5) == case.label
            else:
                conf, raw_ok = answer[1], raw == case.label
        # The deployed L2 policy (agreement table): the AI value when it agrees or when a
        # §1b-eligible disagreement passes the validator, else the rule; L0/L1 never apply it.
        used = agreement.apply(2, agree, case.rule, value, validator_ok=eligible).value_used
        rule_ok = _correct(release, case.rule, case.label)
        out.append(Scored(case, agree, value, conf, rule_ok, _correct(release, used, case.label), ai_ok, eligible,
                          raw_ok))
    return out


def _correct(release: JevRelease, value: Any, label: Any) -> bool:
    if release.purpose == "feature.leakage_suspect":  # the rule's vocabulary is exclude / review_flag / clear
        value = value in ("exclude", "review_flag") or value is True
    return value == label


def wilson(successes: float, n: float, z: float = 1.96) -> tuple[float, float]:
    if n <= 0:
        return 0.0, 1.0
    p, z2 = successes / n, z * z
    centre = (p + z2 / (2 * n)) / (1 + z2 / n)
    half = z * math.sqrt(p * (1 - p) / n + z2 / (4 * n * n)) / (1 + z2 / n)
    return max(0.0, centre - half), min(1.0, centre + half)


def weighted_precision(hits: list[tuple[bool, float]]) -> dict[str, Any]:
    """Precision with Kish effective n (ADR 0008 §4: never a raw-count bound under IPW)."""

    if not hits:
        return {"n": 0, "n_eff": 0.0, "errors": 0, "precision": None, "lb": None, "ub": None}
    total = sum(w for _, w in hits)
    n_eff = total * total / sum(w * w for _, w in hits)
    p = sum(w for ok, w in hits if ok) / total
    lb, ub = wilson(p * n_eff, n_eff)
    return {"n": len(hits), "n_eff": round(n_eff, 2), "errors": sum(1 for ok, _ in hits if not ok),
            "precision": round(p, 4), "lb": round(lb, 4), "ub": round(ub, 4)}


def paired_gap(by_cluster: dict[str, list[float]], *, seed: int = 0, margins: tuple[float, ...] = (0.05, 0.01),
               draws: int = 2000) -> dict[str, Any]:
    """95 % one-sided bounds of mean(AI policy − rule). ≥ 20 clusters: cluster bootstrap
    (percentiles; p = share of draws ≤ −m); fewer: t with G − 1 df on cluster means."""

    clusters = [v for v in by_cluster.values() if v]
    g = len(clusters)
    if g == 0:
        return {"clusters": 0, "point": None, "lb": None, "ub": None, "method": "none", "p_values": {}}
    point = float(np.mean([d for c in clusters for d in c]))
    if g >= 20:
        rng = np.random.default_rng(seed)
        sums, sizes = np.array([sum(c) for c in clusters]), np.array([len(c) for c in clusters])
        picks = rng.integers(0, g, size=(draws, g))
        means = sums[picks].sum(axis=1) / sizes[picks].sum(axis=1)
        lb, ub = float(np.quantile(means, ALPHA)), float(np.quantile(means, 1 - ALPHA))
        p_values = {str(m): max(float(np.mean(means <= -m)), 1 / (draws + 1)) for m in margins}
        method, estimand = "cluster_bootstrap", "pooled case mean"
    else:
        from scipy import stats

        means = [statistics.fmean(c) for c in clusters]
        se = (statistics.stdev(means) / math.sqrt(g)) if g > 1 else float("inf")
        t = float(stats.t.ppf(1 - ALPHA, g - 1)) if g > 1 else float("inf")
        centre = statistics.fmean(means)
        usable = g > 1 and se > 0  # zero spread across few clusters is no evidence, not certainty
        lb, ub = (centre - t * se, centre + t * se) if usable else (float("nan"), float("nan"))
        p_values = {str(m): (float(stats.t.sf((centre + m) / se, g - 1)) if usable else 1.0) for m in margins}
        method, estimand = "t_cluster_means", "mean of cluster means"
    return {"clusters": g, "point": round(point, 4), "lb": round(lb, 4) if math.isfinite(lb) else None,
            "ub": round(ub, 4) if math.isfinite(ub) else None, "method": method, "estimand": estimand,
            "p_values": p_values}


def reliability(confidences: list[float], correct: list[bool], bins: int | None = None) -> dict[str, Any]:
    """Equal-mass reliability table and ECE (reported only; ADR 0008 §4 never gates on ECE)."""

    n = len(confidences)
    if n == 0:
        return {"n": 0, "ece": None, "bins": []}
    k = bins or max(1, min(10, n // 5))
    order = np.argsort(confidences, kind="stable")
    rows, ece = [], 0.0
    for chunk in np.array_split(order, k):
        if len(chunk) == 0:
            continue
        conf = float(np.mean([confidences[i] for i in chunk]))
        acc = float(np.mean([correct[i] for i in chunk]))
        rows.append({"n": int(len(chunk)), "confidence": round(conf, 4), "accuracy": round(acc, 4)})
        ece += len(chunk) / n * abs(acc - conf)
    return {"n": n, "ece": round(ece, 4), "bins": rows, "gate": False, "upper_bound_reported": n >= 1000}


def holm(p_values: dict[str, float]) -> dict[str, float]:
    items = sorted(p_values.items(), key=lambda kv: kv[1])
    out, running, m = {}, 0.0, len(items)
    for rank, (name, p) in enumerate(items):
        running = max(running, min(1.0, (m - rank) * p))
        out[name] = round(running, 6)
    return out


def source_metrics(scored: list[Scored], *, seed: int = 0) -> dict[str, Any]:
    """``n`` and every gate statistic count ANSWERED cases only (an unavailable answer takes the
    rule value and would pull the gap toward 0); ``total`` / ``unavailable_rate`` keep the rest."""

    total = len(scored)
    answered = [s for s in scored if s.agreement != "unavailable"]
    n = len(answered)
    counts = Counter(s.agreement for s in scored)
    base = {"total": total, "n": n, "unavailable_rate": round(counts["unavailable"] / total, 4) if total else None,
            "rule_accuracy_all": round(sum(s.rule_correct for s in scored) / total, 4) if total else None,
            "ai_policy_accuracy_all": round(sum(s.policy_correct for s in scored) / total, 4) if total else None}
    if n == 0:
        return {**base, "datasets": 0}
    in_band = [s for s in answered if s.ai_correct is not None]
    by_cluster: dict[str, list[float]] = defaultdict(list)
    for s in answered:
        by_cluster[s.case.dataset].append(float(s.policy_correct) - float(s.rule_correct))
    overrides = [(bool(s.ai_correct), s.case.weight) for s in in_band if s.override_eligible]
    return {
        **base, "datasets": len(by_cluster),
        "rule_accuracy": round(sum(s.rule_correct for s in answered) / n, 4),
        "ai_policy_accuracy": round(sum(s.policy_correct for s in answered) / n, 4),
        "ai_in_band_accuracy": round(sum(bool(s.ai_correct) for s in in_band) / len(in_band), 4) if in_band else None,
        "disagreement_rate": round(counts["disagree"] / n, 4), "abstain_rate": round(counts["abstain"] / n, 4),
        "in_band": weighted_precision([(bool(s.ai_correct), s.case.weight) for s in in_band]),
        # yes/no points: per band (ADR 0008 §4) — the "yes" band is the one that flags / excludes
        "bands": {name: weighted_precision([(bool(s.ai_correct), s.case.weight) for s in in_band
                                            if s.ai_value is value])
                  for name, value in (("yes", True), ("no", False))},
        "deployed_policy": weighted_precision(overrides),
        "gap": paired_gap(by_cluster, seed=seed),
        "calibration": reliability([s.confidence for s in scored if s.confidence is not None],
                                   [bool(s.raw_correct) for s in scored if s.confidence is not None]),
    }


# --- the run ---------------------------------------------------------------------------


def evaluate(corpus: list[Case], answerer: Answerer, *, seed: int = 0) -> dict[str, dict[str, Any]]:
    """Per decision point, per source partition (sealed / development), the §4 metrics."""

    out: dict[str, dict[str, Any]] = {}
    for key in JEV_POINTS:
        release = RELEASES[key]
        cases = [c for c in corpus if c.purpose == key]
        scored = score(release, cases, answerer(release, cases))
        out[key] = {part: source_metrics([s for s in scored if s.case.partition == part], seed=seed)
                    for part in ("sealed", "development")}
        out[key]["all"] = source_metrics(scored, seed=seed)
    return out


def ablation(corpus: list[Case]) -> dict[str, Any]:
    """AI off must equal the rule baseline on every point (ADR 0008 §8)."""

    off = evaluate(corpus, ai_off)
    report = {}
    for key, parts in off.items():
        metrics = parts["all"]
        report[key] = {"ai_off_accuracy": metrics.get("ai_policy_accuracy_all"),
                       "rule_accuracy": metrics.get("rule_accuracy_all"), "cases": metrics.get("total", 0),
                       "equal": metrics.get("total", 0) > 0 and metrics.get("unavailable_rate") == 1.0
                       and metrics.get("ai_policy_accuracy_all") == metrics.get("rule_accuracy_all")}
    return report


def opted_in(db: Session, workspace_id: UUID) -> bool:
    """The workspace's **own** accepted policy row says ``data.share_r3_aggregates`` (ADR 0008 §4
    explicit opt-in); the platform document is never inherited for this flag."""

    from app.agents.governance.policy import _accepted_head

    head = _accepted_head(db, workspace_id)
    return bool(head is not None and ((head.policy or {}).get("data") or {}).get("share_r3_aggregates") is True)


def pseudonym(workspace_id: UUID, salt: str) -> str:
    """Keyed by a random per-run salt that is never stored or published (``secrets.token_hex`` in
    ``run_r3``): unlinkable across runs and not reversible from the workspace list and the report."""

    return hmac.new(salt.encode(), f"r3:{workspace_id}".encode(), hashlib.sha256).hexdigest()[:16]


SMALL_BUCKET = 10  # aggregates below this count are suppressed before they leave the service


def _suppress(aggregates: dict[str, dict[str, Any]]) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    for key, row in aggregates.items():
        kept: dict[str, Any] = {}
        answers = row.get("answers")
        if answers and answers.get("total", 0) >= SMALL_BUCKET:
            kept["answers"] = {**answers, "by_agreement": {k: v for k, v in answers["by_agreement"].items()
                                                           if v >= SMALL_BUCKET}}
        labels = {src: b for src, b in (row.get("labels") or {}).items() if b.get("n", 0) >= SMALL_BUCKET}
        if labels:
            kept["labels"] = labels
        proposals = row.get("proposals")
        if proposals and proposals.get("total", 0) >= SMALL_BUCKET:
            kept["proposals"] = {**proposals, "by_status": {k: v for k, v in proposals["by_status"].items()
                                                            if v >= SMALL_BUCKET}}
        ledger = row.get("ledger")
        if ledger and ledger.get("calls", 0) >= SMALL_BUCKET:
            kept["ledger"] = ledger
        if kept:
            out[key] = kept
    return out


PARTITIONS = ("both", "development", "sealed")


def run_r3(corpus: list[Case], answerer: Answerer, *, candidate: str, db: Session | None = None,
           workspace_ids: list[UUID] = (), evaluation_workspace_id: UUID | None = None, actor: User | None = None,
           previous: dict[str, Any] | None = None, seed: int = 0, apply_demotions: bool = False,
           partition: str = "both", operator: dict[str, Any] | None = None, settings: Any = None) -> dict[str, Any]:
    """One R3 run: corpus metrics for the candidate answerer, the AI-off ablation and deltas,
    per-workspace aggregates of **opted-in** workspaces (per-run pseudonyms, small buckets
    suppressed; needs a dclab_admin ``actor``) plus the platform evaluation workspace's own ledger,
    promotion / demotion verdicts, Holm-corrected p-values. With a database the run is stored
    (``r3_runs``) — the only form of evidence a platform promotion may cite. ``partition``:
    ``development`` is the prompt-work mode (sealed cases never scored)."""

    if partition not in PARTITIONS:
        raise ValueError(f"partition must be one of {PARTITIONS}")
    if partition != "both":
        corpus = [c for c in corpus if c.partition == partition]
    if workspace_ids and (db is None or actor is None or not is_platform_admin(db, actor)):
        raise GovernanceNotPermitted("r3_tenant_evidence_needs_admin",
                                     "tenant aggregates join a run only for an active dclab_admin")
    live = bool(getattr(answerer, "live", False))  # a property of the answerer, never of a string
    if live:
        evaluation_workspace_id = getattr(answerer, "evaluation_workspace_id", evaluation_workspace_id)
    if evaluation_workspace_id is not None and not (
            db is not None and platform_evaluation_workspace_ok(db, evaluation_workspace_id, settings)):
        raise GovernanceNotPermitted("r3_workspace_not_designated",
                                     "the evaluation workspace must be the designated platform workspace")
    if live and (db is None or actor is None or not is_platform_admin(db, actor)):
        raise GovernanceNotPermitted("r3_live_needs_admin", "a live run is stored only for an active dclab_admin")
    candidate = f"live:{candidate.removeprefix('live:')}" if live else candidate.removeprefix("live:")
    pair = release_pair()
    run_id = str(uuid4())
    salt = secrets.token_hex(16)  # per-run pseudonym key: never stored, never published
    points = evaluate(corpus, answerer, seed=seed)
    off = ablation(corpus)
    evidence: dict[str, Any] = {}
    scopes: list[UUID] = []
    skipped = 0
    if db is not None:
        for w in workspace_ids:
            if opted_in(db, w):
                evidence[pseudonym(w, salt)] = _suppress(workspace_aggregates(db, w))
                scopes.append(w)
            else:
                skipped += 1
        if evaluation_workspace_id is not None:
            evidence["evaluation"] = workspace_aggregates(db, evaluation_workspace_id)
            scopes.append(evaluation_workspace_id)
    report: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION, "run_id": run_id, "created_at": datetime.now(UTC).isoformat(),
        "candidate": candidate, "live": live, "partition": partition, "pair": pair,
        "evaluation_workspace": str(evaluation_workspace_id) if evaluation_workspace_id else None,
        "operator": operator or {}, "cases": len(corpus),
        "sealed_datasets": len({c.dataset for c in corpus if c.partition == "sealed"}),
        "points": {},
        # ADR 0008 §8 replayed through the scorer (rule replay); the real-path check — AI switched off
        # through the port and the gateway — lives in tests/ai_harness/test_ai_r3.py.
        "ablation": {"method": "rule_replay", "real_path_check": "tests/ai_harness/test_ai_r3.py", "points": off},
        "workspace_evidence": evidence, "workspaces_not_opted_in": skipped,
    }
    # Holm across the gates this run tests: every point's next level, each source (ADR 0008 §4).
    p_values: dict[str, float] = {}
    currents = {key: (_platform_level(db, key) if db is not None else 0) for key in REGISTRY}
    for key, metrics in points.items():
        margin = GATES.get(currents[key] + 1, {}).get("margin")
        for source in ("sealed", "blind"):
            p = ((metrics.get(source) or {}).get("gap") or {}).get("p_values", {}).get(str(margin))
            if p is not None:
                p_values[f"{key}:{source}"] = p
    adjusted = holm(p_values)
    for key, point in REGISTRY.items():
        metrics = points.get(key, {})
        ops = _operations(point, evidence)
        incidents = {}
        if db is not None:
            incidents = open_incident_counts(db, None, key)
            for w in scopes:
                for kind, count in open_incident_counts(db, w, key).items():
                    incidents[kind] = incidents.get(kind, 0) + count
        current = currents[key]
        entry = {"key": key, "ai_kind": point.ai_kind, "cap": point.cap, "max_level": _max_level(point),
                 "current_platform_level": current, "sources": metrics, "operations": ops,
                 "open_incidents": incidents, "delta_vs_rule_replay": _delta(metrics.get("all"), off.get(key))}
        prev = (previous or {}).get("points", {}).get(key)
        entry["promotion"] = {f"L{lvl}": asdict(promotion_check(
            entry, lvl, previous=prev, holm=adjusted if lvl == current + 1 else None))
            for lvl in (1, 2, 3) if lvl <= point.cap}
        entry["demotion"] = asdict(demotion_check(entry, current))
        report["points"][key] = entry
    report["holm_adjusted_p_values"] = adjusted
    report["digest"] = report_digest(report)  # content (deterministic across runs of the same inputs)
    report["run_digest"] = run_digest(report)  # identity: content + run id + time (a copy is not a second run)
    if db is not None:
        store_run(db, report)
    if db is not None and apply_demotions:  # ADR 0008 §4: an R3 demotion verdict is an eval_failure incident
        from app.agents.governance.incidents import open_incident

        for key, entry in report["points"].items():
            verdict = entry["demotion"]
            if verdict["allowed"] and entry["current_platform_level"] > 0:
                open_incident(db, workspace_id=None, kind="eval_failure", subject_kind="decision_point",
                              subject_key=key, evidence={"demote_to": verdict["level"], "r3_run": report["run_id"],
                                                         "digest": report["digest"], "reasons": verdict["reasons"]})
    return report


def _verify(report: dict[str, Any]) -> None:
    """A report that writes or proposes levels must carry its own digest (not an edited file)."""

    if report.get("digest") != report_digest(report) or report.get("run_digest") != run_digest(report):
        raise GovernanceNotPermitted("r3_report_tampered", "the report digests do not match its content")


def stored_content(report: dict[str, Any]) -> dict[str, Any]:
    """The digest-covered part of a report plus its identity (never the tenant evidence)."""

    return {k: v for k, v in report.items() if k != "workspace_evidence"}


def store_run(db: Session, report: dict[str, Any]) -> R3Run:
    """Persist the run as it happens (append-only ``r3_runs``); the row is what promotions cite."""

    if len(report["candidate"]) > 128 or len(report["pair"]["release"]) > 512:
        raise ValueError("r3 run identity too long for r3_runs (candidate 128 / pair release 512)")  # never slice
    row = R3Run(id=UUID(report["run_id"]), candidate=report["candidate"], live=bool(report["live"]),
                pair_release=report["pair"]["release"], model_id=report["pair"]["model_id"],
                content_digest=report["digest"], run_digest=report["run_digest"], cases=int(report["cases"]),
                report=stored_content(report), created_at=datetime.fromisoformat(report["created_at"]))
    db.add(row)
    db.flush()
    return row


def load_run(db: Session, run_id: UUID) -> dict[str, Any]:
    """A stored run, re-verified against its digests (a tampered row is refused)."""

    row = db.get(R3Run, run_id)
    if row is None:
        raise GovernanceNotFound(detail="r3 run")
    report = dict(row.report)
    if (report.get("digest") != row.content_digest.strip() or report.get("run_digest") != row.run_digest.strip()
            or str(report.get("run_id")) != str(row.id)):
        raise GovernanceNotPermitted("r3_report_tampered", "the stored run does not match its digests")
    _verify(report)
    return report


def previous_run(db: Session, report: dict[str, Any]) -> dict[str, Any] | None:
    """The stored live run recorded (database clock) immediately before ``report`` for the same pair."""

    this = db.get(R3Run, UUID(report["run_id"]))
    if this is None:
        return None
    row = db.scalar(select(R3Run).where(
        R3Run.pair_release == report["pair"]["release"], R3Run.model_id == report["pair"]["model_id"],
        R3Run.live.is_(True), R3Run.id != this.id, R3Run.recorded_at < this.recorded_at,
    ).order_by(R3Run.recorded_at.desc()).limit(1))
    return load_run(db, row.id) if row is not None else None


def report_digest(report: dict[str, Any]) -> str:
    body = {k: v for k, v in report.items()
            if k not in ("run_id", "created_at", "digest", "run_digest", "workspace_evidence")}
    return hashlib.sha256(json.dumps(body, sort_keys=True, default=str).encode()).hexdigest()


def run_digest(report: dict[str, Any]) -> str:
    return hashlib.sha256(f"{report['digest']}|{report['run_id']}|{report['created_at']}".encode()).hexdigest()


def _max_level(point: DecisionPoint) -> int:
    release = RELEASES.get(point.key)
    return min(point.cap, release.max_level) if release is not None else point.cap


def _delta(candidate: dict[str, Any] | None, off: dict[str, Any] | None) -> dict[str, Any] | None:
    if not candidate or not off or candidate.get("n", 0) == 0:
        return None
    return {"ai_policy_minus_ai_off": round(candidate["ai_policy_accuracy"] - off["ai_off_accuracy"], 4),
            "ai_off_equals_rule": off["equal"]}


def current_pair(db: Session, key: str) -> tuple[UUID | None, str | None]:
    """The (released prompt row, model) pair evidence for ``key`` is keyed by today (Jev points)."""

    release = RELEASES.get(key)
    if release is None:
        return None, None
    release_id = db.scalar(select(PromptRelease.id).where(
        PromptRelease.agent_key == release.agent_key, PromptRelease.version == release.version,
        PromptRelease.status == "released"))
    return release_id, release.model_id


def _platform_level(db: Session, key: str) -> int:
    """The platform head's level for the CURRENT pair: a head covering another (release, model)
    pair — a bump happened — counts as L0 (ADR 0008 §3, §5)."""

    head = _level_head(db, None, key)
    if head is None or head.level == 0:
        return 0
    release_id, model_id = current_pair(db, key)
    if release_id is not None and (head.prompt_release_id, head.model_id) != (release_id, model_id):
        return 0
    if release_id is None:  # agent points: the cited prompt release must still be released
        status = db.scalar(select(PromptRelease.status).where(PromptRelease.id == head.prompt_release_id))
        if status != "released":
            return 0
    return head.level


def platform_evaluation_workspace_ok(db: Session, workspace_id: UUID | None, settings: Any) -> bool:
    """The designated evaluation workspace: configured by id AND carrying the platform-owned
    ``r3_platform_evaluation`` capability (a positive marker only platform staff set)."""

    from app.db.models import WorkspaceCapability, WorkspaceMembership
    from app.services.authorization_service import platform_role_for

    designated = getattr(settings, "r3_eval_workspace_id", None)
    if workspace_id is None or not designated or str(workspace_id) != str(designated):
        return False
    marked = db.scalar(select(WorkspaceCapability.id).where(
        WorkspaceCapability.workspace_id == workspace_id, WorkspaceCapability.capability == EVALUATION_CAPABILITY,
        WorkspaceCapability.enabled.is_(True)).limit(1)) is not None
    if not marked:
        return False
    # and no unsuspended member who is not platform staff: a customer workspace can never qualify
    members = db.execute(select(User).join(WorkspaceMembership, WorkspaceMembership.user_id == User.id).where(
        WorkspaceMembership.workspace_id == workspace_id, WorkspaceMembership.suspended_at.is_(None))).scalars()
    return all(platform_role_for(db, member) is not None for member in members)


EVALUATION_CAPABILITY = "r3_platform_evaluation"


def _operations(point: DecisionPoint, evidence: dict[str, Any]) -> dict[str, Any]:
    """Validator rejection, citation validity, p95 latency and cost from the workspace
    aggregates (ledger + proposals), summed over the opted-in workspaces."""

    proposals = rejected = cited = invalid = calls = micros = 0
    p95: list[float] = []
    for ws in evidence.values():
        row = ws.get(point.key) or {}
        prop = row.get("proposals") or {}
        proposals += prop.get("total", 0)
        rejected += prop.get("rejected_by_validator", 0)
        cited += prop.get("with_citations", 0)
        invalid += prop.get("citation_invalid", 0)
        ledger = row.get("ledger") or {}
        calls += ledger.get("calls", 0)
        micros += ledger.get("cost_micros", 0)
        if ledger.get("p95_latency_ms") is not None:
            p95.append(ledger["p95_latency_ms"])
    latency, cost = (max(p95) if p95 else None), (micros / calls * 1000 if calls else None)
    budget = BUDGETS["jev" if point.ai_kind.startswith("jev:") else "agent"]
    return {"proposals": proposals, "validator_rejection_rate": round(rejected / proposals, 4) if proposals else None,
            "citation_validity": round(1 - invalid / cited, 4) if cited else None, "calls": calls,
            "p95_latency_ms": latency, "cost_micros_per_1000": round(cost) if cost is not None else None,
            "budget": {"p95_latency_ms": budget[0], "cost_micros_per_1000": budget[1]},
            "within_budget": None if latency is None or cost is None else (latency <= budget[0] and cost <= budget[1])}


# --- tenant evidence (aggregates only) --------------------------------------------------


def workspace_evidence(db: Session, *, workspace_id: UUID, actor: User) -> dict[str, Any]:
    """Per-workspace aggregates for an approver of that workspace; a platform admin only
    after the workspace opted in (``data.share_r3_aggregates``). Another tenant's id reads as absent."""

    if can_approve_ai_policy(db, actor, workspace_id):
        return workspace_aggregates(db, workspace_id)
    if is_platform_admin(db, actor) and opted_in(db, workspace_id):
        return _suppress(workspace_aggregates(db, workspace_id))
    raise GovernanceNotFound(detail="workspace")


def workspace_aggregates(db: Session, workspace_id: UUID) -> dict[str, dict[str, Any]]:
    """Counts and rates only (never rows, values or holdout data), keyed by decision point."""

    out: dict[str, dict[str, Any]] = defaultdict(dict)
    agreement_rows = db.execute(
        select(SemanticDecisionAnswer.decision_point_key, SemanticDecisionAnswer.agreement,
               SemanticDecisionAnswer.in_acting_band, func.count())
        .where(SemanticDecisionAnswer.workspace_id == workspace_id)
        .group_by(SemanticDecisionAnswer.decision_point_key, SemanticDecisionAnswer.agreement,
                  SemanticDecisionAnswer.in_acting_band)).all()
    for key, agree, in_band, count in agreement_rows:
        answers = out[key].setdefault("answers", {"total": 0, "in_band": 0, "by_agreement": {}})
        answers["total"] += count
        answers["in_band"] += count if in_band else 0
        answers["by_agreement"][agree] = answers["by_agreement"].get(agree, 0) + count
    labeled = db.execute(
        select(SemanticDecisionAnswer.decision_point_key, SemanticDecisionAnswer.label_source,
               SemanticDecisionAnswer.answer, SemanticDecisionAnswer.confidence, SemanticDecisionAnswer.ground_truth,
               SemanticDecisionAnswer.in_acting_band)
        .where(SemanticDecisionAnswer.workspace_id == workspace_id, SemanticDecisionAnswer.ground_truth.is_not(None),
               SemanticDecisionAnswer.labels_version > 0).limit(100_000)).all()
    for key, source, answer, confidence, truth, in_band in labeled:
        release = RELEASES.get(key)
        if release is None or not in_band:
            continue
        value = agreement.band(release, answer, float(confidence) if confidence is not None else None).value
        bucket = out[key].setdefault("labels", {}).setdefault(source or "unknown", {"n": 0, "correct": 0})
        bucket["n"] += 1
        bucket["correct"] += int(value == (truth or {}).get("value"))
    cited = (func.jsonb_array_length(AgentProposal.citations) > 0).label("cited")
    for key, status, verdict, citations, reasons, count in db.execute(
            select(AgentProposal.decision_point_key, AgentProposal.status, AgentProposal.validator_verdict, cited,
                   AgentProposal.validator_reasons, func.count())
            .where(AgentProposal.workspace_id == workspace_id)
            .group_by(AgentProposal.decision_point_key, AgentProposal.status, AgentProposal.validator_verdict,
                      cited, AgentProposal.validator_reasons)).all():
        prop = out[key].setdefault("proposals", {"total": 0, "by_status": {}, "with_citations": 0, "citation_invalid": 0})
        prop["total"] += count
        prop["by_status"][status] = prop["by_status"].get(status, 0) + count
        prop["rejected_by_validator"] = prop.get("rejected_by_validator", 0) + (count if verdict == "rejected" else 0)
        prop["with_citations"] += count if citations else 0
        if any(str(r if isinstance(r, str) else (r or {}).get("code", "")).startswith("citation") for r in (reasons or [])):
            prop["citation_invalid"] += count
    for key, calls, micros, p95 in db.execute(
            select(LlmInvocation.decision_point_key, func.count(), func.coalesce(func.sum(LlmInvocation.cost_micros), 0),
                   func.percentile_cont(0.95).within_group(LlmInvocation.latency_ms))
            .where(LlmInvocation.workspace_id == workspace_id, LlmInvocation.decision_point_key.is_not(None))
            .group_by(LlmInvocation.decision_point_key)).all():
        out[key]["ledger"] = {"calls": int(calls), "cost_micros": int(micros),
                              "p95_latency_ms": float(p95) if p95 is not None else None}
    return dict(out)


# --- rule engine (ADR 0008 §4) ---------------------------------------------------------


@dataclass
class Verdict:
    level: int | None
    allowed: bool
    checks: dict[str, bool] = field(default_factory=dict)
    reasons: list[str] = field(default_factory=list)


def _check(verdict: Verdict, name: str, ok: bool | None, detail: str) -> None:
    verdict.checks[name] = bool(ok)
    if not ok:
        verdict.reasons.append(f"{name}: {detail}")


BAND_EDGE = {"noul": 0.90, "choice": 0.80}  # ADR 0008 §4 calibration: in-band precision LB ≥ band edge
MAX_UNAVAILABLE = 0.05  # a run whose provider / harness failed more often measures nothing


def clopper_pearson_upper(successes: float, n: float, alpha: float = ALPHA) -> float | None:
    """Exact one-sided upper bound of a proportion (demotion: evidence of harm at any n, never a
    floor that hides strong evidence; thin evidence simply yields a bound near 1)."""

    if n <= 0:
        return None
    if successes >= n:
        return 1.0
    from scipy import stats

    return float(stats.beta.ppf(1 - alpha, successes + 1, n - successes))


def _band_names(key: str) -> tuple[str, ...]:
    """The acting bands the release defines: always ``yes``; ``no`` only with a ``no_at`` edge."""

    release = RELEASES.get(key)
    return ("yes", "no") if release is not None and release.no_at is not None else ("yes",)


def _band_lbs(src: dict[str, Any], key: str) -> list[float | None]:
    """Per-band precision lower bounds of a yes/no point; an empty defined band is None (fails a gate)."""

    return [(src.get("bands") or {}).get(name, {}).get("lb") for name in _band_names(key)]


def promotion_check(entry: dict[str, Any], target: int, *, previous: dict[str, Any] | None = None,
                    holm: dict[str, float] | None = None) -> Verdict:
    """May ``target`` be proposed for this point from the run's evidence? Every §4 gate.
    ``holm``: the run's Holm-adjusted non-inferiority p-values (``<key>:<source>``)."""

    v = Verdict(target, False)
    key, cap, max_level = entry.get("key"), entry["cap"], entry["max_level"]
    sources, ops = entry.get("sources") or {}, entry.get("operations") or {}
    sealed, blind = sources.get("sealed") or {"n": 0}, sources.get("blind") or {"n": 0}
    current = entry.get("current_platform_level", 0)
    _check(v, "within_cap", target <= min(cap, max_level), f"L{target} above cap/max L{min(cap, max_level)}")
    _check(v, "one_step", target == current + 1, f"from L{current} only L{current + 1} may be proposed")
    gate = GATES.get(target)
    if gate is None:
        v.allowed = False
        return v
    if target == 3:
        _check(v, "ops_diagnose_only", key == "ops.diagnose", "L3 exists only for ops.diagnose")
        _check(v, "weeks_at_l2", False, "needs >= 4 weeks at L2 and >= 100 applied decisions (no data)")
        return v
    if target == 2:
        _check(v, "l2_point", key in L2_POINTS, "L2 is allowed only for role / missing-value / families")
        _check(v, "sealed_datasets", sealed.get("datasets", 0) >= gate["sealed_datasets_min"],
               f"{sealed.get('datasets', 0)} sealed datasets < {gate['sealed_datasets_min']}")
        _check(v, "blind_cases", blind.get("n", 0) >= gate["blind_min"], f"{blind.get('n', 0)} blind cases < {gate['blind_min']}")
    else:
        bib = (blind.get("in_band") or {}).get("n", 0)
        _check(v, "blind_in_band", bib >= gate["blind_in_band_min"], f"{bib} blind in-band cases < {gate['blind_in_band_min']}")
    _check(v, "sealed_cases", sealed.get("n", 0) >= gate["sealed_min"],
           f"{sealed.get('n', 0)} answered sealed cases < {gate['sealed_min']}")
    unavailable = sealed.get("unavailable_rate")
    _check(v, "sealed_availability", unavailable is not None and unavailable <= MAX_UNAVAILABLE,
           f"unavailable rate {unavailable} > {MAX_UNAVAILABLE} (harness / provider availability)")
    release = RELEASES.get(key)
    for name, src in (("sealed", sealed), ("blind", blind)):
        lb = (src.get("gap") or {}).get("lb")
        _check(v, f"{name}_non_inferiority", lb is not None and lb >= -gate["margin"],
               f"gap LB {lb} < -{gate['margin']}")
        if holm is not None:
            p = holm.get(f"{key}:{name}")
            _check(v, f"{name}_holm", p is not None and p <= ALPHA, f"Holm-adjusted p {p} > {ALPHA}")
        if key in YES_NO_POINTS:  # per band: the flagging band must stand on its own
            lbs = _band_lbs(src, key)
            _check(v, f"{name}_precision", all(b is not None and b >= gate["precision"] for b in lbs),
                   f"per-band precision LBs {lbs} < {gate['precision']}")
        elif target == 2 and key == "column.semantic_role":
            plb = (src.get("deployed_policy") or {}).get("lb")
            _check(v, f"{name}_precision", plb is not None and plb >= gate["precision"],
                   f"deployed-policy precision LB {plb} < {gate['precision']}")
        if release is not None and release.primitive == "choice":
            edge, elb = BAND_EDGE["choice"], (src.get("in_band") or {}).get("lb")
            _check(v, f"{name}_band_edge", elb is not None and elb >= edge, f"in-band precision LB {elb} < {edge}")
    rate, n_prop = ops.get("validator_rejection_rate"), ops.get("proposals", 0)
    _check(v, "validator_rejection", rate is not None and n_prop >= gate["validator_min_proposals"]
           and rate <= gate["validator_max"], f"rate {rate} over {n_prop} proposals (need >= {gate['validator_min_proposals']}, <= {gate['validator_max']})")
    if target == 1:
        cit = ops.get("citation_validity")
        _check(v, "citation_validity", not str(entry.get("ai_kind", "")).startswith("agent:") or (
            cit is not None and cit >= gate["citation_min"]), f"citation validity {cit} < {gate['citation_min']}")
    incidents = entry.get("open_incidents") or {}
    _check(v, "no_data_exposure", incidents.get("data_exposure", 0) == 0, "open data-exposure incident")
    if target == 2:
        _check(v, "no_open_incident", sum(incidents.values()) == 0, "open incident on the point")
    _check(v, "within_budget", ops.get("within_budget") is True, "p95 latency / cost unknown or over budget")
    prev = ((previous or {}).get("promotion") or {}).get(f"L{target}") or {}
    prev_quality = all(prev.get("checks", {}).get(c, False) for c in v.checks
                       if c.endswith(("inferiority", "precision", "band_edge", "holm")))
    _check(v, "stability_two_runs", bool(prev) and prev_quality, "quality gates must hold in two consecutive runs")
    v.allowed = all(v.checks.values())
    return v


def demotion_check(entry: dict[str, Any], current: int) -> Verdict:
    """Evidence of harm at the point's own bar (ADR 0008 §4): L1 → L0 / L2 → L1."""

    v = Verdict(None, False)
    bars = DEMOTION_BARS.get(current)
    if bars is None:
        return v
    margin, bar = bars
    key, sources = entry.get("key"), entry.get("sources") or {}
    harm = False
    for name in ("sealed", "blind"):
        src = sources.get(name) or {}
        gap = src.get("gap") or {}
        if gap.get("ub") is not None and gap.get("clusters", 0) >= 2 and gap["ub"] < -margin:
            harm = True
            v.reasons.append(f"{name}: gap UB {gap['ub']} < -{margin}")
        stats = ([src.get("deployed_policy")] if current == 2 and key == "column.semantic_role"
                 else [(src.get("bands") or {}).get(b) for b in _band_names(key)] if key in YES_NO_POINTS else [])
        for stat in stats:
            if not stat or stat.get("precision") is None:
                continue
            n_eff = float(stat.get("n_eff") or 0)
            ub = clopper_pearson_upper(stat["precision"] * n_eff, n_eff)  # exact, no sample-size floor
            if ub is not None and ub < bar:
                harm = True
                v.reasons.append(f"{name}: precision exact UB {round(ub, 4)} < {bar} (n_eff {n_eff})")
    if harm:
        v.level, v.allowed = current - 1, True
    return v


# --- levels -----------------------------------------------------------------------------


def record_first_levels(db: Session, report: dict[str, Any], *, admin: User) -> dict[str, str]:
    """Platform L0 rows for every registry key (decision points and Jev purposes) with the R3 run
    as evidence, written by a named platform admin (ADR 0008 §3; rule actors cannot create
    chain roots). Idempotent: an existing platform head is left alone."""

    if not is_platform_admin(db, admin):
        raise GovernanceNotPermitted("platform_levels_need_admin", "recording platform levels needs a dclab_admin")
    _verify(report)
    outcome: dict[str, str] = {}
    for key in REGISTRY:
        if _level_head(db, None, key) is not None:
            outcome[key] = "kept"
            continue
        db.add(_level_row(
            workspace_id=None, key=key, level=0, state="accepted", actor=admin, actor_rule=None, decided_by=admin,
            prompt_release_id=None, model_id=None, supersedes_id=None, self_approved=True,
            rationale=(f"L0 (shadow) from R3 run {report['run_id']} digest {report['digest'][:16]}: the offline "
                       "fake-provider run proves the pipeline and the promotion rules, not model quality; "
                       "promotion needs ADR 0008 §4 thresholds on real data at checkpoint G6"),
            evidence=[{"kind": "r3_run", "id": report["run_id"]}],
        ))
        outcome[key] = "recorded"
    db.flush()
    return outcome


def _subjects(key: str) -> list[str]:
    """Incident subjects touching a point: itself, its purpose / agent, and its provider (N7)."""

    point = REGISTRY.get(key)
    subjects = [f"decision_point:{key}"]
    if point is not None and point.ai_kind != "none":
        kind, _, name = point.ai_kind.partition(":")
        subjects.append(f"{'purpose' if kind == 'jev' else 'agent'}:{name}")
        subjects.append(f"provider:{'typesafe' if kind == 'jev' else 'openai'}")
    return subjects


def latest_demotion_at(db: Session, key: str) -> datetime | None:
    """When the platform row of ``key`` was last lowered by a rule, or an incident touching it
    (incl. a provider-wide one) was opened — a promotion needs an R3 run recorded AFTER that."""

    from app.agents.governance.incidents import AUTO_DEMOTE_RULE

    demoted = db.scalar(select(func.max(DecisionPointPolicy.created_at)).where(
        DecisionPointPolicy.workspace_id.is_(None), DecisionPointPolicy.decision_point_key == key,
        DecisionPointPolicy.state == "accepted", DecisionPointPolicy.actor_rule == AUTO_DEMOTE_RULE))
    opened = db.scalar(select(func.max(AiIncident.opened_at)).where(
        AiIncident.workspace_id.is_(None),
        func.concat(AiIncident.subject_kind, ":", AiIncident.subject_key).in_(_subjects(key))
        | (AiIncident.subject_kind == "workspace")))
    stamps = [s for s in (demoted, opened) if s is not None]
    return max(stamps) if stamps else None


def _ledger_rows(db: Session, report: dict[str, Any], row: R3Run) -> int:
    """Evaluation samples the run left in the evaluation workspace (one row per answered question),
    between the run's start and its storage: a stored "live" run must have really asked."""

    workspace = report.get("evaluation_workspace")
    if not workspace:
        return 0
    return int(db.scalar(select(func.count()).where(
        SemanticDecisionAnswer.workspace_id == UUID(workspace), SemanticDecisionAnswer.model_id == row.model_id,
        SemanticDecisionAnswer.created_at >= datetime.fromisoformat(report["created_at"]) - timedelta(hours=6),
        SemanticDecisionAnswer.created_at <= row.recorded_at)) or 0)


def verify_platform_raise(db: Session, *, run_id: UUID, key: str, level: int, prompt_release_id: UUID | None,
                          model_id: str | None) -> dict[str, Any]:
    """THE verification of a platform raise (ADR 0008 §3, §4), shared by ``propose_promotion`` and
    ``policy.accept_level``: the stored run (digests re-verified) is a live-provider run of the
    current (release, model) pair — the pair the proposal names — over both partitions, backed by
    the evaluation samples it left, recorded after the latest demotion / incident touching the point
    (incl. provider-wide ones); no incident is open; the current pair-aware level is one step below;
    the stability run is the stored live run recorded immediately before it with different content;
    the Holm-adjusted §4 gates pass. Returns the verified report."""

    report = load_run(db, run_id)
    row = db.get(R3Run, run_id)
    if not report.get("live") or not row.live:
        raise GovernanceNotPermitted("r3_candidate_not_live", "only a real-provider R3 run is promotion evidence")
    if report.get("pair") != release_pair():
        raise GovernanceNotPermitted("r3_pair_mismatch", "the run covers another (release, model) pair")
    release_id, current_model = current_pair(db, key)
    if release_id is None or (prompt_release_id, model_id) != (release_id, current_model):
        raise GovernanceNotPermitted("r3_pair_mismatch", "the proposal's (prompt release, model) is not the current pair")
    if report.get("partition") not in (None, "both"):
        raise GovernanceNotPermitted("r3_partition_invalid", "a development-only run is never promotion evidence")
    answered = sum((report["points"].get(k, {}).get("sources") or {}).get("all", {}).get("n", 0) for k in JEV_POINTS)
    if _ledger_rows(db, report, row) < answered:
        raise GovernanceNotPermitted("r3_run_unbacked", "the stored run is not backed by evaluation samples")
    since = latest_demotion_at(db, key)
    if since is not None and row.recorded_at <= since:
        raise GovernanceNotPermitted("r3_run_predates_demotion",
                                     "re-promotion needs a new R3 run after the demotion / incident")
    incidents = open_incident_counts(db, None, key)
    if incidents:
        raise GovernanceNotPermitted("open_incident", f"open incidents on {key}: {sorted(incidents)}")
    previous = previous_run(db, report)
    if previous is not None and previous.get("digest") == report.get("digest"):
        raise GovernanceNotPermitted("r3_previous_invalid", "the previous run has identical content (re-stored copy)")
    entry = dict(report["points"][key], key=key, current_platform_level=_platform_level(db, key),
                 open_incidents=incidents)
    verdict = promotion_check(entry, level, previous=(previous or {}).get("points", {}).get(key),
                              holm=report.get("holm_adjusted_p_values") or {})
    if not verdict.allowed:
        raise GovernanceNotPermitted("promotion_evidence_insufficient", "; ".join(verdict.reasons)[:1000])
    return report


def propose_promotion(db: Session, *, run_id: UUID, key: str, level: int, admin: User):
    """A platform level proposal exists only when ``verify_platform_raise`` allows it; a second
    platform admin accepts (``policy.accept_level`` runs the same verification again)."""

    from app.agents.governance.policy import R3_VERIFIED

    if not is_platform_admin(db, admin):
        raise GovernanceNotPermitted("platform_levels_need_admin", "platform levels need a dclab_admin")
    release_id, model_id = current_pair(db, key)
    if release_id is None:
        raise GovernanceNotPermitted("release_unknown", f"no released prompt row for {key}")
    report = verify_platform_raise(db, run_id=run_id, key=key, level=level, prompt_release_id=release_id,
                                   model_id=model_id)
    return propose_level(db, actor=admin, workspace_id=None, key=key, level=level,
                         rationale=f"R3 run {report['run_id']} ({report['digest'][:16]}) meets the ADR 0008 §4 L{level} gates",
                         prompt_release_id=release_id, model_id=model_id,
                         evidence=[{"kind": "r3_run", "id": report["run_id"]}], verification=R3_VERIFIED)


# --- rendering --------------------------------------------------------------------------


def render_markdown(report: dict[str, Any]) -> str:
    lines = [f"# R3 run {report['run_id']} (candidate `{report['candidate']}`, digest `{report['digest'][:16]}`)", "",
             f"Cases {report['cases']}, sealed datasets {report['sealed_datasets']}, pair "
             f"`{report['pair']['release']}` / `{report['pair']['model_id']}`.", "",
             "| Decision point | AI | sealed n | rule acc | AI policy acc | disagree | in-band prec LB | gap LB/UB | "
             "val. rej. | p95 ms | level | L1 proposable |", "| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |"]
    for key, e in report["points"].items():
        s = (e["sources"] or {}).get("sealed") or {}
        gap, band, ops = s.get("gap") or {}, s.get("in_band") or {}, e["operations"]
        l1 = e["promotion"].get("L1") or {}
        lines.append(f"| `{key}` | {e['ai_kind']} | {s.get('n', 0)} | {s.get('rule_accuracy', '—')} | "
                     f"{s.get('ai_policy_accuracy', '—')} | {s.get('disagreement_rate', '—')} | {band.get('lb', '—')} | "
                     f"{gap.get('lb', '—')} / {gap.get('ub', '—')} | {ops.get('validator_rejection_rate', '—')} | "
                     f"{ops.get('p95_latency_ms', '—')} | L{e['current_platform_level']} | "
                     f"{'yes' if l1.get('allowed') else 'no'} |")
    lines += ["", "Rule replay (AI off == rule baseline through the scorer; the real-path check is the harness): " + ", ".join(
        f"`{k}` {'ok' if v['equal'] else 'MISMATCH'}" for k, v in report["ablation"]["points"].items())]
    return "\n".join(lines) + "\n"


__all__ = ["BUDGETS", "Case", "EVALUATION_CAPABILITY", "GATES", "R3_RULE", "ScriptedAnswerer", "Verdict", "ablation",
           "ai_off", "benchmark_corpus", "clopper_pearson_upper", "current_pair", "demotion_check", "evaluate", "holm",
           "load_run", "paired_gap", "plant", "platform_evaluation_workspace_ok", "port_answerer", "previous_run",
           "promotion_check", "propose_promotion", "record_first_levels", "verify_platform_raise", "release_pair", "reliability",
           "render_markdown", "run_r3", "score", "store_run", "weighted_precision", "wilson", "workspace_aggregates",
           "workspace_evidence"]
