"""Step 4E.4: safe artifact open/download access."""

import json
import time
from pathlib import Path
from typing import Any
from urllib.parse import quote

import pytest
from brainlearn_server import project_store as store_module
from brainlearn_server.app import app, content_disposition, runs
from brainlearn_server.app import store as project_store
from brainlearn_server.auth import reset_session_token_for_tests
from fastapi.testclient import TestClient

TEST_TOKEN = "step4e4-artifact-token-0123456789"
AUTH_HEADERS = {"Authorization": f"Bearer {TEST_TOKEN}"}


@pytest.fixture(autouse=True)
def _isolated_store(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    reset_session_token_for_tests(TEST_TOKEN)
    state_dir = tmp_path / "state"
    monkeypatch.setattr(store_module, "_default_state_dir", lambda: state_dir)
    project_store.state_dir = state_dir
    project_store.allowed_roots.clear()
    yield
    project_store.allowed_roots.clear()
    reset_session_token_for_tests(TEST_TOKEN)


@pytest.fixture
def client() -> TestClient:
    return TestClient(app)


def _copy_workflow(text: str = "artifact-bytes") -> dict[str, Any]:
    return {
        "schema_version": "1.0",
        "id": "artifact-flow",
        "metadata": {
            "name": "Artifact flow",
            "description": "",
            "created_with": "BrainLearn tests",
            "modality": "EEG",
            "status": "example",
        },
        "nodes": [
            {
                "id": "writer",
                "type": "demo.copy",
                "label": "Copy",
                "category": "Demo",
                "position": {"x": 0, "y": 0},
                "ports": [
                    {
                        "id": "output",
                        "label": "Output",
                        "direction": "output",
                        "data_type": "raw_eeg",
                        "required": True,
                    }
                ],
                "parameters": [{"id": "text", "label": "Text", "value": text, "required": False}],
                "pauses_for_review": False,
            }
        ],
        "edges": [],
    }


def _run_to_success(client: TestClient, project: Path) -> tuple[str, dict[str, Any]]:
    response = client.post(
        "/api/projects/create",
        json={"path": str(project), "name": "artifacts"},
        headers=AUTH_HEADERS,
    )
    assert response.status_code == 200, response.text
    started = client.post(
        "/api/runs/start",
        json={"path": str(project), "workflow": _copy_workflow(), "seed": 0},
        headers=AUTH_HEADERS,
    )
    assert started.status_code == 200, started.text
    run_id = started.json()["run_id"]
    deadline = time.monotonic() + 20.0
    while time.monotonic() < deadline:
        opened = client.post(
            "/api/runs/open",
            json={"path": str(project), "run_id": run_id},
            headers=AUTH_HEADERS,
        )
        assert opened.status_code == 200, opened.text
        run = opened.json()["run"]
        if run["state"] == "succeeded":
            (artifact,) = run["node_runs"][0]["artifacts"]
            return run_id, artifact
        time.sleep(0.05)
    raise AssertionError("run never succeeded")


def _open_artifact(client: TestClient, project: Path, run_id: str, artifact_id: str):
    return client.post(
        "/api/artifacts/open",
        json={"path": str(project), "run_id": run_id, "artifact_id": artifact_id},
        headers=AUTH_HEADERS,
    )


def test_open_artifact_returns_verified_bytes(client: TestClient, tmp_path: Path) -> None:
    project = tmp_path / "artifacts"
    run_id, artifact = _run_to_success(client, project)
    response = _open_artifact(client, project, run_id, artifact["artifact_id"])
    assert response.status_code == 200, response.text
    assert response.content == b"artifact-bytes"
    assert response.headers["content-type"].startswith("text/plain")
    assert "output.txt" in response.headers["content-disposition"]


def test_open_unknown_artifact_is_not_found(client: TestClient, tmp_path: Path) -> None:
    project = tmp_path / "artifacts"
    run_id, _artifact = _run_to_success(client, project)
    response = _open_artifact(client, project, run_id, "brainlearn-v1:artifact:" + "0" * 64)
    assert response.status_code == 404


def test_open_unknown_run_is_not_found(client: TestClient, tmp_path: Path) -> None:
    project = tmp_path / "artifacts"
    run_id, artifact = _run_to_success(client, project)
    response = _open_artifact(client, project, "run-missing", artifact["artifact_id"])
    assert response.status_code == 404
    assert run_id  # silence unused warnings about the successful run


def test_open_outside_project_is_forbidden(client: TestClient, tmp_path: Path) -> None:
    project = tmp_path / "artifacts"
    run_id, artifact = _run_to_success(client, project)
    response = _open_artifact(client, tmp_path / "elsewhere", run_id, artifact["artifact_id"])
    assert response.status_code == 403


def test_open_symlinked_artifact_is_refused(client: TestClient, tmp_path: Path) -> None:
    project = tmp_path / "artifacts"
    run_id, artifact = _run_to_success(client, project)
    target = project / artifact["path"]
    assert target.is_file() and not target.is_symlink()
    outside = tmp_path / "outside.txt"
    outside.write_bytes(b"untrusted-bytes")
    target.unlink()
    target.symlink_to(outside)
    response = _open_artifact(client, project, run_id, artifact["artifact_id"])
    assert response.status_code == 409
    assert response.content != b"untrusted-bytes"


def test_open_tampered_artifact_is_refused(client: TestClient, tmp_path: Path) -> None:
    project = tmp_path / "artifacts"
    run_id, artifact = _run_to_success(client, project)
    (project / artifact["path"]).write_bytes(b"tampered-bytes!!")
    response = _open_artifact(client, project, run_id, artifact["artifact_id"])
    assert response.status_code == 409


def test_open_missing_artifact_file_is_not_found(client: TestClient, tmp_path: Path) -> None:
    project = tmp_path / "artifacts"
    run_id, artifact = _run_to_success(client, project)
    (project / artifact["path"]).unlink()
    response = _open_artifact(client, project, run_id, artifact["artifact_id"])
    assert response.status_code == 404


def test_open_artifact_requires_a_session_token(tmp_path: Path) -> None:
    response = TestClient(app).post(
        "/api/artifacts/open",
        json={"path": str(tmp_path), "run_id": "run-x", "artifact_id": "a"},
    )
    assert response.status_code == 401


def _authorize(client: TestClient, project: Path, name: str = "probe") -> None:
    response = client.post(
        "/api/projects/create",
        json={"path": str(project), "name": name},
        headers=AUTH_HEADERS,
    )
    assert response.status_code == 200, response.text


def test_verified_path_rejects_intermediate_symlink(client: TestClient, tmp_path: Path) -> None:
    from brainlearn_server.run_store import _verified_project_path

    project = tmp_path / "intermediate"
    _authorize(client, project)
    real = project / "real"
    (real / "sub").mkdir(parents=True)
    (real / "sub" / "output.txt").write_bytes(b"bytes")
    (project / "linked").symlink_to(real, target_is_directory=True)
    with pytest.raises(ValueError, match="symlink"):
        _verified_project_path(runs.projects, project, "linked/sub/output.txt")


def test_verified_path_rejects_deep_symlink_parent(client: TestClient, tmp_path: Path) -> None:
    from brainlearn_server.run_store import _verified_project_path

    project = tmp_path / "deep"
    _authorize(client, project)
    deep = project / "real"
    current = deep
    parts = []
    for index in range(70):
        current = current / f"d{index}"
        parts.append(f"d{index}")
    current.mkdir(parents=True)
    (current / "output.txt").write_bytes(b"bytes")
    (project / "linked").symlink_to(deep, target_is_directory=True)
    with pytest.raises(ValueError, match="symlink"):
        _verified_project_path(runs.projects, project, "linked/" + "/".join(parts) + "/output.txt")
    resolved = _verified_project_path(
        runs.projects, project, "real/" + "/".join(parts) + "/output.txt"
    )
    assert resolved.is_file()


def test_verified_path_rejects_escape_and_absolute_paths(
    client: TestClient, tmp_path: Path
) -> None:
    from brainlearn_server.run_store import _verified_project_path

    project = tmp_path / "escape"
    _authorize(client, project)
    for bad in ("../evil.txt", "a/../../evil.txt", "/etc/passwd", "", "a//b.txt"):
        with pytest.raises(ValueError, match="escapes"):
            _verified_project_path(runs.projects, project, bad)


def test_verified_path_rejects_symlink_to_second_authorized_root(
    client: TestClient, tmp_path: Path
) -> None:
    from brainlearn_server.run_store import _verified_project_path

    first = tmp_path / "first"
    second = tmp_path / "second"
    _authorize(client, first, name="first")
    _authorize(client, second, name="second")
    (second / "shared.txt").write_bytes(b"same-bytes")
    (first / "linked.txt").symlink_to(second / "shared.txt")
    with pytest.raises(ValueError, match="symlink"):
        _verified_project_path(runs.projects, first, "linked.txt")


def test_content_disposition_uses_plain_form_for_safe_names() -> None:

    assert content_disposition("output.txt") == 'attachment; filename="output.txt"'


def test_content_disposition_neutralizes_quotes_and_controls() -> None:

    hostile = 'evil"\r\nX-Probe: injected.txt'
    header = content_disposition(hostile)
    assert "\r" not in header and "\n" not in header
    assert 'filename="evil___X-Probe: injected.txt"' in header
    assert "filename*=UTF-8''" in header
    header.encode("ascii")


def test_content_disposition_encodes_unicode_names() -> None:

    header = content_disposition("ünïcodé résultat.txt")
    assert "\r" not in header and "\n" not in header
    assert "filename*=UTF-8''" + quote("ünïcodé résultat.txt", safe="") in header
    assert 'filename="unicode resultat.txt"' in header
    header.encode("ascii")


def test_quoted_artifact_path_downloads_with_safe_disposition(
    client: TestClient, tmp_path: Path
) -> None:
    project = tmp_path / "quoted"
    run_id, artifact = _run_to_success(client, project)
    source = project / artifact["path"]
    quoted = source.parent / 'qu"oted.txt'
    source.rename(quoted)
    run_file = next((project / "runs" / run_id).glob("run.json"))
    payload = json.loads(run_file.read_text(encoding="utf-8"))
    for node in payload["node_runs"]:
        for item in node["artifacts"]:
            if item["artifact_id"] == artifact["artifact_id"]:
                item["path"] = str(Path(item["path"]).parent / 'qu"oted.txt').replace("\\", "/")
                item["byte_size"] = quoted.stat().st_size
                item["sha256"] = __import__("hashlib").sha256(quoted.read_bytes()).hexdigest()
    run_file.write_text(json.dumps(payload), encoding="utf-8")
    response = _open_artifact(client, project, run_id, artifact["artifact_id"])
    assert response.status_code == 200, response.text
    assert response.content == b"artifact-bytes"
    disposition = response.headers["content-disposition"]
    assert 'filename="qu_oted.txt"' in disposition
    assert "filename*=UTF-8''" in disposition


def test_crlf_artifact_path_is_rejected_at_the_endpoint(client: TestClient, tmp_path: Path) -> None:
    project = tmp_path / "crlf"
    run_id, artifact = _run_to_success(client, project)
    source = project / artifact["path"]
    evil = source.parent / 'evil"\r\nX-Probe: injected.txt'
    evil.write_bytes(source.read_bytes())
    run_file = next((project / "runs" / run_id).glob("run.json"))
    payload = json.loads(run_file.read_text(encoding="utf-8"))
    for node in payload["node_runs"]:
        for item in node["artifacts"]:
            if item["artifact_id"] == artifact["artifact_id"]:
                item["path"] = str(
                    Path(item["path"]).parent / 'evil"\r\nX-Probe: injected.txt'
                ).replace("\\", "/")
    run_file.write_text(json.dumps(payload), encoding="utf-8")
    response = _open_artifact(client, project, run_id, artifact["artifact_id"])
    assert response.status_code == 409
    assert b"\r\nX-Probe" not in response.content


def test_long_unicode_artifact_path_downloads(client: TestClient, tmp_path: Path) -> None:
    project = tmp_path / "longname"
    run_id, artifact = _run_to_success(client, project)
    source = project / artifact["path"]
    long_name = "ünïcodé-" + "n" * 180 + ".txt"
    target = source.parent / long_name
    target.write_bytes(source.read_bytes())
    run_file = next((project / "runs" / run_id).glob("run.json"))
    payload = json.loads(run_file.read_text(encoding="utf-8"))
    for node in payload["node_runs"]:
        for item in node["artifacts"]:
            if item["artifact_id"] == artifact["artifact_id"]:
                item["path"] = str(Path(item["path"]).parent / long_name).replace("\\", "/")
    run_file.write_text(json.dumps(payload), encoding="utf-8")
    response = _open_artifact(client, project, run_id, artifact["artifact_id"])
    assert response.status_code == 200, response.text
    assert response.content == b"artifact-bytes"
    assert "filename*=UTF-8''" in response.headers["content-disposition"]


def test_artifact_streaming_reads_in_bounded_chunks(
    client: TestClient, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import brainlearn_server.run_store as run_store_module

    project = tmp_path / "streamed"
    payload_text = "s" * (2 * 1024 * 1024 + 123)
    workflow = _copy_workflow("seed")
    workflow["nodes"][0]["parameters"][0]["value"] = payload_text
    response = client.post(
        "/api/projects/create",
        json={"path": str(project), "name": "streamed"},
        headers=AUTH_HEADERS,
    )
    assert response.status_code == 200, response.text
    started = client.post(
        "/api/runs/start",
        json={"path": str(project), "workflow": workflow, "seed": 0},
        headers=AUTH_HEADERS,
    )
    assert started.status_code == 200, started.text
    run_id = started.json()["run_id"]
    deadline = time.monotonic() + 30.0
    artifact: dict = {}
    while time.monotonic() < deadline:
        opened = client.post(
            "/api/runs/open",
            json={"path": str(project), "run_id": run_id},
            headers=AUTH_HEADERS,
        )
        assert opened.status_code == 200, opened.text
        run = opened.json()["run"]
        if run["state"] == "succeeded":
            (artifact,) = run["node_runs"][0]["artifacts"]
            break
        time.sleep(0.05)
    assert artifact, "run never succeeded"

    real_fdopen = run_store_module.os.fdopen
    read_sizes: list[int | None] = []
    closed: list[bool] = []

    class _Proxy:
        def __init__(self, handle):
            self._handle = handle

        def read(self, size=-1):
            read_sizes.append(size)
            if size is None or size < 0:
                raise AssertionError("unbounded artifact read")
            return self._handle.read(size)

        def close(self):
            closed.append(True)
            return self._handle.close()

        def __getattr__(self, name):
            return getattr(self._handle, name)

    def _spying_fdopen(fd, *args, **kwargs):
        return _Proxy(real_fdopen(fd, *args, **kwargs))

    monkeypatch.setattr(run_store_module.os, "fdopen", _spying_fdopen)
    response = _open_artifact(client, project, run_id, artifact["artifact_id"])
    assert response.status_code == 200, response.text
    assert response.content == payload_text.encode()
    assert read_sizes, "expected chunked reads through the verified descriptor"
    assert all(size <= 1024 * 1024 for size in read_sizes if size is not None)
    assert closed, "expected deterministic descriptor cleanup"


def _tamper_record_artifact(
    project: Path, run_id: str, artifact_id: str, **overrides: object
) -> None:
    run_file = next((project / "runs" / run_id).glob("run.json"))
    payload = json.loads(run_file.read_text(encoding="utf-8"))
    for node in payload["node_runs"]:
        for item in node["artifacts"]:
            if item["artifact_id"] == artifact_id:
                item.update(overrides)
    run_file.write_text(json.dumps(payload), encoding="utf-8")


def test_hostile_media_type_is_rejected_without_raw_headers(
    client: TestClient, tmp_path: Path
) -> None:
    project = tmp_path / "hostile-type"
    run_id, artifact = _run_to_success(client, project)
    _tamper_record_artifact(
        project, run_id, artifact["artifact_id"], media_type="text/plain\r\nX-Probe: injected"
    )
    response = _open_artifact(client, project, run_id, artifact["artifact_id"])
    assert response.status_code == 409
    assert b"\r\nX-Probe" not in response.content


def test_directory_artifact_is_a_structured_conflict(client: TestClient, tmp_path: Path) -> None:
    project = tmp_path / "directory"
    run_id, artifact = _run_to_success(client, project)
    target = project / artifact["path"]
    target.unlink()
    target.mkdir()
    response = _open_artifact(client, project, run_id, artifact["artifact_id"])
    assert response.status_code == 409


def test_fifo_artifact_is_rejected_without_blocking(client: TestClient, tmp_path: Path) -> None:
    import os

    if not hasattr(os, "mkfifo"):
        pytest.skip("platform without FIFO support")
    project = tmp_path / "fifo"
    run_id, artifact = _run_to_success(client, project)
    target = project / artifact["path"]
    target.unlink()
    os.mkfifo(target)
    response = _open_artifact(client, project, run_id, artifact["artifact_id"])
    assert response.status_code == 409


def test_artifact_descriptors_are_closed_after_serving(
    client: TestClient, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import brainlearn_server.run_store as run_store_module

    project = tmp_path / "descriptors"
    run_id, artifact = _run_to_success(client, project)
    real_fdopen = run_store_module.os.fdopen
    opened: list[int] = []
    closed: list[int] = []

    class _Proxy:
        def __init__(self, handle):
            self._handle = handle
            opened.append(handle.fileno())

        def close(self):
            closed.append(self._handle.fileno())
            return self._handle.close()

        def __getattr__(self, name):
            return getattr(self._handle, name)

    def _tracking_fdopen(fd, *args, **kwargs):
        return _Proxy(real_fdopen(fd, *args, **kwargs))

    monkeypatch.setattr(run_store_module.os, "fdopen", _tracking_fdopen)
    response = _open_artifact(client, project, run_id, artifact["artifact_id"])
    assert response.status_code == 200, response.text
    assert response.content == b"artifact-bytes"
    assert opened, "expected the verified descriptor to be opened"
    assert set(opened) <= set(closed), "every opened descriptor must be closed"
