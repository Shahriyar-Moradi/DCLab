"""P5.1-A review canary: holdout feature statistics never reach an agent or a service token.

A real run on a table with a planted LABEL PROXY (a column that is empty for every negative
case, so its test-row missing share is the test negative rate). The train -> test checks
store such numbers under holdout-scoped keys (people see them in Studio); the service-token
REST response, the catalog tools (get_findings, get_model_card) and the token model card
carry none of them and no test timestamp, while status, severity and recommendation stay.
"""

from __future__ import annotations

import json
from io import BytesIO
from uuid import UUID

import numpy as np
import pandas as pd
from sqlalchemy import select

from ai_harness.kit import numbers_in
from app.agents.tools.catalog import ToolContext, catalog
from app.agents.tools.shaping import HOLDOUT_KEY
from app.db.models import Experiment, ModelVersion
from app.domain.findings import AGENT_MESSAGES, HOLDOUT_FEATURE_CHECKS
from app.services.service_token_service import create_service_token
from test_investigate_library import _keys, holdout_scope_values
from test_v1_findings import _run
from test_v1_resources_experiments import _csv, _h, setup  # noqa: F401 (fixture)


def _with_label_proxy(seed: int = 31, n: int = 300) -> bytes:
    """A label proxy, an event time (so the split goes forward in time) and a column that drifts
    with time (so a holdout-scoped check warns and reaches the model card's risks)."""

    frame = pd.read_csv(BytesIO(_csv(n=n, seed=seed)))
    rng = np.random.default_rng(seed)
    frame["proxy"] = np.where(frame["label"] == "yes", np.round(rng.normal(5, 1, n), 3), np.nan)
    stamps = pd.Timestamp("2024-01-01") + pd.to_timedelta(np.arange(n) * 12 + rng.integers(0, 600, n), unit="m")
    frame["event_date"] = stamps.strftime("%Y-%m-%d %H:%M")
    frame["spend"] = np.round(frame["spend"] + np.arange(n) * 0.8, 2)
    return frame.to_csv(index=False).encode()


def _text(value) -> str:
    return str(getattr(value, "text", value))


def test_tokens_and_agent_tools_never_carry_holdout_feature_statistics(client, db_session, setup):  # noqa: F811
    db = db_session
    experiment_id = _run(client, db, setup, _with_label_proxy())
    human = client.get(f"/v1/experiments/{experiment_id}/findings", headers=_h(setup)).json()
    db.expire_all()
    experiment = db.get(Experiment, UUID(experiment_id))
    numbers, texts = holdout_scope_values(experiment.result["investigation"])
    assert numbers, "positive control: the run stored holdout-scoped numbers"
    assert numbers & numbers_in(json.dumps(human)), "people (Studio) keep the full detail"

    token = create_service_token(db, creator=setup["alpha_admin"], workspace_id=setup["alpha"].id, name="canary",
                                 scopes=("read",), expires_in_days=1, current_password="test-password")[1]
    db.commit()
    bearer = {"Authorization": f"Bearer {token}"}
    as_token = client.get(f"/v1/experiments/{experiment_id}/findings", headers=bearer)
    assert as_token.status_code == 200, as_token.text
    # The representation depends on the principal: never cached across callers.
    assert as_token.headers["Cache-Control"] == "private, no-store"
    assert {"Authorization", "Cookie"} <= {part.strip() for part in as_token.headers["Vary"].split(",")}
    model_id = db.scalar(select(ModelVersion.id).where(ModelVersion.pipeline_run_id == experiment.id))
    card = client.get(f"/v1/model-versions/{model_id}/card", headers=bearer)
    assert card.status_code == 200, card.text
    ctx = ToolContext(db=db, actor=setup["alpha_admin"], workspace_id=setup["alpha"].id)
    tool_findings = catalog()["get_findings"].read(ctx, {"experiment_id": experiment_id}).payload
    tool_card = catalog()["get_model_card"].read(ctx, {"model_version_id": str(model_id)}).payload
    for name, body in (("token findings", as_token.json()), ("token card", card.json()),
                       ("tool get_findings", tool_findings), ("tool get_model_card", tool_card)):
        text = json.dumps(body, default=str)
        assert not numbers_in(text) & numbers, (name, sorted(numbers_in(text) & numbers))
        assert not [t for t in texts if t in text], name
        assert not [k for k in _keys(json.loads(text)) if HOLDOUT_KEY.search(k)], name
    # One bit per check stays: status, severity and recommendation kind; a fixed message.
    agent_checks = {item["check"]: item for item in as_token.json()["checks"]}
    tool_checks = {_text(item["check"]): item for item in tool_findings["checks"]}
    scoped = [item for item in human["checks"] if item["check"] in HOLDOUT_FEATURE_CHECKS]
    assert any(item["status"] in {"warning", "fail"} for item in scoped), [(i["check"], i["status"]) for i in scoped]
    assert texts, "positive control: the test period's timestamps are holdout-scoped canaries"
    for item in human["checks"]:
        mirrored = agent_checks[item["check"]]
        assert (mirrored["status"], mirrored["severity"], mirrored["recommendation_kind"]) == (
            item["status"], item["severity"], item["recommendation_kind"])
        if item["check"] in HOLDOUT_FEATURE_CHECKS:
            assert mirrored["message"] == AGENT_MESSAGES[item["check"]] != item["message"]
            assert _text(tool_checks[item["check"]]["message"]) == AGENT_MESSAGES[item["check"]]
    # The card's known risks: the fixed text for holdout-scoped checks, on both agent paths.
    risks = {item["check"]: item["message"] for item in card.json()["risks"]["items"]}
    warned = [item for item in scoped if item["status"] in {"warning", "fail"}]
    for item in warned:
        assert risks[item["check"]] == AGENT_MESSAGES[item["check"]]
        assert item["message"] not in _text(tool_card["model_card"]["risks"])
        assert item["message"] not in _text(tool_card["model_card"]["markdown"])
