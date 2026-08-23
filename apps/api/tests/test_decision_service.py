from copy import deepcopy

from app.services.decision_service import decide, load_policy


def _policy(**overrides):
    policy = load_policy()
    policy = deepcopy(policy)
    policy.update(overrides)
    return policy


def _opportunity(**overrides):
    row = {
        "external_id": "opp_test",
        "amount": 100000,
        "currency": "AED",
        "stage": "proposal",
        "source": "inbound",
        "engagement_score": 0.87,
        "last_contact_days_ago": 5,
        "num_interactions": 9,
        "sales_rep_available": True,
        "industry": "telecom",
    }
    row.update(overrides)
    return row


def test_high_probability_selects_contact_today():
    result = decide(_opportunity(), 0.73, load_policy())
    assert result["recommended_action"] == "CONTACT_TODAY"
    assert result["expected_revenue"] == 73000.0
    assert result["policy_version"] == "opportunity_prioritization_v1"
    assert result["reasoning"]
    assert any("0.87" in line for line in result["reasoning"])
    assert any("73000" in line.replace(",", "") for line in result["reasoning"])


def test_below_threshold_is_no_action():
    result = decide(_opportunity(), 0.10, load_policy())
    assert result["recommended_action"] == "NO_ACTION"
    assert result["incremental_value"] == 0
    assert any("0.10" in line or "0.10" in line for line in result["reasoning"])


def test_contact_cap_blocks_contact_today():
    # last_contact_days_ago == 0 maps to 3 contacts this week, hitting the YAML cap.
    result = decide(_opportunity(last_contact_days_ago=0), 0.80, load_policy())
    assert result["recommended_action"] != "CONTACT_TODAY"
    assert result["recommended_action"] in {"SCHEDULE_FOLLOWUP", "SEND_EMAIL"}


def test_unavailable_rep_blocks_contact_today():
    result = decide(_opportunity(sales_rep_available=False), 0.80, load_policy())
    assert result["recommended_action"] != "CONTACT_TODAY"


def test_changing_yaml_threshold_changes_behavior_without_python_changes():
    policy = load_policy()
    low = decide(_opportunity(), 0.40, policy)
    assert low["recommended_action"] != "NO_ACTION"

    raised = deepcopy(policy)
    raised["constraints"]["minimum_probability"] = 0.90
    high_floor = decide(_opportunity(), 0.40, raised)
    assert high_floor["recommended_action"] == "NO_ACTION"
