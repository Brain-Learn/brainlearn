"""Step 4E.3: versioned branched demonstration workflow."""

import json
from pathlib import Path

import pytest
from brainlearn_core import Workflow, migrate_workflow_dict, validate_workflow
from brainlearn_core.scheduler import topological_order
from brainlearn_server.app import app
from brainlearn_server.registry import NODE_REGISTRY_BY_ID
from brainlearn_server.worker import build_run_record
from fastapi.testclient import TestClient

FIXTURE = Path(__file__).parents[1] / "examples" / "demo-branched.workflow.json"
EEG_EXAMPLE = Path(__file__).parents[1] / "examples" / "eeg-first-look.workflow.json"


def _load() -> Workflow:
    return Workflow.model_validate(
        migrate_workflow_dict(json.loads(FIXTURE.read_text(encoding="utf-8")))
    )


def test_fixture_round_trips_without_loss() -> None:
    workflow = _load()
    assert workflow.schema_version == "1.0"
    assert workflow.id == "demo-branched"
    assert Workflow.model_validate_json(workflow.model_dump_json()) == workflow


def test_fixture_validates_cleanly_against_the_registry() -> None:
    assert validate_workflow(_load(), NODE_REGISTRY_BY_ID).valid


def test_fixture_topology_is_an_acyclic_branch() -> None:
    workflow = _load()
    by_id = {node.id: node for node in workflow.nodes}
    assert set(by_id) == {"source", "bridge", "sink", "timer", "gate"}
    assert by_id["gate"].pauses_for_review
    assert all(not node.pauses_for_review for node in workflow.nodes if node.id != "gate")
    assert {port.data_type for node in workflow.nodes for port in node.ports} == {"raw_eeg"}
    dependencies = {
        node.id: sorted(
            {edge.source.node_id for edge in workflow.edges if edge.target.node_id == node.id}
        )
        for node in workflow.nodes
    }
    order = topological_order(dependencies)
    assert order.index("source") < order.index("bridge") < order.index("sink")
    assert dependencies["timer"] == []
    assert dependencies["gate"] == []


def test_fixture_carries_probe_suitable_parameters() -> None:
    values = {
        node.id: {parameter.id: parameter.value for parameter in node.parameters}
        for node in _load().nodes
    }
    assert values["source"]["text"] == "trunk-bytes"
    assert values["sink"]["text"] == "sink-bytes"
    assert values["timer"]["seconds"] == 0.2


def test_fixture_builds_a_queued_run() -> None:
    record = build_run_record(_load())
    assert record.state == "queued"
    assert {node.id for node in record.node_runs} == {
        "source",
        "bridge",
        "sink",
        "timer",
        "gate",
    }
    assert all(node.attempt == 0 for node in record.node_runs)


def test_source_text_change_invalidates_only_trunk_descendants() -> None:
    before = {node.id: node.content_identity for node in build_run_record(_load()).node_runs}
    payload = json.loads(FIXTURE.read_text(encoding="utf-8"))
    for node in payload["nodes"]:
        if node["id"] == "source":
            node["parameters"][0]["value"] = "changed-bytes"
    altered = Workflow.model_validate(migrate_workflow_dict(payload))
    after = {node.id: node.content_identity for node in build_run_record(altered).node_runs}
    assert after["source"] != before["source"]
    assert after["bridge"] != before["bridge"]
    assert after["sink"] != before["sink"]
    assert after["timer"] == before["timer"]
    assert after["gate"] == before["gate"]


def test_fixture_validates_through_the_api() -> None:
    client = TestClient(app)
    response = client.post(
        "/api/workflows/validate", json=json.loads(FIXTURE.read_text(encoding="utf-8"))
    )
    assert response.status_code == 200, response.text
    assert response.json()["validation"] == {"valid": True, "issues": []}


def test_eeg_example_still_validates() -> None:
    workflow = Workflow.model_validate(
        migrate_workflow_dict(json.loads(EEG_EXAMPLE.read_text(encoding="utf-8")))
    )
    assert validate_workflow(workflow, NODE_REGISTRY_BY_ID).valid


def test_example_discovery_lists_versioned_examples() -> None:
    client = TestClient(app)
    response = client.get("/api/workflows/examples")
    assert response.status_code == 200, response.text
    examples = {item["id"]: item for item in response.json()["examples"]}
    assert set(examples) == {"eeg-first-look", "demo-branched"}
    assert examples["demo-branched"]["schema_version"] == "1.0"
    assert examples["demo-branched"]["name"] == "Branched demonstration"


def test_example_load_returns_the_fixture_and_keeps_legacy_route() -> None:
    client = TestClient(app)
    loaded = client.get("/api/workflows/examples/demo-branched")
    assert loaded.status_code == 200, loaded.text
    assert loaded.json()["id"] == "demo-branched"
    assert {node["id"] for node in loaded.json()["nodes"]} == {
        "source",
        "bridge",
        "sink",
        "timer",
        "gate",
    }
    legacy = client.get("/api/workflows/example")
    assert legacy.status_code == 200, legacy.text
    assert legacy.json()["id"] == "eeg-first-look"


def test_unknown_example_returns_404() -> None:
    client = TestClient(app)
    assert client.get("/api/workflows/examples/unknown").status_code == 404


def test_fixture_omits_default_presentation_keys() -> None:
    raw = json.loads(FIXTURE.read_text(encoding="utf-8"))
    assert raw["schema_version"] == "1.0"
    for node in raw["nodes"]:
        assert "presentation" not in node, f"node {node['id']} stores noncanonical defaults"


TEST_TOKEN = "step4e3-test-token-0123456789"
AUTH_HEADERS = {"Authorization": f"Bearer {TEST_TOKEN}"}


@pytest.fixture(autouse=True)
def _isolated_store(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    from brainlearn_server import project_store as store_module
    from brainlearn_server.app import store
    from brainlearn_server.auth import reset_session_token_for_tests

    reset_session_token_for_tests(TEST_TOKEN)
    state_dir = tmp_path / "state"
    monkeypatch.setattr(store_module, "_default_state_dir", lambda: state_dir)
    store.state_dir = state_dir
    store.allowed_roots.clear()
    yield
    store.allowed_roots.clear()
    reset_session_token_for_tests(TEST_TOKEN)


@pytest.fixture
def client() -> TestClient:
    return TestClient(app)


def _make_project(client: TestClient, tmp_path: Path) -> Path:
    project = tmp_path / "demo"
    response = client.post(
        "/api/projects/create",
        json={"path": str(project), "name": "demo"},
        headers=AUTH_HEADERS,
    )
    assert response.status_code == 200, response.text
    return project


def _start(client: TestClient, project: Path) -> dict:
    response = client.post(
        "/api/runs/start",
        json={
            "path": str(project),
            "workflow": json.loads(FIXTURE.read_text(encoding="utf-8")),
            "seed": 0,
        },
        headers=AUTH_HEADERS,
    )
    assert response.status_code == 200, response.text
    return response.json()


def _wait_for_run(
    client: TestClient, project: Path, run_id: str, states: set[str], timeout: float = 30.0
) -> dict:
    import time

    deadline = time.monotonic() + timeout
    body: dict = {}
    while time.monotonic() < deadline:
        response = client.post(
            "/api/runs/open",
            json={"path": str(project), "run_id": run_id},
            headers=AUTH_HEADERS,
        )
        assert response.status_code == 200, response.text
        body = response.json()["run"]
        if body["state"] in states:
            return body
        time.sleep(0.05)
    raise AssertionError(f"Run {run_id} never reached {states}; last: {body.get('state')}")


def _approve_gate(client: TestClient, project: Path, run_id: str) -> None:
    response = client.post(
        "/api/runs/review",
        json={
            "path": str(project),
            "run_id": run_id,
            "node_run_id": "gate",
            "decision": "approved",
            "note": "fixture exercise",
        },
        headers=AUTH_HEADERS,
    )
    assert response.status_code == 200, response.text


def test_two_runs_reuse_trunk_and_reexecute_zero_output_nodes(
    client: TestClient, tmp_path: Path
) -> None:
    project = _make_project(client, tmp_path)
    first = _start(client, project)
    _wait_for_run(client, project, first["run_id"], {"waiting_for_review"})
    _approve_gate(client, project, first["run_id"])
    finished_first = _wait_for_run(client, project, first["run_id"], {"succeeded"})
    by_id = {node["id"]: node for node in finished_first["node_runs"]}
    assert all(by_id[name]["state"] == "succeeded" for name in ("source", "bridge", "sink"))
    assert all(by_id[name]["attempt"] == 1 for name in ("source", "bridge", "sink"))
    assert by_id["timer"]["state"] == "succeeded"
    assert by_id["gate"]["state"] == "succeeded"
    first_identities = {name: by_id[name]["content_identity"] for name in by_id}

    second = _start(client, project)
    parked = _wait_for_run(client, project, second["run_id"], {"waiting_for_review"})
    rerun = {node["id"]: node for node in parked["node_runs"]}
    for name in ("source", "bridge", "sink"):
        assert rerun[name]["state"] == "cache_reused"
        assert rerun[name]["attempt"] == 0
        assert rerun[name]["content_identity"] == first_identities[name]
    assert rerun["timer"]["state"] in ("queued", "running", "succeeded")
    assert rerun["timer"]["content_identity"] == first_identities["timer"]
    assert rerun["gate"]["state"] == "waiting_for_review"

    _approve_gate(client, project, second["run_id"])
    finished_second = _wait_for_run(client, project, second["run_id"], {"succeeded"})
    final = {node["id"]: node for node in finished_second["node_runs"]}
    assert final["timer"]["state"] == "succeeded"
    assert final["timer"]["attempt"] == 1
    gate = final["gate"]
    assert gate["state"] == "succeeded"
    assert gate["attempt"] == 1
