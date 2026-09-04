"""Step 0 guardrail: the /admin vs /app split is enforced by role, not by convention.

These tests walk the live FastAPI route table rather than a hand-maintained list, so
a newly added admin endpoint is covered automatically the moment it is registered.
"""

from __future__ import annotations

import re

import pytest

from app.main import app

METHODS_WITH_BODY = {"POST", "PUT", "PATCH"}
PLACEHOLDER = "00000000-0000-0000-0000-000000000000"


def _routes_under(prefix: str) -> list[tuple[str, str]]:
    """Every (method, concrete path) pair the app actually serves under a prefix.

    Read from the OpenAPI schema rather than app.routes: this FastAPI version keeps
    included routers nested, so walking app.routes silently returns nothing and the
    audit would pass while testing zero endpoints.
    """
    pairs: list[tuple[str, str]] = []
    for path, operations in app.openapi()["paths"].items():
        if not path.startswith(prefix):
            continue
        concrete = re.sub(r"\{[^}]+\}", PLACEHOLDER, path)
        for method in operations:
            if method.upper() in {"HEAD", "OPTIONS", "PARAMETERS"}:
                continue
            pairs.append((method.upper(), concrete))
    return sorted(set(pairs))


def _call(client, method: str, path: str):
    kwargs = {"json": {}} if method in METHODS_WITH_BODY else {}
    return client.request(method, path, **kwargs)


def test_admin_routes_exist():
    """Guard against the audit silently passing because nothing is mounted."""
    admin_routes = _routes_under("/admin")
    assert len(admin_routes) >= 10, f"expected a populated admin tree, got {admin_routes}"


@pytest.mark.parametrize("method,path", _routes_under("/admin"))
def test_every_admin_route_rejects_client_token(client, client_token, method, path):
    response = _call_with_token(client, method, path, client_token)
    assert response.status_code == 403, (
        f"{method} {path} returned {response.status_code} for a client token; expected 403"
    )


@pytest.mark.parametrize("method,path", _routes_under("/admin"))
def test_every_admin_route_rejects_anonymous(client, method, path):
    response = _call(client, method, path)
    assert response.status_code == 401, (
        f"{method} {path} returned {response.status_code} without a token; expected 401"
    )


@pytest.mark.parametrize("method,path", _routes_under("/app"))
def test_every_client_route_rejects_anonymous(client, method, path):
    response = _call(client, method, path)
    assert response.status_code == 401, (
        f"{method} {path} returned {response.status_code} without a token; expected 401"
    )


def test_business_routes_exist():
    business_routes = _routes_under("/business")
    assert len(business_routes) >= 8, (
        f"expected a populated business tree, got {business_routes}"
    )


@pytest.mark.parametrize("method,path", _routes_under("/business"))
def test_every_business_route_rejects_anonymous(client, method, path):
    response = _call(client, method, path)
    assert response.status_code == 401, (
        f"{method} {path} returned {response.status_code} without a token; expected 401"
    )


@pytest.mark.parametrize("method,path", _routes_under("/business"))
def test_every_business_route_rejects_client_token(client, client_token, method, path):
    response = _call_with_token(client, method, path, client_token)
    assert response.status_code == 403, (
        f"{method} {path} returned {response.status_code} for a client token; expected 403"
    )


def _call_with_token(client, method: str, path: str, token: str):
    kwargs = {"json": {}} if method in METHODS_WITH_BODY else {}
    return client.request(method, path, headers={"Authorization": f"Bearer {token}"}, **kwargs)


def test_admin_token_is_not_blocked_by_the_role_guard(client, admin_token):
    """The guard must reject client tokens without also breaking admin access."""
    response = client.get(
        "/admin/experiments", headers={"Authorization": f"Bearer {admin_token}"}
    )
    assert response.status_code == 200


def test_client_token_reaches_the_client_tree(client, client_token):
    response = client.get(
        "/app/opportunities", headers={"Authorization": f"Bearer {client_token}"}
    )
    assert response.status_code == 200


def test_login_token_carries_name_role_and_long_expiry(client, client_user):
    import jwt

    from app.config import get_settings

    response = client.post(
        "/auth/login", json={"email": client_user.email, "password": "client-pass-123"}
    )
    assert response.status_code == 200
    body = response.json()
    assert body["user"]["full_name"] == "Client User"
    payload = jwt.decode(body["access_token"], get_settings().jwt_secret, algorithms=["HS256"])
    assert payload["full_name"] == "Client User"
    assert payload["role"] == "client_user"
    assert payload["email"] == client_user.email
    assert payload["exp"] - payload["iat"] >= 60 * 60 * 24 * 29


def test_demo_staff_and_customer_have_separate_access(client, db_session):
    from app.services.auth_service import (
        DEMO_ADMIN_EMAIL,
        DEMO_ADMIN_PASSWORD,
        DEMO_CLIENT_EMAIL,
        DEMO_CLIENT_PASSWORD,
        ensure_demo_users,
    )

    ensure_demo_users(db_session)
    db_session.commit()

    staff = client.post(
        "/auth/login", json={"email": DEMO_ADMIN_EMAIL, "password": DEMO_ADMIN_PASSWORD}
    )
    customer = client.post(
        "/auth/login", json={"email": DEMO_CLIENT_EMAIL, "password": DEMO_CLIENT_PASSWORD}
    )
    assert staff.status_code == 200
    assert customer.status_code == 200
    assert staff.json()["user"]["role"] == "dclab_admin"
    assert staff.json()["user"]["full_name"] == "Admin"
    assert customer.json()["user"]["role"] == "client_user"
    assert customer.json()["user"]["full_name"] == "Business Client"

    staff_token = staff.json()["access_token"]
    customer_token = customer.json()["access_token"]
    assert (
        client.get("/admin/experiments", headers={"Authorization": f"Bearer {customer_token}"}).status_code
        == 403
    )
    assert (
        client.get("/admin/experiments", headers={"Authorization": f"Bearer {staff_token}"}).status_code
        == 200
    )
    assert (
        client.get("/app/opportunities", headers={"Authorization": f"Bearer {customer_token}"}).status_code
        == 200
    )


def test_login_returns_a_usable_token(client, client_user):
    response = client.post(
        "/auth/login", json={"email": client_user.email, "password": "client-pass-123"}
    )
    assert response.status_code == 200
    body = response.json()
    assert body["token_type"] == "bearer"
    assert body["user"]["role"] == "client_user"

    me = client.get("/auth/me", headers={"Authorization": f"Bearer {body['access_token']}"})
    assert me.status_code == 200
    assert me.json()["email"] == client_user.email


def test_login_rejects_a_wrong_password(client, client_user):
    response = client.post(
        "/auth/login", json={"email": client_user.email, "password": "not-the-password"}
    )
    assert response.status_code == 401


def test_a_garbage_token_is_rejected(client):
    response = client.get("/admin/experiments", headers={"Authorization": "Bearer not.a.jwt"})
    assert response.status_code == 401


class TestNoClientModelsTab:
    """Step 4 — there is no client-facing 'Models' tab in this codebase to remove:
    everything the build doc describes (candidate/selection counts, precision/
    recall/AUC/calibration, feature contribution, model selection stats) has lived
    under `/admin/lab/experiments/{id}` — unrestricted, admin-only — since the
    Step 0 route split. `test_every_admin_route_rejects_client_token` /
    `..._rejects_anonymous` above already generically enforce this for every
    admin route including the model-selection ones. This class proves the two
    things that generic audit can't: (1) no legacy `/models` path was left
    reachable outside `/admin`, and (2) the admin view is genuinely the full,
    unrestricted content — not a stripped-down copy — matching the doc's literal
    verify commands.
    """

    def _seed_completed_experiment(self, db_session):
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
            config=SearchConfig(max_candidates=4, max_feature_group_combinations=2, n_robustness_folds=2, seed=11),
        )
        executed = execute_experiment(db_session, experiment)
        assert executed.status == "COMPLETED"
        return executed

    def test_no_models_route_exists_outside_admin(self, auth_client, admin_client):
        for path in ("/models", "/app/models", "/model-registry", "/app/model-registry"):
            assert auth_client.get(path).status_code == 404, path
            assert admin_client.get(path).status_code == 404, path

    def test_admin_sees_full_unrestricted_model_selection_detail(self, db_session, admin_client, auth_client):
        executed = self._seed_completed_experiment(db_session)

        models = admin_client.get(f"/admin/experiments/{executed.id}/models")
        assert models.status_code == 200
        models_body = models.json()
        assert "selected_ids" in models_body
        assert "best_single" in models_body
        assert models_body["best_single"].get("model_family")
        assert isinstance(models_body["best_single"].get("score"), (int, float))

        candidates = admin_client.get(f"/admin/experiments/{executed.id}/candidates")
        assert candidates.status_code == 200
        candidate_rows = candidates.json()
        assert len(candidate_rows) >= 1
        assert all("model_family" in row and "score" in row for row in candidate_rows)

        ensemble = admin_client.get(f"/admin/experiments/{executed.id}/ensemble")
        assert ensemble.status_code == 200
        assert "fusion" in ensemble.json()
        assert "test_metrics" in ensemble.json()

        # Same content is entirely unreachable for a client_user token.
        for suffix in ("models", "candidates", "ensemble", "metrics"):
            response = auth_client.get(f"/admin/experiments/{executed.id}/{suffix}")
            assert response.status_code == 403


def test_client_surface_audit_script_knows_about_every_live_app_operation():
    """Step 8 — scripts/audit_client_surface.py exercises a fixed, hand-checked
    set of /app operations (KNOWN_CLIENT_OPERATIONS) with real data and scans
    the real response bytes for banned terms; that's what makes it catch bugs
    static schema scanning can't (see app/translation/simulations.py's
    "offer_training" fix). But a fixed set only stays "not sampled" if it's
    kept in sync with whatever the app actually serves. This runs at unit-test
    speed on every CI run — no live servers needed — so a newly added /app
    endpoint fails the fast test suite immediately instead of only being
    caught later by the slow live crawl (or not at all, if no one reruns it).
    """
    from scripts.audit_client_surface import KNOWN_CLIENT_OPERATIONS

    # Raw (un-substituted) path templates, same as the live OpenAPI schema the
    # script itself reads -- unlike _routes_under() above, dynamic segments
    # stay as "{opportunity_id}" rather than being replaced with a UUID.
    live_ops = {
        (method.upper(), path)
        for path, operations in app.openapi()["paths"].items()
        if path.startswith("/app")
        for method in operations
        if method.upper() not in {"HEAD", "OPTIONS", "PARAMETERS"}
    }
    assert live_ops == KNOWN_CLIENT_OPERATIONS, (
        "scripts/audit_client_surface.py's KNOWN_CLIENT_OPERATIONS is out of sync with the "
        f"live /app surface. Live: {sorted(live_ops - KNOWN_CLIENT_OPERATIONS)} not covered, "
        f"stale entries: {sorted(KNOWN_CLIENT_OPERATIONS - live_ops)}"
    )
