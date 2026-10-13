"""P7.6-A: the Business view (``GET /v1/projects/{project_id}/outcomes``).

Pure sentence builders over hand-made model cards (binary, multiclass, regression with error
metrics stored negated, missing / withheld final test), the business-view word rules over every
template, and one real auto-train run + real scoring jobs for the route: people of every
workspace role read it (a business user included), tokens get the headline withheld, other
tenants get 404, the list is the latest COMPLETED scoring of the model in use, and the JSON
carries no ids (other than the list to download), emails or digests.
"""

from __future__ import annotations

import json
import re
from datetime import UTC, datetime
from uuid import UUID, uuid4

import pytest
from sqlalchemy import select

from app.db.models import ModelVersion, UserRole, WorkspaceRole
from app.domain import outcomes as o
from app.domain.errors import ExperimentRequestError
from app.domain.model_card import ModelCardRead
from app.services import outcomes_service
from app.services.auth_service import create_user
from app.services.project_service import create_project
from app.services.service_token_service import create_service_token
from app.translation.banned_terms import find_banned_terms
from test_v1_batch_predictions import _frame, _predict, _trained, _upload, _work
from test_v1_contract_conventions import _assert_envelope
from test_v1_resources_experiments import _h, _key, _member, setup  # noqa: F401 (fixture)

# Business-view words (v7 words table): never on this page, in any sentence.
V7_BANNED = re.compile(
    r"\b(release|champion|ref|refs|digest|holdout|hold-out|feature contract|trust check|cv|cross-validation|"
    r"candidate|experiment|fold|folds|accuracy|fresh estimate|L[0-3])\b",
    re.IGNORECASE,
)
TOP_KEYS = {"project_name", "state", "message", "model_in_use", "headline", "how_built", "weekly_list",
            "weekly_list_note", "limits", "boundary", "untrusted_fields"}
UUID_RE = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")
NOW = datetime(2026, 10, 1, 9, 0, tzinfo=UTC)


def _card(task: str, final: dict | None, *, status: str = "reported", rows: int | None = 200, **target) -> ModelCardRead:
    return ModelCardRead.model_validate({
        "model_version_id": str(uuid4()), "version": "v1", "experiment_id": str(uuid4()), "created_at": NOW,
        "content_digest": "a" * 64, "family": "lightgbm",
        "target": {"column": target.get("column", "churned"), "task_type": task,
                   "positive_label": target.get("positive"), "class_labels": target.get("classes", []),
                   "prediction_unit": target.get("unit")},
        "objective": {}, "metric_in_words": {"text": "", "basis": "x"}, "cv": {},
        "baseline": {"available": False, "text": ""}, "drivers": {"status": "not_computed", "text": ""},
        "risks": {"investigated": False, "text": ""},
        "data": {"row_count": 1000, "content_digest": "b" * 64, "name": "rows.csv"},
        "split": {"evaluation_split_strategy": target.get("strategy", "temporal_future"), "evaluation_fraction": 0.2,
                  "train_rows": 800, "evaluation_rows": rows, "time_column": "signup_date"},
        "llm": {"used": False},
        "final_evaluation": {"status": status, "metrics": final or {}, "decision_threshold": 0.31,
                             "metric": "recall", "value": (final or {}).get("recall")},
    })


# 200 rows, 50 positives, 40 found, 15 false flags (55 flagged): every rate agrees with whole numbers.
BINARY = {"recall": 0.8, "precision": 40 / 55, "specificity": 1 - 15 / 150, "positive_rate": 0.25, "accuracy": 0.85}


def _all_text(body: o.OutcomesRead | dict) -> str:
    data = body.model_dump(mode="json") if isinstance(body, o.OutcomesRead) else body
    data = {k: v for k, v in data.items() if k != "untrusted_fields"}
    return json.dumps(data)


def _sentences(body: o.OutcomesRead) -> list[str]:
    out = [body.message or "", body.boundary, body.weekly_list_note or "", *body.limits]
    if body.model_in_use:
        out += [body.model_in_use.approval_text]
    if body.headline:
        out += [*body.headline.sentences, body.headline.counts_text or "", body.headline.basis]
    if body.how_built:
        out += body.how_built.sentences
    if body.weekly_list:
        out.append(body.weekly_list.text)
    return [text for text in out if text]


def _assert_words(texts: list[str]) -> None:
    for text in texts:
        assert not V7_BANNED.search(text), text
        # The legacy client list, except "model" (the v7 words: "the model in use").
        assert set(find_banned_terms(text)) <= {"model"}, (text, find_banned_terms(text))


def _build(card, *, include_final=True, approved=None, weekly=None) -> o.OutcomesRead:
    return o.build_outcomes(project_name="Churn", saved_at=NOW, approved_at=approved, card=card,
                            weekly_list=weekly, include_final=include_final)


# --- pure builders ----------------------------------------------------------------------


def test_binary_headline_counts_and_sentences():
    body = _build(_card("binary", BINARY, positive="yes", unit="customer"))
    h = body.headline
    assert body.state == "ready" and h.status == "reported" and h.task == "binary"
    assert h.sentences == [
        'Of every 10 customers with churned "yes" in the final test, the model flagged 8.',
        'Of every 10 customers it flagged, 7 really had churned "yes".',
    ]
    assert (h.numbers.positives, h.numbers.found, h.numbers.flagged, h.numbers.rows_tested) == (50, 40, 55, 200)
    assert h.numbers.recall == 0.8 and h.numbers.decision_threshold == 0.31
    assert h.counts_text == ('Exact counts: 40 of 50 customers with churned "yes" were flagged; 40 of the 55 flagged '
                             "customers really had it; 200 customers tested at the cut-off score 0.31.")
    assert "200 rows it never saw while it was being built" in h.basis and "final test" in h.basis
    assert not any("accuracy" in text.lower() for text in _sentences(body))


def test_binary_counts_are_dropped_when_the_rates_do_not_agree():
    body = _build(_card("binary", BINARY, rows=203))  # 0.25 * 203 is not a whole number of rows
    h = body.headline
    assert h.counts_text is None and h.numbers.positives is None and h.numbers.found is None
    assert len(h.sentences) == 2 and "rows with the positive churned answer" in h.sentences[0]
    nothing = _build(_card("binary", {**BINARY, "recall": 0.0, "precision": 0.0, "specificity": 1.0})).headline
    assert nothing.sentences == [
        "Of every 10 rows with the positive churned answer in the final test, the model flagged 0.",
        "The model flagged no rows in the final test.",
    ]
    assert nothing.counts_text == ("Exact counts: 0 of 50 rows with the positive churned answer were flagged; "
                                   "200 rows tested at the cut-off score 0.31.")
    # Without counts, precision 0 cannot say whether anything was flagged: no precision sentence.
    unknown = _build(_card("binary", {"recall": 0.0, "precision": 0.0}, rows=None)).headline
    assert unknown.sentences == ["Of every 10 rows with the positive churned answer in the final test, the model flagged 0."]


def test_multiclass_headline_words():
    card = _card("multiclass", {"balanced_accuracy": 0.74, "accuracy": 0.9, "macro_f1": 0.7, "n_classes": 3.0},
                 column="tier", classes=["bronze", "silver", "gold"])
    h = _build(card).headline
    assert h.task == "multiclass" and h.numbers.balanced_accuracy == 0.74 and h.numbers.class_count == 3
    assert h.sentences[0] == ("For a typical group of tier, the model put about 7 of every 10 rows in the right group "
                              "in the final test (averaged over the 3 groups, each counted equally).")
    assert h.counts_text == "200 rows tested."


@pytest.mark.parametrize("sign", [1, -1])
def test_regression_errors_are_natural_units_whichever_sign_was_stored(sign):
    card = _card("regression", {"mae": sign * 12.3456, "rmse": sign * 1834.2, "r2": 0.61}, column="amount")
    h = _build(card).headline
    assert h.numbers.typical_error == pytest.approx(12.3456) and h.numbers.large_miss_error == pytest.approx(1834.2)
    assert h.sentences == ["In the final test, the model's predictions of amount were off by about 12.3 on average."]
    assert h.counts_text == "200 rows tested; a second measure that counts big misses more is about 1,834."
    assert h.numbers.worse_than_average is False
    worse = _build(_card("regression", {"mae": 5.0, "r2": -0.2}, column="amount")).headline
    assert worse.sentences[-1] == "That is worse than always guessing the average amount." and worse.numbers.worse_than_average


def test_missing_withheld_and_no_model_states():
    missing = _build(_card("binary", None, status="missing"))
    assert missing.state == "no_result" and missing.message == o.NO_RESULT
    assert missing.headline.status == "missing" and missing.headline.sentences == [] and missing.headline.numbers is None
    assert missing.how_built is not None  # the card exists; only the final test is missing
    assert o.LIMIT_TESTED_ONCE not in missing.limits and o.LIMIT_CAN_BE_WRONG in missing.limits
    locked_out = _build(None)  # no locked evidence: no card at all
    assert locked_out.state == "no_result" and locked_out.how_built is None
    withheld = _build(_card("binary", BINARY), include_final=False)
    assert withheld.state == "ready" and withheld.headline.status == "withheld"
    assert withheld.headline.numbers is None and withheld.headline.sentences == []
    assert withheld.headline.basis == o.WITHHELD and o.LIMIT_TESTED_ONCE not in withheld.limits
    assert o.LIMIT_TESTED_ONCE in _build(_card("binary", BINARY)).limits
    assert o.APPROVED == "Approved for scoring by a person." and "workspace" not in o.APPROVED
    assert o.NOT_APPROVED == "No person has approved this model for scoring yet."
    none = o.build_outcomes(project_name="Churn", saved_at=None, approved_at=None, card=None, weekly_list=None,
                            include_final=True)
    assert none.state == "no_model" and none.message == o.NO_MODEL
    assert none.model_in_use is None and none.headline is None and none.limits == []
    assert none.boundary == o.BOUNDARY == "DCLab scores and explains; it does not contact customers or take business actions."


def test_how_built_three_sentences_and_test_designs():
    hb = _build(_card("binary", BINARY, positive="yes")).how_built
    assert hb.sentences == [
        'It learned from 800 of the 1,000 rows in your data to predict whether a row has churned "yes".',
        "It is a set of many small decision trees, each one correcting the trees before it. Within its run, it was "
        "picked by comparing tries on the learning rows, before the final test.",
        "Then 200 rows (the latest 20% of the rows by signup_date) were set aside and used once, at the end, to test it.",
    ]
    for strategy, words in (("random", "a random 20% of the rows"), ("group_disjoint", "with each group kept"),
                            ("stratified_random", "same share of each answer"), ("unknown", None)):
        design = o.design_words(_card("binary", BINARY, strategy=strategy))
        assert (design is None) if words is None else (words in design), (strategy, design)
    assert o.family_words("weird__family") == "a method called weird family"


def test_user_names_are_one_capped_line_without_quotes():
    name = 'Churn "q3"\n\x1b[31m\u202egnp.exe\u2066x\u2069\u2028y\u202a' + "x" * 200
    cleaned = o.clean(name)
    assert '"' not in cleaned and "\n" not in cleaned and "\x1b" not in cleaned and len(cleaned) <= 60
    assert not any(ch in cleaned for ch in "\u2028\u2029\u202a\u202b\u202c\u202d\u202e\u2066\u2067\u2068\u2069")
    assert o.display_name("Churn") == "Churn model" and o.display_name("Churn model") == "Churn model"


def test_every_template_keeps_the_business_words():
    """Loop over every sentence the builders can produce, with neutral names."""

    weekly = o.OutcomesList(id=uuid4(), completed_at=NOW, rows=1234, has_reasons=True, download_path="/v1/x",
                            text=o.list_text(1234, has_reasons=True, top_k=3))
    bodies = [
        _build(_card("binary", BINARY, positive="yes", unit="customer"), weekly=weekly, approved=NOW),
        _build(_card("binary", {**BINARY, "recall": 0.0, "precision": 0.0, "specificity": 1.0})),
        _build(_card("multiclass", {"balanced_accuracy": 0.5}, column="tier")),
        _build(_card("regression", {"mae": -3.0, "rmse": -4.0, "r2": -0.1}, column="amount")),
        _build(_card("binary", None, status="missing")), _build(None), _build(_card("binary", BINARY), include_final=False),
        o.build_outcomes(project_name="P", saved_at=None, approved_at=None, card=None, weekly_list=None, include_final=True),
    ]
    texts = [text for body in bodies for text in _sentences(body)]
    texts += [*o.FAMILY_WORDS.values(), *o.TEST_DESIGN_WORDS.values(), o.LIMIT_REASONS, o.APPROVED, o.NOT_APPROVED]
    texts += [o.design_words(_card("binary", BINARY, strategy=s)) or "" for s in o.TEST_DESIGN_WORDS]
    assert len(texts) > 40
    _assert_words(texts)
    assert o.LIMIT_REASONS in bodies[0].limits and o.LIMIT_REASONS not in bodies[1].limits
    assert "fresh" not in o.LIMIT_TESTED_ONCE and "once" in o.LIMIT_TESTED_ONCE


def test_key_set_is_exact():
    body = _build(_card("binary", BINARY)).model_dump(mode="json")
    assert set(body) == TOP_KEYS
    assert set(body["model_in_use"]) == {"display_name", "saved_at", "approved_at", "approval_text"}
    assert set(body["headline"]) == {"status", "task", "sentences", "counts_text", "basis", "numbers"}
    assert set(body["how_built"]) == {"sentences", "rows", "learned_rows", "tested_rows", "target", "method", "test_design"}
    assert set(o.OutcomesList.model_fields) == {"id", "completed_at", "rows", "has_reasons", "text", "download_path"}
    assert set(o.OutcomesProjectList.model_fields) == {"items"} and set(o.OutcomesProjectChoice.model_fields) == {"id", "name"}


# --- the route over a real run ----------------------------------------------------------


def _get(client, setup, project_id, headers=None, status=200):  # noqa: F811
    response = client.get(f"/v1/projects/{project_id}/outcomes", headers=headers or _h(setup))
    assert response.status_code == status, response.text
    if status == 200:
        assert response.headers["ETag"] and response.headers["Cache-Control"] == "private, no-store"
    return response.json()


def _assert_private_free(body: dict, *, allowed_ids: set[str]) -> None:
    text = _all_text(body)
    assert set(UUID_RE.findall(text)) <= allowed_ids, set(UUID_RE.findall(text)) - allowed_ids
    assert "@" not in text and not re.search(r"\b[0-9a-f]{64}\b", text)
    assert set(body) == TOP_KEYS


def test_route_over_a_real_run(client, db_session, setup, monkeypatch):  # noqa: F811
    db = db_session
    frame = _frame("binary", n=200)
    experiment, mv_id = _trained(client, db, setup, frame, "label")
    pid = experiment.project_id

    body = _get(client, setup, pid)
    assert body["state"] == "ready" and body["headline"]["status"] == "reported" and body["headline"]["task"] == "binary"
    assert body["model_in_use"]["approved_at"] is None and body["model_in_use"]["approval_text"] == o.NOT_APPROVED
    assert body["weekly_list"] is None and body["weekly_list_note"] == o.NO_LIST
    card = client.get(f"/v1/model-versions/{mv_id}/card", headers=_h(setup)).json()
    final = card["final_evaluation"]["metrics"]
    numbers = body["headline"]["numbers"]
    assert numbers["recall"] == final["recall"] and numbers["precision"] == final["precision"]
    assert numbers["rows_tested"] == card["split"]["evaluation_rows"]
    if numbers["positives"] is not None:  # exact counts agree with the stored rates
        assert numbers["found"] == round(final["recall"] * numbers["positives"])
    assert len(body["how_built"]["sentences"]) == 3
    _assert_words(body["headline"]["sentences"] + body["how_built"]["sentences"] + body["limits"])
    _assert_private_free(body, allowed_ids=set())

    # The project choice: id and name only, projects with a model in use only, this workspace only.
    fresh = create_project(db, actor=setup["alpha_admin"], workspace_id=setup["alpha"].id, name="No model yet")
    db.commit()
    choices = client.get("/v1/outcomes/projects", headers=_h(setup))
    assert choices.status_code == 200 and choices.headers["Cache-Control"] == "private, no-store"
    items = choices.json()["items"]
    assert set(choices.json()) == {"items"} and all(set(item) == {"id", "name"} for item in items)
    assert str(pid) in {item["id"] for item in items} and str(fresh.id) not in {item["id"] for item in items}
    other = client.get("/v1/outcomes/projects", headers=_h(setup, setup["beta_admin"], workspace=setup["beta"])).json()
    assert str(pid) not in {item["id"] for item in other["items"]}

    # Every workspace reader sees the same view: a viewer and a business user (client role).
    viewer = _member(db, setup, WorkspaceRole.VIEWER)
    business = create_user(db, email=f"p76a-client-{uuid4().hex}@test.invalid", password="test-password",
                           role=UserRole.CLIENT_USER, workspace_id=setup["alpha"].id)
    db.commit()
    assert _get(client, setup, pid, _h(setup, viewer))["headline"] == body["headline"]
    assert _get(client, setup, pid, _h(setup, business))["headline"] == body["headline"]
    assert client.get("/v1/outcomes/projects", headers=_h(setup, business)).json() == choices.json()

    # Tokens: same view, final test withheld (no final value anywhere in the JSON).
    token = create_service_token(db, creator=setup["alpha_admin"], workspace_id=setup["alpha"].id, name="r",
                                 scopes=("read",), expires_in_days=1, current_password="test-password")[1]
    db.commit()
    agent = _get(client, setup, pid, {"Authorization": f"Bearer {token}", "X-Workspace-Id": str(setup["alpha"].id)})
    assert agent["headline"]["status"] == "withheld" and agent["headline"]["numbers"] is None
    assert agent["how_built"] == body["how_built"]
    token_headers = {"Authorization": f"Bearer {token}", "X-Workspace-Id": str(setup["alpha"].id)}
    assert client.get("/v1/outcomes/projects", headers=token_headers).json() == choices.json()
    for key in ("recall", "precision"):
        assert repr(final[key]) not in _all_text(agent) or final[key] in (0.0, 1.0)

    # Other tenants: 404 both ways, indistinguishable from a missing project.
    _assert_envelope(client.get(f"/v1/projects/{pid}/outcomes",
                                headers=_h(setup, setup["beta_admin"], workspace=setup["beta"])), 404, "not_found")
    _assert_envelope(client.get(f"/v1/projects/{setup['beta_project'].id}/outcomes", headers=_h(setup)), 404, "not_found")

    # The list is the newest COMPLETED scoring of the model in use.
    scoring = _upload(client, setup, frame.drop(columns=["label"]).head(30))
    first = _predict(client, setup, mv_id, scoring["id"]).json()["id"]
    assert _work(db, first).status == "completed"
    queued = _predict(client, setup, mv_id, scoring["id"]).json()["id"]  # never worked: stays queued
    listed = _get(client, setup, pid, _h(setup, business))
    assert listed["weekly_list"]["id"] == first and listed["weekly_list"]["rows"] == 30
    assert listed["weekly_list"]["download_path"] == f"/v1/predictions/{first}/download"
    assert listed["weekly_list_note"] is None
    assert (o.LIMIT_REASONS in listed["limits"]) is listed["weekly_list"]["has_reasons"]
    assert listed["weekly_list"]["text"].startswith("The latest list has 30 rows, each with a score")
    _assert_private_free(listed, allowed_ids={first})
    # The business user downloads it through the existing authorized route.
    download = client.get(listed["weekly_list"]["download_path"], headers=_h(setup, business))
    assert download.status_code == 200 and download.headers["content-disposition"].startswith("attachment")
    assert _work(db, queued).status == "completed"
    assert _get(client, setup, pid)["weekly_list"]["id"] == queued

    # A person approves the model in use: the approval date appears.
    refs = {r["ref_kind"]: r for r in client.get(f"/v1/projects/{pid}/refs", headers=_h(setup)).json()["items"]}
    model = db.get(ModelVersion, mv_id)
    companions = [{"ref_kind": "feature_recipe", "target_id": str(model.feature_set_version_id),
                   "expected_version": refs["feature_recipe"]["version"]}] if "feature_recipe" in refs else []
    approve = client.post(
        f"/v1/projects/{pid}/refs/champion_model",
        json={"target_id": str(mv_id), "rationale": "approved for scoring", "companion_moves": companions,
              "evidence_refs": [{"kind": "candidate", "id": str(model.selected_candidate_id), "scope": "final_holdout"}]},
        headers=_h(setup, key=_key(), **{"If-Match": refs["champion_model"]["etag"]}),
    )
    assert approve.status_code == 200, approve.text
    approved = _get(client, setup, pid)["model_in_use"]
    assert approved["approved_at"] is not None and approved["approval_text"] == o.APPROVED

    # No locked evidence for the model in use: a plain empty state, never invented numbers.
    def unavailable(*_args, **_kwargs):
        raise ExperimentRequestError("model_card_unavailable", "no locked evidence", status_code=409)

    monkeypatch.setattr(outcomes_service, "model_card_read", unavailable)
    empty = _get(client, setup, pid)
    assert empty["state"] == "no_result" and empty["message"] == o.NO_RESULT and empty["how_built"] is None
    assert empty["headline"]["numbers"] is None and empty["weekly_list"]["id"] == queued


def test_no_model_in_use_and_unknown_project(client, db_session, setup):  # noqa: F811
    project = create_project(db_session, actor=setup["alpha_admin"], workspace_id=setup["alpha"].id, name="Fresh")
    db_session.commit()
    body = _get(client, setup, project.id)
    assert body["state"] == "no_model" and body["message"] == o.NO_MODEL and body["project_name"] == "Fresh"
    assert body["model_in_use"] is None and body["headline"] is None and body["weekly_list"] is None
    assert body["boundary"] == o.BOUNDARY
    _assert_envelope(client.get(f"/v1/projects/{uuid4()}/outcomes", headers=_h(setup)), 404, "not_found")
    _assert_envelope(client.get(f"/v1/projects/{project.id}/outcomes"), 401, "unauthenticated")
    assert UUID(str(project.id))
    assert db_session.scalar(select(ModelVersion.id).where(ModelVersion.project_id == project.id)) is None
