"""Step 4D: content-addressed cache and downstream invalidation."""

import hashlib
import json
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

import pytest
from brainlearn_core import Workflow
from brainlearn_server import project_store as store_module
from brainlearn_server.app import app, store
from brainlearn_server.auth import reset_session_token_for_tests
from fastapi.testclient import TestClient

TEST_TOKEN = "step4d-test-token-0123456789"
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


def _copy_workflow(text: str = "cached-bytes") -> dict[str, Any]:
    return _workflow([_node("writer", "demo.copy", {"text": text}, out_port="output")], [])


def _chain_workflow(text: str = "chain-bytes") -> dict[str, Any]:
    return _workflow(
        [
            _node("first", "demo.copy", {"text": text}, out_port="output"),
            _node("second", "demo.relay", inputs=True, out_port="output"),
        ],
        [_edge("e1", "first", "second", source_port="output", target_port="in")],
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


def _by_id(run: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {node["id"]: node for node in run["node_runs"]}


def _cache_entries(project: Path) -> list[Path]:
    root = project / "cache" / "nodes"
    if not root.is_dir():
        return []
    return sorted(path for path in root.iterdir() if path.is_dir())


def _posix_only() -> None:
    import os

    if os.name == "nt":
        pytest.skip("symlinks need privileges on Windows")


# -- hit, miss, and reuse semantics ----------------------------------------------


def test_second_identical_run_reuses_cache(client: TestClient, tmp_path: Path) -> None:
    project = _make_project(client, tmp_path)
    first = _start(client, project, _copy_workflow("hello-cache"))
    finished_first = _wait_for_state(client, project, first["run_id"], {"succeeded"})
    node_first = _by_id(finished_first)["writer"]
    assert node_first["state"] == "succeeded"
    assert node_first["attempt"] == 1
    assert [event["kind"] for event in finished_first["events"]].count("node_started") == 1
    assert "cache_reused" not in [event["kind"] for event in finished_first["events"]]
    entries = _cache_entries(project)
    assert len(entries) == 1
    entry = json.loads((entries[0] / "entry.json").read_text(encoding="utf-8"))
    assert entry["content_identity"] == node_first["content_identity"]
    assert entry["outputs"][0]["sha256"] == node_first["artifacts"][0]["sha256"]

    second = _start(client, project, _copy_workflow("hello-cache"))
    finished_second = _wait_for_state(client, project, second["run_id"], {"succeeded"})
    node_second = _by_id(finished_second)["writer"]
    assert node_second["state"] == "cache_reused"
    assert node_second["attempt"] == 0
    assert node_second["started_at"] is None
    assert node_second["finished_at"] is None
    assert node_second["content_identity"] == node_first["content_identity"]
    kinds = [event["kind"] for event in finished_second["events"]]
    assert "node_started" not in kinds
    assert kinds.count("cache_reused") == 1
    reuse_event = next(
        event for event in finished_second["events"] if event["kind"] == "cache_reused"
    )
    assert reuse_event["attempt"] == 0
    assert reuse_event["node_run_id"] == "writer"
    assert [event["seq"] for event in finished_second["events"]] == list(
        range(len(finished_second["events"]))
    )
    assert len(node_second["artifacts"]) == 1
    assert node_second["artifacts"][0]["sha256"] == node_first["artifacts"][0]["sha256"]
    assert node_second["artifacts"][0]["byte_size"] == node_first["artifacts"][0]["byte_size"]
    assert node_second["artifacts"][0]["artifact_id"] != node_first["artifacts"][0]["artifact_id"]
    body = (project / node_second["artifacts"][0]["path"]).read_bytes()
    assert body == (project / node_first["artifacts"][0]["path"]).read_bytes() == b"hello-cache"


def test_reused_run_lists_cache_backed_artifacts(client: TestClient, tmp_path: Path) -> None:
    project = _make_project(client, tmp_path)
    first = _start(client, project, _copy_workflow("exact-bytes"))
    finished_first = _wait_for_state(client, project, first["run_id"], {"succeeded"})
    node_first = _by_id(finished_first)["writer"]
    expected_sha = hashlib.sha256(b"exact-bytes").hexdigest()

    second = _start(client, project, _copy_workflow("exact-bytes"))
    finished_second = _wait_for_state(client, project, second["run_id"], {"succeeded"})
    (artifact,) = _by_id(finished_second)["writer"]["artifacts"]
    assert artifact["sha256"] == expected_sha == node_first["artifacts"][0]["sha256"]
    assert artifact["byte_size"] == len(b"exact-bytes")
    assert artifact["port_id"] == "output"
    assert artifact["produced_by_node"] == "writer"
    assert artifact["media_type"] == "text/plain"
    stored = project / artifact["path"]
    assert stored.is_file() and not stored.is_symlink()
    assert stored.read_bytes() == b"exact-bytes"


def test_zero_output_nodes_never_publish(client: TestClient, tmp_path: Path) -> None:
    project = _make_project(client, tmp_path)
    workflow = _workflow([_node("slow", "demo.delay", {"seconds": 0.01})], [])
    first = _start(client, project, workflow)
    finished_first = _wait_for_state(client, project, first["run_id"], {"succeeded"})
    assert _by_id(finished_first)["slow"]["attempt"] == 1
    second = _start(client, project, workflow)
    finished_second = _wait_for_state(client, project, second["run_id"], {"succeeded"})
    assert _by_id(finished_second)["slow"]["state"] == "succeeded"
    assert _by_id(finished_second)["slow"]["attempt"] == 1
    assert _cache_entries(project) == []


# -- corruption, symlinks, traversal ---------------------------------------------


def test_corrupt_entry_json_is_miss_and_heals(client: TestClient, tmp_path: Path) -> None:
    project = _make_project(client, tmp_path)
    first = _start(client, project, _copy_workflow("heal-me"))
    _wait_for_state(client, project, first["run_id"], {"succeeded"})
    (entry_dir,) = _cache_entries(project)
    (entry_dir / "entry.json").write_text("{not valid json", encoding="utf-8")

    second = _start(client, project, _copy_workflow("heal-me"))
    finished = _wait_for_state(client, project, second["run_id"], {"succeeded"})
    assert _by_id(finished)["writer"]["state"] == "succeeded"
    assert _by_id(finished)["writer"]["attempt"] == 1
    healed = json.loads((entry_dir / "entry.json").read_text(encoding="utf-8"))
    assert healed["content_identity"].startswith("brainlearn-v1:node:")
    assert healed["outputs"][0]["sha256"] == hashlib.sha256(b"heal-me").hexdigest()

    third = _start(client, project, _copy_workflow("heal-me"))
    finished_third = _wait_for_state(client, project, third["run_id"], {"succeeded"})
    assert _by_id(finished_third)["writer"]["state"] == "cache_reused"


def test_corrupt_cache_file_is_miss_and_heals(client: TestClient, tmp_path: Path) -> None:
    project = _make_project(client, tmp_path)
    first = _start(client, project, _copy_workflow("file-heal"))
    _wait_for_state(client, project, first["run_id"], {"succeeded"})
    (entry_dir,) = _cache_entries(project)
    files = list((entry_dir / "files").rglob("*"))
    target = next(path for path in files if path.is_file())
    target.write_bytes(b"tampered-bytes")

    second = _start(client, project, _copy_workflow("file-heal"))
    finished = _wait_for_state(client, project, second["run_id"], {"succeeded"})
    assert _by_id(finished)["writer"]["state"] == "succeeded"
    assert (
        project / _by_id(finished)["writer"]["artifacts"][0]["path"]
    ).read_bytes() == b"file-heal"

    third = _start(client, project, _copy_workflow("file-heal"))
    finished_third = _wait_for_state(client, project, third["run_id"], {"succeeded"})
    assert _by_id(finished_third)["writer"]["state"] == "cache_reused"


def test_symlinked_cache_file_is_refused(client: TestClient, tmp_path: Path) -> None:
    _posix_only()
    project = _make_project(client, tmp_path)
    first = _start(client, project, _copy_workflow("link-heal"))
    _wait_for_state(client, project, first["run_id"], {"succeeded"})
    (entry_dir,) = _cache_entries(project)
    cached = next((entry_dir / "files").rglob("*"))
    assert cached.is_file()
    external = tmp_path / "external"
    external.mkdir(parents=True, exist_ok=True)
    sentinel = external / "sentinel.txt"
    sentinel.write_text("untouched", encoding="utf-8")
    cached.unlink()
    cached.symlink_to(sentinel)

    second = _start(client, project, _copy_workflow("link-heal"))
    finished = _wait_for_state(client, project, second["run_id"], {"succeeded"})
    assert _by_id(finished)["writer"]["state"] == "succeeded"
    assert sentinel.read_text(encoding="utf-8") == "untouched"
    assert list(external.rglob("*")) == [sentinel]


def test_traversal_entry_is_refused(client: TestClient, tmp_path: Path) -> None:
    from brainlearn_server.worker import build_run_record

    project = _make_project(client, tmp_path)
    workflow = _copy_workflow("traversal")
    record = build_run_record(Workflow.model_validate(workflow))
    sha = record.node_runs[0].content_identity.split(":")[-1]
    planted = project / "cache" / "nodes" / sha
    planted.mkdir(parents=True, exist_ok=True)
    (planted / "entry.json").write_text(
        json.dumps(
            {
                "schema_version": "1.0",
                "content_identity": record.node_runs[0].content_identity,
                "node_type": "demo.copy",
                "node_version": "0.1.0",
                "environment_identity": record.node_runs[0].environment_identity,
                "seed": 0,
                "inputs": {},
                "parameters": {"text": "traversal"},
                "settings": record.node_runs[0].settings,
                "outputs": [
                    {
                        "port_id": "output",
                        "relative_path": "../evil.txt",
                        "media_type": "text/plain",
                        "byte_size": 1,
                        "sha256": "0" * 64,
                    }
                ],
                "created_at": record.created_at,
            }
        ),
        encoding="utf-8",
    )
    evil = tmp_path / "evil.txt"
    started = _start(client, project, workflow)
    finished = _wait_for_state(client, project, started["run_id"], {"succeeded"})
    assert _by_id(finished)["writer"]["state"] == "succeeded"
    assert not evil.exists()
    assert not (project / "evil.txt").exists()


def test_symlinked_cache_root_is_refused(client: TestClient, tmp_path: Path) -> None:
    _posix_only()
    project = _make_project(client, tmp_path)
    external = tmp_path / "external"
    external.mkdir(parents=True, exist_ok=True)
    sentinel = external / "sentinel.txt"
    sentinel.write_text("untouched", encoding="utf-8")
    (project / "cache").symlink_to(external, target_is_directory=True)

    started = _start(client, project, _copy_workflow("no-cache-dir"))
    finished = _wait_for_state(client, project, started["run_id"], {"succeeded"})
    assert _by_id(finished)["writer"]["state"] == "succeeded"
    assert (
        project / _by_id(finished)["writer"]["artifacts"][0]["path"]
    ).read_text() == "no-cache-dir"
    assert sentinel.read_text(encoding="utf-8") == "untouched"
    assert list(external.rglob("*")) == [sentinel]


# -- interrupted publish and concurrency ------------------------------------------


def test_interrupted_publish_recovers_without_failing_run(
    client: TestClient, tmp_path: Path
) -> None:
    project = _make_project(client, tmp_path)
    # Simulate a process crash between cache staging and atomic rename: an
    # incomplete staging tree with bytes but no entry.json.
    stale = project / "cache" / "staging" / "abc123-partial"
    (stale / "files").mkdir(parents=True, exist_ok=True)
    (stale / "files" / "output.txt").write_bytes(b"stale-partial")

    started = _start(client, project, _copy_workflow("crash-publish"))
    finished = _wait_for_state(client, project, started["run_id"], {"succeeded"})
    assert _by_id(finished)["writer"]["state"] == "succeeded"
    assert (stale / "files" / "output.txt").read_bytes() == b"stale-partial"
    assert len(_cache_entries(project)) == 1

    recovered = client.post("/api/runs/recover", json={"path": str(project)}, headers=AUTH_HEADERS)
    assert recovered.status_code == 200, recovered.text
    assert list((project / "cache" / "staging").rglob("*")) == []
    quarantined = list((project / "cache" / "quarantine").rglob("output.txt"))
    assert len(quarantined) == 1
    assert quarantined[0].read_bytes() == b"stale-partial"
    # The complete entry published by the successful run survives recovery.
    assert len(_cache_entries(project)) == 1

    again = _start(client, project, _copy_workflow("crash-publish"))
    finished_again = _wait_for_state(client, project, again["run_id"], {"succeeded"})
    assert _by_id(finished_again)["writer"]["state"] == "cache_reused"


def test_concurrent_identical_runs_succeed_without_corruption(
    client: TestClient, tmp_path: Path
) -> None:
    project = _make_project(client, tmp_path)
    workflow = _copy_workflow("race-bytes")

    with ThreadPoolExecutor(max_workers=2) as pool:
        first_future = pool.submit(_start, client, project, workflow)
        second_future = pool.submit(_start, client, project, workflow)
        first = first_future.result()
        second = second_future.result()
    assert first["run_id"] != second["run_id"]
    finished_first = _wait_for_state(client, project, first["run_id"], {"succeeded"})
    finished_second = _wait_for_state(client, project, second["run_id"], {"succeeded"})
    for finished in (finished_first, finished_second):
        node = _by_id(finished)["writer"]
        assert node["state"] in {"succeeded", "cache_reused"}
        assert (project / node["artifacts"][0]["path"]).read_bytes() == b"race-bytes"
    assert len(_cache_entries(project)) == 1
    (entry_dir,) = _cache_entries(project)
    entry = json.loads((entry_dir / "entry.json").read_text(encoding="utf-8"))
    assert entry["outputs"][0]["sha256"] == hashlib.sha256(b"race-bytes").hexdigest()


# -- invalidation ------------------------------------------------------------------


def test_param_change_invalidates_node_and_descendants(client: TestClient, tmp_path: Path) -> None:
    project = _make_project(client, tmp_path)
    first = _start(client, project, _chain_workflow("v1"))
    finished_first = _wait_for_state(client, project, first["run_id"], {"succeeded"})
    assert (
        _by_id(finished_first)["second"]["inputs"]["in"]
        == _by_id(finished_first)["first"]["content_identity"]
    )

    second = _start(client, project, _chain_workflow("v1"))
    finished_second = _wait_for_state(client, project, second["run_id"], {"succeeded"})
    assert _by_id(finished_second)["first"]["state"] == "cache_reused"
    assert _by_id(finished_second)["second"]["state"] == "cache_reused"

    third = _start(client, project, _chain_workflow("v2"))
    finished_third = _wait_for_state(client, project, third["run_id"], {"succeeded"})
    by_third = _by_id(finished_third)
    assert by_third["first"]["state"] == "succeeded"
    assert by_third["first"]["attempt"] == 1
    assert by_third["second"]["state"] == "succeeded"
    assert by_third["second"]["attempt"] == 1
    assert by_third["second"]["inputs"]["in"] == by_third["first"]["content_identity"]
    assert by_third["second"]["inputs"]["in"] != _by_id(finished_first)["first"]["content_identity"]
    downstream = project / by_third["second"]["artifacts"][0]["path"]
    assert "v2" in downstream.read_text(encoding="utf-8")


def test_implementation_version_change_invalidates(
    client: TestClient, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from brainlearn_server import demo_nodes

    project = _make_project(client, tmp_path)
    first = _start(client, project, _copy_workflow("versioned"))
    _wait_for_state(client, project, first["run_id"], {"succeeded"})

    second = _start(client, project, _copy_workflow("versioned"))
    finished_second = _wait_for_state(client, project, second["run_id"], {"succeeded"})
    assert _by_id(finished_second)["writer"]["state"] == "cache_reused"

    original = demo_nodes.DEMO_NODES["demo.copy"]
    monkeypatch.setitem(
        demo_nodes.DEMO_NODES,
        "demo.copy",
        demo_nodes.DemoNodeManifest(
            version="9.9.9",
            inputs=dict(original.inputs),
            outputs=dict(original.outputs),
            handler=original.handler,
        ),
    )
    third = _start(client, project, _copy_workflow("versioned"))
    finished_third = _wait_for_state(client, project, third["run_id"], {"succeeded"})
    assert _by_id(finished_third)["writer"]["state"] == "succeeded"


def test_environment_change_invalidates(
    client: TestClient, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from brainlearn_server import worker as worker_module

    project = _make_project(client, tmp_path)
    first = _start(client, project, _copy_workflow("env-bytes"))
    _wait_for_state(client, project, first["run_id"], {"succeeded"})

    second = _start(client, project, _copy_workflow("env-bytes"))
    finished_second = _wait_for_state(client, project, second["run_id"], {"succeeded"})
    assert _by_id(finished_second)["writer"]["state"] == "cache_reused"

    real_environment = worker_module.service_environment()
    altered = real_environment.model_copy(update={"accelerator": "gpu-test"})
    monkeypatch.setattr(worker_module, "service_environment", lambda: altered)
    third = _start(client, project, _copy_workflow("env-bytes"))
    finished_third = _wait_for_state(client, project, third["run_id"], {"succeeded"})
    assert _by_id(finished_third)["writer"]["state"] == "succeeded"


def test_seed_change_invalidates(client: TestClient, tmp_path: Path) -> None:
    project = _make_project(client, tmp_path)
    first = _start(client, project, _copy_workflow("seed-bytes"), seed=0)
    _wait_for_state(client, project, first["run_id"], {"succeeded"})

    second = _start(client, project, _copy_workflow("seed-bytes"), seed=0)
    finished_second = _wait_for_state(client, project, second["run_id"], {"succeeded"})
    assert _by_id(finished_second)["writer"]["state"] == "cache_reused"

    third = _start(client, project, _copy_workflow("seed-bytes"), seed=1)
    finished_third = _wait_for_state(client, project, third["run_id"], {"succeeded"})
    assert _by_id(finished_third)["writer"]["state"] == "succeeded"
    assert _by_id(finished_third)["writer"]["attempt"] == 1


def test_setting_change_invalidates(client: TestClient, tmp_path: Path) -> None:
    project = _make_project(client, tmp_path)
    without_output = _workflow([_node("slow", "demo.delay", {"seconds": 0.01}, outputs=False)], [])
    with_output = _workflow([_node("slow", "demo.delay", {"seconds": 0.01})], [])
    first = _start(client, project, without_output)
    _wait_for_state(client, project, first["run_id"], {"succeeded"})
    # Control nodes never publish, so an identical repeat executes again.
    repeat = _start(client, project, without_output)
    finished_repeat = _wait_for_state(client, project, repeat["run_id"], {"succeeded"})
    assert _by_id(finished_repeat)["slow"]["attempt"] == 1

    # Declaring the optional output changes settings and therefore identity.
    from brainlearn_server.worker import build_run_record

    first_identity = build_run_record(Workflow.model_validate(without_output)).node_runs[0]
    second_identity = build_run_record(Workflow.model_validate(with_output)).node_runs[0]
    assert first_identity.content_identity != second_identity.content_identity


def test_independent_branch_stays_reusable(client: TestClient, tmp_path: Path) -> None:
    def _branched(text_a: str) -> dict[str, Any]:
        return _workflow(
            [
                _node("a", "demo.copy", {"text": text_a}, out_port="output"),
                _node("b", "demo.relay", inputs=True, out_port="output"),
                _node("c", "demo.copy", {"text": "stable"}, out_port="output"),
            ],
            [_edge("e1", "a", "b", source_port="output", target_port="in")],
        )

    project = _make_project(client, tmp_path)
    first = _start(client, project, _branched("left-v1"))
    _wait_for_state(client, project, first["run_id"], {"succeeded"})

    second = _start(client, project, _branched("left-v1"))
    finished_second = _wait_for_state(client, project, second["run_id"], {"succeeded"})
    assert _by_id(finished_second)["a"]["state"] == "cache_reused"
    assert _by_id(finished_second)["b"]["state"] == "cache_reused"
    assert _by_id(finished_second)["c"]["state"] == "cache_reused"

    third = _start(client, project, _branched("left-v2"))
    finished_third = _wait_for_state(client, project, third["run_id"], {"succeeded"})
    by_third = _by_id(finished_third)
    assert by_third["a"]["state"] == "succeeded"
    assert by_third["b"]["state"] == "succeeded"
    assert by_third["c"]["state"] == "cache_reused"
    assert by_third["c"]["attempt"] == 0
    assert (project / by_third["c"]["artifacts"][0]["path"]).read_bytes() == b"stable"


def test_downstream_relay_reads_cached_upstream_bytes(
    client: TestClient, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from brainlearn_server import demo_nodes

    project = _make_project(client, tmp_path)
    first = _start(client, project, _chain_workflow("upstream-bytes"))
    finished_first = _wait_for_state(client, project, first["run_id"], {"succeeded"})
    assert _by_id(finished_first)["second"]["state"] == "succeeded"

    original = demo_nodes.DEMO_NODES["demo.relay"]
    monkeypatch.setitem(
        demo_nodes.DEMO_NODES,
        "demo.relay",
        demo_nodes.DemoNodeManifest(
            version="7.7.7",
            inputs=dict(original.inputs),
            outputs=dict(original.outputs),
            handler=original.handler,
        ),
    )
    second = _start(client, project, _chain_workflow("upstream-bytes"))
    finished_second = _wait_for_state(client, project, second["run_id"], {"succeeded"})
    by_second = _by_id(finished_second)
    assert by_second["first"]["state"] == "cache_reused"
    assert by_second["second"]["state"] == "succeeded"
    content = (project / by_second["second"]["artifacts"][0]["path"]).read_text(encoding="utf-8")
    assert "upstream-bytes" in content


# -- contract unit tests -----------------------------------------------------------


def test_cache_entry_rejects_traversal_and_duplicates() -> None:
    import re

    from brainlearn_core import CacheEntry, node_content_identity
    from pydantic import ValidationError

    fields: dict[str, Any] = {
        "node_type": "demo.copy",
        "node_version": "0.1.0",
        "inputs": {},
        "parameters": {},
        "environment_identity": "brainlearn-v1:environment:" + "b" * 64,
        "seed": 0,
        "settings": {},
    }
    base: dict[str, Any] = {
        "schema_version": "1.0",
        "content_identity": node_content_identity(**fields),
        **fields,
        "outputs": [
            {
                "port_id": "output",
                "relative_path": "output.txt",
                "media_type": "text/plain",
                "byte_size": 1,
                "sha256": "c" * 64,
            }
        ],
        "created_at": "2026-09-12T00:00:00+00:00",
    }
    CacheEntry.model_validate(base)
    assert re.fullmatch(r"[0-9a-f]{64}", base["content_identity"].split(":")[-1])

    with pytest.raises(ValidationError):
        CacheEntry.model_validate({**base, "outputs": []})
    with pytest.raises(ValidationError):
        CacheEntry.model_validate(
            {
                **base,
                "outputs": [
                    {**base["outputs"][0], "relative_path": "../evil.txt"},
                ],
            }
        )
    with pytest.raises(ValidationError):
        CacheEntry.model_validate(
            {
                **base,
                "outputs": [base["outputs"][0], base["outputs"][0]],
            }
        )


# -- rebinding regressions -------------------------------------------------------


def _rewrite_entry(project: Path, mutate: Any) -> Path:
    candidates = [
        path for path in (project / "cache" / "nodes").iterdir() if (path / "entry.json").is_file()
    ]
    assert len(candidates) == 1
    entry_file = candidates[0] / "entry.json"
    payload = json.loads(entry_file.read_text(encoding="utf-8"))
    mutate(payload)
    entry_file.write_text(json.dumps(payload, sort_keys=True), encoding="utf-8")
    return candidates[0]


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("node_type", "demo.relay"),
        ("node_version", "9.9.9"),
        ("inputs", {"in": "brainlearn-v1:node:" + "d" * 64}),
        ("parameters", {"text": "mutated"}),
        ("environment_identity", "brainlearn-v1:environment:" + "e" * 64),
        ("seed", 1),
        ("settings", {"input_sources": {}, "declared_outputs": ["output", "extra"]}),
    ],
)
def test_mutated_cache_field_executes_instead_of_reusing(
    client: TestClient, tmp_path: Path, field: str, value: Any
) -> None:
    project = _make_project(client, tmp_path)
    first = _start(client, project, _copy_workflow("bound-bytes"))
    finished_first = _wait_for_state(client, project, first["run_id"], {"succeeded"})
    assert _by_id(finished_first)["writer"]["state"] == "succeeded"

    def _mutate(payload: dict[str, Any]) -> None:
        payload[field] = value

    _rewrite_entry(project, _mutate)
    second = _start(client, project, _copy_workflow("bound-bytes"))
    finished = _wait_for_state(client, project, second["run_id"], {"succeeded"})
    node = _by_id(finished)["writer"]
    assert node["state"] == "succeeded"
    assert node["attempt"] == 1
    assert "cache_reused" not in [event["kind"] for event in finished["events"]]
    assert (project / node["artifacts"][0]["path"]).read_bytes() == b"bound-bytes"


def test_wrong_output_port_executes_instead_of_reusing(client: TestClient, tmp_path: Path) -> None:
    project = _make_project(client, tmp_path)
    first = _start(client, project, _copy_workflow("ported"))
    _wait_for_state(client, project, first["run_id"], {"succeeded"})

    def _mutate(payload: dict[str, Any]) -> None:
        payload["outputs"][0]["port_id"] = "wrong"

    _rewrite_entry(project, _mutate)
    second = _start(client, project, _copy_workflow("ported"))
    finished = _wait_for_state(client, project, second["run_id"], {"succeeded"})
    node = _by_id(finished)["writer"]
    assert node["state"] == "succeeded"
    assert node["attempt"] == 1
    assert node["artifacts"][0]["port_id"] == "output"


def _dual_handler(ctx: Any) -> list[Any]:
    from brainlearn_server.demo_nodes import StagedOutput

    ctx.staging_dir.mkdir(parents=True, exist_ok=True)
    (ctx.staging_dir / "a.txt").write_text("aaa", encoding="utf-8")
    (ctx.staging_dir / "b.txt").write_text("bbb", encoding="utf-8")
    return [
        StagedOutput(port_id="a", relative_path="a.txt", media_type="text/plain"),
        StagedOutput(port_id="b", relative_path="b.txt", media_type="text/plain"),
    ]


def _dual_workflow() -> dict[str, Any]:
    return _workflow(
        [
            {
                "id": "dual",
                "type": "demo.dual",
                "label": "dual",
                "category": "Demo",
                "description": "Two-output demonstration node.",
                "position": {"x": 0, "y": 0},
                "ports": [_port("a", "output"), _port("b", "output")],
                "parameters": [],
                "pauses_for_review": False,
            }
        ],
        [],
    )


def _register_dual(monkeypatch: pytest.MonkeyPatch) -> None:
    from brainlearn_server import demo_nodes

    monkeypatch.setitem(
        demo_nodes.DEMO_NODES,
        "demo.dual",
        demo_nodes.DemoNodeManifest(
            version="0.1.0", inputs={}, outputs={"a": True, "b": True}, handler=_dual_handler
        ),
    )


def test_missing_required_output_port_executes_instead_of_reusing(
    client: TestClient, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _register_dual(monkeypatch)
    project = _make_project(client, tmp_path)
    first = _start(client, project, _dual_workflow())
    finished_first = _wait_for_state(client, project, first["run_id"], {"succeeded"})
    assert {artifact["port_id"] for artifact in _by_id(finished_first)["dual"]["artifacts"]} == {
        "a",
        "b",
    }

    def _mutate(payload: dict[str, Any]) -> None:
        payload["outputs"] = [output for output in payload["outputs"] if output["port_id"] == "a"]

    _rewrite_entry(project, _mutate)
    second = _start(client, project, _dual_workflow())
    finished = _wait_for_state(client, project, second["run_id"], {"succeeded"})
    node = _by_id(finished)["dual"]
    assert node["state"] == "succeeded"
    assert {artifact["port_id"] for artifact in node["artifacts"]} == {"a", "b"}


def test_late_reuse_failure_leaves_no_partial_tree(
    client: TestClient, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import shutil

    _register_dual(monkeypatch)
    project = _make_project(client, tmp_path)
    first = _start(client, project, _dual_workflow())
    _wait_for_state(client, project, first["run_id"], {"succeeded"})

    real_copy = shutil.copy2

    def _fail_second_copy(src: Any, dst: Any, *args: Any, **kwargs: Any) -> Any:
        if "reuse-" in str(dst) and str(dst).endswith("b.txt"):
            raise OSError("simulated late reuse failure")
        return real_copy(src, dst, *args, **kwargs)

    monkeypatch.setattr(shutil, "copy2", _fail_second_copy)
    second = _start(client, project, _dual_workflow())
    finished = _wait_for_state(client, project, second["run_id"], {"succeeded"})
    node = _by_id(finished)["dual"]
    assert node["state"] == "succeeded"
    assert node["attempt"] == 1
    attempt_dir = project / "runs" / second["run_id"] / "artifacts" / "dual" / "attempt-1"
    assert (attempt_dir / "a.txt").read_bytes() == b"aaa"
    assert (attempt_dir / "b.txt").read_bytes() == b"bbb"
    reused = project / "runs" / second["run_id"] / "artifacts" / "dual" / "cache-reused"
    assert not reused.exists()
    assert list((project / "runs" / second["run_id"] / "staging").rglob("*")) == []


def test_regular_file_occupant_heals_then_reuses(client: TestClient, tmp_path: Path) -> None:
    from brainlearn_server.worker import build_run_record

    project = _make_project(client, tmp_path)
    workflow = _copy_workflow("heal-file-key")
    record = build_run_record(Workflow.model_validate(workflow))
    sha = record.node_runs[0].content_identity.split(":")[-1]
    occupant = project / "cache" / "nodes" / sha
    occupant.parent.mkdir(parents=True, exist_ok=True)
    occupant.write_bytes(b"stale-occupant")

    first = _start(client, project, workflow)
    finished_first = _wait_for_state(client, project, first["run_id"], {"succeeded"})
    assert _by_id(finished_first)["writer"]["state"] == "succeeded"
    assert _by_id(finished_first)["writer"]["attempt"] == 1
    assert occupant.is_dir()
    assert (occupant / "entry.json").is_file()
    quarantined = list((project / "cache" / "quarantine").rglob("*"))
    assert any(path.is_file() and path.read_bytes() == b"stale-occupant" for path in quarantined)

    second = _start(client, project, workflow)
    finished_second = _wait_for_state(client, project, second["run_id"], {"succeeded"})
    assert _by_id(finished_second)["writer"]["state"] == "cache_reused"


# -- cancellation during reuse -----------------------------------------------------


def _set_project_cancel_flag(project: Path) -> None:
    from brainlearn_server.app import workers

    with workers._guard:
        for (proj, _run_id), flag in workers._cancel.items():
            if proj == str(project):
                flag.set()


def test_cancel_during_reuse_copy_cancels_run(
    client: TestClient, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import shutil

    project = _make_project(client, tmp_path)
    first = _start(client, project, _copy_workflow("cancel-copy"))
    _wait_for_state(client, project, first["run_id"], {"succeeded"})

    real_copy = shutil.copy2

    def _cancel_on_reuse_copy(src: Any, dst: Any, *args: Any, **kwargs: Any) -> Any:
        if "reuse-" in str(dst):
            _set_project_cancel_flag(project)
        return real_copy(src, dst, *args, **kwargs)

    monkeypatch.setattr(shutil, "copy2", _cancel_on_reuse_copy)
    second = _start(client, project, _copy_workflow("cancel-copy"))
    finished = _wait_for_state(client, project, second["run_id"], {"cancelled"})
    node = _by_id(finished)["writer"]
    assert node["state"] == "cancelled"
    assert node["attempt"] == 0
    assert node["started_at"] is None
    assert node["finished_at"] is not None
    assert node["artifacts"] == []
    assert finished["state"] == "cancelled"
    run_dir = project / "runs" / second["run_id"]
    assert not (run_dir / "artifacts" / "writer" / "cache-reused").exists()
    assert list((run_dir / "staging").rglob("*")) == []


def test_cancel_after_reuse_rename_cancels_run(
    client: TestClient, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import os

    project = _make_project(client, tmp_path)
    first = _start(client, project, _copy_workflow("cancel-rename"))
    _wait_for_state(client, project, first["run_id"], {"succeeded"})

    real_replace = os.replace

    def _cancel_on_reuse_rename(src: Any, dst: Any, *args: Any, **kwargs: Any) -> Any:
        result = real_replace(src, dst, *args, **kwargs)
        if "reuse-" in str(src):
            _set_project_cancel_flag(project)
        return result

    monkeypatch.setattr(os, "replace", _cancel_on_reuse_rename)
    second = _start(client, project, _copy_workflow("cancel-rename"))
    finished = _wait_for_state(client, project, second["run_id"], {"cancelled"})
    node = _by_id(finished)["writer"]
    assert node["state"] == "cancelled"
    assert node["attempt"] == 0
    assert node["started_at"] is None
    assert node["finished_at"] is not None
    assert node["artifacts"] == []
    assert finished["state"] == "cancelled"
    run_dir = project / "runs" / second["run_id"]
    assert not (run_dir / "artifacts" / "writer" / "cache-reused").exists()
    assert list((run_dir / "staging").rglob("*")) == []
