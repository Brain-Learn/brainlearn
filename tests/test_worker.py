"""Step 4C: worker lifecycle, cancellation, review, streaming, artifacts."""

import json
import time
from pathlib import Path
from typing import Any

import pytest
from brainlearn_core import NodeRunState, RunEvent, RunEventKind, RunRecord, RunState, Workflow
from brainlearn_server import project_store as store_module
from brainlearn_server.app import app, runs, store
from brainlearn_server.auth import reset_session_token_for_tests
from brainlearn_server.worker import build_run_record
from fastapi.testclient import TestClient

FIXTURES = Path(__file__).parent / "fixtures"

TEST_TOKEN = "step4c-test-token-0123456789"
AUTH_HEADERS = {"Authorization": f"Bearer {TEST_TOKEN}"}


@pytest.fixture(autouse=True)
def _isolated_store(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
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


def _port(port_id: str, direction: str) -> dict[str, Any]:
    return {
        "id": port_id,
        "label": port_id,
        "direction": direction,
        "data_type": "raw_eeg",
        "required": True,
    }


def _node(
    node_id: str,
    node_type: str,
    parameters: dict[str, Any] | None = None,
    inputs: bool = False,
    outputs: bool = True,
    out_port: str = "out",
) -> dict[str, Any]:
    ports = []
    if inputs:
        ports.append(_port("in", "input"))
    if outputs:
        ports.append(_port(out_port, "output"))
    return {
        "id": node_id,
        "type": node_type,
        "label": node_id,
        "category": "Demo",
        "description": "Demonstration node.",
        "position": {"x": 0, "y": 0},
        "ports": ports,
        "parameters": [
            {"id": key, "label": key, "value": value, "required": False}
            for key, value in (parameters or {}).items()
        ],
        "pauses_for_review": False,
    }


def _workflow(nodes: list[dict[str, Any]], edges: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "schema_version": "1.0",
        "id": "demo-flow",
        "metadata": {
            "name": "Demo flow",
            "description": "",
            "created_with": "BrainLearn tests",
            "modality": "EEG",
            "status": "example",
        },
        "nodes": nodes,
        "edges": edges,
    }


def _edge(
    edge_id: str, source: str, target: str, source_port: str = "out", target_port: str = "in"
) -> dict[str, Any]:
    return {
        "id": edge_id,
        "source": {"node_id": source, "port_id": source_port},
        "target": {"node_id": target, "port_id": target_port},
    }


def _chain_workflow() -> dict[str, Any]:
    return _workflow(
        [
            _node("first", "demo.delay", {"seconds": 0.01}),
            _node("second", "demo.copy", {"text": "hello-runs"}, inputs=True, out_port="output"),
        ],
        [_edge("e1", "first", "second")],
    )


def _start(
    client: TestClient, project: Path, workflow: dict[str, Any], seed: int = 0
) -> dict[str, Any]:
    response = client.post(
        "/api/runs/start",
        json={"path": str(project), "workflow": workflow, "seed": seed},
        headers=AUTH_HEADERS,
    )
    assert response.status_code == 200, response.text
    return response.json()


def _make_project(client: TestClient, tmp_path: Path, name: str = "work") -> Path:
    project = tmp_path / name
    response = client.post(
        "/api/projects/create",
        json={"path": str(project), "name": name},
        headers=AUTH_HEADERS,
    )
    assert response.status_code == 200, response.text
    return project


def _wait_for_state(
    client: TestClient, project: Path, run_id: str, states: set[str], timeout: float = 20.0
) -> dict[str, Any]:
    deadline = time.monotonic() + timeout
    body: dict[str, Any] = {}
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


def _kinds(run: dict[str, Any]) -> list[str]:
    return [event["kind"] for event in run["events"]]


# -- execution -----------------------------------------------------------------


def test_start_runs_chain_to_success(client: TestClient, tmp_path: Path) -> None:
    project = _make_project(client, tmp_path)
    started = _start(client, project, _chain_workflow())
    run_id = started["run_id"]
    assert started["run"]["state"] == "queued"

    finished = _wait_for_state(client, project, run_id, {"succeeded"})
    assert [event["seq"] for event in finished["events"]] == list(range(len(finished["events"])))
    by_id = {node["id"]: node for node in finished["node_runs"]}
    assert by_id["first"]["attempt"] == 1
    assert by_id["second"]["attempt"] == 1
    assert by_id["second"]["artifacts"][0]["port_id"] == "output"

    artifact = project / by_id["second"]["artifacts"][0]["path"]
    assert artifact.read_text(encoding="utf-8") == "hello-runs"
    assert "run_started" in _kinds(finished)
    assert _kinds(finished).count("node_started") == 2
    assert list((project / "runs" / run_id / "staging").rglob("*")) == []


def test_failure_skips_downstream_and_fails_run(client: TestClient, tmp_path: Path) -> None:
    project = _make_project(client, tmp_path)
    workflow = _workflow(
        [
            _node("bad", "demo.fail", {"message": "boom"}),
            _node("down", "demo.copy", {"text": "x"}, inputs=True, out_port="output"),
        ],
        [_edge("e1", "bad", "down")],
    )
    started = _start(client, project, workflow)
    finished = _wait_for_state(client, project, started["run_id"], {"failed"})

    by_id = {node["id"]: node for node in finished["node_runs"]}
    assert by_id["bad"]["failure"]["code"] == "node_failed"
    assert by_id["bad"]["artifacts"] == []
    assert by_id["down"]["state"] == "dependency_skipped"
    assert by_id["down"]["attempt"] == 0
    assert finished["failure"]["node_run_id"] == "bad"
    assert "run_failed" in _kinds(finished)
    assert "node_skipped" in _kinds(finished)
    assert not list((project / "runs" / started["run_id"] / "artifacts").rglob("*"))


def test_unsupported_node_types_fail_deterministically(client: TestClient, tmp_path: Path) -> None:
    project = _make_project(client, tmp_path)
    workflow = _workflow(
        [_node("mystery", "eeg.filter", {"low_hz": 1.0}, inputs=True, outputs=True)], []
    )
    started = _start(client, project, workflow)
    finished = _wait_for_state(client, project, started["run_id"], {"failed"})
    assert finished["node_runs"][0]["failure"]["code"] == "unsupported_node_type"


def test_start_rejects_invalid_workflows_and_bad_requests(
    client: TestClient, tmp_path: Path
) -> None:
    project = _make_project(client, tmp_path)
    cyclic = _workflow(
        [
            _node("a", "demo.delay", inputs=True, outputs=True),
            _node("b", "demo.delay", inputs=True, outputs=True),
        ],
        [_edge("e1", "a", "b"), _edge("e2", "b", "a")],
    )
    assert (
        client.post(
            "/api/runs/start",
            json={"path": str(project), "workflow": cyclic},
            headers=AUTH_HEADERS,
        ).status_code
        == 400
    )
    both = client.post(
        "/api/runs/start",
        json={"path": str(project), "workflow": cyclic, "run_id": "x"},
        headers=AUTH_HEADERS,
    )
    assert both.status_code == 400


def test_resume_terminal_run_is_rejected(client: TestClient, tmp_path: Path) -> None:
    project = _make_project(client, tmp_path)
    started = _start(client, project, _chain_workflow())
    _wait_for_state(client, project, started["run_id"], {"succeeded"})
    response = client.post(
        "/api/runs/start",
        json={"path": str(project), "run_id": started["run_id"]},
        headers=AUTH_HEADERS,
    )
    assert response.status_code == 400


def test_concurrent_starts_drive_single_execution(client: TestClient, tmp_path: Path) -> None:
    project = _make_project(client, tmp_path)
    workflow = _workflow([_node("slow", "demo.delay", {"seconds": 2.0})], [])
    first = _start(client, project, workflow)
    second = client.post(
        "/api/runs/start",
        json={"path": str(project), "run_id": first["run_id"]},
        headers=AUTH_HEADERS,
    )
    assert second.status_code == 200
    finished = _wait_for_state(client, project, first["run_id"], {"succeeded"}, timeout=25.0)
    assert _kinds(finished).count("node_started") == 1


# -- cancellation ----------------------------------------------------------------


def test_cancel_parked_run_is_idempotent(client: TestClient, tmp_path: Path) -> None:
    project = _make_project(client, tmp_path)
    workflow = _workflow(
        [
            _node("slow", "demo.delay", {"seconds": 0.05}),
            _node("later", "demo.delay", {"seconds": 0.05}),
        ],
        [],
    )
    record = build_run_record(Workflow.model_validate(workflow))
    runs.create_run(str(project), record)

    cancelled = client.post(
        "/api/runs/cancel",
        json={"path": str(project), "run_id": record.id},
        headers=AUTH_HEADERS,
    )
    assert cancelled.status_code == 200
    body = cancelled.json()["run"]
    assert body["state"] == "cancelled"
    for node in body["node_runs"]:
        assert node["state"] == "cancelled"
        assert node["attempt"] == 0
        assert node["started_at"] is None
        assert node["finished_at"] is not None
    kinds = [event["kind"] for event in body["events"]]
    assert kinds.count("node_cancelled") == 2
    assert all(
        event["attempt"] == 0 for event in body["events"] if event["kind"] == "node_cancelled"
    )
    again = client.post(
        "/api/runs/cancel",
        json={"path": str(project), "run_id": record.id},
        headers=AUTH_HEADERS,
    )
    assert again.status_code == 200
    assert again.json()["run"] == body


def test_cancel_during_work_reaches_terminal_state(client: TestClient, tmp_path: Path) -> None:
    project = _make_project(client, tmp_path)
    workflow = _workflow(
        [
            _node("slow", "demo.delay", {"seconds": 30.0}),
            _node("down", "demo.copy", {"text": "x"}, inputs=True, out_port="output"),
        ],
        [_edge("e1", "slow", "down")],
    )
    started = _start(client, project, workflow)
    deadline = time.monotonic() + 15.0
    while time.monotonic() < deadline:
        current = client.post(
            "/api/runs/open",
            json={"path": str(project), "run_id": started["run_id"]},
            headers=AUTH_HEADERS,
        ).json()["run"]
        if current["node_runs"][0]["state"] == "running":
            break
        time.sleep(0.05)
    else:
        raise AssertionError("node never started running")
    begun = time.monotonic()
    cancelled = client.post(
        "/api/runs/cancel",
        json={"path": str(project), "run_id": started["run_id"]},
        headers=AUTH_HEADERS,
    )
    elapsed = time.monotonic() - begun
    assert cancelled.status_code == 200
    body = cancelled.json()["run"]
    assert body["state"] == "cancelled"
    assert elapsed < 12.0
    by_id = {node["id"]: node for node in body["node_runs"]}
    assert by_id["slow"]["attempt"] == 1
    assert by_id["slow"]["started_at"] is not None
    assert by_id["slow"]["artifacts"] == []
    assert by_id["down"]["attempt"] == 0
    assert by_id["down"]["started_at"] is None
    # Repeated cancellation is idempotent.
    again = client.post(
        "/api/runs/cancel",
        json={"path": str(project), "run_id": started["run_id"]},
        headers=AUTH_HEADERS,
    )
    assert again.status_code == 200
    assert again.json()["run"] == body


def test_cancel_missing_run_is_not_found(client: TestClient, tmp_path: Path) -> None:
    project = _make_project(client, tmp_path)
    response = client.post(
        "/api/runs/cancel",
        json={"path": str(project), "run_id": "run-missing"},
        headers=AUTH_HEADERS,
    )
    assert response.status_code == 404


# -- review ----------------------------------------------------------------------


def _review_workflow() -> dict[str, Any]:
    return _workflow([_node("gate", "demo.review", outputs=False)], [])


def test_review_approve_resumes_to_success(client: TestClient, tmp_path: Path) -> None:
    project = _make_project(client, tmp_path)
    started = _start(client, project, _review_workflow())
    parked = _wait_for_state(client, project, started["run_id"], {"waiting_for_review"})
    assert parked["node_runs"][0]["review_pause"]["decision"] is None

    decided = client.post(
        "/api/runs/review",
        json={
            "path": str(project),
            "run_id": started["run_id"],
            "node_run_id": "gate",
            "decision": "approved",
            "note": "looks good",
        },
        headers=AUTH_HEADERS,
    )
    assert decided.status_code == 200, decided.text
    finished = _wait_for_state(client, project, started["run_id"], {"succeeded"})
    assert finished["node_runs"][0]["review_pause"]["decision"] == "approved"
    assert "review_decided" in _kinds(finished)


def test_review_approve_redrives_while_parking_thread_exits(
    client: TestClient, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Approval cannot be lost while the original driver is still unwinding."""

    import threading

    from brainlearn_server.worker import WorkerService

    parked = threading.Event()
    release = threading.Event()
    original_settle = WorkerService._settle

    def _blocked_settle(
        self: WorkerService, project: str, record: RunRecord, has_waiting: bool
    ) -> None:
        original_settle(self, project, record, has_waiting)
        if has_waiting:
            parked.set()
            assert release.wait(timeout=5.0), "test did not release the parked driver"

    monkeypatch.setattr(WorkerService, "_settle", _blocked_settle)
    project = _make_project(client, tmp_path)
    started = _start(client, project, _review_workflow())
    assert parked.wait(timeout=5.0), "driver never reached its parked exit window"

    try:
        decided = client.post(
            "/api/runs/review",
            json={
                "path": str(project),
                "run_id": started["run_id"],
                "node_run_id": "gate",
                "decision": "approved",
                "note": "resume after parked-driver race",
            },
            headers=AUTH_HEADERS,
        )
        assert decided.status_code == 200, decided.text
    finally:
        release.set()

    finished = _wait_for_state(client, project, started["run_id"], {"succeeded"}, timeout=5.0)
    assert finished["node_runs"][0]["review_pause"]["decision"] == "approved"


def test_review_reject_fails_run(client: TestClient, tmp_path: Path) -> None:
    project = _make_project(client, tmp_path)
    started = _start(client, project, _review_workflow())
    _wait_for_state(client, project, started["run_id"], {"waiting_for_review"})

    decided = client.post(
        "/api/runs/review",
        json={
            "path": str(project),
            "run_id": started["run_id"],
            "node_run_id": "gate",
            "decision": "rejected",
            "note": "artifacts look wrong",
        },
        headers=AUTH_HEADERS,
    )
    assert decided.status_code == 200
    assert decided.json()["run"]["state"] == "failed"
    assert decided.json()["run"]["failure"]["code"] == "review_rejected"


def test_review_without_pending_pause_conflicts(client: TestClient, tmp_path: Path) -> None:
    project = _make_project(client, tmp_path)
    started = _start(client, project, _chain_workflow())
    response = client.post(
        "/api/runs/review",
        json={
            "path": str(project),
            "run_id": started["run_id"],
            "node_run_id": "first",
            "decision": "approved",
        },
        headers=AUTH_HEADERS,
    )
    assert response.status_code == 409


# -- restart, attempts, streaming --------------------------------------------------


def test_restart_resume_uses_fresh_attempts(client: TestClient, tmp_path: Path) -> None:
    project = _make_project(client, tmp_path)
    workflow = _workflow([_node("slow", "demo.delay", {"seconds": 0.01})], [])
    record = build_run_record(Workflow.model_validate(workflow))
    assert record.node_runs[0].attempt == 0
    runs.create_run(str(project), record)

    recovered = client.post("/api/runs/recover", json={"path": str(project)}, headers=AUTH_HEADERS)
    assert recovered.status_code == 200
    assert recovered.json() == []

    started = client.post(
        "/api/runs/start",
        json={"path": str(project), "run_id": record.id},
        headers=AUTH_HEADERS,
    )
    assert started.status_code == 200
    finished = _wait_for_state(client, project, record.id, {"succeeded"})
    assert finished["node_runs"][0]["attempt"] == 1
    assert any(
        event["kind"] == "node_started" and event["attempt"] == 1 for event in finished["events"]
    )


def test_recover_resumes_interrupted_run_automatically(client: TestClient, tmp_path: Path) -> None:
    project = _make_project(client, tmp_path)
    workflow = _workflow([_node("slow", "demo.delay", {"seconds": 0.01})], [])
    record = build_run_record(Workflow.model_validate(workflow))
    created = record.created_at
    node = record.node_runs[0].model_copy(
        update={
            "state": NodeRunState.RUNNING,
            "started_at": created,
            "attempt": 3,
        }
    )
    events = [
        *record.events,
        RunEvent(
            schema_version="1.0",
            seq=1,
            at=created,
            kind=RunEventKind.RUN_STARTED,
            node_run_id=None,
            message="Run started.",
        ),
        RunEvent(
            schema_version="1.0",
            seq=2,
            at=created,
            kind=RunEventKind.NODE_STARTED,
            node_run_id=node.id,
            attempt=3,
            message="Attempt 3 started.",
        ),
    ]
    running = record.model_copy(
        update={
            "state": RunState.RUNNING,
            "started_at": created,
            "node_runs": [node],
            "events": events,
        }
    )
    persisted = RunRecord.model_validate(running.model_dump(mode="json"))
    runs.create_run(str(project), persisted)
    staging = project / "runs" / persisted.id / "staging" / node.id / "attempt-3"
    staging.mkdir(parents=True, exist_ok=True)
    (staging / "partial.bin").write_bytes(b"interrupted")

    recovered = client.post("/api/runs/recover", json={"path": str(project)}, headers=AUTH_HEADERS)
    assert recovered.status_code == 200, recovered.text
    assert [entry["run_id"] for entry in recovered.json()] == [persisted.id]

    quarantined = project / "runs" / persisted.id / "quarantine" / f"{node.id}-interrupted"
    assert (quarantined / "attempt-3" / "partial.bin").read_bytes() == b"interrupted"
    assert list((project / "runs" / persisted.id / "staging").rglob("*")) == []

    finished = _wait_for_state(client, project, persisted.id, {"succeeded"})
    assert finished["node_runs"][0]["attempt"] == 4
    assert finished["node_runs"][0]["state"] == "succeeded"
    assert not list((project / "runs" / persisted.id / "staging").rglob("*"))
    leftover_files = [
        path
        for path in (project / "runs" / persisted.id / "artifacts").rglob("*")
        if path.is_file()
    ]
    assert leftover_files == []


def test_events_stream_replays_and_follows_to_terminal(client: TestClient, tmp_path: Path) -> None:
    project = _make_project(client, tmp_path)
    started = _start(client, project, _chain_workflow())
    run_id = started["run_id"]
    _wait_for_state(client, project, run_id, {"succeeded"})

    with client.stream(
        "GET",
        "/api/runs/events",
        params={"path": str(project), "run_id": run_id, "after": 1},
        headers=AUTH_HEADERS,
    ) as response:
        assert response.status_code == 200
        assert "text/event-stream" in response.headers["content-type"]
        kinds = []
        for line in response.iter_lines():
            if line.startswith("data: "):
                kinds.append(json.loads(line[len("data: ") :])["kind"])
    assert kinds[0] != "run_queued"
    assert kinds[-1] == "run_succeeded"
    seqs = []
    with client.stream(
        "GET",
        "/api/runs/events",
        params={"path": str(project), "run_id": run_id, "after": -1},
        headers=AUTH_HEADERS,
    ) as response:
        for line in response.iter_lines():
            if line.startswith("data: "):
                seqs.append(json.loads(line[len("data: ") :])["seq"])
    stored = client.post(
        "/api/runs/open",
        json={"path": str(project), "run_id": run_id},
        headers=AUTH_HEADERS,
    ).json()["run"]
    assert seqs == [event["seq"] for event in stored["events"]]


def test_events_stream_requires_auth_and_known_run(client: TestClient, tmp_path: Path) -> None:
    project = _make_project(client, tmp_path)
    with client.stream(
        "GET", "/api/runs/events", params={"path": str(project), "run_id": "x"}
    ) as response:
        assert response.status_code == 401
    _start(client, project, _chain_workflow())
    with client.stream(
        "GET",
        "/api/runs/events",
        params={"path": str(project), "run_id": "run-missing"},
        headers=AUTH_HEADERS,
    ) as response:
        assert response.status_code == 404


def test_cancelled_staging_is_quarantined_not_promoted(client: TestClient, tmp_path: Path) -> None:
    project = _make_project(client, tmp_path)
    workflow = _workflow([_node("slow", "demo.delay", {"seconds": 30.0})], [])
    started = _start(client, project, workflow)
    run_id = started["run_id"]
    deadline = time.monotonic() + 15.0
    while time.monotonic() < deadline:
        current = client.post(
            "/api/runs/open",
            json={"path": str(project), "run_id": run_id},
            headers=AUTH_HEADERS,
        ).json()["run"]
        if current["node_runs"][0]["state"] == "running":
            break
        time.sleep(0.05)
    else:
        raise AssertionError("node never started running")
    cancelled = client.post(
        "/api/runs/cancel",
        json={"path": str(project), "run_id": run_id},
        headers=AUTH_HEADERS,
    )
    assert cancelled.json()["run"]["state"] == "cancelled"
    assert not list((project / "runs" / run_id / "artifacts").rglob("*"))
    quarantine = list((project / "runs" / run_id / "quarantine").rglob("*"))
    assert quarantine, "interrupted staging must be quarantined"
    assert list((project / "runs" / run_id / "staging").rglob("*")) == []


# -- promotion containment ---------------------------------------------------------


def _evil_workflow(
    monkeypatch: pytest.MonkeyPatch, outputs: list[dict[str, str]]
) -> dict[str, Any]:
    from brainlearn_server import demo_nodes
    from brainlearn_server.demo_nodes import StagedOutput

    def _evil(ctx: Any) -> list[StagedOutput]:
        return [StagedOutput(port_id=item["port"], relative_path=item["path"]) for item in outputs]

    monkeypatch.setitem(
        demo_nodes.DEMO_NODES,
        "demo.evil",
        demo_nodes.DemoNodeManifest(
            version="0.1.0", inputs={}, outputs={"out": True}, handler=_evil
        ),
    )
    return _workflow([_node("evil", "demo.evil")], [])


def _run_evil(
    client: TestClient,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    outputs: list[dict[str, str]],
) -> tuple[Path, str]:
    project = _make_project(client, tmp_path)
    started = _start(client, project, _evil_workflow(monkeypatch, outputs))
    run_id = started["run_id"]
    finished = _wait_for_state(client, project, run_id, {"failed"})
    assert finished["node_runs"][0]["failure"]["code"] == "node_failed"
    return project, run_id


@pytest.mark.parametrize(
    "path",
    [
        "../../../escaped.txt",
        "/absolute.txt",
        "C:\\outside.txt",
        "C:/outside.txt",
        "..\\outside.txt",
        "a/../../outside.txt",
    ],
)
def test_promotion_rejects_unsafe_output_paths(
    client: TestClient, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, path: str
) -> None:
    project, run_id = _run_evil(client, tmp_path, monkeypatch, [{"port": "out", "path": path}])
    assert not (tmp_path / "escaped.txt").exists()
    assert not (project / "escaped.txt").exists()
    assert list((project / "runs" / run_id / "artifacts").rglob("*")) == []


def test_promotion_rejects_symlink_duplicate_and_undeclared_outputs(
    client: TestClient, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from brainlearn_server import demo_nodes
    from brainlearn_server.demo_nodes import StagedOutput

    def _tricky(ctx: Any) -> list[StagedOutput]:
        staging = ctx.staging_dir
        staging.mkdir(parents=True, exist_ok=True)
        (staging / "real.txt").write_text("real", encoding="utf-8")
        try:
            (staging / "link.txt").symlink_to(staging / "real.txt")
        except OSError:
            pass
        return [StagedOutput(port_id="out", relative_path="link.txt")]

    monkeypatch.setitem(
        demo_nodes.DEMO_NODES,
        "demo.evil",
        demo_nodes.DemoNodeManifest(
            version="0.1.0", inputs={}, outputs={"out": True}, handler=_tricky
        ),
    )
    project = _make_project(client, tmp_path)
    started = _start(client, project, _workflow([_node("evil", "demo.evil")], []))
    finished = _wait_for_state(client, project, started["run_id"], {"failed"})
    assert "link.txt" in finished["node_runs"][0]["failure"]["message"]
    assert list((project / "runs" / started["run_id"] / "artifacts").rglob("*")) == []

    project2 = _make_project(client, tmp_path, name="work2")

    def _dupe(ctx: Any) -> list[StagedOutput]:
        ctx.staging_dir.mkdir(parents=True, exist_ok=True)
        (ctx.staging_dir / "a.txt").write_text("a", encoding="utf-8")
        (ctx.staging_dir / "b.txt").write_text("b", encoding="utf-8")
        return [
            StagedOutput(port_id="out", relative_path="a.txt"),
            StagedOutput(port_id="out", relative_path="b.txt"),
        ]

    monkeypatch.setitem(
        demo_nodes.DEMO_NODES,
        "demo.evil",
        demo_nodes.DemoNodeManifest(
            version="0.1.0", inputs={}, outputs={"out": True}, handler=_dupe
        ),
    )
    started2 = _start(client, project2, _workflow([_node("evil", "demo.evil")], []))
    finished2 = _wait_for_state(client, project2, started2["run_id"], {"failed"})
    assert "duplicate output port" in finished2["node_runs"][0]["failure"]["message"]

    def _undeclared(ctx: Any) -> list[StagedOutput]:
        return [StagedOutput(port_id="nope", relative_path="a.txt")]

    monkeypatch.setitem(
        demo_nodes.DEMO_NODES,
        "demo.evil",
        demo_nodes.DemoNodeManifest(
            version="0.1.0", inputs={}, outputs={"out": True}, handler=_undeclared
        ),
    )
    started3 = _start(client, project2, _workflow([_node("evil2", "demo.evil")], []))
    finished3 = _wait_for_state(client, project2, started3["run_id"], {"failed"})
    assert "undeclared output port" in finished3["node_runs"][0]["failure"]["message"]

    def _missing(ctx: Any) -> list[StagedOutput]:
        return [StagedOutput(port_id="out", relative_path="never-written.txt")]

    monkeypatch.setitem(
        demo_nodes.DEMO_NODES,
        "demo.evil",
        demo_nodes.DemoNodeManifest(
            version="0.1.0", inputs={}, outputs={"out": True}, handler=_missing
        ),
    )
    started4 = _start(client, project2, _workflow([_node("evil3", "demo.evil")], []))
    finished4 = _wait_for_state(client, project2, started4["run_id"], {"failed"})
    assert "missing staged output" in finished4["node_runs"][0]["failure"]["message"]
    assert list((project2 / "runs" / started4["run_id"] / "artifacts").rglob("*")) == []


def test_second_output_failure_leaves_no_partial_artifacts(
    client: TestClient, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from brainlearn_server import demo_nodes
    from brainlearn_server.demo_nodes import StagedOutput

    def _partial(ctx: Any) -> list[StagedOutput]:
        ctx.staging_dir.mkdir(parents=True, exist_ok=True)
        (ctx.staging_dir / "one.txt").write_text("one", encoding="utf-8")
        return [
            StagedOutput(port_id="first", relative_path="one.txt"),
            StagedOutput(port_id="second", relative_path="missing.txt"),
        ]

    monkeypatch.setitem(
        demo_nodes.DEMO_NODES,
        "demo.evil",
        demo_nodes.DemoNodeManifest(
            version="0.1.0", inputs={}, outputs={"first": True, "second": True}, handler=_partial
        ),
    )
    project = _make_project(client, tmp_path)
    node = _node("evil", "demo.evil", outputs=False)
    node["ports"] = [_port("first", "output"), _port("second", "output")]
    started = _start(client, project, _workflow([node], []))
    finished = _wait_for_state(client, project, started["run_id"], {"failed"})
    assert "missing staged output" in finished["node_runs"][0]["failure"]["message"]
    assert finished["node_runs"][0]["artifacts"] == []
    assert list((project / "runs" / started["run_id"] / "artifacts").rglob("*")) == []


def test_cancel_after_promotion_removes_unreferenced_attempt(
    client: TestClient, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from brainlearn_server import demo_nodes
    from brainlearn_server.demo_nodes import StagedOutput
    from brainlearn_server.worker import WorkerService

    def _plain(ctx: Any) -> list[StagedOutput]:
        ctx.staging_dir.mkdir(parents=True, exist_ok=True)
        (ctx.staging_dir / "output.txt").write_text("too late", encoding="utf-8")
        return [StagedOutput(port_id="out", relative_path="output.txt")]

    monkeypatch.setitem(
        demo_nodes.DEMO_NODES,
        "demo.evil",
        demo_nodes.DemoNodeManifest(
            version="0.1.0", inputs={}, outputs={"out": True}, handler=_plain
        ),
    )
    promoted: dict[str, bool] = {}
    original_promote = WorkerService._promote

    def _spying_promote(self: Any, *args: Any, **kwargs: Any) -> Any:
        result = original_promote(self, *args, **kwargs)
        promoted["done"] = True
        self._cancel_flag(args[0], args[1]).set()
        return result

    monkeypatch.setattr(WorkerService, "_promote", _spying_promote)
    project = _make_project(client, tmp_path)
    started = _start(client, project, _workflow([_node("evil", "demo.evil")], []))
    finished = _wait_for_state(client, project, started["run_id"], {"cancelled"})
    assert promoted.get("done") is True, "promotion must run before cancellation cleanup"
    assert finished["node_runs"][0]["state"] == "cancelled"
    assert finished["node_runs"][0]["artifacts"] == []
    assert list((project / "runs" / started["run_id"] / "artifacts").rglob("*")) == []


# -- workflow identity ---------------------------------------------------------------


def test_workflow_identity_ignores_presentation() -> None:
    from brainlearn_core import Workflow as WorkflowModel
    from brainlearn_server.worker import workflow_identity

    base = _chain_workflow()
    renamed_edges = _workflow(base["nodes"], [{**base["edges"][0], "id": "renamed-edge"}])
    relabeled = _workflow(
        [
            {**node, "label": "Different", "description": "changed", "position": {"x": 9, "y": 9}}
            for node in base["nodes"]
        ],
        base["edges"],
    )
    reordered = _workflow(list(reversed(base["nodes"])), list(reversed(base["edges"])))
    reference = workflow_identity(WorkflowModel.model_validate(base))
    assert workflow_identity(WorkflowModel.model_validate(renamed_edges)) == reference
    assert workflow_identity(WorkflowModel.model_validate(relabeled)) == reference
    assert workflow_identity(WorkflowModel.model_validate(reordered)) == reference


def test_workflow_identity_tracks_topology_and_endpoints() -> None:
    from brainlearn_core import Workflow as WorkflowModel
    from brainlearn_server.worker import workflow_identity

    base = _chain_workflow()
    reference = workflow_identity(WorkflowModel.model_validate(base))
    changed_port = _workflow(
        base["nodes"],
        [
            {
                "id": "e1",
                "source": {"node_id": "first", "port_id": "out"},
                "target": {"node_id": "second", "port_id": "other"},
            }
        ],
    )
    removed_edge = _workflow(base["nodes"], [])
    changed_param = _workflow(
        [{**base["nodes"][1], "parameters": [{"id": "text", "label": "text", "value": "other"}]}]
        + [base["nodes"][0]],
        base["edges"],
    )
    assert workflow_identity(WorkflowModel.model_validate(changed_port)) != reference
    assert workflow_identity(WorkflowModel.model_validate(removed_edge)) != reference
    assert workflow_identity(WorkflowModel.model_validate(changed_param)) != reference


# -- recovery of unreferenced final attempts ----------------------------------------


def test_recovery_quarantines_unreferenced_final_attempt(
    client: TestClient, tmp_path: Path
) -> None:
    project = _make_project(client, tmp_path)
    workflow = _workflow([_node("slow", "demo.delay", {"seconds": 0.01})], [])
    record = build_run_record(Workflow.model_validate(workflow))
    created = record.created_at
    node = record.node_runs[0].model_copy(
        update={"state": NodeRunState.RUNNING, "started_at": created, "attempt": 2}
    )
    events = [
        *record.events,
        RunEvent(
            schema_version="1.0",
            seq=1,
            at=created,
            kind=RunEventKind.RUN_STARTED,
            node_run_id=None,
            message="Run started.",
        ),
        RunEvent(
            schema_version="1.0",
            seq=2,
            at=created,
            kind=RunEventKind.NODE_STARTED,
            node_run_id=node.id,
            attempt=2,
            message="Attempt 2 started.",
        ),
    ]
    running = RunRecord.model_validate(
        record.model_copy(
            update={
                "state": RunState.RUNNING,
                "started_at": created,
                "node_runs": [node],
                "events": list(events),
            }
        ).model_dump(mode="json")
    )
    runs.create_run(str(project), running)
    # Simulate a crash after promotion but before record persistence: a final
    # attempt directory exists while the record lists no artifacts.
    attempt_dir = project / "runs" / running.id / "artifacts" / node.id / "attempt-2"
    attempt_dir.mkdir(parents=True, exist_ok=True)
    (attempt_dir / "output.txt").write_text("orphan", encoding="utf-8")

    recovered = client.post("/api/runs/recover", json={"path": str(project)}, headers=AUTH_HEADERS)
    assert recovered.status_code == 200, recovered.text
    quarantine = list((project / "runs" / running.id / "quarantine").rglob("output.txt"))
    assert len(quarantine) == 1
    assert quarantine[0].read_text(encoding="utf-8") == "orphan"
    assert quarantine[0].parent.name == "slow-attempt-2-orphaned"
    assert not (attempt_dir / "output.txt").exists()

    finished = _wait_for_state(client, project, running.id, {"succeeded"})
    assert finished["node_runs"][0]["attempt"] == 3


# -- adapter manifest and port contracts ---------------------------------------------


def test_build_rejects_unknown_and_missing_ports(client: TestClient, tmp_path: Path) -> None:
    project = _make_project(client, tmp_path)

    bad_input = _workflow([_node("c", "demo.copy", inputs=True, out_port="bogus")], [])
    response = client.post(
        "/api/runs/start",
        json={"path": str(project), "workflow": bad_input},
        headers=AUTH_HEADERS,
    )
    assert response.status_code == 400
    assert "unknown output port" in response.json()["detail"]

    missing_required = _workflow([_node("r", "demo.relay", out_port="output")], [])
    response = client.post(
        "/api/runs/start",
        json={"path": str(project), "workflow": missing_required},
        headers=AUTH_HEADERS,
    )
    assert response.status_code == 400
    assert "missing required input port" in response.json()["detail"]


def test_upstream_bytes_reach_downstream_node(client: TestClient, tmp_path: Path) -> None:
    project = _make_project(client, tmp_path)
    workflow = _workflow(
        [
            _node("first", "demo.copy", {"text": "payload-123"}, out_port="output"),
            _node("second", "demo.relay", inputs=True, out_port="output"),
        ],
        [_edge("e1", "first", "second", source_port="output", target_port="in")],
    )
    started = _start(client, project, workflow)
    finished = _wait_for_state(client, project, started["run_id"], {"succeeded"})
    by_id = {node["id"]: node for node in finished["node_runs"]}
    assert by_id["second"]["inputs"]["in"] == by_id["first"]["content_identity"]
    [artifact] = by_id["second"]["artifacts"]
    content = (project / artifact["path"]).read_text(encoding="utf-8")
    first_artifact = (project / by_id["first"]["artifacts"][0]["path"]).read_text(encoding="utf-8")
    assert (
        content == f"{project}/runs/{started['run_id']}/artifacts/first/attempt-1/output.txt"
        f"\n{first_artifact}"
    )
    assert first_artifact == "payload-123"


# -- destination containment -----------------------------------------------------------


def _posix_only() -> None:
    import os

    if os.name == "nt":
        pytest.skip("symlinks need privileges on Windows")


def test_symlinked_artifact_parent_is_refused(client: TestClient, tmp_path: Path) -> None:
    _posix_only()
    project = _make_project(client, tmp_path)
    workflow = _workflow([_node("writer", "demo.copy", {"text": "x"}, out_port="output")], [])
    record = build_run_record(Workflow.model_validate(workflow))
    runs.create_run(str(project), record)
    external = tmp_path / "external"
    external.mkdir(parents=True, exist_ok=True)
    sentinel = external / "sentinel.txt"
    sentinel.write_text("untouched", encoding="utf-8")
    (project / "runs" / record.id / "artifacts").mkdir(parents=True, exist_ok=True)
    (project / "runs" / record.id / "artifacts" / "writer").symlink_to(
        external, target_is_directory=True
    )

    started = client.post(
        "/api/runs/start",
        json={"path": str(project), "run_id": record.id},
        headers=AUTH_HEADERS,
    )
    assert started.status_code == 200
    finished = _wait_for_state(client, project, record.id, {"failed"})
    assert "symlink" in finished["node_runs"][0]["failure"]["message"]
    assert sentinel.read_text(encoding="utf-8") == "untouched"
    assert list(external.rglob("*")) == [sentinel]


def test_adapter_precreated_assembly_is_refused(client: TestClient, tmp_path: Path) -> None:
    _posix_only()
    project = _make_project(client, tmp_path)
    workflow = _workflow([_node("writer", "demo.copy", {"text": "x"}, out_port="output")], [])
    record = build_run_record(Workflow.model_validate(workflow))
    runs.create_run(str(project), record)
    planted = project / "runs" / record.id / "staging" / "writer" / "attempt-1" / "complete"
    planted.mkdir(parents=True, exist_ok=True)
    sentinel = planted / "sentinel.txt"
    sentinel.write_text("untouched", encoding="utf-8")

    started = client.post(
        "/api/runs/start",
        json={"path": str(project), "run_id": record.id},
        headers=AUTH_HEADERS,
    )
    assert started.status_code == 200
    finished = _wait_for_state(client, project, record.id, {"failed"})
    assert "must not precreate" in finished["node_runs"][0]["failure"]["message"]
    quarantined = list((project / "runs" / record.id / "quarantine").rglob("sentinel.txt"))
    assert len(quarantined) == 1
    assert quarantined[0].read_text(encoding="utf-8") == "untouched"
    assert list((project / "runs" / record.id / "artifacts").rglob("*")) == []


def test_symlinked_quarantine_target_uses_free_name(client: TestClient, tmp_path: Path) -> None:
    _posix_only()
    project = _make_project(client, tmp_path)
    workflow = _workflow([_node("slow", "demo.delay", {"seconds": 0.01})], [])
    record = build_run_record(Workflow.model_validate(workflow))
    created = record.created_at
    node = record.node_runs[0].model_copy(
        update={"state": NodeRunState.RUNNING, "started_at": created, "attempt": 1}
    )
    events = [
        *record.events,
        RunEvent(
            schema_version="1.0",
            seq=1,
            at=created,
            kind=RunEventKind.RUN_STARTED,
            node_run_id=None,
            message="Run started.",
        ),
    ]
    running = RunRecord.model_validate(
        record.model_copy(
            update={
                "state": RunState.RUNNING,
                "started_at": created,
                "node_runs": [node],
                "events": list(events),
            }
        ).model_dump(mode="json")
    )
    runs.create_run(str(project), running)
    leftover = project / "runs" / running.id / "staging" / node.id / "attempt-1"
    leftover.mkdir(parents=True, exist_ok=True)
    (leftover / "partial.bin").write_bytes(b"stale")
    external = tmp_path / "external"
    external.mkdir(parents=True, exist_ok=True)
    sentinel = external / "sentinel.txt"
    sentinel.write_text("untouched", encoding="utf-8")
    (project / "runs" / running.id / "quarantine").mkdir(parents=True, exist_ok=True)
    (project / "runs" / running.id / "quarantine" / f"{node.id}-interrupted").symlink_to(
        external, target_is_directory=True
    )

    recovered = client.post("/api/runs/recover", json={"path": str(project)}, headers=AUTH_HEADERS)
    assert recovered.status_code == 200, recovered.text
    # A blocked preferred name must not be overwritten and must not strand
    # staging: the leftover moves to the first free suffixed name.
    assert not (leftover / "partial.bin").exists()
    assert sentinel.read_text(encoding="utf-8") == "untouched"
    assert list(external.rglob("*")) == [sentinel]
    moved = list((project / "runs" / running.id / "quarantine").rglob("partial.bin"))
    assert len(moved) == 1
    assert moved[0].read_bytes() == b"stale"
    assert moved[0].parent.parent.name != f"{node.id}-interrupted"


def test_post_promotion_failure_with_blocked_quarantine_cleans_up(
    client: TestClient, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _posix_only()
    from brainlearn_server.worker import WorkerService

    project = _make_project(client, tmp_path)
    workflow = _workflow([_node("writer", "demo.copy", {"text": "x"}, out_port="output")], [])
    record = build_run_record(Workflow.model_validate(workflow))
    runs.create_run(str(project), record)
    external = tmp_path / "external"
    external.mkdir(parents=True, exist_ok=True)
    sentinel = external / "sentinel.txt"
    sentinel.write_text("untouched", encoding="utf-8")
    (project / "runs" / record.id / "quarantine").mkdir(parents=True, exist_ok=True)
    (project / "runs" / record.id / "quarantine" / "writer-attempt-1-uncommitted").symlink_to(
        external, target_is_directory=True
    )

    def _boom(self: object, *args: object, **kwargs: object) -> None:
        raise RuntimeError("persistence boom after promotion")

    monkeypatch.setattr(WorkerService, "_finish_node", _boom)
    started = client.post(
        "/api/runs/start",
        json={"path": str(project), "run_id": record.id},
        headers=AUTH_HEADERS,
    )
    assert started.status_code == 200, started.text
    finished = _wait_for_state(client, project, record.id, {"failed"})
    assert finished["node_runs"][0]["artifacts"] == []
    assert sentinel.read_text(encoding="utf-8") == "untouched"
    assert list(external.rglob("*")) == [sentinel]
    assert list((project / "runs" / record.id / "artifacts").rglob("*")) == []
    quarantined = list((project / "runs" / record.id / "quarantine").rglob("output.txt"))
    assert len(quarantined) == 1
    assert quarantined[0].read_text(encoding="utf-8") == "x"
    assert quarantined[0].parent.name != "writer-attempt-1-uncommitted"


def test_recovery_quarantines_orphan_with_blocked_target(
    client: TestClient, tmp_path: Path
) -> None:
    _posix_only()
    project = _make_project(client, tmp_path)
    workflow = _workflow([_node("slow", "demo.delay", {"seconds": 0.01})], [])
    record = build_run_record(Workflow.model_validate(workflow))
    created = record.created_at
    node = record.node_runs[0].model_copy(
        update={"state": NodeRunState.RUNNING, "started_at": created, "attempt": 2}
    )
    events = [
        *record.events,
        RunEvent(
            schema_version="1.0",
            seq=1,
            at=created,
            kind=RunEventKind.RUN_STARTED,
            node_run_id=None,
            message="Run started.",
        ),
        RunEvent(
            schema_version="1.0",
            seq=2,
            at=created,
            kind=RunEventKind.NODE_STARTED,
            node_run_id=node.id,
            attempt=2,
            message="Attempt 2 started.",
        ),
    ]
    running = RunRecord.model_validate(
        record.model_copy(
            update={
                "state": RunState.RUNNING,
                "started_at": created,
                "node_runs": [node],
                "events": list(events),
            }
        ).model_dump(mode="json")
    )
    runs.create_run(str(project), running)
    attempt_dir = project / "runs" / running.id / "artifacts" / node.id / "attempt-2"
    attempt_dir.mkdir(parents=True, exist_ok=True)
    (attempt_dir / "output.txt").write_text("orphan", encoding="utf-8")
    external = tmp_path / "external"
    external.mkdir(parents=True, exist_ok=True)
    sentinel = external / "sentinel.txt"
    sentinel.write_text("untouched", encoding="utf-8")
    (project / "runs" / running.id / "quarantine").mkdir(parents=True, exist_ok=True)
    (project / "runs" / running.id / "quarantine" / f"{node.id}-attempt-2-orphaned").symlink_to(
        external, target_is_directory=True
    )

    recovered = client.post("/api/runs/recover", json={"path": str(project)}, headers=AUTH_HEADERS)
    assert recovered.status_code == 200, recovered.text
    assert sentinel.read_text(encoding="utf-8") == "untouched"
    assert list(external.rglob("*")) == [sentinel]
    assert not (attempt_dir / "output.txt").exists()
    moved = list((project / "runs" / running.id / "quarantine").rglob("output.txt"))
    assert len(moved) == 1
    assert moved[0].read_text(encoding="utf-8") == "orphan"


def test_build_rejects_missing_required_output(client: TestClient, tmp_path: Path) -> None:
    project = _make_project(client, tmp_path)
    workflow = _workflow([_node("c", "demo.copy", outputs=False)], [])
    response = client.post(
        "/api/runs/start",
        json={"path": str(project), "workflow": workflow},
        headers=AUTH_HEADERS,
    )
    assert response.status_code == 400
    assert "missing required output" in response.json()["detail"]


def test_build_rejects_disconnected_required_input(client: TestClient, tmp_path: Path) -> None:
    project = _make_project(client, tmp_path)
    workflow = _workflow([_node("r", "demo.relay", inputs=True, out_port="output")], [])
    response = client.post(
        "/api/runs/start",
        json={"path": str(project), "workflow": workflow},
        headers=AUTH_HEADERS,
    )
    assert response.status_code == 400
    assert "no edge supplies" in response.json()["detail"]


def test_success_omitting_required_output_fails(
    client: TestClient, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from brainlearn_server import demo_nodes

    def _empty(ctx: Any) -> list[Any]:
        ctx.staging_dir.mkdir(parents=True, exist_ok=True)
        return []

    monkeypatch.setitem(
        demo_nodes.DEMO_NODES,
        "demo.evil",
        demo_nodes.DemoNodeManifest(
            version="0.1.0", inputs={}, outputs={"out": True}, handler=_empty
        ),
    )
    project = _make_project(client, tmp_path)
    started = _start(client, project, _workflow([_node("evil", "demo.evil")], []))
    finished = _wait_for_state(client, project, started["run_id"], {"failed"})
    assert "omitted required output" in finished["node_runs"][0]["failure"]["message"]
    assert finished["node_runs"][0]["artifacts"] == []
    assert list((project / "runs" / started["run_id"] / "artifacts").rglob("*")) == []


def test_staging_root_symlink_fails_run_instead_of_stranding(
    client: TestClient, tmp_path: Path
) -> None:
    _posix_only()
    project = _make_project(client, tmp_path)
    workflow = _workflow([_node("writer", "demo.copy", {"text": "x"}, out_port="output")], [])
    record = build_run_record(Workflow.model_validate(workflow))
    runs.create_run(str(project), record)
    external = tmp_path / "external"
    external.mkdir(parents=True, exist_ok=True)
    sentinel = external / "sentinel.txt"
    sentinel.write_text("untouched", encoding="utf-8")
    (project / "runs" / record.id / "staging").symlink_to(external, target_is_directory=True)

    started = client.post(
        "/api/runs/start",
        json={"path": str(project), "run_id": record.id},
        headers=AUTH_HEADERS,
    )
    assert started.status_code == 200, started.text
    finished = _wait_for_state(client, project, record.id, {"failed"})
    assert finished["node_runs"][0]["state"] == "failed"
    assert "symlink" in finished["node_runs"][0]["failure"]["message"]
    assert finished["node_runs"][0]["artifacts"] == []
    assert sentinel.read_text(encoding="utf-8") == "untouched"
    assert list(external.rglob("*")) == [sentinel]
    assert list((project / "runs" / record.id / "artifacts").rglob("*")) == []
