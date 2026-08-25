"""Train the conversion-probability layer factory and persist the fused artifact.

Explores a small set of feature-group × algorithm candidates, drops weak and
redundant models, blends the rest, and keeps the blend only if it beats the
best single member. This does not choose business actions — the policy engine does.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import joblib
import numpy as np
import pandas as pd
from sklearn.calibration import calibration_curve
from sklearn.metrics import average_precision_score, brier_score_loss, roc_auc_score
from sklearn.model_selection import train_test_split

from app.config import REPO_ROOT, get_settings
from app.ml.candidates import CandidateSpec, build_candidate_specs, make_estimator
from app.ml.ensemble import blend_probabilities, blend_weights, choose_fusion
from app.ml.feature_groups import load_layer_config
from app.ml.features import build_features, feature_vector
from app.ml.selection import greedy_diverse_selection

SAMPLE_CSV = REPO_ROOT / "data" / "sample" / "opportunities.csv"


def _load_labeled_frame(path: Path, target: str = "converted") -> pd.DataFrame:
    frame = pd.read_csv(path)
    if target not in frame.columns:
        raise ValueError(f"Training CSV is missing target column {target!r}")
    frame = frame.dropna(subset=[target])
    frame[target] = frame[target].astype(int)
    frame = frame[frame[target].isin([0, 1])]
    return frame.reset_index(drop=True)


def _opportunity_feature_table(frame: pd.DataFrame, target: str) -> tuple[pd.DataFrame, np.ndarray]:
    rows = []
    labels = []
    for _, row in frame.iterrows():
        feats = build_features(row.to_dict())
        feats.pop("defaulted", None)
        rows.append(feats)
        labels.append(int(row[target]))
    return pd.DataFrame(rows), np.asarray(labels, dtype=int)


def _generic_feature_table(frame: pd.DataFrame, config: dict, target: str) -> tuple[pd.DataFrame, np.ndarray]:
    from app.ml.feature_groups import features_for_groups

    group_names = list((config.get("feature_groups") or {}).keys())
    columns = features_for_groups(config, group_names)
    missing = [col for col in columns if col not in frame.columns]
    if missing:
        raise ValueError(f"Training frame missing feature columns: {missing}")
    table = frame[columns].apply(pd.to_numeric, errors="coerce").fillna(0.0)
    labels = frame[target].astype(int).to_numpy()
    return table, labels


def _feature_table(
    frame: pd.DataFrame, config: dict, target: str
) -> tuple[pd.DataFrame, np.ndarray]:
    if config.get("kind") == "simulation":
        return _generic_feature_table(frame, config, target)
    return _opportunity_feature_table(frame, target)


def _evaluate(y_true, probability) -> dict[str, float]:
    y_true = list(y_true)
    probability = np.asarray(probability, dtype=float)
    roc = float(roc_auc_score(y_true, probability))
    pr = float(average_precision_score(y_true, probability))
    brier = float(brier_score_loss(y_true, probability))
    frac_pos, mean_pred = calibration_curve(y_true, probability, n_bins=5, strategy="uniform")
    calibration_gap = float(abs(frac_pos - mean_pred).mean()) if len(frac_pos) else 1.0
    return {
        "roc_auc": roc,
        "pr_auc": pr,
        "brier": brier,
        "calibration_gap": calibration_gap,
    }


def _split(frame: pd.DataFrame, target: str = "converted"):
    if "created_at" in frame.columns:
        dated = frame.copy()
        dated["_created"] = pd.to_datetime(dated["created_at"], errors="coerce")
        if dated["_created"].notna().mean() > 0.8 and dated["_created"].nunique() > 20:
            dated = dated.sort_values("_created")
            cut = int(len(dated) * 0.8)
            train_df = dated.iloc[:cut].drop(columns=["_created"])
            test_df = dated.iloc[cut:].drop(columns=["_created"])
            if test_df[target].nunique() == 2 and train_df[target].nunique() == 2:
                return train_df, test_df, "time"
    train_df, test_df = train_test_split(
        frame, test_size=0.2, random_state=42, stratify=frame[target]
    )
    return train_df, test_df, "stratified"


def _slice_matrix(table: pd.DataFrame, spec: CandidateSpec) -> list[list[float]]:
    records = table.to_dict(orient="records")
    return [feature_vector(record, spec.features) for record in records]


def train_and_save(
    csv_path: Path | None = None,
    model_dir: Path | None = None,
    layer_path: Path | None = None,
    target_col: str | None = None,
) -> dict:
    csv_path = csv_path or SAMPLE_CSV
    settings = get_settings()
    model_dir = Path(model_dir or settings.model_dir)
    model_dir.mkdir(parents=True, exist_ok=True)
    members_dir = model_dir / "members"
    members_dir.mkdir(parents=True, exist_ok=True)

    config = load_layer_config(layer_path)
    target = target_col or str(config.get("target") or "converted")
    specs = build_candidate_specs(config)
    min_auc = float(config.get("min_roc_auc", 0.55))
    retain_min = int(config.get("retain_min", 3))
    retain_max = int(config.get("retain_max", 7))
    max_corr = float(config.get("max_abs_correlation", 0.95))

    frame = _load_labeled_frame(csv_path, target)
    train_df, test_df, split_kind = _split(frame, target)
    x_train, y_train = _feature_table(train_df, config, target)
    x_test, y_test = _feature_table(test_df, config, target)

    fitted: dict[str, object] = {}
    metrics: dict[str, dict] = {}
    test_probas: dict[str, np.ndarray] = {}

    layer_name = str(config.get("layer", "conversion_probability"))
    print(f"Evaluating {len(specs)} {layer_name} candidates")
    for spec in specs:
        model = make_estimator(spec)
        train_x = _slice_matrix(x_train, spec)
        test_x = _slice_matrix(x_test, spec)
        model.fit(train_x, y_train)
        proba = model.predict_proba(test_x)[:, 1]
        score = _evaluate(y_test, proba)
        fitted[spec.id] = model
        metrics[spec.id] = {**score, "algorithm": spec.algorithm, "groups": list(spec.groups)}
        test_probas[spec.id] = proba
        print(
            f"{spec.id}: ROC-AUC={score['roc_auc']:.4f} PR-AUC={score['pr_auc']:.4f} "
            f"groups={','.join(spec.groups)}"
        )

    viable = {mid: metrics[mid]["roc_auc"] for mid in metrics if metrics[mid]["roc_auc"] > min_auc}
    if not viable:
        viable = {mid: metrics[mid]["roc_auc"] for mid in metrics if metrics[mid]["roc_auc"] > 0.5}
    if not viable:
        raise RuntimeError("No candidate beat ROC-AUC 0.5. Refusing to save.")

    pred_frame = pd.DataFrame({mid: test_probas[mid] for mid in viable})
    selected_ids = greedy_diverse_selection(
        pred_frame,
        viable,
        retain_max=retain_max,
        retain_min=min(retain_min, len(viable)),
        max_abs_correlation=max_corr,
    )
    print(f"Selected {len(selected_ids)} diverse members: {selected_ids}")

    member_scores = {mid: metrics[mid]["roc_auc"] for mid in selected_ids}
    best_single_id = max(member_scores, key=member_scores.get)
    best_single_auc = member_scores[best_single_id]
    weights = blend_weights(member_scores, selected_ids)
    blended = blend_probabilities({mid: test_probas[mid] for mid in selected_ids}, weights)
    blend_metrics = _evaluate(y_test, blended)
    fusion = choose_fusion(
        blend_metric=blend_metrics["roc_auc"],
        best_single_metric=best_single_auc,
        best_single_id=best_single_id,
    )
    if fusion == "weighted_blend":
        winning_metrics = blend_metrics
        winning_auc = blend_metrics["roc_auc"]
    else:
        winning_metrics = metrics[best_single_id]
        winning_auc = best_single_auc
        weights = {best_single_id: 1.0}

    if winning_auc <= 0.5:
        raise RuntimeError(
            f"Winning ROC-AUC {winning_auc:.4f} is not meaningfully above 0.5. Refusing to save."
        )

    spec_by_id = {spec.id: spec for spec in specs}
    member_meta = []
    for mid in selected_ids if fusion == "weighted_blend" else [best_single_id]:
        artifact_name = f"{mid}.joblib"
        joblib.dump(fitted[mid], members_dir / artifact_name)
        spec = spec_by_id[mid]
        member_meta.append(
            {
                "id": mid,
                "algorithm": spec.algorithm,
                "groups": list(spec.groups),
                "features": list(spec.features),
                "artifact": f"members/{artifact_name}",
                "metrics": {k: metrics[mid][k] for k in ("roc_auc", "pr_auc", "brier", "calibration_gap")},
                "weight": weights.get(mid, 0.0),
            }
        )

    # Compatibility pointer for older loaders: dump the fused serving recipe, not a sklearn object.
    serving = {
        "fusion": fusion,
        "members": member_meta,
        "weights": weights,
    }
    joblib.dump(serving, model_dir / "model.joblib")

    correlations = {}
    if len(selected_ids) > 1:
        corr = pred_frame[selected_ids].corr().abs()
        correlations = {
            f"{a}|{b}": float(corr.loc[a, b])
            for i, a in enumerate(selected_ids)
            for b in selected_ids[i + 1 :]
        }

    metadata = {
        "model_version": str(config["version"]),
        "layer": config.get("layer", "conversion_probability"),
        "fusion": fusion,
        "algorithm": fusion,
        "trained_at": datetime.now(timezone.utc).isoformat(),
        "split": split_kind,
        "n_train": int(len(train_df)),
        "n_test": int(len(test_df)),
        "n_candidates_evaluated": len(specs),
        "metrics": winning_metrics,
        "all_metrics": metrics,
        "members": member_meta,
        "weights": weights,
        "feature_groups_used": sorted(
            {group for spec in (spec_by_id[m] for m in [row["id"] for row in member_meta]) for group in spec.groups}
        ),
        "feature_list": list(dict.fromkeys(feat for row in member_meta for feat in row["features"])),
        "diversity": {"max_abs_correlation": max_corr, "pairwise_abs_corr": correlations},
        "artifact": "model.joblib",
        "target": target,
        "kind": config.get("kind"),
        "test_external_ids": (
            [str(x) for x in test_df["external_id"].tolist()] if "external_id" in test_df.columns else []
        ),
    }
    (model_dir / "metadata.json").write_text(json.dumps(metadata, indent=2) + "\n")
    print(
        f"Saved {fusion} (ROC-AUC={winning_auc:.4f}) with {len(member_meta)} member(s) → {model_dir}"
    )
    return metadata


def main() -> None:
    train_and_save()


if __name__ == "__main__":
    main()
