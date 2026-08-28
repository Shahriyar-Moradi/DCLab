from app.engine.datasets.lab_workbook import make_lab_workbook
from app.engine.lab.column_map import (
    assign_group,
    build_feature_groups,
    pick_entity_column,
    pick_target,
    pick_time_column,
    planned_targets,
)
from app.domain.lab_use_cases import LAB_USE_CASES, use_case_by_slug
from app.engine.types import SearchConfig, TaskSpec
from app.engine.search.generator import assemble_candidates


def test_workbook_maps_all_five_use_cases():
    frame = make_lab_workbook(n=80)
    columns = list(frame.columns)
    targets = planned_targets(columns)
    assert set(targets) == {item.slug for item in LAB_USE_CASES}
    assert pick_entity_column(columns) == "entity_id"
    assert pick_time_column(columns) == "as_of_date"


def test_opportunities_columns_train_conversion_and_leads():
    columns = [
        "external_id",
        "customer_id",
        "amount",
        "currency",
        "stage",
        "source",
        "owner_id",
        "created_at",
        "engagement_score",
        "converted",
    ]
    targets = planned_targets(columns)
    assert targets["conversion"] == "converted"
    assert targets["lead_conversion"] == "converted"
    assert "customer_value" not in targets
    assert "churn" not in targets
    assert "purchase" not in targets
    churn = use_case_by_slug("churn")
    assert pick_target(columns, churn) is None


def test_feature_groups_hold_out_other_labels_and_ids():
    columns = [
        "entity_id",
        "as_of_date",
        "amount",
        "stage",
        "source",
        "engagement_score",
        "churned",
        "converted",
        "currency",
    ]
    groups = build_feature_groups(
        columns,
        target="converted",
        holdouts={"churned", "converted"},
        entity="entity_id",
        time_col="as_of_date",
        preferred=("monetary", "pipeline", "engagement", "acquisition"),
    )
    flat = [col for cols in groups.values() for col in cols]
    assert "converted" not in flat
    assert "churned" not in flat
    assert "entity_id" not in flat
    assert "as_of_date" not in flat
    assert "currency" not in flat
    assert "amount" in flat
    assert assign_group("engagement_score") == "engagement"


def test_use_case_search_builds_five_distinct_families():
    spec = TaskSpec(
        id="conversion",
        name="Conversion",
        task_type="binary",
        target="converted",
        feature_groups={
            "monetary": ["amount"],
            "pipeline": ["stage"],
            "engagement": ["engagement_score"],
        },
    )
    candidates = assemble_candidates(spec, SearchConfig(strategy="use_case", max_candidates=5))
    families = [row.model_family for row in candidates]
    assert len(candidates) == 5
    assert families[0] == "majority"
    assert "logistic_regression" in families
    assert "random_forest" in families
    assert "gradient_boosting" in families
    assert "extra_trees" in families
