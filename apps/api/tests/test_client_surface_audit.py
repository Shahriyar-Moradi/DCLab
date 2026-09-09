"""Client-surface audit: conversion seed and immediate generate-503 reporting."""

from __future__ import annotations

import json

import pytest

from app.ml.predict import predict_conversion, reset_model_cache
from scripts.audit_client_surface import (
    _response_detail,
    crawl_api,
    fail_if_generate_unsuccessful,
)
from scripts.seed_conversion_model import conversion_artifact_ready, seed_conversion_artifact


def test_seed_conversion_artifact_is_loadable_and_idempotent(tmp_path):
    first = seed_conversion_artifact(tmp_path)
    assert first == tmp_path
    assert conversion_artifact_ready(tmp_path)
    metadata = json.loads((tmp_path / "metadata.json").read_text(encoding="utf-8"))
    assert metadata["model_version"] == "conversion_v2"
    stamp = (tmp_path / "model.joblib").stat().st_mtime_ns

    again = seed_conversion_artifact(tmp_path)
    assert again == tmp_path
    assert (tmp_path / "model.joblib").stat().st_mtime_ns == stamp

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
    assert version == "conversion_v2"
    reset_model_cache()


def test_fail_if_generate_unsuccessful_reports_503_immediately(capsys):
    body = json.dumps(
        {
            "detail": (
                "No trained model found at models/revenue_prediction/model.joblib. "
                "Run `python -m app.ml.train` first."
            )
        }
    ).encode()
    with pytest.raises(SystemExit) as exc:
        fail_if_generate_unsuccessful(503, body)
    assert exc.value.code == 2
    err = capsys.readouterr().err
    assert "HTTP 503" in err
    assert "No trained model found" in err
    assert "were not actually exercised" not in err


def test_fail_if_generate_unsuccessful_ignores_200():
    fail_if_generate_unsuccessful(200, b'{"opportunity_id":"opp_1"}')


def test_response_detail_extracts_fastapi_detail():
    assert _response_detail(b'{"detail":"missing artifact"}') == "missing artifact"


def test_crawl_api_exits_on_generate_503_before_listing_uncovered(monkeypatch, capsys):
    from scripts import audit_client_surface as audit

    monkeypatch.setattr(audit, "seed_conversion_artifact", lambda: None)

    def fake_json(method, path, token=None, body=None):
        if path == "/app/opportunities":
            return 200, json.dumps({"items": [{"id": "opp_1"}]}).encode()
        if path == "/app/opportunities/opp_1":
            return 200, b"{}"
        if path == "/app/decisions/generate":
            return 503, json.dumps({"detail": "No trained model found at /tmp/model.joblib"}).encode()
        raise AssertionError(f"unexpected {method} {path} — audit continued after generate 503")

    monkeypatch.setattr(audit, "_json_request", fake_json)
    with pytest.raises(SystemExit) as exc:
        crawl_api("token")
    assert exc.value.code == 2
    err = capsys.readouterr().err
    assert "HTTP 503" in err
    assert "No trained model found" in err
    assert "were not actually exercised" not in err
