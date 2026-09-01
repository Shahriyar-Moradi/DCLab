"""Step 1 — Business Translation Layer.

Verifies:
  * the banned-terms detector itself (word-boundary correctness on
    underscore-joined identifiers, phrase matching)
  * every registered translator (the opportunity/decision flow + all eight
    simulation use cases) produces a `ClientFacingInsight` free of banned terms
  * the live client API response schemas are clean, checked against the actual
    route table rather than a hand-written list
  * the live client frontend source tree is clean
  * the scanners themselves actually catch a reintroduced violation (proving the
    guardrail isn't a no-op) — done against isolated fixtures, not by breaking the
    real app
"""

from __future__ import annotations

from datetime import datetime

import pytest
from pydantic import BaseModel

from app.translation.banned_terms import find_banned_terms, is_clean
from app.translation.decisions import translate_opportunity_decision
from app.translation.models import ClientFacingInsight, ConfidenceBand
from app.translation.scanner import (
    _scan_model,
    scan_client_api_response_models,
    scan_frontend_client_tree,
)
from app.translation.simulations import CATEGORY_BY_USE_CASE, translate_simulation_outcome


class TestBannedTermsDetector:
    def test_flags_plain_word(self):
        assert find_banned_terms("This was scored by our model") == ["model"]

    def test_flags_word_inside_underscore_identifier(self):
        # "\b" alone would miss this because "_" is a word character.
        assert "model" in find_banned_terms("model_version: conversion_v2")

    def test_flags_multiword_phrase(self):
        assert "feature importance" in find_banned_terms("See the feature importance chart")

    def test_clean_business_text_passes(self):
        text = "High engagement, contacted recently, expected to add AED 1,200 in value."
        assert is_clean(text)
        assert find_banned_terms(text) == []

    def test_allows_client_milestone_copy_without_opening_other_model_uses(self):
        assert find_banned_terms("Building your model") == []
        assert find_banned_terms("This was scored by our model") == ["model"]
        assert "model" in find_banned_terms("Building your model. See the model card.")


class TestOpportunityDecisionTranslator:
    def _opportunity(self, **overrides):
        base = dict(
            external_id="OPP-1",
            currency="AED",
            engagement_score=0.82,
            last_contact_days_ago=2,
            stage="negotiation",
            industry="retail",
            sales_rep_available=True,
        )
        base.update(overrides)
        return base

    def _decision_result(self, **overrides):
        base = dict(
            recommended_action="CONTACT_TODAY",
            action_key="contact_today",
            expected_revenue=4500.0,
            incremental_value=900.0,
            confidence=0.81,
            policy_version="1",
        )
        base.update(overrides)
        return base

    def test_returns_client_facing_insight(self):
        insight = translate_opportunity_decision(
            self._opportunity(),
            conversion_probability=0.78,
            decision_result=self._decision_result(),
        )
        assert isinstance(insight, ClientFacingInsight)
        assert insight.confidence_band == ConfidenceBand.HIGH
        assert insight.recommended_action == "Contact today"
        assert insight.subject_id == "OPP-1"

    def test_output_is_free_of_banned_terms(self):
        insight = translate_opportunity_decision(
            self._opportunity(),
            conversion_probability=0.55,
            decision_result=self._decision_result(recommended_action="SCHEDULE_FOLLOWUP", action_key="schedule_followup"),
        )
        blob = " ".join([insight.headline, insight.recommended_action, *insight.reasoning])
        assert find_banned_terms(blob) == []

    def test_reasoning_never_states_raw_probability(self):
        insight = translate_opportunity_decision(
            self._opportunity(),
            conversion_probability=0.6321,
            decision_result=self._decision_result(),
        )
        for line in insight.reasoning:
            assert "0.6321" not in line
            assert "0.63" not in line


class TestSimulationTranslator:
    SAMPLE_FEATURES = {
        "churn": {"login_frequency_change": -0.4, "negative_support": 2, "days_until_renewal": 12, "email_engagement": 0.1},
        "purchase": {"cart_abandonments": 1, "days_since_last_purchase": 3, "product_views_7d": 20},
        "lead_conversion": {"demo_request": 1.0, "pricing_page_visits": 4, "campaign_engagement": 0.7},
        "upsell": {"feature_usage_change": 0.3, "features_used": 9},
        "cross_sell": {"past_hotels": 2, "past_activities": 1, "booking_lead_days": 14},
        "campaign_response": {"email_engagement": 0.6, "last_login_days": 2},
        "customer_value": {"monthly_revenue": 399, "days_until_renewal": 90},
        "custom_support": {"support_tickets": 4, "negative_support": 2},
    }

    @pytest.mark.parametrize("use_case_name", sorted(CATEGORY_BY_USE_CASE))
    def test_every_use_case_has_a_clean_translator(self, use_case_name):
        insight = translate_simulation_outcome(
            use_case_name,
            external_id="C-1",
            features=self.SAMPLE_FEATURES[use_case_name],
            agreement=0.9,
            recommended_action_key="email",
            expected_value=1200.0,
            incremental_value=300.0,
        )
        assert isinstance(insight, ClientFacingInsight)
        blob = " ".join([insight.headline, insight.recommended_action, *insight.reasoning])
        assert find_banned_terms(blob) == [], f"{use_case_name}: {find_banned_terms(blob)}"

    def test_unknown_use_case_rejected(self):
        with pytest.raises(ValueError):
            translate_simulation_outcome(
                "not_a_real_use_case",
                external_id="X",
                features={},
                agreement=0.5,
                recommended_action_key="do_nothing",
                expected_value=0.0,
                incremental_value=0.0,
            )

    def test_every_real_policy_action_key_humanizes_clean(self):
        """Step 8 caught this class of bug live (scripts/audit_client_surface.py
        found "offer_training" -> "Offer training", a real policy action that
        collides with the banned word "training"), and it slipped past
        `test_every_use_case_has_a_clean_translator` above because that test
        always calls with the fixed, safe `recommended_action_key="email"` --
        never the use case's own real action set. This test closes that gap by
        reading every action key straight out of every use case's actual
        policy YAML, the same source `app.sim.decide` picks a real action
        from, and humanizing each one for real."""
        import yaml

        from app.sim.catalog import all_use_cases

        checked = 0
        for spec in all_use_cases():
            with open(spec.policy_path) as handle:
                policy = yaml.safe_load(handle)
            for action_key in policy["actions"]:
                insight = translate_simulation_outcome(
                    spec.name,
                    external_id="C-AUDIT",
                    features=self.SAMPLE_FEATURES[spec.name],
                    agreement=0.9,
                    recommended_action_key=action_key,
                    expected_value=1200.0,
                    incremental_value=300.0,
                )
                hits = find_banned_terms(insight.recommended_action)
                assert hits == [], f"{spec.name}/{action_key} -> {insight.recommended_action!r}: {hits}"
                checked += 1
        assert checked >= 30  # sanity: this really iterated real policy data, not an empty catalog


class TestLiveClientSurfaceIsClean:
    """These two are the actual CI guardrail: they run against the real app / real
    frontend tree, not a fixture. A regression here means a banned term genuinely
    reached a client-facing schema or source file."""

    def test_client_api_response_schemas_are_clean(self):
        violations = scan_client_api_response_models()
        assert violations == {}, violations

    def test_client_frontend_source_is_clean(self):
        violations = scan_frontend_client_tree()
        assert violations == {}, violations


class TestScannerCatchesRegressions:
    """Proves the detectors aren't a no-op by feeding them a real violation in an
    isolated fixture, independent of the live app/frontend state."""

    def test_response_model_scanner_flags_banned_field_name(self):
        class LeakyInsight(BaseModel):
            subject_id: str
            model_version: str  # banned: must never appear on a client schema

        violations = _scan_model(LeakyInsight)
        assert violations, "scanner failed to flag a field literally named model_version"
        assert any("model" in terms for terms in violations.values())

    def test_response_model_scanner_flags_banned_description(self):
        from pydantic import Field

        class OtherwiseCleanInsight(BaseModel):
            headline: str = Field(description="Uses the ensemble's raw output directly")

        violations = _scan_model(OtherwiseCleanInsight)
        assert violations

    def test_frontend_scanner_flags_reintroduced_term(self, tmp_path, monkeypatch):
        bad_file = tmp_path / "LeakyComponent.tsx"
        bad_file.write_text('export const Leaky = () => <span>Model confidence: {auc}</span>;')

        import app.translation.scanner as scanner_module

        monkeypatch.setattr(scanner_module, "CLIENT_SCAN_DIRS", (tmp_path,))
        monkeypatch.setattr(scanner_module, "CLIENT_SCAN_FILES", ())
        monkeypatch.setattr(scanner_module, "CLIENT_SCHEMA_FILE", tmp_path / "does-not-exist.ts")

        violations = scanner_module.scan_frontend_client_tree()
        assert violations, "scanner failed to flag a reintroduced banned term in a client component"

        # Clean up: prove the same fixture directory passes once the term is gone.
        bad_file.write_text('export const Clean = () => <span>Recommended action: send email</span>;')
        assert scanner_module.scan_frontend_client_tree() == {}


class TestClientDashboardIsolatedFromMlOps:
    """Step 2 — the client Dashboard's 'Recent Decisions' feed is sourced entirely
    from `/app/decisions`, which is itself entirely translated (see the classes
    above). This seeds a *real* admin-side model retrain (an actual completed Lab
    experiment, not a fabricated fixture) and proves it cannot reach any
    client-facing response, directly answering the Step 2 Verify instruction:
    "Load the dashboard with seeded activity including a model retrain event;
    confirm it either doesn't appear or appears translated ... with no leaked
    terms."

    This system has no "decision approved" / "outcome measured" / "campaign
    completed" event types in its data model yet (no approval workflow, no
    outcome tracking, no campaign entity) — inventing fixture data for events
    that don't exist would be exactly the kind of shortcut this document warns
    against. The one real event type the feed emits (a decision being generated)
    is already a business event and already passes through translation.
    """

    def test_seeded_model_retrain_never_reaches_client_dashboard_feed(self, admin_client, auth_client, db_session):
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
            config=SearchConfig(max_candidates=4, max_feature_group_combinations=2, n_robustness_folds=2, seed=7),
        )
        executed = execute_experiment(db_session, experiment)
        assert executed.status == "COMPLETED"
        assert executed.result and executed.result.get("funnel")  # a genuine model retrain happened

        # Sanity: the admin side can see it (proves the seed actually worked).
        admin_view = admin_client.get(f"/admin/experiments/{executed.id}")
        assert admin_view.status_code == 200

        # The client can't reach it at all (Step 0 guardrail, re-checked here).
        client_view = auth_client.get(f"/admin/experiments/{executed.id}")
        assert client_view.status_code == 403

        # And nothing about it appears anywhere in the client's actual dashboard
        # data sources — not the id, not a metric, not the word "model".
        opportunities = auth_client.get("/app/opportunities")
        decisions = auth_client.get("/app/decisions")
        assert opportunities.status_code == 200
        assert decisions.status_code == 200

        experiment_id = str(executed.id)
        for response in (opportunities, decisions):
            raw = response.text
            assert experiment_id not in raw
            assert find_banned_terms(raw) == [], find_banned_terms(raw)


class TestClientInsightsSection:
    """Step 3 — the client Insights section, organized by business function.

    `/app/insights` reads the latest completed simulation run per use case
    (admin-produced) and serves it through the Step 1 translator, grouped by
    `InsightCategory`. Nothing here trains a model or accepts a client-triggered
    run — that bounded, translated trigger is Step 5 (Client Labs).
    """

    def _seed_run(self, db_session, *, use_case: str, external_id: str, features: dict, hours_ago: int = 0):
        from datetime import UTC, datetime, timedelta

        from app.db.models import SimulationRun

        row = SimulationRun(
            use_case=use_case,
            model_version=f"{use_case}_sim_v1",
            policy_version=f"{use_case}_sim_v1",
            fusion="single:gb_all",
            payload={
                "heroes": [
                    {
                        "external_id": external_id,
                        "agreement": 0.88,
                        "action_key": "email",
                        "recommended_action": "EMAIL",
                        "expected_value": 500.0,
                        "incremental_value": 120.0,
                        "features": features,
                    }
                ],
                "sample_decisions": [],
            },
            created_at=datetime.now(UTC) - timedelta(hours=hours_ago),
        )
        db_session.add(row)
        db_session.commit()
        return row

    def test_empty_state_still_returns_all_six_categories(self, auth_client):
        response = auth_client.get("/app/insights")
        assert response.status_code == 200
        body = response.json()
        categories = {group["category"] for group in body["categories"]}
        assert categories == {
            "Marketing",
            "Sales",
            "Revenue",
            "Churn & Retention",
            "Customer Value",
            "Custom",
        }
        assert all(group["insights"] == [] for group in body["categories"])

    def test_seeded_runs_appear_translated_and_grouped_by_category(self, db_session, auth_client):
        self._seed_run(
            db_session,
            use_case="churn",
            external_id="C-1",
            features={"login_frequency_change": -0.4, "negative_support": 2, "days_until_renewal": 10, "email_engagement": 0.1},
        )
        self._seed_run(
            db_session,
            use_case="campaign_response",
            external_id="C-2",
            features={"email_engagement": 0.7, "last_login_days": 1},
        )

        response = auth_client.get("/app/insights")
        assert response.status_code == 200
        by_category = {group["category"]: group["insights"] for group in response.json()["categories"]}

        assert len(by_category["Churn & Retention"]) == 1
        assert by_category["Churn & Retention"][0]["subject_id"] == "C-1"
        assert len(by_category["Marketing"]) == 1
        assert by_category["Marketing"][0]["subject_id"] == "C-2"
        assert by_category["Sales"] == []

        assert find_banned_terms(response.text) == []

    def test_only_the_latest_run_per_use_case_is_shown(self, db_session, auth_client):
        self._seed_run(db_session, use_case="churn", external_id="OLD", features={}, hours_ago=5)
        self._seed_run(db_session, use_case="churn", external_id="NEW", features={}, hours_ago=0)

        response = auth_client.get("/app/insights")
        by_category = {group["category"]: group["insights"] for group in response.json()["categories"]}
        subject_ids = {item["subject_id"] for item in by_category["Churn & Retention"]}
        assert subject_ids == {"NEW"}

    def test_insights_response_schema_is_registered_with_the_scanner(self):
        violations = scan_client_api_response_models()
        assert violations == {}, violations


class TestInsightSerializationHasNoRawFields:
    def test_no_probability_or_model_fields_on_the_model(self):
        field_names = set(ClientFacingInsight.model_fields.keys())
        assert "conversion_probability" not in field_names
        assert "model_version" not in field_names
        assert "confidence" not in field_names  # only confidence_band, a qualitative enum
        for name in field_names:
            assert find_banned_terms(name) == []

    def test_generated_at_is_a_real_timestamp(self):
        insight = translate_opportunity_decision(
            {"external_id": "OPP-2", "currency": "AED"},
            conversion_probability=0.5,
            decision_result={"recommended_action": "NO_ACTION", "action_key": "no_action", "expected_revenue": 0.0, "incremental_value": 0.0},
        )
        assert isinstance(insight.generated_at, datetime)
