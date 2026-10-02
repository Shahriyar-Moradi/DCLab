"""P3.5-A: golden MCP transcripts (classification, regression, branch-and-compare).

``dclab_mcp.golden`` drives the MCP tools in a fixed order through a real MCP client session
against the test app; the normalised transcript (ids by first appearance, timestamps, digests,
durations and generated code blanked, floats rounded) must equal
``tests/golden/mcp/<scenario>.json``. Regenerate with ``DCLAB_UPDATE_GOLDEN=1``.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

pytest.importorskip("mcp")

import anyio  # noqa: E402
from mcp import Client  # noqa: E402

from app.services.project_service import create_project  # noqa: E402
from dclab_client import DCLabClient  # noqa: E402
from dclab_mcp import golden  # noqa: E402
from test_dclab_mcp_server import ALL_SCOPES, _server  # noqa: E402
from test_v1_resources_experiments import _work  # noqa: E402
from test_v1_service_tokens import _token, st  # noqa: E402, F401  (fixture)

GOLDEN_DIR = Path(__file__).parent / "golden" / "mcp"


@pytest.mark.parametrize("scenario", golden.SCENARIOS)
def test_golden_transcript(scenario, client, db_session, st, tmp_path):  # noqa: F811
    db = db_session
    project = create_project(db, actor=st.admin, workspace_id=st.alpha.id, name=f"Golden {scenario}",
                             slug=f"golden-{scenario.replace('_', '-')}")
    db.commit()
    raw = _token(db, st, scopes=ALL_SCOPES)
    make_csv, _target = golden.DATASETS[scenario]
    path = tmp_path / "golden.csv"
    path.write_bytes(make_csv())
    with DCLabClient(str(client.base_url), token=raw, http=client) as api:
        dataset = api.datasets.upload(project.id, path)
    server = _server(client, raw)

    def call(name, args=None):
        async def go():
            async with Client(server) as mcp_client:
                return await mcp_client.call_tool(name, args or {})
        result = anyio.run(go)
        assert not result.is_error, result.structured_content
        assert raw not in result.content[0].text
        return result.structured_content

    recorded, log = golden.record(call)
    golden.RUNNERS[scenario](recorded, lambda eid: _work(db, eid), str(project.id), str(dataset.id))
    actual = json.loads(json.dumps(golden.normalize(log)))  # plain JSON types, as stored

    target = GOLDEN_DIR / f"{scenario}.json"
    if os.environ.get("DCLAB_UPDATE_GOLDEN") == "1":
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(actual, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    assert target.exists(), f"missing {target}; run with DCLAB_UPDATE_GOLDEN=1"
    assert actual == json.loads(target.read_text(encoding="utf-8"))
    # Authority invariants hold in every recorded transcript.
    assert all(s["result"].get("write_performed") is not True for s in actual)
    assert [s["tool"] for s in actual][-2:] == ["accept_proposal", "list_decisions"]
