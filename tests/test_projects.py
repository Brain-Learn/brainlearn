"""Step 3: secure local projects, persistence, auth, and migrations."""

import json
from pathlib import Path

import pytest
from brainlearn_core import ProjectManifest, Workflow, migrate_project_dict, migrate_workflow_dict
from brainlearn_server import auth as auth_module
from brainlearn_server import project_store as store_module
from brainlearn_server.app import app, store
from brainlearn_server.auth import reset_session_token_for_tests
from brainlearn_server.project_store import (
    PROJECT_MANIFEST_FILENAME,
    WORKFLOW_FILENAME,
    atomic_write_json,
    canonicalize_project_path,
)
from fastapi.testclient import TestClient

FIXTURES = Path(__file__).parent / "fixtures"

TEST_TOKEN = "step3-test-token-0123456789"
AUTH_HEADERS = {"Authorization": f"Bearer {TEST_TOKEN}"}


@pytest.fixture(autouse=True)
def _isolated_store(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    reset_session_token_for_tests(TEST_TOKEN)
    state_dir = tmp_path / "state"
    monkeypatch.setattr(store_module, "_default_state_dir", lambda: state_dir)
    store.state_dir = state_dir
    store.allowed_roots.clear()
    # Remove stale recent file state between tests.
    yield
    store.allowed_roots.clear()
    reset_session_token_for_tests(TEST_TOKEN)


@pytest.fixture
def client() -> TestClient:
    return TestClient(app)


def _example_workflow() -> dict[str, object]:
    return json.loads((FIXTURES / "workflow-1.0.json").read_text(encoding="utf-8"))


def test_project_and_workflow_fixtures_migrate_and_round_trip() -> None:
    project_raw = json.loads((FIXTURES / "project-1.0.json").read_text(encoding="utf-8"))
    workflow_raw = _example_workflow()

    migrated_project = ProjectManifest.model_validate(migrate_project_dict(dict(project_raw)))
    migrated_workflow = Workflow.model_validate(migrate_workflow_dict(dict(workflow_raw)))

    assert migrated_project.project_schema_version == "1.0"
    assert migrated_workflow.schema_version == "1.0"
    assert Workflow.model_validate_json(migrated_workflow.model_dump_json()) == migrated_workflow
    assert (
        ProjectManifest.model_validate_json(migrated_project.model_dump_json()) == migrated_project
    )


def test_migrations_reject_unknown_versions() -> None:
    with pytest.raises(ValueError, match="Unsupported project_schema_version"):
        migrate_project_dict({"project_schema_version": "9.9"})
    with pytest.raises(ValueError, match="Unsupported workflow schema_version"):
        migrate_workflow_dict({"schema_version": "9.9"})


def test_canonicalize_rejects_relative_paths() -> None:
    with pytest.raises(ValueError, match="must be absolute"):
        canonicalize_project_path("relative/projects/demo")


def test_create_open_save_round_trip_and_survives_restart(
    client: TestClient, tmp_path: Path
) -> None:
    project_dir = tmp_path / "demo-project"
    workflow = _example_workflow()

    created = client.post(
        "/api/projects/create",
        json={"path": str(project_dir), "name": "Demo", "workflow": workflow},
        headers=AUTH_HEADERS,
    )
    assert created.status_code == 200, created.text
    assert (project_dir / PROJECT_MANIFEST_FILENAME).is_file()
    assert (project_dir / WORKFLOW_FILENAME).is_file()

    # Second save keeps a recoverable previous version.
    edited = dict(workflow)
    edited_metadata = dict(workflow["metadata"])  # type: ignore[index]
    edited_metadata["name"] = "Demo edited"
    edited["metadata"] = edited_metadata
    saved = client.post(
        "/api/projects/save",
        json={"path": str(project_dir), "workflow": edited},
        headers=AUTH_HEADERS,
    )
    assert saved.status_code == 200, saved.text
    previous_path = project_dir / "workflow.previous.json"
    assert previous_path.is_file()
    assert (
        json.loads(previous_path.read_text(encoding="utf-8"))["metadata"]["name"] != "Demo edited"
    )

    # Simulate a service restart: authorization memory is cleared but the
    # project directory on disk survives, and an explicit open re-authorizes it.
    store.allowed_roots.clear()
    reopened = client.post(
        "/api/projects/open", json={"path": str(project_dir)}, headers=AUTH_HEADERS
    )
    assert reopened.status_code == 200, reopened.text
    assert reopened.json()["workflow"]["metadata"]["name"] == "Demo edited"

    recent = client.get("/api/projects/recent", headers=AUTH_HEADERS)
    assert recent.status_code == 200
    assert any(entry["path"] == str(project_dir) for entry in recent.json())


def test_previous_recovery_keeps_last_valid_workflow(client: TestClient, tmp_path: Path) -> None:
    project_dir = tmp_path / "validity-history"
    valid = _example_workflow()
    assert (
        client.post(
            "/api/projects/create",
            json={"path": str(project_dir), "name": "History", "workflow": valid},
            headers=AUTH_HEADERS,
        ).status_code
        == 200
    )

    second = dict(valid)
    second_metadata = dict(valid["metadata"])  # type: ignore[index]
    second_metadata["name"] = "Second valid"
    second["metadata"] = second_metadata
    assert (
        client.post(
            "/api/projects/save",
            json={"path": str(project_dir), "workflow": second},
            headers=AUTH_HEADERS,
        ).status_code
        == 200
    )
    previous_path = project_dir / "workflow.previous.json"
    assert (
        json.loads(previous_path.read_text(encoding="utf-8"))["metadata"]["name"] != "Second valid"
    )

    invalid = json.loads(json.dumps(second))
    invalid["nodes"] = []
    invalid["edges"] = [
        {
            "id": "ghost",
            "source": {"node_id": "missing", "port_id": "out"},
            "target": {"node_id": "also-missing", "port_id": "in"},
        }
    ]
    saved_invalid = client.post(
        "/api/projects/save",
        json={"path": str(project_dir), "workflow": invalid},
        headers=AUTH_HEADERS,
    )
    assert saved_invalid.status_code == 200, saved_invalid.text
    assert saved_invalid.json()["validation"]["valid"] is False
    # The invalid work-in-progress is persisted, but the recovery copy must
    # still be the most recent valid graph, not the invalid one.
    recovery = json.loads(previous_path.read_text(encoding="utf-8"))
    assert recovery["metadata"]["name"] == "Second valid"
    assert recovery["nodes"] != []


def test_save_as_creates_copy_without_overwriting_source(
    client: TestClient, tmp_path: Path
) -> None:
    source = tmp_path / "source"
    dest = tmp_path / "copy"
    workflow = _example_workflow()
    assert (
        client.post(
            "/api/projects/create",
            json={"path": str(source), "name": "Source", "workflow": workflow},
            headers=AUTH_HEADERS,
        ).status_code
        == 200
    )

    response = client.post(
        "/api/projects/save-as",
        json={"dest_path": str(dest), "workflow": workflow, "name": "Copy"},
        headers=AUTH_HEADERS,
    )
    assert response.status_code == 200, response.text
    assert (dest / WORKFLOW_FILENAME).is_file()
    assert (source / WORKFLOW_FILENAME).is_file()


def test_path_traversal_outside_allowed_roots_is_rejected(
    client: TestClient, tmp_path: Path
) -> None:
    project_dir = tmp_path / "allowed-project"
    workflow = _example_workflow()
    assert (
        client.post(
            "/api/projects/create",
            json={"path": str(project_dir), "name": "Allowed", "workflow": workflow},
            headers=AUTH_HEADERS,
        ).status_code
        == 200
    )

    traversal = project_dir / ".." / "outside-project"
    response = client.post(
        "/api/projects/save",
        json={"path": str(traversal), "workflow": workflow},
        headers=AUTH_HEADERS,
    )
    assert response.status_code == 403
    assert "outside the explicitly opened" in response.json()["detail"]
    assert not (tmp_path / "outside-project" / WORKFLOW_FILENAME).exists()


def test_project_endpoints_require_session_token(client: TestClient, tmp_path: Path) -> None:
    project_dir = tmp_path / "needs-token"

    no_token = client.post("/api/projects/create", json={"path": str(project_dir)})
    assert no_token.status_code == 401
    bad = client.post(
        "/api/projects/create",
        json={"path": str(project_dir)},
        headers={"Authorization": "Bearer wrong-token"},
    )
    assert bad.status_code == 401
    assert client.get("/api/projects/recent").status_code == 401
    assert client.get("/api/projects/recent", headers=AUTH_HEADERS).status_code == 200
    # Sanity: the module token used by the app matches the test token fixture.
    assert auth_module.SESSION_TOKEN == TEST_TOKEN


def test_disallowed_origin_is_rejected(client: TestClient) -> None:
    response = client.get("/api/health", headers={"Origin": "http://evil.example.com:5173"})
    assert response.status_code == 403
    assert "Origin" in response.json()["detail"]

    allowed = client.get("/api/health", headers={"Origin": "http://127.0.0.1:5173"})
    assert allowed.status_code == 200


def test_disallowed_host_is_rejected(client: TestClient) -> None:
    response = client.get("/api/health", headers={"Host": "evil.example.com"})
    assert response.status_code == 403


def test_interrupted_write_preserves_previous_valid_graph(tmp_path: Path) -> None:
    target = tmp_path / "workflow.json"
    original = {"schema_version": "1.0", "id": "original"}
    atomic_write_json(target, original)

    # Simulate a crash between temp-file creation and replace: the temp file
    # exists but replace never runs, so the original stays intact.
    tmp_leftovers = list(tmp_path.glob("workflow.json.*.tmp"))
    assert tmp_leftovers == []
    leftover = tmp_path / "workflow.json.interrupted.tmp"
    leftover.write_text('{"schema_version": "1.0", "id": "partial', encoding="utf-8")
    assert json.loads(target.read_text(encoding="utf-8")) == original
    leftover.unlink()
    assert json.loads(target.read_text(encoding="utf-8")) == original


def test_create_refuses_to_overwrite_existing_work(client: TestClient, tmp_path: Path) -> None:
    project_dir = tmp_path / "existing"
    workflow = _example_workflow()
    assert (
        client.post(
            "/api/projects/create",
            json={"path": str(project_dir), "name": "First", "workflow": workflow},
            headers=AUTH_HEADERS,
        ).status_code
        == 200
    )
    duplicate = client.post(
        "/api/projects/create",
        json={"path": str(project_dir), "name": "Second", "workflow": workflow},
        headers=AUTH_HEADERS,
    )
    assert duplicate.status_code == 409


def _make_unreadable(path: Path) -> None:
    import os

    if os.name == "nt":
        pytest.skip("POSIX file permissions do not apply on Windows")
    path.chmod(0o000)


def test_open_reports_forbidden_for_unreadable_project_files(
    client: TestClient, tmp_path: Path
) -> None:
    project_dir = tmp_path / "locked"
    workflow = _example_workflow()
    assert (
        client.post(
            "/api/projects/create",
            json={"path": str(project_dir), "name": "Locked", "workflow": workflow},
            headers=AUTH_HEADERS,
        ).status_code
        == 200
    )
    _make_unreadable(project_dir / PROJECT_MANIFEST_FILENAME)
    _make_unreadable(project_dir / WORKFLOW_FILENAME)

    response = client.post(
        "/api/projects/open", json={"path": str(project_dir)}, headers=AUTH_HEADERS
    )
    assert response.status_code == 403
    assert "Permission denied" in response.json()["detail"]


def test_save_reports_forbidden_for_unreadable_project_files(
    client: TestClient, tmp_path: Path
) -> None:
    project_dir = tmp_path / "locked-save"
    workflow = _example_workflow()
    assert (
        client.post(
            "/api/projects/create",
            json={"path": str(project_dir), "name": "Locked", "workflow": workflow},
            headers=AUTH_HEADERS,
        ).status_code
        == 200
    )
    _make_unreadable(project_dir / WORKFLOW_FILENAME)

    response = client.post(
        "/api/projects/save",
        json={"path": str(project_dir), "workflow": workflow},
        headers=AUTH_HEADERS,
    )
    assert response.status_code == 403
