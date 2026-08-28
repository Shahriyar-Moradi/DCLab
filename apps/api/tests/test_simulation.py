from uuid import uuid4

from app.db.models import SimulationRun
from app.ml.candidates import build_candidate_specs
from app.ml.feature_groups import load_layer_config
from app.ml.predict import reset_model_cache
from app.ml.train import train_and_save
from app.services.decision_service import load_policy
from app.sim.catalog import use_case
from app.sim.decide import decide_simulated
from app.sim.generate import generate_northstar_customers


def test_production_conversion_candidate_cap_unchanged():
    specs = build_candidate_specs(load_layer_config())
    assert 5 <= len(specs) <= 12


def test_sim_layer_allows_up_to_twenty_candidates():
    config = load_layer_config(use_case("churn").layer_path)
    specs = build_candidate_specs(config)
    assert 5 <= len(specs) <= 20
    assert config["kind"] == "simulation"


def test_do_nothing_can_win_campaign_c():
    policy = load_policy(use_case("campaign_response").policy_path)
    entity = {
        "external_id": "C-CAMP-C",
        "incremental_margin": 8.0,
        "true_p0_response": 0.60,
    }
    result = decide_simulated(entity, 0.60, policy)
    assert result["action_key"] == "do_nothing"
    assert result["uplift_is_simulated"] is True
    assert any(row["action"] == "do_nothing" for row in result["action_table"])


def test_purchase_product_rec_beats_higher_conversion_discount():
    policy = load_policy(use_case("purchase").policy_path)
    entity = {
        "external_id": "S-1001",
        "expected_margin": 40.0,
        "true_p0_purchase": 0.82,
    }
    result = decide_simulated(entity, 0.82, policy)
    by_action = {row["action"]: row for row in result["action_table"]}
    assert by_action["offer_discount"]["probability"] > by_action["product_recommendation"]["probability"]
    assert by_action["product_recommendation"]["expected_value"] > by_action["offer_discount"]["expected_value"]
    assert result["action_key"] == "product_recommendation"


def test_upsell_email_beats_aggressive_call_because_of_churn_risk():
    policy = load_policy(use_case("upsell").policy_path)
    entity = {
        "external_id": "C-UPSELL-71",
        "upgrade_arpu": 1200.0,
        "remaining_arr": 2988.0,
        "true_p0_upsell": 0.71,
    }
    result = decide_simulated(entity, 0.71, policy)
    by_action = {row["action"]: row for row in result["action_table"]}
    assert by_action["sales_call"]["probability"] > by_action["send_email"]["probability"]
    assert result["action_key"] == "send_email"


def test_churn_csm_beats_do_nothing_on_hero():
    policy = load_policy(use_case("churn").policy_path)
    entity = {
        "external_id": "C-92831",
        "remaining_arr": 2988.0,
        "true_p0_churn": 0.77,
    }
    result = decide_simulated(entity, 0.77, policy)
    assert result["action_key"] == "assign_csm"
    assert result["incremental_value"] > 0


def test_compare_naive_discount_loses_to_policy_on_planted_truth():
    import pandas as pd

    from app.sim.compare import compare_holdout

    policy = load_policy(use_case("purchase").policy_path)
    entity = {"external_id": "S-1001", "expected_margin": 40.0, "true_p0_purchase": 0.82}
    scored = [
        {"entity": entity, "probability": 0.90, "best_single_probability": 0.90},
        {"entity": {**entity, "external_id": "S-2"}, "probability": 0.40, "best_single_probability": 0.40},
    ]
    result = compare_holdout(pd.DataFrame(), scored, policy)
    assert result["naive"]["profit_vs_do_nothing"] < result["fusion"]["profit_vs_do_nothing"]
    assert result["oracle"]["regret_vs_oracle"] == 0


def test_sim_factory_trains_and_fuses(tmp_path):
    frame = generate_northstar_customers(n=280)
    csv_path = tmp_path / "customers.csv"
    frame.to_csv(csv_path, index=False)
    spec = use_case("churn")
    reset_model_cache()
    metadata = train_and_save(
        csv_path=csv_path,
        model_dir=tmp_path / "model",
        layer_path=spec.layer_path,
        target_col=spec.target,
    )
    assert metadata["n_candidates_evaluated"] >= 5
    assert metadata["members"]
    assert metadata["metrics"]["roc_auc"] > 0.5
    assert metadata["fusion"].startswith("single:") or metadata["fusion"] == "weighted_blend"
    if metadata["fusion"] == "weighted_blend":
        blend = metadata["metrics"]["roc_auc"]
        best = max(metadata["all_metrics"][mid]["roc_auc"] for mid in metadata["weights"])
        assert blend > best
    reset_model_cache()


def test_simulation_api_persists_and_returns_uplift_flag(admin_client, db_session):
    payload = {
        "use_case": "churn",
        "company": "Northstar SaaS",
        "question": "churn",
        "model_version": "churn_sim_v1",
        "policy_version": "churn_sim_v1",
        "fusion": "single:gb_all",
        "n_candidates_evaluated": 12,
        "members": [{"id": "gb_all", "weight": 1.0, "groups": ["behavioral"]}],
        "metrics": {"roc_auc": 0.8},
        "all_metrics": {},
        "comparison": {
            "naive": {"profit_vs_do_nothing": 10},
            "best_single": {"profit_vs_do_nothing": 20},
            "fusion": {"profit_vs_do_nothing": 22},
            "oracle": {"profit_vs_do_nothing": 40},
        },
        "heroes": [
            {
                "external_id": "C-92831",
                "probability": 0.77,
                "agreement": 0.91,
                "recommended_action": "ASSIGN_CSM",
                "action_key": "assign_csm",
                "expected_value": -1415.0,
                "incremental_value": 886.0,
                "action_table": [
                    {"action": "do_nothing", "probability": 0.77, "expected_value": -2301},
                    {"action": "assign_csm", "probability": 0.44, "expected_value": -1415},
                ],
                "evidence": {"models_used": 3, "fusion": "single:gb_all", "member_probabilities": {"gb_all": 0.77}},
                "uplift_is_simulated": True,
                "model_version": "churn_sim_v1",
                "policy_version": "churn_sim_v1",
            }
        ],
        "sample_decisions": [],
        "uplift_is_simulated": True,
        "layer": "churn",
        "target": "churned",
        "n_train": 100,
        "n_test": 20,
    }
    row = SimulationRun(
        id=uuid4(),
        use_case="churn",
        model_version="churn_sim_v1",
        policy_version="churn_sim_v1",
        fusion="single:gb_all",
        payload=payload,
    )
    db_session.add(row)
    db_session.commit()

    listed = admin_client.get("/admin/simulations/runs")
    assert listed.status_code == 200
    assert listed.json()["total"] >= 1

    detail = admin_client.get(f"/admin/simulations/runs/{row.id}")
    assert detail.status_code == 200
    assert detail.json()["use_case"] == "churn"

    decision = admin_client.get(f"/admin/simulations/runs/{row.id}/decisions/C-92831")
    assert decision.status_code == 200
    body = decision.json()
    assert body["uplift_is_simulated"] is True
    for key in (
        "conversion_probability",
        "expected_revenue",
        "recommended_action",
        "confidence",
        "reasoning",
        "model_version",
        "policy_version",
    ):
        assert key in body
    assert body["recommended_action"] == "ASSIGN_CSM"


def test_simulation_run_endpoint_uses_engine(admin_client, monkeypatch, db_session):
    def _fake_run(name: str):
        return {
            "use_case": name,
            "company": "Northstar SaaS",
            "question": "q",
            "model_version": "churn_sim_v1",
            "policy_version": "churn_sim_v1",
            "fusion": "single:gb_all",
            "n_candidates_evaluated": 12,
            "members": [],
            "metrics": {"roc_auc": 0.7},
            "all_metrics": {},
            "comparison": {},
            "heroes": [],
            "sample_decisions": [],
            "uplift_is_simulated": True,
            "layer": name,
            "target": "churned",
            "n_train": 10,
            "n_test": 5,
        }

    monkeypatch.setattr("app.api.simulations.run_use_case", _fake_run)
    response = admin_client.post("/admin/simulations/run", json={"use_case": "churn"})
    assert response.status_code == 200
    body = response.json()
    assert body["use_case"] == "churn"
    assert body["payload"]["uplift_is_simulated"] is True
