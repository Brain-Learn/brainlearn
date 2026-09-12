"""Step 4B: project-scoped run persistence, recovery, and run endpoints."""

import json
from pathlib import Path
from typing import Any

import pytest
from brainlearn_core import RunRecord, RunState
from brainlearn_server import auth as auth_module
from brainlearn_server import project_store as store_module
from brainlearn_server.app import app, store
from brainlearn_server.auth import reset_session_token_for_tests
from brainlearn_server.run_store import RunStore
from fastapi.testclient import TestClient

FIXTURES = Path(__file__).parent / "fixtures"

TEST_TOKEN = "step4b-test-token-0123456789"
AUTH_HEADERS = {"Authorization": f"Bearer {TEST_TOKEN}"}
RECOVERY_TIME = "2026-09-12T10:00:00+00:00"

ENV_IDENTITY = (
    "brainlearn-v1:environment:ff07c69cbbcba662faf1f5bf3934f5206e99d149d4214ca83b5e49455e2ccbaa"
)
INSPECT_IDENTITY = (
    "brainlearn-v1:node:a18463b1cbce1c2c32e9a9f60dece71b35a60435fb17e845c2a6c51a4497b2b1"
)
FILTER_IDENTITY = (
    "brainlearn-v1:node:bc9e6acbbdc9c13dd5795575ae60d7a47830f26f63171e0dbc32fc04dbe34feb"
)
ICA_DEPENDENT_IDENTITY = (
    "brainlearn-v1:node:1cb24c05b45761645e30e3b8dc41f845f2d91a4a7493fc970981b14d0e36ad21"
)
ICA_INDEPENDENT_IDENTITY = (
    "brainlearn-v1:node:cbeffbc609e808cc858756ffdb456c237f63baf362afe863fcd5359bf284843d"
)


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


def _load_run(name: str) -> dict[str, Any]:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def _make_project(tmp_path: Path, name: str = "demo") -> Path:
    project = tmp_path / name
    project.mkdir(parents=True, exist_ok=True)
    (project / "brainlearn.project.json").write_text(
        json.dumps(
            {
                "project_schema_version": "1.0",
                "id": name,
                "name": name,
                "description": "",
                "workflow_id": "eeg-first-look",
                "brainlearn_version": "0.1.0",
                "created_at": "2026-09-12T00:00:00+00:00",
                "updated_at": "2026-09-12T00:00:00+00:00",
            }
        ),
        encoding="utf-8",
    )
    (project / "workflow.json").write_text(
        (FIXTURES / "workflow-1.0.json").read_text(encoding="utf-8"), encoding="utf-8"
    )
    store.register_root(project)
    return project


def _run_store() -> RunStore:
    return RunStore(store)


# -- persistence ---------------------------------------------------------


def test_create_get_list_save_round_trip(tmp_path: Path) -> None:
    project = _make_project(tmp_path)
    backend = _run_store()
    record = RunRecord.model_validate(_load_run("run-1.0.json"))

    created = backend.create_run(str(project), record)
    assert created.id == "run-demo-001"
    assert (project / "runs" / "run-demo-001" / "run.json").is_file()

    assert backend.get_run(str(project), "run-demo-001") == created
    assert [run.id for run in backend.list_runs(str(project))] == ["run-demo-001"]

    running = RunRecord.model_validate(_load_run("run-running-1.0.json"))
    backend.create_run(str(project), running)
    noted_events = [
        event.model_copy(update={"message": "operator note"}) if event.seq == 0 else event
        for event in running.events
    ]
    edited = running.model_copy(update={"events": noted_events})
    assert backend.save_run(str(project), edited).events[0].message == "operator note"
    assert backend.get_run(str(project), "run-demo-running").events[0].message == "operator note"


def test_create_refuses_duplicate_run_ids(tmp_path: Path) -> None:
    project = _make_project(tmp_path)
    backend = _run_store()
    record = RunRecord.model_validate(_load_run("run-1.0.json"))
    backend.create_run(str(project), record)
    with pytest.raises(FileExistsError, match="already exists"):
        backend.create_run(str(project), record)


def test_save_refuses_unknown_runs(tmp_path: Path) -> None:
    project = _make_project(tmp_path)
    backend = _run_store()
    with pytest.raises(FileNotFoundError, match="not found"):
        backend.save_run(str(project), RunRecord.model_validate(_load_run("run-1.0.json")))
    with pytest.raises(FileNotFoundError, match="not found"):
        backend.get_run(str(project), "run-missing")


def _terminal_run(state: str) -> RunRecord:
    raw = _load_run("run-1.0.json")
    raw["state"] = state
    if state == "failed":
        raw["failure"] = {
            "schema_version": "1.0",
            "code": "node_failed",
            "message": "Filter diverged from its reference summary.",
            "node_run_id": "node-run-filter-001",
            "at": "2026-09-12T09:05:30+00:00",
        }
    return RunRecord.model_validate(raw)


@pytest.mark.parametrize("state", ["succeeded", "failed", "cancelled"])
def test_save_preserves_terminal_history(tmp_path: Path, state: str) -> None:
    project = _make_project(tmp_path)
    backend = _run_store()
    record = _terminal_run(state)
    backend.create_run(str(project), record)
    target = project / "runs" / record.id / "run.json"
    before = target.read_bytes()

    edited_events = [
        event.model_copy(update={"message": "rewritten provenance"}) if event.seq == 0 else event
        for event in record.events
    ]
    with pytest.raises(ValueError, match="immutable"):
        backend.save_run(str(project), record.model_copy(update={"events": edited_events}))
    assert target.read_bytes() == before
    assert backend.get_run(str(project), record.id) == record


def test_run_ids_reject_path_traversal(tmp_path: Path) -> None:
    project = _make_project(tmp_path)
    backend = _run_store()
    for run_id in ["../escape", "a/b", "", ".."]:
        with pytest.raises(ValueError, match="Run ID"):
            backend.get_run(str(project), run_id)


def test_runs_outside_authorized_roots_are_rejected(tmp_path: Path) -> None:
    backend = _run_store()
    record = RunRecord.model_validate(_load_run("run-1.0.json"))
    with pytest.raises(PermissionError, match="explicitly opened"):
        backend.create_run(str(tmp_path / "elsewhere"), record)


def test_list_runs_ignores_unrelated_files(tmp_path: Path) -> None:
    project = _make_project(tmp_path)
    backend = _run_store()
    backend.create_run(str(project), RunRecord.model_validate(_load_run("run-1.0.json")))
    (project / "runs" / "notes.txt").write_text("not a run", encoding="utf-8")
    assert [run.id for run in backend.list_runs(str(project))] == ["run-demo-001"]


def _symlink_supported() -> bool:
    import os

    return os.name != "nt"


def test_discovery_ignores_external_symlinked_directories(tmp_path: Path) -> None:
    if not _symlink_supported():
        pytest.skip("symlinks need privileges on Windows")
    project = _make_project(tmp_path)
    backend = _run_store()
    backend.create_run(str(project), RunRecord.model_validate(_load_run("run-1.0.json")))
    external = tmp_path / "external"
    external.mkdir(parents=True, exist_ok=True)
    (external / "run.json").write_text(
        (FIXTURES / "run-1.0.json").read_text(encoding="utf-8"), encoding="utf-8"
    )
    (project / "runs" / "linked").symlink_to(external, target_is_directory=True)
    assert [run.id for run in backend.list_runs(str(project))] == ["run-demo-001"]
    assert backend.recover_runs(str(project), now=RECOVERY_TIME) == []


def test_discovery_ignores_symlinked_run_files(tmp_path: Path) -> None:
    if not _symlink_supported():
        pytest.skip("symlinks need privileges on Windows")
    project = _make_project(tmp_path)
    backend = _run_store()
    slot = project / "runs" / "run-linked-file"
    slot.mkdir(parents=True, exist_ok=True)
    (slot / "run.json").symlink_to(FIXTURES / "run-1.0.json")
    assert backend.list_runs(str(project)) == []


def test_discovery_ignores_invalid_directory_names(tmp_path: Path) -> None:
    project = _make_project(tmp_path)
    backend = _run_store()
    slot = project / "runs" / "not a run id!"
    slot.mkdir(parents=True, exist_ok=True)
    (slot / "run.json").write_text(
        (FIXTURES / "run-1.0.json").read_text(encoding="utf-8"), encoding="utf-8"
    )
    assert backend.list_runs(str(project)) == []


def test_discovery_rejects_directory_record_mismatch(tmp_path: Path) -> None:
    project = _make_project(tmp_path)
    backend = _run_store()
    slot = project / "runs" / "run-other-name"
    slot.mkdir(parents=True, exist_ok=True)
    (slot / "run.json").write_text(
        (FIXTURES / "run-1.0.json").read_text(encoding="utf-8"), encoding="utf-8"
    )
    with pytest.raises(
        ValueError, match="run-other-name.*run-demo-001|run-demo-001.*run-other-name"
    ):
        backend.list_runs(str(project))
    with pytest.raises(ValueError, match="run-other-name"):
        backend.get_run(str(project), "run-other-name")


def _corrupt_run_file(tmp_path: Path, run_id: str, text: str) -> Path:
    project = _make_project(tmp_path)
    target = project / "runs" / run_id
    target.mkdir(parents=True, exist_ok=True)
    candidate = target / "run.json"
    candidate.write_text(text, encoding="utf-8")
    return project


@pytest.mark.parametrize(
    ("text", "match"),
    [
        ("{not json", "cannot be read"),
        ('{"schema_version": "9.9"}', "invalid"),
        ('{"schema_version": "1.0", "id": "broken"}', "invalid"),
    ],
)
def test_list_runs_surfaces_corrupt_files(tmp_path: Path, text: str, match: str) -> None:
    project = _corrupt_run_file(tmp_path, "run-broken", text)
    backend = _run_store()
    with pytest.raises(ValueError, match=f"run-broken.*{match}|{match}.*run-broken"):
        backend.list_runs(str(project))
    with pytest.raises(ValueError, match="run-broken"):
        backend.recover_runs(str(project), now=RECOVERY_TIME)


def test_descendant_paths_are_not_project_roots(tmp_path: Path) -> None:
    project = _make_project(tmp_path)
    backend = _run_store()
    nested = project / "nested"
    nested.mkdir(parents=True, exist_ok=True)
    with pytest.raises(ValueError, match="not an explicitly authorized project root"):
        backend.create_run(str(nested), RunRecord.model_validate(_load_run("run-1.0.json")))
    with pytest.raises(ValueError, match="not an explicitly authorized project root"):
        backend.list_runs(str(nested))


def test_deleted_project_roots_are_rejected(tmp_path: Path) -> None:
    project = _make_project(tmp_path)
    backend = _run_store()
    (project / "brainlearn.project.json").unlink()
    with pytest.raises(FileNotFoundError, match="complete project"):
        backend.list_runs(str(project))


# -- recovery --------------------------------------------------------------


def test_recovery_requeues_interrupted_nodes(tmp_path: Path) -> None:
    project = _make_project(tmp_path)
    backend = _run_store()
    backend.create_run(str(project), RunRecord.model_validate(_load_run("run-running-1.0.json")))

    recovered = backend.recover_runs(str(project), now=RECOVERY_TIME)
    assert [run.id for run in recovered] == ["run-demo-running"]

    repaired = backend.get_run(str(project), "run-demo-running")
    assert repaired.state == RunState.RUNNING
    assert repaired.started_at == "2026-09-12T09:01:00+00:00"
    by_id = {node.id: node for node in repaired.node_runs}
    assert by_id["node-run-filter-001"].state.value == "queued"
    assert by_id["node-run-filter-001"].attempt == 0
    assert by_id["node-run-filter-001"].started_at is None
    assert by_id["node-run-inspect-001"].state.value == "succeeded"
    kinds = [event.kind.value for event in repaired.events]
    assert kinds[-1:] == ["node_queued"]
    assert repaired.events[-1].at == RECOVERY_TIME
    assert "attempt 1 was voided" in repaired.events[-1].message


def test_recovery_returns_run_to_queued_when_nothing_completed(tmp_path: Path) -> None:
    project = _make_project(tmp_path)
    backend = _run_store()
    raw = _load_run("run-running-1.0.json")
    only_filter = dict(raw["node_runs"][1], dependencies=[])
    raw["node_runs"] = [only_filter]
    raw["events"] = [event for event in raw["events"] if event["seq"] == 0]
    backend.create_run(str(project), RunRecord.model_validate(raw))

    (repaired,) = backend.recover_runs(str(project), now=RECOVERY_TIME)
    assert repaired.state == RunState.QUEUED
    assert repaired.started_at is None
    assert repaired.node_runs[0].state.value == "queued"
    assert repaired.node_runs[0].attempt == 0
    assert [event.kind.value for event in repaired.events][-2:] == [
        "node_queued",
        "run_queued",
    ]


def _waiting_ica(
    dependencies: list[str],
    inputs: dict[str, str],
    content_identity: str,
    review_input: str,
) -> dict[str, Any]:
    return {
        "schema_version": "1.0",
        "id": "node-run-ica-001",
        "node_id": "ica",
        "node_type": "eeg.ica_review",
        "node_version": "0.1.0",
        "dependencies": dependencies,
        "attempt": 1,
        "state": "waiting_for_review",
        "started_at": "2026-09-12T09:05:00+00:00",
        "finished_at": None,
        "inputs": inputs,
        "parameters": {},
        "environment_identity": ENV_IDENTITY,
        "seed": 7,
        "settings": {},
        "content_identity": content_identity,
        "artifacts": [],
        "failure": None,
        "review_pause": {
            "schema_version": "1.0",
            "id": "review-ica-001",
            "node_run_id": "node-run-ica-001",
            "input_identity": review_input,
            "requested_at": "2026-09-12T09:05:30+00:00",
            "decided_at": None,
            "decision": None,
            "note": "",
        },
    }


def test_recovery_preserves_independent_pending_reviews(tmp_path: Path) -> None:
    project = _make_project(tmp_path)
    backend = _run_store()
    raw = _load_run("run-running-1.0.json")
    waiting = _waiting_ica(
        ["node-run-inspect-001"],
        {"raw": INSPECT_IDENTITY},
        ICA_INDEPENDENT_IDENTITY,
        INSPECT_IDENTITY,
    )
    raw["node_runs"] = [*raw["node_runs"], waiting]
    raw["state"] = "waiting_for_review"
    backend.create_run(str(project), RunRecord.model_validate(raw))

    (recovered,) = backend.recover_runs(str(project), now=RECOVERY_TIME)
    assert recovered.state == RunState.WAITING_FOR_REVIEW
    by_id = {node.id: node for node in recovered.node_runs}
    assert by_id["node-run-filter-001"].state.value == "queued"
    assert by_id["node-run-ica-001"].state.value == "waiting_for_review"
    assert by_id["node-run-ica-001"].review_pause is not None
    assert by_id["node-run-ica-001"].review_pause.decided_at is None


def test_recovery_invalidates_reviews_depending_on_interrupted_nodes(tmp_path: Path) -> None:
    project = _make_project(tmp_path)
    backend = _run_store()
    raw = _load_run("run-running-1.0.json")
    waiting = _waiting_ica(
        ["node-run-filter-001"],
        {"raw": FILTER_IDENTITY},
        ICA_DEPENDENT_IDENTITY,
        FILTER_IDENTITY,
    )
    raw["node_runs"] = [*raw["node_runs"], waiting]
    raw["state"] = "waiting_for_review"
    backend.create_run(str(project), RunRecord.model_validate(raw))

    (recovered,) = backend.recover_runs(str(project), now=RECOVERY_TIME)
    assert recovered.state == RunState.RUNNING
    by_id = {node.id: node for node in recovered.node_runs}
    assert by_id["node-run-filter-001"].state.value == "queued"
    invalidated = by_id["node-run-ica-001"]
    assert invalidated.state.value == "queued"
    assert invalidated.attempt == 0
    assert invalidated.review_pause is None
    node_queued = [event for event in recovered.events if event.kind.value == "node_queued"]
    assert {event.node_run_id for event in node_queued} == {
        "node-run-filter-001",
        "node-run-ica-001",
    }
    assert all(event.attempt == 1 for event in node_queued)


def test_recovery_leaves_terminal_history_untouched(tmp_path: Path) -> None:
    project = _make_project(tmp_path)
    backend = _run_store()
    backend.create_run(str(project), RunRecord.model_validate(_load_run("run-1.0.json")))
    before = (project / "runs" / "run-demo-001" / "run.json").read_text(encoding="utf-8")

    assert backend.recover_runs(str(project), now=RECOVERY_TIME) == []
    assert backend.find_nonterminal_runs(str(project)) == []
    assert (project / "runs" / "run-demo-001" / "run.json").read_text(encoding="utf-8") == before


def test_recovery_reconciles_running_run_with_only_queued_nodes(tmp_path: Path) -> None:
    project = _make_project(tmp_path)
    backend = _run_store()
    raw = _load_run("run-running-1.0.json")
    queued_filter = dict(
        raw["node_runs"][1], state="queued", started_at=None, attempt=0, dependencies=[]
    )
    raw["node_runs"] = [queued_filter]
    raw["events"] = [event for event in raw["events"] if event["seq"] == 0]
    backend.create_run(str(project), RunRecord.model_validate(raw))

    (repaired,) = backend.recover_runs(str(project), now=RECOVERY_TIME)
    assert repaired.state == RunState.QUEUED
    assert repaired.started_at is None
    assert [event.kind.value for event in repaired.events][-1:] == ["run_queued"]


def test_recovery_leaves_consistent_mixed_run_unchanged(tmp_path: Path) -> None:
    project = _make_project(tmp_path)
    backend = _run_store()
    raw = _load_run("run-running-1.0.json")
    queued_filter = dict(raw["node_runs"][1], state="queued", started_at=None, attempt=0)
    raw["node_runs"] = [raw["node_runs"][0], queued_filter]
    record = RunRecord.model_validate(raw)
    backend.create_run(str(project), record)
    before = (project / "runs" / record.id / "run.json").read_bytes()

    assert backend.recover_runs(str(project), now=RECOVERY_TIME) == []
    assert (project / "runs" / record.id / "run.json").read_bytes() == before


def test_recovery_records_voided_attempt_structurally(tmp_path: Path) -> None:
    project = _make_project(tmp_path)
    backend = _run_store()
    raw = _load_run("run-running-1.0.json")
    raw["node_runs"][1]["attempt"] = 3
    backend.create_run(str(project), RunRecord.model_validate(raw))

    (repaired,) = backend.recover_runs(str(project), now=RECOVERY_TIME)
    reset_events = [event for event in repaired.events if event.kind.value == "node_queued"]
    assert len(reset_events) == 1
    assert reset_events[0].node_run_id == "node-run-filter-001"
    assert reset_events[0].attempt == 3
    assert (
        repaired.model_dump_json()
        and RunRecord.model_validate_json(repaired.model_dump_json()) == repaired
    )


def test_nonterminal_runs_survive_service_restart(tmp_path: Path) -> None:
    project = _make_project(tmp_path)
    _run_store().create_run(
        str(project), RunRecord.model_validate(_load_run("run-running-1.0.json"))
    )

    restarted = RunStore(store)
    found = restarted.find_nonterminal_runs(str(project))
    assert [run.id for run in found] == ["run-demo-running"]
    assert restarted.recover_runs(str(project), now=RECOVERY_TIME)[0].state == RunState.RUNNING


def test_overlapping_terminal_and_stale_saves_keep_terminal_history(tmp_path: Path) -> None:
    import threading

    project = _make_project(tmp_path)
    backend = _run_store()
    backend.create_run(str(project), RunRecord.model_validate(_load_run("run-running-1.0.json")))

    terminal = RunRecord.model_validate({**_load_run("run-1.0.json"), "id": "run-demo-running"})
    assert terminal.id == "run-demo-running"
    stale = RunRecord.model_validate(_load_run("run-running-1.0.json"))

    barrier = threading.Barrier(2)

    def _save_terminal() -> None:
        barrier.wait(timeout=10)
        backend.save_run(str(project), terminal)

    def _save_stale() -> None:
        barrier.wait(timeout=10)
        try:
            backend.save_run(str(project), stale)
        except ValueError:
            pass

    first = threading.Thread(target=_save_terminal)
    second = threading.Thread(target=_save_stale)
    first.start()
    second.start()
    first.join(timeout=30)
    second.join(timeout=30)

    final = backend.get_run(str(project), "run-demo-running")
    assert final.state == RunState.SUCCEEDED
    assert final == terminal


def test_overlapping_duplicate_creates_allow_exactly_one(tmp_path: Path) -> None:
    import threading

    project = _make_project(tmp_path)
    backend = _run_store()
    record = RunRecord.model_validate(_load_run("run-1.0.json"))
    barrier = threading.Barrier(2)
    outcomes: list[str] = []

    def _create() -> None:
        barrier.wait(timeout=10)
        try:
            backend.create_run(str(project), record)
            outcomes.append("created")
        except FileExistsError:
            outcomes.append("rejected")

    threads = [threading.Thread(target=_create) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=30)
    assert sorted(outcomes) == ["created", "rejected"]
    assert backend.get_run(str(project), "run-demo-001") == record


# -- endpoints ---------------------------------------------------------------


def test_run_endpoints_require_session_token(client: TestClient, tmp_path: Path) -> None:
    project = _make_project(tmp_path)
    run = _load_run("run-1.0.json")

    assert (
        client.post("/api/runs/create", json={"path": str(project), "run": run}).status_code == 401
    )
    assert (
        client.post("/api/runs/open", json={"path": str(project), "run_id": "x"}).status_code == 401
    )
    assert client.get("/api/runs/list", params={"path": str(project)}).status_code == 401
    assert client.post("/api/runs/recover", json={"path": str(project)}).status_code == 401
    assert auth_module.SESSION_TOKEN == TEST_TOKEN


def test_run_endpoints_create_open_list_recover(client: TestClient, tmp_path: Path) -> None:
    project = _make_project(tmp_path)
    created = client.post(
        "/api/runs/create",
        json={"path": str(project), "run": _load_run("run-running-1.0.json")},
        headers=AUTH_HEADERS,
    )
    assert created.status_code == 200, created.text
    assert created.json()["run_id"] == "run-demo-running"

    duplicate = client.post(
        "/api/runs/create",
        json={"path": str(project), "run": _load_run("run-running-1.0.json")},
        headers=AUTH_HEADERS,
    )
    assert duplicate.status_code == 409

    opened = client.post(
        "/api/runs/open",
        json={"path": str(project), "run_id": "run-demo-running"},
        headers=AUTH_HEADERS,
    )
    assert opened.status_code == 200
    assert opened.json()["run"]["state"] == "running"

    missing = client.post(
        "/api/runs/open",
        json={"path": str(project), "run_id": "run-missing"},
        headers=AUTH_HEADERS,
    )
    assert missing.status_code == 404

    listed = client.get("/api/runs/list", params={"path": str(project)}, headers=AUTH_HEADERS)
    assert listed.status_code == 200
    assert [entry["run_id"] for entry in listed.json()] == ["run-demo-running"]

    recovered = client.post("/api/runs/recover", json={"path": str(project)}, headers=AUTH_HEADERS)
    assert recovered.status_code == 200, recovered.text
    assert recovered.json()[0]["run"]["state"] == "running"

    forbidden = client.post(
        "/api/runs/create",
        json={"path": str(tmp_path / "elsewhere"), "run": _load_run("run-1.0.json")},
        headers=AUTH_HEADERS,
    )
    assert forbidden.status_code == 403


def test_run_save_endpoint_rejects_terminal_history(client: TestClient, tmp_path: Path) -> None:
    project = _make_project(tmp_path)
    run = _load_run("run-1.0.json")
    assert (
        client.post(
            "/api/runs/create", json={"path": str(project), "run": run}, headers=AUTH_HEADERS
        ).status_code
        == 200
    )
    target = project / "runs" / "run-demo-001" / "run.json"
    before = target.read_bytes()

    edited = dict(run)
    edited["events"] = [{**run["events"][0], "message": "rewritten"}]
    rejected = client.post(
        "/api/runs/save", json={"path": str(project), "run": edited}, headers=AUTH_HEADERS
    )
    assert rejected.status_code == 400
    assert "immutable" in rejected.json()["detail"]
    assert target.read_bytes() == before


def test_run_endpoints_reject_non_root_and_deleted_projects(
    client: TestClient, tmp_path: Path
) -> None:
    project = _make_project(tmp_path)
    nested = project / "nested"
    nested.mkdir(parents=True, exist_ok=True)
    descendant = client.post(
        "/api/runs/create",
        json={"path": str(nested), "run": _load_run("run-1.0.json")},
        headers=AUTH_HEADERS,
    )
    assert descendant.status_code == 400
    assert "project root" in descendant.json()["detail"]

    (project / "brainlearn.project.json").unlink()
    deleted = client.get("/api/runs/list", params={"path": str(project)}, headers=AUTH_HEADERS)
    assert deleted.status_code == 404


def test_run_list_endpoint_surfaces_corrupt_files(client: TestClient, tmp_path: Path) -> None:
    project = _make_project(tmp_path)
    broken = project / "runs" / "run-broken"
    broken.mkdir(parents=True, exist_ok=True)
    (broken / "run.json").write_text("{not json", encoding="utf-8")

    listed = client.get("/api/runs/list", params={"path": str(project)}, headers=AUTH_HEADERS)
    assert listed.status_code == 400
    assert "run-broken" in listed.json()["detail"]

    recovered = client.post("/api/runs/recover", json={"path": str(project)}, headers=AUTH_HEADERS)
    assert recovered.status_code == 400
    assert "run-broken" in recovered.json()["detail"]
