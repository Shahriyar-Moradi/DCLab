"""Step 6 — Admin side: Organizations, Model Registry, Monitoring.

Generic 401/403 coverage for these routes already comes free from the
route-table sweep in `test_access_control.py` (`test_every_admin_route_...`).
These tests instead prove the surfaces show real, correct, and — for the
Model Registry / Monitoring surfaces — genuinely *unrestricted* ML detail.
"""

from __future__ import annotations

from uuid import uuid4

import pytest

from app.db.models import SimulationRun


def _seed_completed_experiment(db_session, *, seed: int = 11):
    from app.engine.datasets.synthetic import SYNTHETIC_GROUPS
    from app.engine.types import SearchConfig, TaskSpec
    from app.services.lab_service import create_experiment, execute_experiment, ingest_synthetic, seed_dogfood, upsert_task

    env = seed_dogfood(db_session)
    dataset = ingest_synthetic(db_session, env, n=200)
    spec = TaskSpec(
        id="purchase_prediction",
        name="Purchase",
        task_type="binary",
        target="purchase_within_60d",
        entity_id="entity_id",
        prediction_time_column="as_of_date",
        evaluation_metric="pr_auc",
        feature_groups=SYNTHETIC_GROUPS,
        validation_strategy="time",
    )
    task = upsert_task(db_session, env, spec)
    experiment = create_experiment(
        db_session,
        environment=env,
        dataset=dataset,
        task=task,
        config=SearchConfig(max_candidates=4, max_feature_group_combinations=2, n_robustness_folds=2, seed=seed),
    )
    executed = execute_experiment(db_session, experiment)
    assert executed.status == "COMPLETED"
    return executed


def _insert_simulation_run(db_session, *, use_case: str, roc_auc: float) -> SimulationRun:
    row = SimulationRun(
        id=uuid4(),
        use_case=use_case,
        model_version=f"{use_case}_sim_v1",
        policy_version=f"{use_case}_sim_v1",
        fusion="single:gb_all",
        payload={
            "use_case": use_case,
            "metrics": {"roc_auc": roc_auc, "pr_auc": roc_auc - 0.05},
            "n_candidates_evaluated": 6,
        },
    )
    db_session.add(row)
    db_session.commit()
    return row


class TestAdminOrganizations:
    def test_lists_workspaces_with_real_usage_counts(self, db_session, admin_client, client_user):
        from app.db.models import DEFAULT_WORKSPACE_ID
        from app.services.ingestion_service import ingest_opportunities_csv

        ingest_opportunities_csv(
            db_session,
            (
                b"external_id,customer_id,amount,currency,stage,source,owner_id,created_at,"
                b"close_date,last_contact_days_ago,engagement_score,sales_rep_available,"
                b"industry,num_interactions,converted\n"
                b"org_opp_1,cust_1,50000,AED,proposal,inbound,rep_1,2026-01-15,2026-09-01,5,0.7,true,retail,5,0\n"
            ),
            workspace_id=DEFAULT_WORKSPACE_ID,
        )

        response = admin_client.get("/admin/organizations")
        assert response.status_code == 200
        rows = response.json()
        default = next(row for row in rows if row["id"] == str(DEFAULT_WORKSPACE_ID))
        assert default["user_count"] >= 1  # client_user fixture
        assert default["opportunity_count"] >= 1

    def test_detail_includes_user_roster(self, db_session, admin_client, client_user):
        from app.db.models import DEFAULT_WORKSPACE_ID

        response = admin_client.get(f"/admin/organizations/{DEFAULT_WORKSPACE_ID}")
        assert response.status_code == 200
        body = response.json()
        assert body["slug"]
        emails = {u["email"] for u in body["users"]}
        assert client_user.email in emails

    def test_unknown_workspace_is_404(self, admin_client):
        response = admin_client.get(f"/admin/organizations/{uuid4()}")
        assert response.status_code == 404


class TestAdminModelRegistry:
    def test_combines_experiments_and_simulation_runs_unrestricted(self, db_session, admin_client):
        executed = _seed_completed_experiment(db_session)
        sim_row = _insert_simulation_run(db_session, use_case="churn", roc_auc=0.9)

        response = admin_client.get("/admin/models")
        assert response.status_code == 200
        rows = response.json()

        experiment_row = next(row for row in rows if row["id"] == str(executed.id))
        assert experiment_row["source"] == "experiment"
        assert experiment_row["model_family"]
        assert "roc_auc" in experiment_row["metrics"]

        simulation_row = next(row for row in rows if row["id"] == str(sim_row.id))
        assert simulation_row["source"] == "simulation"
        assert simulation_row["name"] == "churn"
        assert simulation_row["metrics"]["roc_auc"] == 0.9


class TestAdminClientUploads:
    """Admin-only visibility into the simple-case auto-train job that runs
    behind a Labs custom-box upload — see docs/LABS_DATA_UNDERSTANDING.md and
    apps/api/app/services/auto_train_service.py. Generic 401/403 coverage for
    these two routes already comes from the route-table sweep above.
    """

    def _seed_upload_and_run(self, db_session, *, n: int = 200):
        import numpy as np
        import pandas as pd

        from app.db.models import DEFAULT_WORKSPACE_ID, ClientLabUpload
        from app.services.auto_train_service import run_auto_train_job

        rng = np.random.default_rng(7)
        tenure = rng.integers(1, 72, n)
        monthly = rng.uniform(20, 120, n)
        total = tenure * monthly + rng.normal(0, 40, n)
        total_str = [f"{value:.2f}" if i >= 11 else " " for i, value in enumerate(total)]
        contract = rng.choice(["Month-to-month", "One year", "Two year"], n)
        churn_p = np.where(contract == "Month-to-month", 0.6, 0.15)
        churn = rng.binomial(1, churn_p)
        frame = pd.DataFrame(
            {
                "tenure": tenure,
                "MonthlyCharges": monthly,
                "TotalCharges": total_str,
                "contract": contract,
                "churn": np.where(churn == 1, "Yes", "No"),
            }
        )
        path = f"/tmp/admin_client_upload_test_{n}.csv"
        frame.to_csv(path, index=False)

        row = ClientLabUpload(
            workspace_id=DEFAULT_WORKSPACE_ID,
            category="Revenue",
            original_filename="telco.csv",
            stored_path=path,
            kind="spreadsheet",
            record_count=n,
            fields_noticed=list(frame.columns),
            has_named_fields=True,
        )
        db_session.add(row)
        db_session.commit()
        db_session.refresh(row)

        run_auto_train_job(db_session, row.id)
        db_session.refresh(row)
        return row

    def test_list_shows_pipeline_status_for_every_upload(self, db_session, admin_client):
        row = self._seed_upload_and_run(db_session)

        response = admin_client.get("/admin/client-uploads")
        assert response.status_code == 200
        rows = response.json()
        match = next(r for r in rows if r["id"] == str(row.id))
        assert match["pipeline_status"] == "completed"
        assert match["experiment_id"] == str(row.experiment_id)

    def test_detail_shows_full_unrestricted_pipeline_log_and_experiment_link(self, db_session, admin_client):
        row = self._seed_upload_and_run(db_session)

        response = admin_client.get(f"/admin/client-uploads/{row.id}")
        assert response.status_code == 200
        body = response.json()
        assert body["pipeline_status"] == "completed"
        assert body["stored_path"] == row.stored_path
        assert body["experiment_id"] == str(row.experiment_id)

        log = body["pipeline_log"]
        assert log["target"]["column"] == "churn"
        assert "numerical_cols" in log
        assert "categorical_cols" in log
        assert log["missing_value_decisions"]

        records = body["decision_records"]
        feature_columns = [name for name in row.fields_noticed if name != log["target"]["column"]]
        assert {item["column"] for item in records} == set(feature_columns)
        for item in records:
            assert item["source"] in {"rule", "llm", "fallback"}
            assert item["rule_decision"]
            assert item["final_decision"]
            assert item["validator_verdict"]
            assert item["prompt_version"]
            assert item["evidence_snapshot"]["column"] == item["column"]
            assert "missing_count" in item["evidence_snapshot"]
            assert "missing_fraction" in item["evidence_snapshot"]
            assert "missingness_cooccurrence" in item["evidence_snapshot"]
            assert "id" in item
            if item["source"] == "rule":
                assert item["raw_llm_output"] is None
                assert item["rule_decision"] == item["final_decision"]

        experiment = admin_client.get(f"/admin/experiments/{row.experiment_id}")
        assert experiment.status_code == 200

        ml = body["ml_run"]
        assert ml["run_id"] == str(row.id)
        assert ml["status"] == "completed"
        assert ml["analysis"]["rows"] == row.record_count
        assert any(step["action"] == "Median imputation" and step["column"] == "TotalCharges" for step in ml["cleaning"])
        assert ml["validation"]["cv_strategy"] == "StratifiedKFold"
        assert ml["validation"]["n_folds"] == 5
        assert ml["validation"]["random_state"] == 42
        assert ml["validation"]["train_rows"] + ml["validation"]["test_rows"] == row.record_count
        families = {item["model_family"] for item in ml["model_comparison"]}
        assert "logistic_regression" in families
        assert "random_forest" in families
        for item in ml["model_comparison"]:
            assert item["cv_auc"] is not None
            if item["selected"]:
                assert item["test_auc"] is not None
            else:
                assert item["test_auc"] is None
        selected = next(item for item in ml["model_comparison"] if item["selected"])
        assert ml["final_model"]["model_family"] == selected["model_family"]
        assert ml["final_model"]["test_metrics"]["roc_auc"] == selected["test_auc"]
        assert ml["predictions"]["count"] == ml["validation"]["test_rows"]
        assert ml["predictions"]["download_available"] is True
        assert ml["predictions"]["distribution"]
        assert ml["processing_summary"]["cleaning_completed"] is True
        assert ml["processing_summary"]["training_completed"] is True
        assert ml["processing_summary"]["predictions_completed"] is True
        assert ml["target"] == "churn"

        csv_response = admin_client.get(f"/admin/client-uploads/{row.id}/predictions.csv")
        assert csv_response.status_code == 200
        assert "text/csv" in csv_response.headers["content-type"]
        csv_text = csv_response.content.decode()
        assert "y_pred" in csv_text
        assert "y_true" in csv_text
        assert csv_text.count("\n") >= ml["predictions"]["count"]

        report_response = admin_client.get(f"/admin/client-uploads/{row.id}/report.docx")
        assert report_response.status_code == 200
        assert report_response.content.startswith(b"PK")
        assert "application/vnd.openxmlformats" in report_response.headers["content-type"]
        assert 'filename="DCLab ML Run Report.docx"' in report_response.headers["content-disposition"]

    def test_predictions_csv_is_404_when_run_has_no_experiment(self, db_session, admin_client):
        from app.db.models import DEFAULT_WORKSPACE_ID, ClientLabUpload

        row = ClientLabUpload(
            workspace_id=DEFAULT_WORKSPACE_ID,
            category="Revenue",
            original_filename="empty.csv",
            stored_path="/tmp/missing.csv",
            kind="spreadsheet",
            record_count=0,
            fields_noticed=[],
            has_named_fields=True,
            pipeline_status="queued",
        )
        db_session.add(row)
        db_session.commit()
        response = admin_client.get(f"/admin/client-uploads/{row.id}/predictions.csv")
        assert response.status_code == 404

    def test_unknown_upload_is_404(self, admin_client):
        response = admin_client.get(f"/admin/client-uploads/{uuid4()}")
        assert response.status_code == 404


class TestAdminMonitoring:
    def test_metric_deltas_are_computed_from_consecutive_real_retrains(self, db_session, admin_client):
        _insert_simulation_run(db_session, use_case="churn", roc_auc=0.89)
        second = _insert_simulation_run(db_session, use_case="churn", roc_auc=0.90)

        response = admin_client.get("/admin/monitoring")
        assert response.status_code == 200
        body = response.json()

        second_event = next(e for e in body["retrain_events"] if e["id"] == str(second.id))
        assert second_event["metric_deltas"]["roc_auc"]["previous"] == 0.89
        assert second_event["metric_deltas"]["roc_auc"]["current"] == 0.9
        assert second_event["metric_deltas"]["roc_auc"]["delta"] == pytest.approx(0.01, abs=1e-6)

        # First run of a use case has nothing to diff against yet.
        first_id = min(
            (e for e in body["retrain_events"] if e["name"] == "churn"),
            key=lambda e: e["created_at"],
        )["id"]
        first_event = next(e for e in body["retrain_events"] if e["id"] == first_id)
        assert first_event["metric_deltas"] == {}

        assert body["drift_detection_note"]
        assert isinstance(body["dataset_health"], list)

    def test_dataset_health_reflects_real_dataset_rows(self, db_session, admin_client):
        from app.services.lab_service import ingest_synthetic, seed_dogfood

        env = seed_dogfood(db_session)
        dataset = ingest_synthetic(db_session, env, n=50)

        response = admin_client.get("/admin/monitoring")
        assert response.status_code == 200
        health_row = next(row for row in response.json()["dataset_health"] if row["id"] == str(dataset.id))
        assert health_row["row_count"] == 50
        assert health_row["status"] in {"healthy", "not_profiled"}
