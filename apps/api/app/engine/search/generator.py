"""Build candidates from feature-group combinations × model families."""

from __future__ import annotations

from app.engine.features.combinations import features_for_groups, generate_group_combinations
from app.engine.models.registry import available_families, baseline_families, cheap_families, strong_families
from app.engine.search.fingerprint import candidate_fingerprint
from app.engine.types import Candidate, SearchConfig, TaskSpec

DUMMY_FAMILIES = {"majority", "mean"}

OPEN_INGEST_PREPROCESS = {
    "kind": "column_transformer",
    "numeric_imputer": "median",
    "numeric_scaler": "standard",
    "categorical_imputer": "most_frequent",
    "categorical_encoder": "onehot",
}


def open_ingest_families(task_type: str) -> list[str]:
    """Classification: LR, RF, XGBoost, LightGBM if installed.
    Regression: Linear, RF, XGB/LGBM regressors if installed.
    """
    avail = set(available_families(task_type))
    if task_type == "binary":
        wanted = ["logistic_regression", "random_forest", "xgboost", "lightgbm"]
    else:
        wanted = [
            "linear_regression",
            "random_forest_regressor",
            "xgboost_regressor",
            "lightgbm_regressor",
        ]
    return [name for name in wanted if name in avail]


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


def _open_ingest_candidates(
    task: TaskSpec,
    config: SearchConfig,
    *,
    dataset_version: str,
) -> list[Candidate]:
    """One feature group, registry families, a single ColumnTransformer preprocessor.

    Runner.py recognises ``preprocessing.kind == "column_transformer"`` and uses
    sklearn pipelines + K-fold on the training split only. Default
    ``use_case`` / ``progressive`` strategies are unchanged.
    """
    groups = list(task.feature_groups.keys())
    if not groups:
        return []
    combo = tuple(sorted(groups))
    feats = tuple(features_for_groups(task.feature_groups, combo))
    if not feats:
        return []

    families = open_ingest_families(task.task_type)
    candidates: list[Candidate] = []
    for family in families:
        if len(candidates) >= config.max_candidates:
            return candidates
        payload = _fingerprint_payload(
            task, features=feats, family=family, seed=config.seed, dataset_version=dataset_version
        )
        payload["preprocess"] = "column_transformer"
        candidates.append(
            Candidate(
                candidate_id=family,
                task_id=task.id,
                feature_groups=combo,
                features=feats,
                model_family=family,
                random_seed=config.seed,
                validation_strategy=task.validation_strategy,
                preprocessing=dict(OPEN_INGEST_PREPROCESS),
                fingerprint=candidate_fingerprint(payload),
            )
        )
    return candidates


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
    if config.strategy == "open_ingest":
        return _open_ingest_candidates(task, config, dataset_version=dataset_version)
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

    if config.strategy == "use_case":
        dummy = "majority" if task.task_type == "binary" else "mean"
        linear = "logistic_regression" if task.task_type == "binary" else "linear_regression"
        forest = "random_forest" if task.task_type == "binary" else "random_forest_regressor"
        boost = "gradient_boosting" if task.task_type == "binary" else "gradient_boosting_regressor"
        extra = "extra_trees" if task.task_type == "binary" else "extra_trees_regressor"
        pairs = [combo for combo in combos if len(combo) == 2]
        primary = pairs[0] if pairs else (singles[0] if singles else full)
        secondary = pairs[1] if len(pairs) > 1 else (singles[-1] if singles else full)
        for family, combo in (
            (dummy, singles[0] if singles else full),
            (linear, full),
            (forest, full),
            (boost, primary),
            (extra, secondary),
        ):
            if not add(family, combo):
                break
        return candidates

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
