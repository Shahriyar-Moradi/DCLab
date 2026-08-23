from datetime import datetime, timezone

from app.ml.features import FEATURE_NAMES, build_features, feature_vector


NOW = datetime(2026, 8, 23, tzinfo=timezone.utc)


def _row(**overrides):
    base = {
        "amount": 100000,
        "stage": "proposal",
        "source": "inbound",
        "engagement_score": 0.87,
        "last_contact_days_ago": 3,
        "num_interactions": 12,
        "sales_rep_available": True,
        "created_at": datetime(2026, 5, 23, tzinfo=timezone.utc),
    }
    base.update(overrides)
    return base


def test_normal_row_values():
    feats = build_features(_row(), now=NOW)
    assert feats["deal_size"] == 100000.0
    assert feats["stage_encoded"] == 2
    assert feats["source_encoded"] == 0
    assert feats["engagement_score"] == 0.87
    assert feats["last_contact_days_ago"] == 3
    assert feats["num_interactions"] == 12
    assert feats["sales_rep_available"] == 1
    assert feats["opportunity_age_days"] == 92
    assert feats["defaulted"] == []


def test_nulls_fill_defaults_without_raising():
    feats = build_features(
        {
            "amount": None,
            "stage": None,
            "source": None,
            "engagement_score": None,
            "last_contact_days_ago": None,
            "num_interactions": None,
            "sales_rep_available": None,
            "created_at": None,
        },
        now=NOW,
    )
    assert feats["deal_size"] == 0.0
    assert feats["stage_encoded"] == -1
    assert feats["source_encoded"] == -1
    assert feats["engagement_score"] == 0.0
    assert feats["last_contact_days_ago"] == 30
    assert feats["num_interactions"] == 0
    assert feats["sales_rep_available"] == 0
    assert feats["opportunity_age_days"] == 0
    assert set(feats["defaulted"]) == {
        "deal_size",
        "stage_encoded",
        "source_encoded",
        "engagement_score",
        "last_contact_days_ago",
        "num_interactions",
        "sales_rep_available",
        "opportunity_age_days",
    }


def test_edge_case_naive_date_and_unknown_stage():
    feats = build_features(
        _row(stage="unknown-stage", created_at=datetime(2026, 8, 20, 12, 0, 0)),
        now=NOW,
    )
    assert feats["stage_encoded"] == -1
    assert "stage_encoded" in feats["defaulted"]
    assert feats["opportunity_age_days"] == 2


def test_sales_rep_string_and_zero_age():
    feats = build_features(_row(sales_rep_available="no", created_at=NOW), now=NOW)
    assert feats["sales_rep_available"] == 0
    assert feats["opportunity_age_days"] == 0


def test_output_is_deterministic():
    first = build_features(_row(), now=NOW)
    second = build_features(_row(), now=NOW)
    assert first == second
    assert feature_vector(first) == feature_vector(second)
    assert [name in first for name in FEATURE_NAMES] == [True] * len(FEATURE_NAMES)
