"""Build candidates from feature-group combinations × model families."""

from __future__ import annotations

from app.engine.features.combinations import features_for_groups, generate_group_combinations
from app.engine.models.registry import baseline_families, cheap_families, strong_families
from app.engine.search.fingerprint import candidate_fingerprint
from app.engine.types import Candidate, SearchConfig, TaskSpec

DUMMY_FAMILIES = {"majority", "mean"}


def _fingerprint_payload(
    task: TaskSpec,
    *,
    features: tuple[str, ...],
    family: str,
    seed: int,
    dataset_version: str,
) -> dict:
    return {
        "dataset_version": dataset_version,
        "task_id": task.id,
        "target": task.target,
        "features": list(features),
        "family": family,
        "hyperparams": {},
        "preprocess": "default",
        "seed": seed,
        "validation": task.validation_strategy,
        "split": task.validation_strategy,
    }


def _make_candidate(
    task: TaskSpec,
    family: str,
    combo: tuple[str, ...],
    *,
    seed: int,
    dataset_version: str,
) -> Candidate | None:
    feats = tuple(features_for_groups(task.feature_groups, combo))
    if not feats:
        return None
    return Candidate(
        candidate_id=f"{family}__{'_'.join(combo)}",
        task_id=task.id,
        feature_groups=combo,
        features=feats,
        model_family=family,
        random_seed=seed,
        validation_strategy=task.validation_strategy,
        fingerprint=candidate_fingerprint(
            _fingerprint_payload(
                task, features=feats, family=family, seed=seed, dataset_version=dataset_version
            )
        ),
    )


def _combos(task: TaskSpec, config: SearchConfig) -> list[tuple[str, ...]]:
    groups = list(task.feature_groups.keys())
    strategy = "limited" if config.strategy == "progressive" else config.strategy
    combos = generate_group_combinations(
        groups,
        strategy=strategy,
        max_combinations=config.max_feature_group_combinations,
        seed=config.seed,
    )
    full = tuple(sorted(groups))
    if full and full not in combos:
        combos = [full, *combos]
        combos = combos[: config.max_feature_group_combinations + 1]
    return combos


def assemble_candidates(
    task: TaskSpec,
    config: SearchConfig,
    *,
    dataset_version: str = "v1",
) -> list[Candidate]:
    """Progressive search reserves slots for baselines *and* stronger families."""
    groups = list(task.feature_groups.keys())
    if not groups:
        return []
    combos = _combos(task, config)
    full = tuple(sorted(groups))
    singles = [combo for combo in combos if len(combo) == 1] or combos[:1]
    candidates: list[Candidate] = []
    seen: set[str] = set()

    def add(family: str, combo: tuple[str, ...]) -> bool:
        if len(candidates) >= config.max_candidates:
            return False
        row = _make_candidate(
            task, family, combo, seed=config.seed, dataset_version=dataset_version
        )
        if row is None or row.candidate_id in seen:
            return True
        seen.add(row.candidate_id)
        candidates.append(row)
        return True

    if config.strategy == "progressive":
        dummy = "majority" if task.task_type == "binary" else "mean"
        add(dummy, singles[0])
        cheap = [name for name in cheap_families(task.task_type) if name not in DUMMY_FAMILIES]
        for combo in singles:
            for family in cheap:
                if not add(family, combo):
                    return candidates
        strong = strong_families(task.task_type) or [
            name for name in baseline_families(task.task_type) if name not in DUMMY_FAMILIES
        ]
        ordered = [full, *[combo for combo in combos if combo != full]]
        for combo in ordered:
            for family in strong:
                if not add(family, combo):
                    return candidates
        return candidates

    families = list(
        dict.fromkeys(cheap_families(task.task_type) + strong_families(task.task_type))
    )
    for combo in combos:
        for family in families:
            if not add(family, combo):
                return candidates
    return candidates


def generate_candidates(
    task: TaskSpec,
    config: SearchConfig,
    *,
    stage: str = "all",
    dataset_version: str = "v1",
    limit: int | None = None,
) -> list[Candidate]:
    """Stage-aware generator. Prefer ``assemble_candidates`` for a full run."""
    if stage == "all":
        rows = assemble_candidates(task, config, dataset_version=dataset_version)
        return rows[: limit or config.max_candidates]

    groups = list(task.feature_groups.keys())
    combos = _combos(task, config)
    cap = limit if limit is not None else config.max_candidates
    if stage == "baselines":
        families = [name for name in cheap_families(task.task_type)]
        combos = [combo for combo in combos if len(combo) == 1][: max(1, min(8, len(groups)))]
        if not combos and groups:
            combos = [(groups[0],)]
    elif stage == "strong":
        families = strong_families(task.task_type) or baseline_families(task.task_type)
        combos = combos[: min(len(combos), max(4, config.max_feature_group_combinations))]
    else:
        families = cheap_families(task.task_type) + strong_families(task.task_type)

    candidates: list[Candidate] = []
    for combo in combos:
        for family in families:
            row = _make_candidate(
                task, family, combo, seed=config.seed, dataset_version=dataset_version
            )
            if row is None:
                continue
            candidates.append(row)
            if len(candidates) >= cap:
                return candidates
    return candidates
