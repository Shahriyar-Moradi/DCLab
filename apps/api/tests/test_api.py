from __future__ import annotations

from app.config import get_settings
from app.ml.predict import reset_model_cache
from app.ml.train import train_and_save


def test_health_ok(client):
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok", "db": "connected"}


def test_upload_happy_and_list_paginated(auth_client, sample_csv_bytes):
    response = auth_client.post("/app/opportunities/upload", files={"file": ("opp.csv", sample_csv_bytes, "text/csv")})
    assert response.status_code == 200
    body = response.json()
    assert body["inserted"] == 3
    assert body["rejected"] == 0

    listed = auth_client.get("/app/opportunities?limit=2")
    assert listed.status_code == 200
    payload = listed.json()
    assert payload["limit"] == 2
    assert payload["total"] == 3
    assert len(payload["items"]) == 2

    sorted_amount = auth_client.get("/app/opportunities?sort=amount&order=desc")
    assert sorted_amount.status_code == 200
    amounts = [row["amount"] for row in sorted_amount.json()["items"]]
    assert amounts == sorted(amounts, reverse=True)

    filtered = auth_client.get("/app/opportunities?stage=proposal")
    assert filtered.status_code == 200
    assert filtered.json()["total"] == 1
    assert filtered.json()["items"][0]["external_id"] == "opp_1"

    detail = auth_client.get("/app/opportunities/opp_1")
    assert detail.status_code == 200
    assert detail.json()["external_id"] == "opp_1"


def test_upload_rejects_invalid_rows(auth_client):
    csv = (
        "external_id,customer_id,amount,currency,stage,source,owner_id,created_at,converted\n"
        "opp_ok,cust_1,1000,AED,proposal,inbound,rep_1,2026-01-01,1\n"
        "opp_neg,cust_2,-25,AED,proposal,inbound,rep_1,2026-01-01,0\n"
        "opp_date,cust_3,1000,AED,proposal,inbound,rep_1,32/13/2026,0\n"
    ).encode()
    response = auth_client.post("/app/opportunities/upload", files={"file": ("bad.csv", csv, "text/csv")})
    assert response.status_code == 200
    body = response.json()
    assert body["inserted"] == 1
    assert body["rejected"] == 2
    assert len(body["errors"]) == 2


def test_reupload_does_not_duplicate(auth_client, sample_csv_bytes):
    auth_client.post("/app/opportunities/upload", files={"file": ("opp.csv", sample_csv_bytes, "text/csv")})
    again = auth_client.post("/app/opportunities/upload", files={"file": ("opp.csv", sample_csv_bytes, "text/csv")})
    assert again.status_code == 200
    listed = auth_client.get("/app/opportunities?limit=50")
    assert listed.json()["total"] == 3


def test_empty_csv_upload(auth_client):
    response = auth_client.post("/app/opportunities/upload", files={"file": ("empty.csv", b"", "text/csv")})
    assert response.status_code == 200
    assert response.json()["rejected"] == 1


def test_all_invalid_csv_upload(auth_client):
    csv = (
        "external_id,customer_id,amount,currency,stage,source,owner_id,created_at\n"
        ",cust_1,1000,AED,proposal,inbound,rep_1,2026-01-01\n"
        "opp_x,cust_2,-1,AED,proposal,inbound,rep_1,2026-01-01\n"
    ).encode()
    response = auth_client.post("/app/opportunities/upload", files={"file": ("bad.csv", csv, "text/csv")})
    assert response.status_code == 200
    body = response.json()
    assert body["inserted"] == 0
    assert body["rejected"] == 2


def test_get_opportunity_missing(auth_client):
    response = auth_client.get("/app/opportunities/does-not-exist")
    assert response.status_code == 404


def test_optional_fields_accepted(auth_client):
    csv = (
        "external_id,customer_id,amount,currency,stage,source,owner_id\n"
        "opp_sparse,cust_9,1500,AED,qualification,website,rep_9\n"
    ).encode()
    response = auth_client.post("/app/opportunities/upload", files={"file": ("sparse.csv", csv, "text/csv")})
    assert response.status_code == 200
    assert response.json()["inserted"] == 1
    detail = auth_client.get("/app/opportunities/opp_sparse")
    assert detail.status_code == 200
    assert detail.json()["converted"] is None


def test_generate_unknown_opportunity_404(auth_client):
    response = auth_client.post("/app/decisions/generate", json={"opportunity_id": "missing"})
    assert response.status_code == 404


def test_generate_without_model_is_503_not_500(auth_client, sample_csv_bytes, tmp_path, monkeypatch):
    auth_client.post("/app/opportunities/upload", files={"file": ("opp.csv", sample_csv_bytes, "text/csv")})
    settings = get_settings()
    monkeypatch.setattr(settings, "model_dir", tmp_path / "missing-model")
    reset_model_cache()
    response = auth_client.post("/app/decisions/generate", json={"opportunity_id": "opp_1"})
    assert response.status_code == 503
    reset_model_cache()


def test_generate_happy_path_and_no_duplicate(auth_client, sample_csv_bytes, tmp_path, monkeypatch):
    csv_path = tmp_path / "train.csv"
    csv_path.write_bytes(sample_csv_bytes)
    # Tiny set is enough for a fitted model in tests if we expand it.
    rows = sample_csv_bytes.decode().strip().split("\n")
    header, *data = rows
    expanded = [header]
    for i in range(40):
        for line in data:
            parts = line.split(",")
            parts[0] = f"{parts[0]}_{i}"
            expanded.append(",".join(parts))
    csv_path.write_text("\n".join(expanded) + "\n")
    train_and_save(csv_path, tmp_path)
    settings = get_settings()
    monkeypatch.setattr(settings, "model_dir", tmp_path)
    reset_model_cache()

    auth_client.post("/app/opportunities/upload", files={"file": ("opp.csv", sample_csv_bytes, "text/csv")})
    first = auth_client.post("/app/decisions/generate", json={"opportunity_id": "opp_1"})
    assert first.status_code == 200
    body = first.json()
    assert set(body.keys()) == {
        "opportunity_id",
        "expected_revenue",
        "recommended_action",
        "confidence_band",
        "reasoning",
        "policy_version",
    }
    assert body["opportunity_id"] == "opp_1"
    assert body["confidence_band"] in {"High", "Medium", "Low"}
    assert body["policy_version"]
    assert body["reasoning"]

    second = auth_client.post("/app/decisions/generate", json={"opportunity_id": "opp_1"})
    assert second.status_code == 200
    listed = auth_client.get("/app/decisions")
    assert listed.status_code == 200
    assert listed.json()["total"] == 1

    decision_id = listed.json()["items"][0]["id"]
    detail = auth_client.get(f"/app/decisions/{decision_id}")
    assert detail.status_code == 200
    assert detail.json()["confidence_band"] in {"High", "Medium", "Low"}
    assert detail.json()["policy_version"] == body["policy_version"]

    by_action = auth_client.get(f"/app/decisions?action={body['recommended_action']}")
    assert by_action.status_code == 200
    assert by_action.json()["total"] == 1
    by_external = auth_client.get("/app/decisions?opportunity_id=opp_1")
    assert by_external.json()["total"] == 1
    by_uuid = auth_client.get(f"/app/decisions?opportunity_id={listed.json()['items'][0]['opportunity_id']}")
    assert by_uuid.json()["total"] == 1
    missing = auth_client.get("/app/decisions?opportunity_id=does-not-exist")
    assert missing.json()["total"] == 0
    reset_model_cache()


def test_cors_allows_local_frontend(client):
    response = client.get("/health", headers={"Origin": "http://localhost:3001"})
    assert response.status_code == 200
    assert response.headers.get("access-control-allow-origin") == "http://localhost:3001"


def test_get_decision_missing(auth_client):
    response = auth_client.get("/app/decisions/00000000-0000-0000-0000-000000000001")
    assert response.status_code == 404


def test_predict_conversion_unit(tmp_path, sample_csv_bytes):
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
    train_and_save(csv_path, tmp_path)
    reset_model_cache()
    from app.ml.predict import predict_conversion

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
    assert version == "conversion_v2"
    reset_model_cache()
