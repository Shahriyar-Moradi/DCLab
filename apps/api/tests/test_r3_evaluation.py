"""P6.8-A: R3 evaluation harness and trust levels (ADR 0008 §3, §4, §8; ADR 0009 §2.6, §2.9).

Calibration and interval math against hand-computed values; the promotion and demotion
rules at their boundaries; the benchmark corpus and the AI-off ablation; the first
platform levels (every registry key, STATUS.md table); auto-demotion from planted
incidents (safety → L0 + switch off, drift → one level; human-reversible only); tenant
scope of the workspace evidence; the ``dclab r3`` CLI.
"""

from __future__ import annotations

import json
import re
from dataclasses import asdict
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from sqlalchemy import text

from app.agents.governance import incidents as inc
from app.agents.governance import policy as policy_module
from app.agents.governance.decision_points import REGISTRY
from app.agents.governance.policy import (
    GovernanceNotPermitted,
    _level_head,
    accept_level,
    effective_level,
    propose_level,
    set_level,
)
from app.agents.governance.policy import GovernanceError, GovernanceNotFound, accept_policy, propose_policy
from app.agents.governance.switches import SwitchHeldByIncident, effective_switches, re_enable
from app.agents.semantic.policy import apply
from app.agents.semantic.releases import RELEASES, sync_jev_releases
from app.db.models import UserRole
from app.services import r3_evaluation_service as r3
from app.services.auth_service import create_user
from test_ai_governance import _level_row as _platform_row
from test_ai_governance import _policy, _promote, gov  # noqa: F401 - fixture

STATUS = Path(__file__).resolve().parents[3] / "docs" / "mvp" / "STATUS.md"
ROLE, IDENT = "column.semantic_role", "column.is_identifier"


# --- math, hand-computed ------------------------------------------------------------------


def test_wilson_bounds_match_the_adr_0008_reachable_n_and_a_hand_computed_interval():
    assert r3.wilson(189, 189)[0] >= 0.98 > r3.wilson(188, 188)[0]
    assert r3.wilson(279, 280)[0] >= 0.98 > r3.wilson(278, 279)[0]
    assert r3.wilson(96, 100)[0] >= 0.90 > r3.wilson(95, 100)[0]
    lb, ub = r3.wilson(8, 10)  # p = 0.8, n = 10, z = 1.96 by hand
    assert (round(lb, 4), round(ub, 4)) == (0.4902, 0.9433)
    assert r3.wilson(0, 0) == (0.0, 1.0)


def test_ece_and_reliability_table_hand_computed():
    table = r3.reliability([0.9, 0.8, 0.6, 0.7], [True, True, False, True], bins=2)
    # equal-mass bins: {0.6, 0.7} acc 0.5 conf 0.65 -> 0.15; {0.8, 0.9} acc 1.0 conf 0.85 -> 0.15; ECE = 0.15
    assert table["ece"] == 0.15 and [b["accuracy"] for b in table["bins"]] == [0.5, 1.0]
    assert [b["confidence"] for b in table["bins"]] == [0.65, 0.85] and table["gate"] is False
    assert r3.reliability([], [])["ece"] is None


def test_weighted_precision_uses_the_kish_effective_sample_size():
    weighted = r3.weighted_precision([(True, 1.0)] * 50 + [(True, 4.0)] * 10)  # n_eff = 90² / 210
    assert (weighted["n"], weighted["n_eff"], weighted["errors"], weighted["precision"]) == (60, 38.57, 0, 1.0)
    assert weighted["lb"] < r3.weighted_precision([(True, 1.0)] * 60)["lb"]
    assert r3.weighted_precision([])["precision"] is None


def test_paired_gap_bootstraps_many_clusters_and_uses_t_for_few():
    many = {f"d{i}": [0.02 * (i % 3) - 0.01] * 10 for i in range(30)}
    boot = r3.paired_gap(many, seed=1)
    few = r3.paired_gap({k: many[k] for k in list(many)[:6]}, seed=1)
    assert boot["method"] == "cluster_bootstrap" and boot["lb"] <= boot["point"] <= boot["ub"]
    assert few["method"] == "t_cluster_means" and few["ub"] - few["lb"] > boot["ub"] - boot["lb"]
    assert set(boot["p_values"]) == {"0.05", "0.01"} and boot["p_values"]["0.05"] <= boot["p_values"]["0.01"]
    assert r3.paired_gap({})["clusters"] == 0 and r3.paired_gap({})["lb"] is None


def test_holm_correction():
    assert r3.holm({"a": 0.01, "b": 0.04, "c": 0.03}) == {"a": 0.03, "c": 0.06, "b": 0.06}


# --- the rule engine at the boundaries ------------------------------------------------------


def _entry(key=IDENT, current=0, **overrides):
    band = {"n": 150, "n_eff": 150.0, "precision": 1.0, "lb": 0.98, "ub": 0.995}
    source = {"n": 300, "total": 300, "unavailable_rate": 0.0, "datasets": 25,
              "gap": {"lb": -0.01, "ub": 0.1, "clusters": 25, "p_values": {}},
              "in_band": {"n": 300, "n_eff": 300.0, "precision": 1.0, "lb": 0.98, "ub": 0.995},
              "bands": {"yes": dict(band), "no": dict(band)},
              "deployed_policy": {"n": 200, "n_eff": 200.0, "precision": 1.0, "lb": 0.98, "ub": 1.0}}
    entry = {"key": key, "ai_kind": REGISTRY[key].ai_kind, "cap": REGISTRY[key].cap, "max_level": REGISTRY[key].cap,
             "current_platform_level": current, "sources": {"sealed": json.loads(json.dumps(source)),
                                                           "blind": json.loads(json.dumps(source))},
             "operations": {"proposals": 100, "validator_rejection_rate": 0.02, "citation_validity": 1.0,
                            "within_budget": True}, "open_incidents": {}}
    for path, value in overrides.items():
        node = entry
        *parents, leaf = path.split("__")
        for part in parents:
            node = node[part]
        node[leaf] = value
    return entry


_PASSED = {"checks": {f"{source}_{gate}": True for source in ("sealed", "blind")
                      for gate in ("non_inferiority", "precision", "band_edge", "holm")}}
PREVIOUS = {"promotion": {"L1": _PASSED, "L2": _PASSED}}


@pytest.mark.parametrize("path, below, above", [
    ("sources__sealed__n", 99, 100), ("sources__blind__in_band__n", 29, 30),
    ("sources__sealed__gap__lb", -0.0501, -0.05), ("sources__blind__gap__lb", -0.0501, -0.05),
    ("sources__sealed__bands__yes__lb", 0.8999, 0.90), ("sources__blind__bands__no__lb", 0.8999, 0.90),
    ("operations__validator_rejection_rate", 0.0501, 0.05), ("operations__proposals", 49, 50),
])
def test_l0_to_l1_gates_just_below_and_just_above(path, below, above):
    assert r3.promotion_check(_entry(**{path: above}), 1, previous=PREVIOUS).allowed
    verdict = r3.promotion_check(_entry(**{path: below}), 1, previous=PREVIOUS)
    assert not verdict.allowed and len(verdict.reasons) == 1, verdict.reasons


def test_holm_adjusted_p_values_band_edges_and_empty_bands_gate():
    holm_ok = {f"{IDENT}:sealed": 0.05, f"{IDENT}:blind": 0.01}
    assert r3.promotion_check(_entry(), 1, previous=PREVIOUS, holm=holm_ok).allowed
    verdict = r3.promotion_check(_entry(), 1, previous=PREVIOUS, holm={**holm_ok, f"{IDENT}:sealed": 0.0501})
    assert verdict.reasons == [f"sealed_holm: Holm-adjusted p 0.0501 > {r3.ALPHA}"]
    empty = _entry(sources__sealed__bands__yes={"n": 0, "n_eff": 0.0, "lb": None, "ub": None})
    assert "sealed_precision" in " ".join(r3.promotion_check(empty, 1, previous=PREVIOUS).reasons)
    # the leakage release has no "no" band (no_at None): only the flagging band is gated
    leak = _entry("feature.leakage_suspect", sources__sealed__bands__no={"n": 0, "n_eff": 0.0, "lb": None, "ub": None},
                  sources__blind__bands__no={"n": 0, "n_eff": 0.0, "lb": None, "ub": None})
    assert r3.promotion_check(leak, 1, previous=PREVIOUS).allowed
    bad = {"n": 150, "n_eff": 150.0, "precision": 0.1, "lb": 0.06, "ub": 0.16}
    assert r3.demotion_check(_entry("feature.leakage_suspect", 1, sources__sealed__bands__no=bad), 1).level is None
    assert r3.demotion_check(_entry("feature.leakage_suspect", 1, sources__sealed__bands__yes=bad), 1).level == 0
    assert r3.promotion_check(_entry(ROLE, sources__sealed__in_band__lb=0.80), 1, previous=PREVIOUS).allowed
    assert not r3.promotion_check(_entry(ROLE, sources__sealed__in_band__lb=0.7999), 1, previous=PREVIOUS).allowed


def test_l1_needs_two_runs_no_data_exposure_a_known_budget_and_one_step():
    assert not r3.promotion_check(_entry(), 1).allowed  # no previous run: stability check fails
    assert r3.promotion_check(_entry(), 1, previous=PREVIOUS).allowed
    assert not r3.promotion_check(_entry(open_incidents={"data_exposure": 1}), 1, previous=PREVIOUS).allowed
    assert not r3.promotion_check(_entry(operations__within_budget=None), 1, previous=PREVIOUS).allowed
    assert not r3.promotion_check(_entry(current=1), 1, previous=PREVIOUS).allowed  # already there
    assert not r3.promotion_check(_entry(), 2, previous=PREVIOUS).allowed  # L2 from L0 skips a step
    agent = _entry("experiment.review", operations__citation_validity=0.989)
    assert "citation_validity" in " ".join(r3.promotion_check(agent, 1, previous=PREVIOUS).reasons)


@pytest.mark.parametrize("path, below, above", [
    ("sources__sealed__datasets", 19, 20), ("sources__sealed__n", 299, 300), ("sources__blind__n", 99, 100),
    ("sources__sealed__gap__lb", -0.0101, -0.01), ("sources__sealed__deployed_policy__lb", 0.9799, 0.98),
    ("operations__validator_rejection_rate", 0.0201, 0.02), ("operations__proposals", 99, 100),
])
def test_l1_to_l2_gates_for_semantic_role(path, below, above):
    assert r3.promotion_check(_entry(ROLE, 1, **{path: above}), 2, previous=PREVIOUS).allowed
    assert not r3.promotion_check(_entry(ROLE, 1, **{path: below}), 2, previous=PREVIOUS).allowed


def test_l2_only_where_adr_0008_allows_it_and_l3_only_for_ops_diagnose():
    assert not r3.promotion_check(_entry(IDENT, 1), 2, previous=PREVIOUS).checks["within_cap"]
    assert not r3.promotion_check(_entry("experiment.review", 1), 2, previous=PREVIOUS).allowed
    assert not r3.promotion_check(_entry(ROLE, 1, open_incidents={"drift": 1}), 2, previous=PREVIOUS).allowed
    verdict = r3.promotion_check(_entry("ops.diagnose", 2), 3)
    assert verdict.checks["ops_diagnose_only"] and not verdict.allowed
    assert not r3.promotion_check(_entry("proposal.completeness"), 1, previous=PREVIOUS).checks["within_cap"]


def test_demotion_fires_on_upper_bound_harm_at_the_levels_own_bar_never_on_thin_evidence():
    assert r3.demotion_check(_entry(current=1, sources__sealed__gap__ub=-0.0501), 1).level == 0
    assert r3.demotion_check(_entry(current=1, sources__sealed__gap__ub=-0.05), 1).level is None
    weak_band = {"n": 150, "n_eff": 150.0, "precision": 0.8, "lb": 0.73, "ub": 0.86}  # exact UB ~0.85 < 0.90
    assert r3.demotion_check(_entry(current=1, sources__blind__bands__yes=weak_band), 1).level == 0
    assert r3.demotion_check(_entry(ROLE, 2, sources__sealed__gap__ub=-0.0101), 2).level == 1
    assert r3.demotion_check(_entry(ROLE, 2, sources__sealed__gap__ub=-0.02), 2).level == 1  # L2 bar is -1 pt
    weak_policy = {"n": 200, "n_eff": 200.0, "precision": 0.95, "lb": 0.91, "ub": 0.97}  # exact UB ~0.97 < 0.98
    assert r3.demotion_check(_entry(ROLE, 2, sources__sealed__deployed_policy=weak_policy), 2).level == 1
    assert r3.demotion_check(_entry(ROLE, 1, sources__sealed__in_band__ub=0.5), 1).level is None  # choice point
    assert r3.demotion_check(_entry(current=0, sources__sealed__gap__ub=-0.9), 0).level is None
    # exact Clopper-Pearson upper bounds, no sample-size floor: 0/1 right is no evidence (UB 0.95),
    # 4/21 right is (UB ~0.40); a single cluster never carries the gap clause
    assert round(r3.clopper_pearson_upper(0, 1), 4) == 0.95 and round(r3.clopper_pearson_upper(4, 21), 2) == 0.38
    one = _entry(current=1, sources__sealed__bands__yes={"n": 1, "n_eff": 1.0, "precision": 0.0, "lb": 0.0, "ub": 0.79})
    assert r3.demotion_check(one, 1).level is None
    few = _entry(current=1, sources__sealed__bands__yes={"n": 21, "n_eff": 21.0, "precision": 0.1905, "lb": 0.08, "ub": 0.40})
    assert r3.demotion_check(few, 1).level == 0
    assert r3.demotion_check(_entry(current=1, sources__sealed__gap={"ub": -0.5, "clusters": 1}), 1).level is None
    strong = _entry(ROLE, 2, sources__sealed__deployed_policy={"n_eff": 99.0, "precision": 0.5, "ub": 0.6})
    assert r3.demotion_check(strong, 2).level == 1


# --- corpus, ablation, report ----------------------------------------------------------------


@pytest.fixture(scope="module")
def corpus():
    return r3.benchmark_corpus("quick", tasks=["sk-iris"], seed=42)


def test_corpus_is_labeled_planted_partitioned_and_holdout_free(corpus):
    assert {c.purpose for c in corpus} == set(r3.JEV_POINTS) and {c.variant for c in corpus} == {"plain", "obfuscated"}
    assert all(c.label is not None and c.partition in ("sealed", "development") for c in corpus)
    assert {c.dataset for c in corpus} == {"sk-iris"}  # both name variants share the task's rows: one cluster
    roles = {c.column: c.label for c in corpus if c.purpose == ROLE and c.variant == "plain"}
    assert (roles["row_id"], roles["signup_date"], roles["notes"], roles["segment_code"]) == (
        "identifier", "datetime", "free_text", "categorical_code")
    # the rule is the deployed role map (datetime / free text / identifier), not a rewrite
    rules = {c.column: c.rule for c in corpus if c.purpose == ROLE and c.variant == "plain"}
    assert (rules["row_id"], rules["signup_date"], rules["notes"]) == ("identifier", "datetime", "free_text")
    leak = {c.column: c for c in corpus if c.purpose == "feature.leakage_suspect" and c.variant == "plain"}
    assert leak["outcome_flag"].label is True and leak["outcome_flag"].rule is True  # the auditor flags the copy
    assert "row_id" not in leak  # the deployed point never asks about identifier columns
    idents = {c.column: c.rule for c in corpus if c.purpose == IDENT and c.variant == "plain"}
    assert idents["row_id"] is True and idents["signup_date"] is False  # the engineered role map, not a raw guess
    # availability comes from the auditor, never from the labels (it would give the answer away)
    assert {c.fields["availability"] for c in leak.values()} <= {"before_prediction", "after_outcome", "unknown"}
    assert leak["outcome_flag"].fields["availability"] == leak["notes"].fields["availability"]
    text = json.dumps([asdict(c) for c in corpus])
    assert not re.search(r"holdout|final_test", text, re.IGNORECASE)
    assert r3.partition_for("sk-iris", IDENT) == "development"  # a development task never becomes sealed
    assert {r3.partition_for(f"t{i}", IDENT) for i in range(40)} == {"sealed", "development"}
    # keyed by the purpose's own release: another purpose's bump never re-draws this one
    assert [r3.partition_for(f"t{i}", IDENT) for i in range(40)] != [r3.partition_for(f"t{i}", ROLE) for i in range(40)]
    # one-way across a version bump: development tasks stay development, only sealed ones may move
    v1 = {f"t{i}": r3.partition_for(f"t{i}", IDENT, version=1) for i in range(60)}
    v2 = {f"t{i}": r3.partition_for(f"t{i}", IDENT, version=2) for i in range(60)}
    assert all(v2[k] == "development" for k, v in v1.items() if v == "development")
    assert sum(v == "development" for v in v2.values()) > sum(v == "development" for v in v1.values())
    assert all(c.source == ("sk-iris" if c.variant == "plain" else "sk-iris#obf") for c in corpus)
    accepts = {c.column: c.accepts for c in corpus if c.purpose == ROLE and c.variant == "plain"}
    assert accepts["row_id"] == () and "numeric" not in accepts["segment_code"]  # the deployed RoleValidator


def test_plant_shuffles_rows_drops_constants_and_labels_binary_columns_as_codes():
    import pandas as pd

    frame = pd.DataFrame({"x": range(40), "flag": [0, 1] * 20, "truth": [True, False] * 20, "const": [7] * 40,
                          r3.TARGET: [0] * 20 + [1] * 20})
    planted, labels = r3.plant(frame, "binary", seed=3)
    assert "const" not in planted.columns and "const" not in labels
    # the deployed role map: a numeric 0/1 column is numeric, a boolean dtype is a categorical code
    assert (labels["flag"]["role"], labels["truth"]["role"], labels["x"]["role"]) == ("numeric", "categorical_code", "numeric")
    assert abs(planted["row_id"].corr(planted[r3.TARGET].astype(float))) < 0.5  # no longer a target proxy
    assert list(planted["x"]) != list(range(40))  # shuffled (seeded)


def test_ablation_ai_off_equals_the_rule_baseline_and_a_run_is_deterministic(corpus):
    first = r3.run_r3(corpus, r3.ScriptedAnswerer(), candidate="fake")
    second = r3.run_r3(corpus, r3.ScriptedAnswerer(), candidate="fake")
    assert first["digest"] == second["digest"] and first["run_id"] != second["run_id"]
    assert first["ablation"]["method"] == "rule_replay" and all(v["equal"] for v in first["ablation"]["points"].values())
    off = r3.evaluate(corpus, r3.ai_off)
    for key in r3.JEV_POINTS:
        cases = [c for c in corpus if c.purpose == key]
        assert cases and off[key]["all"]["total"] == len(cases) and off[key]["all"]["unavailable_rate"] == 1.0
        assert off[key]["all"]["n"] == 0  # unanswered cases never count as evidence
        assert off[key]["all"]["ai_policy_accuracy_all"] == off[key]["all"]["rule_accuracy_all"]
        for scored in r3.score(RELEASES[key], cases, r3.ai_off(RELEASES[key], cases)):  # per case, every level
            assert scored.agreement == "unavailable" and scored.policy_correct == scored.rule_correct
            for level in range(0, 3):
                assert apply(level, "unavailable", scored.case.rule, None, validator_ok=True).value_used == scored.case.rule
        point = first["points"][key]
        assert point["delta_vs_rule_replay"]["ai_off_equals_rule"] and point["current_platform_level"] == 0
        assert not point["promotion"]["L1"]["allowed"]  # fake evidence never promotes
    assert set(first["points"]) == set(REGISTRY)
    wrong = r3.run_r3(corpus, r3.ScriptedAnswerer(accuracy=0.0, abstain=0.0), candidate="wrong")
    sealed_or_all = wrong["points"][IDENT]["sources"]["all"]
    assert sealed_or_all["ai_policy_accuracy"] < sealed_or_all["rule_accuracy"]
    harmed = dict(wrong["points"][IDENT], current_platform_level=1)
    harmed["sources"]["sealed"] = sealed_or_all
    # an always-wrong flagging band is exact evidence of harm even on one small dataset (Clopper-Pearson)
    assert sealed_or_all["gap"]["clusters"] == 1 and sealed_or_all["bands"]["yes"]["errors"] > 0
    assert r3.demotion_check(harmed, 1).level == 0
    assert not wrong["points"][IDENT]["promotion"]["L1"]["allowed"]
    # a source dominated by unavailable answers: n counts answered cases only and the availability gate fails
    flaky = r3.evaluate(corpus, lambda release, cases: [None if i % 2 else a for i, a in enumerate(
        r3.ScriptedAnswerer()(release, cases))])
    half = flaky[IDENT]["all"]
    assert half["unavailable_rate"] == 0.5 and half["n"] == half["total"] - half["total"] // 2
    thin = dict(_entry(IDENT), sources={"sealed": {**_entry()["sources"]["sealed"], "unavailable_rate": 0.5},
                                        "blind": _entry()["sources"]["blind"]})
    assert "sealed_availability" in " ".join(r3.promotion_check(thin, 1, previous=PREVIOUS).reasons)
    markdown = r3.render_markdown(first)
    assert "| `column.semantic_role` |" in markdown and "Rule replay" in markdown
    json.dumps(first)  # machine-readable for P6.11-A


# --- database: levels, stored runs, incidents, tenancy, CLI ----------------------------------


def _status_rows() -> dict[str, str]:
    section = STATUS.read_text(encoding="utf-8").split("## Agent / Jev release decisions", 1)[1]
    section = section.split("\n## ", 1)[0]
    rows = {}
    for line in section.splitlines():
        match = re.match(r"\|\s*`([a-z0-9_.]+)`.*?\|\s*(L[0-3])", line)
        if match:
            rows[match.group(1)] = match.group(2)
    return rows


def _store_live(db, corpus, key, *, when: str, passing: bool = True, salt: str = "") -> dict:
    """A stored run standing in for a real-provider run (written through the same ``store_run``
    a live ``dclab r3 run`` uses); ``passing`` plants gate-meeting metrics for ``key``. The run
    claims no answered cases (``n`` 0 outside ``key``), so the ledger cross-check is 0 ≥ 0."""

    report = r3.run_r3(corpus, r3.ScriptedAnswerer(), candidate="fake")
    report["candidate"], report["live"], report["created_at"] = "live:jev:test", True, when
    for point in r3.JEV_POINTS:  # no real samples were left: claim none
        for source in report["points"][point]["sources"].values():
            source["n"] = 0
    if passing:
        report["points"][key] = {**_entry(key), **PREVIOUS, "salt": salt}  # metrics + the verdict a passing run records
        report["holm_adjusted_p_values"].update({f"{key}:sealed": 0.01, f"{key}:blind": 0.01})
    report["digest"] = r3.report_digest(report)
    report["run_digest"] = r3.run_digest(report)
    r3.store_run(db, report)
    db.commit()
    return report


def _second_admin(db):
    return create_user(db, email=f"staff2-{uuid4().hex[:6]}@gov.test", password="pw-12345678",
                       role=UserRole.DCLAB_ADMIN, workspace_id=None)


def test_first_levels_cover_every_registry_key_in_the_database_and_in_status_md(db_session, gov, corpus):
    db = db_session
    report = r3.run_r3(corpus, r3.ScriptedAnswerer(), candidate="fake")
    with pytest.raises(GovernanceNotPermitted):
        r3.record_first_levels(db, report, admin=gov.owner)
    outcome = r3.record_first_levels(db, report, admin=gov.platform_admin)
    db.commit()
    assert set(outcome) == set(REGISTRY) and set(outcome.values()) == {"recorded"}
    assert set(r3.record_first_levels(db, report, admin=gov.platform_admin).values()) == {"kept"}
    for key in REGISTRY:
        head = _level_head(db, None, key)
        assert head is not None and head.level == 0 and head.actor_kind == "human"
        assert head.evidence == [{"kind": "r3_run", "id": report["run_id"]}] and "G6" in head.rationale
    rows = _status_rows()
    missing = sorted(set(REGISTRY) - set(rows))
    assert not missing, f"STATUS.md § Agent / Jev release decisions lacks {missing}"
    assert set(rows[key] for key in REGISTRY) == {"L0"}


def test_a_run_is_stored_as_it_happens_and_a_tampered_or_unknown_run_is_refused(db_session, gov, corpus):
    db = db_session
    report = r3.run_r3(corpus, r3.ScriptedAnswerer(), candidate="fake", db=db)
    db.commit()
    stored = r3.load_run(db, UUID(report["run_id"]))
    assert stored["digest"] == report["digest"] and "workspace_evidence" not in stored and stored["live"] is False
    with pytest.raises(GovernanceNotFound):
        r3.load_run(db, uuid4())  # a made-up run id
    forged = {**stored, "points": {**stored["points"], IDENT: _entry(IDENT)}}  # edited content, old digests
    row = r3.store_run(db, {**forged, "run_id": str(uuid4())})
    db.commit()
    with pytest.raises(GovernanceNotPermitted, match="r3_report_tampered"):
        r3.load_run(db, row.id)
    for statement in ("UPDATE r3_runs SET cases = 1 WHERE id = :id", "DELETE FROM r3_runs WHERE id = :id"):
        with pytest.raises(Exception, match="immutable|mutation"):  # append-only
            db.execute(text(statement), {"id": str(report["run_id"])})
            db.commit()
        db.rollback()
    with pytest.raises(Exception, match="ck_r3_runs_created_at"):  # an operator clock far ahead is refused
        r3.store_run(db, {**stored, "run_id": str(uuid4()), "created_at": "2099-01-01T00:00:00+00:00"})
        db.commit()
    db.rollback()


def test_platform_promotion_needs_a_stored_live_run_the_rule_engine_and_two_admins(db_session, gov, corpus):
    db = db_session
    sync_jev_releases(db)
    db.commit()
    fake = r3.run_r3(corpus, r3.ScriptedAnswerer(), candidate="fake", db=db)  # offline evidence: never promotes
    db.commit()
    with pytest.raises(GovernanceNotPermitted, match="r3_candidate_not_live"):
        r3.propose_promotion(db, run_id=UUID(fake["run_id"]), key=IDENT, level=1, admin=gov.platform_admin)
    with pytest.raises(GovernanceNotFound):
        r3.propose_promotion(db, run_id=uuid4(), key=IDENT, level=1, admin=gov.platform_admin)
    weak = _store_live(db, corpus, IDENT, when="2026-10-01T00:00:00+00:00", passing=False)
    with pytest.raises(GovernanceNotPermitted, match="promotion_evidence_insufficient"):
        r3.propose_promotion(db, run_id=UUID(weak["run_id"]), key=IDENT, level=1, admin=gov.platform_admin)
    first = _store_live(db, corpus, IDENT, when="2026-10-02T00:00:00+00:00")
    with pytest.raises(GovernanceNotPermitted, match="stability_two_runs"):  # the previous run (weak) did not pass
        r3.propose_promotion(db, run_id=UUID(first["run_id"]), key=IDENT, level=1, admin=gov.platform_admin)
    second = _store_live(db, corpus, IDENT, when="2026-10-03T00:00:00+00:00", salt="b")
    assert r3.previous_run(db, second)["run_id"] == first["run_id"]  # the stored run immediately before
    twin = _store_live(db, corpus, IDENT, when="2026-10-03T00:01:00+00:00", salt="b")  # re-stored identical content
    with pytest.raises(GovernanceNotPermitted, match="r3_previous_invalid"):
        r3.propose_promotion(db, run_id=UUID(twin["run_id"]), key=IDENT, level=1, admin=gov.platform_admin)
    with pytest.raises(GovernanceNotPermitted, match="platform_levels_need_admin"):
        r3.propose_promotion(db, run_id=UUID(second["run_id"]), key=IDENT, level=1, admin=gov.owner)
    with pytest.raises(GovernanceNotPermitted, match="platform_level_needs_r3_verification"):  # no direct path
        propose_level(db, actor=gov.platform_admin, workspace_id=None, key=IDENT, level=1, rationale="bypass",
                      prompt_release_id=r3.current_pair(db, IDENT)[0], model_id="jev-1.13.0",
                      evidence=[{"kind": "r3_run", "id": second["run_id"]}])
    verified = policy_module.R3_VERIFIED
    with pytest.raises(GovernanceNotPermitted, match="platform_level_one_step"):  # the policy layer itself
        propose_level(db, actor=gov.platform_admin, workspace_id=None, key=ROLE, level=2, rationale="jump",
                      prompt_release_id=gov.release, model_id="jev-1.13.0", evidence=[{"kind": "r3_run", "id": "x"}],
                      verification=verified)
    with pytest.raises(GovernanceNotPermitted, match="platform_level_needs_r3_run"):
        propose_level(db, actor=gov.platform_admin, workspace_id=None, key=ROLE, level=1, rationale="bare",
                      prompt_release_id=gov.release, model_id="jev-1.13.0", verification=verified)
    proposal = r3.propose_promotion(db, run_id=UUID(second["run_id"]), key=IDENT, level=1, admin=gov.platform_admin)
    db.commit()
    assert (proposal.state, proposal.workspace_id, proposal.level) == ("proposed", None, 1)
    with pytest.raises(GovernanceNotPermitted, match="self_approval_not_allowed"):
        accept_level(db, approver=gov.platform_admin, workspace_id=None, proposal_id=proposal.id)
    with pytest.raises(GovernanceNotPermitted):
        accept_level(db, approver=gov.owner, workspace_id=None, proposal_id=proposal.id)
    # accept re-runs the whole verification: a made-up run id, a run the gates refuse and a stale
    # prompt release behind the evidence are all refused even for a second admin
    admin2 = _second_admin(db)
    bogus = propose_level(db, actor=gov.platform_admin, workspace_id=None, key=ROLE, level=1, rationale="x",
                          prompt_release_id=gov.release, model_id="jev-1.13.0",
                          evidence=[{"kind": "r3_run", "id": str(uuid4())}], verification=verified)
    with pytest.raises(GovernanceNotFound):
        accept_level(db, approver=admin2, workspace_id=None, proposal_id=bogus.id)
    failing = propose_level(db, actor=gov.platform_admin, workspace_id=None, key=IDENT, level=1, rationale="x",
                            prompt_release_id=r3.current_pair(db, IDENT)[0], model_id="jev-1.13.0",
                            evidence=[{"kind": "r3_run", "id": weak["run_id"]}], verification=verified)
    with pytest.raises(GovernanceNotPermitted, match="promotion_evidence_insufficient"):
        accept_level(db, approver=admin2, workspace_id=None, proposal_id=failing.id)
    stale_release = propose_level(db, actor=gov.platform_admin, workspace_id=None, key=IDENT, level=1, rationale="x",
                                  prompt_release_id=gov.release, model_id="jev-1.13.0",  # another purpose's release
                                  evidence=[{"kind": "r3_run", "id": second["run_id"]}], verification=verified)
    with pytest.raises(GovernanceNotPermitted, match="r3_pair_mismatch"):
        accept_level(db, approver=admin2, workspace_id=None, proposal_id=stale_release.id)
    db.commit()
    row = accept_level(db, approver=admin2, workspace_id=None, proposal_id=proposal.id)
    db.commit()
    head = _level_head(db, None, IDENT)
    assert head.id == row.id and head.level == 1 and head.decided_by_user_id == admin2.id
    assert head.evidence == [{"kind": "r3_run", "id": second["run_id"]}] and head.model_id == "jev-1.13.0"


def test_a_stale_run_cannot_repromote_after_an_auto_demotion(db_session, gov, corpus):
    db = db_session
    sync_jev_releases(db)
    db.commit()
    admin2 = _second_admin(db)
    _store_live(db, corpus, IDENT, when="2026-10-02T00:00:00+00:00")
    run = _store_live(db, corpus, IDENT, when="2026-10-03T00:00:00+00:00", salt="b")
    proposal = r3.propose_promotion(db, run_id=UUID(run["run_id"]), key=IDENT, level=1, admin=gov.platform_admin)
    accept_level(db, approver=admin2, workspace_id=None, proposal_id=proposal.id)
    db.commit()
    assert r3._platform_level(db, IDENT) == 1
    incident = inc.open_incident(db, workspace_id=None, kind="drift", subject_kind="decision_point",
                                 subject_key=IDENT, evidence={"disagreement_rate": 0.4})
    db.commit()
    assert r3._platform_level(db, IDENT) == 0 and incident.action == "auto_demote"
    with pytest.raises(GovernanceNotPermitted, match="r3_run_predates_demotion"):  # the exact reproduction
        r3.propose_promotion(db, run_id=UUID(run["run_id"]), key=IDENT, level=1, admin=gov.platform_admin)
    inc.resolve_incident(db, incident_id=incident.id, actor=gov.platform_admin, resolution="investigated")
    db.commit()
    with pytest.raises(GovernanceNotPermitted, match="r3_run_predates_demotion"):  # resolving is not new evidence
        r3.propose_promotion(db, run_id=UUID(run["run_id"]), key=IDENT, level=1, admin=gov.platform_admin)
    fresh = _store_live(db, corpus, IDENT, when=(datetime.now(UTC) + timedelta(minutes=1)).isoformat(), salt="c")
    proposal = r3.propose_promotion(db, run_id=UUID(fresh["run_id"]), key=IDENT, level=1, admin=gov.platform_admin)
    # an incident opened between proposal and acceptance blocks the accept too
    later = inc.open_incident(db, workspace_id=None, kind="validator_rejections", subject_kind="purpose",
                              subject_key=IDENT, evidence={"rejections_24h": 6})
    db.commit()
    assert later.action == "none"  # nothing above L0 to demote
    with pytest.raises(GovernanceNotPermitted, match="open_incident|r3_run_predates_demotion"):
        accept_level(db, approver=admin2, workspace_id=None, proposal_id=proposal.id)
    db.rollback()


def test_the_platform_level_is_keyed_by_the_evidence_pair(db_session, gov):
    db = db_session
    sync_jev_releases(db)
    db.commit()
    release_id, model = r3.current_pair(db, ROLE)
    assert (release_id, model) == (gov.release, "jev-1.13.0")
    # head at L1 for an OLD pair (another model id): L0 for the current pair, so L2 is not one step away
    _platform_row(db, gov, workspace_id=None, level=1, actor_user_id=gov.platform_admin.id,
                  decided_by_user_id=gov.platform_admin.id, prompt_release_id=gov.release, model_id="jev-1.12.0")
    db.commit()
    assert _level_head(db, None, ROLE).level == 1 and r3._platform_level(db, ROLE) == 0
    with pytest.raises(GovernanceNotPermitted, match="platform_level_one_step"):
        propose_level(db, actor=gov.platform_admin, workspace_id=None, key=ROLE, level=2, rationale="bump",
                      prompt_release_id=release_id, model_id=model, evidence=[{"kind": "r3_run", "id": str(uuid4())}],
                      verification=policy_module.R3_VERIFIED)
    assert not r3.promotion_check(_entry(ROLE, current=r3._platform_level(db, ROLE)), 2, previous=PREVIOUS).checks["one_step"]
    # the accept side: L1 for the current pair over an old-pair L1 head IS a raise and is verified
    same_level = propose_level(db, actor=gov.platform_admin, workspace_id=None, key=ROLE, level=1, rationale="pair",
                               prompt_release_id=release_id, model_id=model,
                               evidence=[{"kind": "r3_run", "id": str(uuid4())}], verification=policy_module.R3_VERIFIED)
    inc.open_incident(db, workspace_id=None, kind="manual", subject_kind="decision_point", subject_key=ROLE, evidence={})
    db.commit()
    with pytest.raises(GovernanceError):  # not accepted silently: the verification runs (and fails)
        accept_level(db, approver=_second_admin(db), workspace_id=None, proposal_id=same_level.id)
    db.rollback()
    assert r3._platform_level(db, ROLE) == 0


def test_auto_demotion_from_planted_incidents_is_audited_and_human_reversible_only(db_session, gov):
    db = db_session
    pair = {"prompt_release_id": gov.release, "model_id": "jev-1.13.0"}
    _platform_row(db, gov, workspace_id=None, level=2, actor_user_id=gov.platform_admin.id,
                  decided_by_user_id=gov.platform_admin.id, **pair)  # the platform row R3 + G6 would set
    _promote(db, gov, ROLE, 2, pair)
    assert effective_level(db, gov.ws_a, ROLE, "role_numeric_categorical", **pair) == 2
    # drift: one level, audited by a rule row citing the incident
    drift = inc.open_incident(db, workspace_id=gov.ws_a, kind="drift", subject_kind="decision_point",
                              subject_key=ROLE, evidence={"disagreement_rate": 0.4, "baseline": 0.1})
    db.commit()
    head = _level_head(db, gov.ws_a, ROLE)
    assert (head.level, head.actor_kind, head.actor_rule) == (1, "rule", inc.AUTO_DEMOTE_RULE)
    assert head.evidence == [{"kind": "incident", "id": str(drift.id)}] and head.prompt_release_id == gov.release
    assert (drift.action, drift.action_level_policy_id) == ("auto_demote", head.id)
    assert effective_level(db, gov.ws_a, ROLE, "role_numeric_categorical", **pair) == 1
    assert _level_head(db, gov.ws_b, ROLE) is None and _level_head(db, None, ROLE).level == 2  # scope only
    assert inc.open_incident_counts(db, gov.ws_a, ROLE) == {"drift": 1}
    # safety: L0 and the purpose switch off until an approver resolves the incident
    safety = inc.open_incident(db, workspace_id=gov.ws_a, kind="data_exposure", subject_kind="purpose",
                               subject_key=ROLE, evidence={"finding_id": "f-0"})
    db.commit()
    assert _level_head(db, gov.ws_a, ROLE).level == 0 and safety.action == "switch_off"
    assert effective_switches(db, gov.ws_a).blocking(ai_enabled=True, provider="typesafe", purpose=ROLE)
    with pytest.raises(SwitchHeldByIncident):
        re_enable(db, workspace_id=gov.ws_a, switch_key=f"purpose:{ROLE}", reason="r", actor=gov.owner)
    db.rollback()
    with pytest.raises(GovernanceError):
        inc.resolve_incident(db, incident_id=safety.id, actor=gov.engineer, resolution="no")
    with pytest.raises(GovernanceNotFound):  # another tenant's incident reads as absent
        inc.resolve_incident(db, incident_id=safety.id, actor=gov.owner_b, resolution="other tenant")
    inc.resolve_incident(db, incident_id=safety.id, actor=gov.owner, resolution="redaction fixed")
    db.commit()
    re_enable(db, workspace_id=gov.ws_a, switch_key=f"purpose:{ROLE}", reason="fixed", actor=gov.owner)
    # a rule or a non-approver cannot restore the level; an approver can, through a proposal
    with pytest.raises(Exception, match="rule_only_down"):
        set_level(db, workspace_id=gov.ws_a, key=ROLE, level=1, rationale="r", actor_rule=inc.AUTO_DEMOTE_RULE, **pair)
    db.rollback()
    with pytest.raises(GovernanceNotPermitted):
        set_level(db, workspace_id=gov.ws_a, key=ROLE, level=1, rationale="r", actor=gov.engineer, **pair)
    proposal = propose_level(db, actor=gov.engineer, workspace_id=gov.ws_a, key=ROLE, level=1, rationale="fix", **pair)
    accept_level(db, approver=gov.owner, workspace_id=gov.ws_a, proposal_id=proposal.id)
    db.commit()
    assert effective_level(db, gov.ws_a, ROLE, "role_numeric_categorical", **pair) == 1
    # eval_failure names its target; incidents without a demotion rule or at L0 change nothing
    failed = inc.open_incident(db, workspace_id=gov.ws_a, kind="eval_failure", subject_kind="decision_point",
                               subject_key=ROLE, evidence={"demote_to": 0, "r3_run": "x"})
    assert failed.action == "auto_demote" and _level_head(db, gov.ws_a, ROLE).level == 0
    quiet = inc.open_incident(db, workspace_id=gov.ws_a, kind="provider_failure", subject_kind="provider",
                              subject_key="typesafe", evidence={})
    again = inc.open_incident(db, workspace_id=gov.ws_a, kind="drift", subject_kind="decision_point",
                              subject_key=ROLE, evidence={})
    db.commit()
    assert (quiet.action, again.action, _level_head(db, gov.ws_a, ROLE).level) == ("none", "none", 0)
    assert inc.affected_keys("agent", "experiment_planner") == ["spec.objective", "split.strategy",
                                                                "training.families_budget"]
    assert inc.switch_key_for("experiment.review") == "agent:experiment_critic"
    # a workspace-wide or provider data exposure switches the scope / provider off (never silently nothing)
    whole = inc.open_incident(db, workspace_id=gov.ws_a, kind="data_exposure", subject_kind="workspace",
                              subject_key="redaction", evidence={"finding_id": "f-1"})
    prov = inc.open_incident(db, workspace_id=None, kind="data_exposure", subject_kind="provider",
                             subject_key="typesafe", evidence={"finding_id": "f-2"})
    db.commit()
    assert (whole.action, prov.action) == ("switch_off", "switch_off")
    assert effective_switches(db, gov.ws_a).blocking(ai_enabled=True, provider="openai", purpose=IDENT)
    assert effective_switches(db, gov.ws_b).blocking(ai_enabled=True, provider="typesafe", purpose=IDENT)
    assert inc.open_incident_counts(db, gov.ws_a, IDENT).get("data_exposure") == 1  # the workspace incident counts
    assert inc.open_incident_counts(db, None, IDENT).get("data_exposure") == 1  # the provider-wide one too
    for subject_kind, subject_key in (("purpose", "Bad Key"), ("agent", "a.b"), ("provider", "x-y")):
        with pytest.raises(ValueError):
            inc.open_incident(db, workspace_id=gov.ws_a, kind="drift", subject_kind=subject_kind, subject_key=subject_key)
    with pytest.raises(ValueError):  # a platform-wide workspace incident must say so
        inc.open_incident(db, workspace_id=None, kind="data_exposure", subject_kind="workspace", subject_key="all")


def test_workspace_evidence_is_aggregate_only_approver_scoped_opt_in_and_admin_only_in_runs(db_session, gov, corpus):
    db = db_session
    for actor in (gov.engineer, gov.owner_b, gov.platform_admin):  # platform staff: not before the opt-in
        with pytest.raises(GovernanceNotFound):
            r3.workspace_evidence(db, workspace_id=gov.ws_a, actor=actor)
    assert isinstance(r3.workspace_evidence(db, workspace_id=gov.ws_a, actor=gov.owner), dict)
    with pytest.raises(GovernanceNotPermitted, match="r3_tenant_evidence_needs_admin"):  # no actor / not an admin
        r3.run_r3(corpus, r3.ScriptedAnswerer(), candidate="fake", db=db, workspace_ids=[gov.ws_a])
    with pytest.raises(GovernanceNotPermitted, match="r3_tenant_evidence_needs_admin"):
        r3.run_r3(corpus, r3.ScriptedAnswerer(), candidate="fake", db=db, workspace_ids=[gov.ws_a], actor=gov.owner)
    report = r3.run_r3(corpus, r3.ScriptedAnswerer(), candidate="fake", db=db, workspace_ids=[gov.ws_a, gov.ws_b],
                       actor=gov.platform_admin)
    assert report["workspace_evidence"] == {} and report["workspaces_not_opted_in"] == 2
    proposal = propose_policy(db, actor=gov.owner, workspace_id=gov.ws_a, rationale="share R3 aggregates",
                              policy=_policy(data__share_r3_aggregates=True))
    accept_policy(db, approver=gov.admin, workspace_id=gov.ws_a, proposal_id=proposal.id)
    db.commit()
    evidence = r3.workspace_evidence(db, workspace_id=gov.ws_a, actor=gov.platform_admin)
    assert not re.search(r"holdout|final_test|rows|sample", json.dumps(evidence), re.IGNORECASE)
    report = r3.run_r3(corpus, r3.ScriptedAnswerer(), candidate="fake", db=db, workspace_ids=[gov.ws_a, gov.ws_b],
                       actor=gov.platform_admin)
    assert len(report["workspace_evidence"]) == 1 and report["workspaces_not_opted_in"] == 1
    assert str(gov.ws_a) not in json.dumps(report)
    assert r3.pseudonym(gov.ws_a, report["run_id"]) not in report["workspace_evidence"]  # not derivable from the run id
    assert r3.pseudonym(gov.ws_a, "salt-a") != r3.pseudonym(gov.ws_a, "salt-b")  # unlinkable across runs
    # small buckets never leave the service
    assert r3._suppress({IDENT: {"answers": {"total": 3, "in_band": 1, "by_agreement": {"agree": 3}},
                                 "ledger": {"calls": 2, "cost_micros": 5, "p95_latency_ms": 9.0}}}) == {}


def test_cli_run_report_promote_check_and_refusals(tmp_path, monkeypatch, capsys):
    from app.cli.main import main

    out = tmp_path / "r3.json"
    assert main(["r3", "run", "--task", "sk-iris", "--out", str(out)]) == 0
    assert oct(out.stat().st_mode & 0o777) == "0o600"
    report = json.loads(out.read_text())
    assert set(report["points"]) == set(REGISTRY) and report["candidate"] == "fake:42" and report["live"] is False
    assert main(["r3", "report", "--report", str(out)]) == 0
    assert main(["r3", "promote-check", "--report", str(out), "--key", IDENT, "--level", "1"]) == 1
    text_out = capsys.readouterr().out
    verdict = json.loads(text_out[text_out.rfind('{\n "level"'):])
    assert verdict["allowed"] is False and "stability_two_runs" in verdict["checks"]
    assert main(["r3", "run", "--live", "--task", "sk-iris", "--out", str(out)]) == 2  # no --admin
    assert main(["r3", "run", "--workspace", str(uuid4()), "--task", "sk-iris", "--out", str(out)]) == 2
    monkeypatch.setenv("CI", "1")
    assert main(["r3", "run", "--live", "--admin", "a@b.c", "--task", "sk-iris", "--out", str(out)]) == 2
