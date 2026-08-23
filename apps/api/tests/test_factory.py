from app.ml.candidates import build_candidate_specs
from app.ml.feature_groups import load_layer_config
from app.ml.predict import predict_conversion, predict_with_evidence, reset_model_cache
from app.ml.train import train_and_save


def test_layer_config_builds_between_five_and_twelve_candidates():
    config = load_layer_config()
    specs = build_candidate_specs(config)
    assert 5 <= len(specs) <= 12
    assert len({spec.id for spec in specs}) == len(specs)


def test_factory_trains_diverse_members_and_serves_probability(tmp_path, sample_csv_bytes):
    csv_path = tmp_path / "train.csv"
    rows = sample_csv_bytes.decode().strip().split("\n")
    header, *data = rows
    expanded = [header]
    for i in range(40):
        for line in data:
            parts = line.split(",")
            parts[0] = f"{parts[0]}_{i}"
            expanded.append(",".join(parts))
    csv_path.write_text("\n".join(expanded) + "\n")

    metadata = train_and_save(csv_path, tmp_path)
    assert metadata["n_candidates_evaluated"] >= 5
    assert metadata["members"]
    assert metadata["metrics"]["roc_auc"] > 0.5
    assert metadata["fusion"]
    reset_model_cache()

    probability, version = predict_conversion(
        {
            "amount": 100000,
            "stage": "proposal",
            "source": "inbound",
            "engagement_score": 0.88,
            "last_contact_days_ago": 5,
            "num_interactions": 14,
            "sales_rep_available": True,
            "created_at": "2026-01-15",
        },
        model_dir=tmp_path,
    )
    assert 0.0 <= probability <= 1.0
    assert version == metadata["model_version"]

    detailed = predict_with_evidence(
        {
            "amount": 100000,
            "stage": "proposal",
            "source": "inbound",
            "engagement_score": 0.88,
            "last_contact_days_ago": 5,
            "num_interactions": 14,
            "sales_rep_available": True,
            "created_at": "2026-01-15",
        },
        model_dir=tmp_path,
    )
    assert detailed["evidence"]["models_used"] >= 1
    assert "fusion" in detailed["evidence"]
    reset_model_cache()
