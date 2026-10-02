"""P3.3-A: customer CLI smoke tests against the FastAPI test app.

login -> projects -> data upload -> experiments run -> (worker) get/compare/code
-> decisions list, plus --json output and the auth / not-found exit codes. The
CLI talks to the app through the TestClient exactly as the SDK tests do.
"""

from __future__ import annotations

import io
import json
from uuid import uuid4

import pytest

from app.services.auth_service import create_access_token
from dclab_client import cli
from test_data_model_lineage import make_lineage_setup
from test_v1_resources_experiments import _csv, _work


@pytest.fixture()
def setup(db_session, tmp_path):
    return make_lineage_setup(db_session, tmp_path)


class Runner:
    def __init__(self, client, tmp_path, token, workspace):
        self.client, self.tmp_path, self.token, self.workspace = client, tmp_path, token, workspace

    def __call__(self, *argv, token=None, stdin=""):
        out, err = io.StringIO(), io.StringIO()
        env = {"XDG_CONFIG_HOME": str(self.tmp_path / "cfg")}
        if token is not False:
            env["DCLAB_TOKEN"] = token or self.token
            env["DCLAB_WORKSPACE"] = str(self.workspace)
            env["DCLAB_API_URL"] = str(self.client.base_url)
        code = cli.main(list(argv), env=env, stdin=io.StringIO(stdin), stdout=out, stderr=err,
                        http=self.client)
        return code, out.getvalue(), err.getvalue()


@pytest.fixture()
def dclab(client, setup, tmp_path):
    return Runner(client, tmp_path, create_access_token(setup["alpha_admin"]), setup["alpha"].id)


def test_cli_end_to_end_loop(dclab, db_session, setup, tmp_path):
    token = dclab.token
    # login (validates against /v1/me, stores 0600, token never printed)
    code, out, err = dclab("login", "--workspace", str(setup["alpha"].id), "--api-url",
                           str(dclab.client.base_url), token=False, stdin=token + "\n")
    assert code == 0, err
    assert token not in out + err

    code, out, err = dclab("projects", "create", "--name", "CLI loop", "--json")
    assert code == 0, err
    project = json.loads(out)
    code, out, _ = dclab("projects", "list", "--json")
    assert code == 0 and project["id"] in {p["id"] for p in json.loads(out)}
    code, out, _ = dclab("projects", "get", project["id"])
    assert code == 0 and "CLI loop" in out

    data = tmp_path / "rows.csv"
    data.write_bytes(_csv())
    code, out, err = dclab("data", "upload", str(data), "--project", project["id"], "--json")
    assert code == 0, err
    dataset = json.loads(out)
    assert dataset["ingestion"]["publication_state"] == "published"

    key = f"cli-{uuid4().hex}"
    argv = ["experiments", "run", "--project", project["id"], "--dataset", dataset["id"],
            "--target", "label", "--intent", "cli baseline", "--idempotency-key", key, "--json"]
    code, out, err = dclab(*argv)
    assert code == 0, err
    root = json.loads(out)
    assert root["status"] == "queued"
    assert json.loads(dclab(*argv)[1])["id"] == root["id"]  # same key -> same run

    assert _work(db_session, root["id"]).status == "completed"
    code, out, _ = dclab("experiments", "get", root["id"], "--json")
    assert code == 0 and json.loads(out)["status"] == "completed"
    code, out, _ = dclab("experiments", "list", "--project", project["id"])
    assert code == 0 and root["id"] in out

    code, out, _ = dclab("experiments", "code", root["id"])
    assert code == 0 and out.strip()
    target = tmp_path / "repro.py"
    code, out, _ = dclab("experiments", "code", root["id"], "-o", str(target), "--json")
    assert code == 0 and target.read_text() and json.loads(out)["written_to"] == str(target)

    code, out, err = dclab("experiments", "branch", root["id"], "--intent", "no xgboost", "--changes",
                           '[{"kind": "family_exclude", "family": "xgboost"}]', "--json")
    assert code == 0, err
    assert json.loads(out)["lineage"]["parent_experiment_id"] == root["id"]
    code, out, err = dclab("experiments", "cancel", root["id"])
    assert code == cli.EXIT_CONFLICT and "not_cancellable" in err  # completed run

    code, out, _ = dclab("decisions", "list", "--project", project["id"], "--json")
    assert code == 0 and "items" in json.loads(out)
    code, out, err = dclab("decisions", "propose", "--project", project["id"], "--type", "bogus_type",
                           "--subject-kind", "project", "--rationale", "x")
    assert code == cli.EXIT_VALIDATION, (code, err)
    code, out, err = dclab("decisions", "propose", "--project", project["id"], "--type",
                           "experiment_accepted", "--subject-kind", "experiment", "--subject-id",
                           root["id"], "--rationale", "looks good", "--json")
    assert code == 0, err
    proposed = json.loads(out)
    code, out, _ = dclab("decisions", "get", proposed["id"], "--json")
    assert code == 0 and json.loads(out)["effective_state"] == "proposed"
    code, out, _ = dclab("decisions", "list", "--project", project["id"], "--type", "experiment_accepted")
    assert code == 0 and proposed["id"] in out

    # Stored config alone (no env) keeps working.
    out_buf, err_buf = io.StringIO(), io.StringIO()
    code = cli.main(["projects", "list", "--json"], env={"XDG_CONFIG_HOME": str(tmp_path / "cfg")},
                    stdout=out_buf, stderr=err_buf, http=dclab.client)
    assert code == 0, err_buf.getvalue()


def test_exit_codes_for_auth_failure_and_not_found(dclab, setup):
    code, _, err = dclab("projects", "list", token="not-a-token")
    assert code == cli.EXIT_AUTH and "request_id=" in err
    code, out, err = dclab("projects", "get", str(uuid4()))
    assert code == cli.EXIT_NOT_FOUND and "not_found" in err and out == ""
    code, _, err = dclab("experiments", "get", str(uuid4()), "--json")
    assert code == cli.EXIT_NOT_FOUND and json.loads(err)["error"]["code"]
    code, _, err = dclab("login", "--token", "junk", "--workspace", str(setup["alpha"].id), token=False)
    assert code == cli.EXIT_AUTH
    code, _, err = dclab("projects", "list", token=False)
    assert code == cli.EXIT_AUTH


def test_foreign_workspace_resources_are_not_found_for_the_cli(dclab, db_session, setup):
    foreign = setup["beta_project"]
    code, _, _ = dclab("projects", "get", str(foreign.id))
    assert code == cli.EXIT_NOT_FOUND
    code, _, _ = dclab("decisions", "list", "--project", str(foreign.id))
    assert code == cli.EXIT_NOT_FOUND
