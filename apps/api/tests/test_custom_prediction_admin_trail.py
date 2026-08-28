"""Step 7 — Custom Prediction output wiring.

There is no separate "custom prediction" feature in this codebase distinct
from Client Labs (Step 5): Client Labs *is* the client-triggered request that
kicks off a new prediction task on demand. This step's two Definition-of-Done
items are both about that same flow:

  1. The result a client sees already goes through app.translation (proven by
     `test_client_labs.py`) — re-asserted here for the actual HTTP responses.
  2. NEW: whatever ML task a client's request triggers must be logged and
     reviewable on the Admin Model Registry side with full, unrestricted
     detail. Before this step, `run_trial` computed a full raw result and then
     discarded it after extracting translated insights — nothing admin-side
     recorded that a client had triggered a real training run at all. This
     file proves that gap is closed via `ClientLabRunAudit`.
"""

from __future__ import annotations

from app.translation.banned_terms import find_banned_terms

USE_CASE = "cross_sell"


def test_a_completed_trial_is_logged_on_the_model_registry_with_full_detail(auth_client, admin_client):
    created = auth_client.post("/app/labs/runs", data={"use_case": USE_CASE})
    assert created.status_code == 200, created.text
    run_id = created.json()["id"]

    registry = admin_client.get("/admin/models")
    assert registry.status_code == 200
    client_trial_rows = [row for row in registry.json() if row["source"] == "client_trial"]
    assert len(client_trial_rows) == 1
    row = client_trial_rows[0]
    assert row["client_lab_run_id"] == run_id
    assert row["fusion"]
    assert isinstance(row["metrics"], dict) and row["metrics"]

    detail = admin_client.get(f"/admin/models/client-trials/{row['id']}")
    assert detail.status_code == 200
    detail_body = detail.json()
    assert detail_body["client_lab_run_id"] == run_id
    assert detail_body["use_case"] == USE_CASE
    # Full, unrestricted payload -- exactly what the client-facing side never sees.
    assert "metrics" in detail_body["payload"]
    assert "model_version" in detail_body["payload"]

    # The same content is unreachable to a client_user token (generic sweep in
    # test_access_control.py covers this for every /admin route already; this
    # re-confirms it specifically for the two new endpoints touched here).
    assert auth_client.get("/admin/models").status_code == 403
    assert auth_client.get(f"/admin/models/client-trials/{row['id']}").status_code == 403


def test_a_completed_trial_appears_in_monitoring_with_a_use_case_delta(auth_client, admin_client):
    first = auth_client.post("/app/labs/runs", data={"use_case": USE_CASE})
    assert first.status_code == 200
    second = auth_client.post("/app/labs/runs", data={"use_case": USE_CASE})
    assert second.status_code == 200

    monitoring = admin_client.get("/admin/monitoring")
    assert monitoring.status_code == 200
    events = [e for e in monitoring.json()["retrain_events"] if e["source"] == "client_trial" and e["name"] == USE_CASE]
    assert len(events) == 2
    events.sort(key=lambda e: e["created_at"])
    assert events[0]["metric_deltas"] == {}
    assert all(e["client_lab_run_id"] for e in events)


def test_a_failed_trial_is_not_logged_as_a_registered_model(auth_client, admin_client, monkeypatch, db_session):
    import time

    import app.services.client_lab_service as service

    monkeypatch.setattr(service, "TRIAL_TIMEOUT_SECONDS", 0.05)
    monkeypatch.setattr(service, "run_use_case", lambda *a, **k: (time.sleep(2), {})[1])

    response = auth_client.post("/app/labs/runs", data={"use_case": USE_CASE})
    assert response.status_code == 200
    assert response.json()["status"] == "failed"

    from app.db.models import ClientLabRunAudit

    assert db_session.query(ClientLabRunAudit).count() == 0
    registry = admin_client.get("/admin/models")
    assert all(row["source"] != "client_trial" for row in registry.json())


def test_client_facing_trial_responses_stay_translated_only(auth_client):
    """DoD 1 — re-asserted at the HTTP layer rather than just at the schema
    layer, since the whole point of Step 7 is trusting the live response."""
    created = auth_client.post("/app/labs/runs", data={"use_case": USE_CASE})
    assert created.status_code == 200
    run_id = created.json()["id"]
    assert find_banned_terms(created.text) == []

    detail = auth_client.get(f"/app/labs/runs/{run_id}")
    assert detail.status_code == 200
    assert find_banned_terms(detail.text) == []

    listed = auth_client.get("/app/labs/runs")
    assert listed.status_code == 200
    assert find_banned_terms(listed.text) == []
