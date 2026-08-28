from io import BytesIO

from app.engine.datasets.lab_workbook import make_lab_workbook


def _workbook_bytes(n: int = 160) -> bytes:
    buffer = BytesIO()
    make_lab_workbook(n=n).to_csv(buffer, index=False)
    return buffer.getvalue()


def test_upload_plans_five_trainable_use_cases(admin_client):
    uploaded = admin_client.post(
        "/admin/datasets/upload",
        files={"file": ("lab.csv", _workbook_bytes(), "text/csv")},
        params={"name": "lab_csv"},
    )
    assert uploaded.status_code == 200, uploaded.text
    dataset_id = uploaded.json()["id"]
    assert uploaded.json()["row_count"] == 160
    profile = admin_client.get(f"/admin/datasets/{dataset_id}/profile")
    assert profile.status_code == 200
    plan = admin_client.get(f"/admin/datasets/{dataset_id}/use-cases")
    assert plan.status_code == 200
    body = plan.json()
    assert body["trainable_count"] == 5
    assert [item["slug"] for item in body["use_cases"]] == [
        "churn",
        "conversion",
        "lead_conversion",
        "purchase",
        "customer_value",
    ]
    assert all(item["trainable"] for item in body["use_cases"])


def test_train_conversion_fits_five_models(admin_client):
    uploaded = admin_client.post(
        "/admin/datasets/upload",
        files={"file": ("lab.csv", _workbook_bytes(180), "text/csv")},
        params={"name": "lab_train"},
    )
    dataset_id = uploaded.json()["id"]
    trained = admin_client.post(
        f"/admin/datasets/{dataset_id}/use-cases/conversion/train",
        json={"max_models": 5},
    )
    assert trained.status_code == 200, trained.text
    body = trained.json()
    assert body["status"] == "COMPLETED"
    assert body["use_case"] == "conversion"
    assert body["task_name"] == "Conversion"
    funnel = (body.get("result") or {}).get("funnel") or {}
    assert funnel.get("trained", 0) >= 4
    listed = admin_client.get("/admin/experiments")
    assert listed.status_code == 200
    assert listed.json()[0]["use_case"] == "conversion"


def test_sample_workbook_endpoint_is_ready_to_train(admin_client):
    created = admin_client.post("/admin/datasets/sample-workbook")
    assert created.status_code == 200, created.text
    dataset_id = created.json()["id"]
    plan = admin_client.get(f"/admin/datasets/{dataset_id}/use-cases").json()
    assert plan["trainable_count"] == 5
    assert created.json()["row_count"] >= 200


def test_client_cannot_upload_lab_dataset(auth_client):
    response = auth_client.post(
        "/admin/datasets/upload",
        files={"file": ("lab.csv", b"a,b\n1,2\n", "text/csv")},
    )
    assert response.status_code == 403
