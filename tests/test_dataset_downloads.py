"""Step 5A.4: verified dataset download lifecycle, fully offline.

Every byte comes from an injected deterministic source double; no test in
this file may open a socket. Drivers run in real worker threads and tests
poll terminal states with deadlines, mirroring the run-worker suite.
"""

import hashlib
import io
import json
import os
import shutil
import tarfile
import threading
import time
import urllib.request
import zipfile
from pathlib import Path
from typing import Any

import pytest
from brainlearn_core import (
    CatalogEntry,
    DownloadRecord,
    MockDatasetProvider,
    ScriptedDownloadSource,
    ScriptedSnapshotArchive,
    catalog_entry_identity,
    migrate_download_dict,
)
from brainlearn_server import project_store as store_module
from brainlearn_server.app import app, downloads, store
from brainlearn_server.auth import reset_session_token_for_tests
from fastapi.testclient import TestClient

FIXTURES = Path(__file__).parent / "fixtures"

TEST_TOKEN = "step5a4-test-token-0123456789abcdef"
AUTH_HEADERS = {"Authorization": f"Bearer {TEST_TOKEN}"}


@pytest.fixture(autouse=True)
def _isolated_store(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    reset_session_token_for_tests(TEST_TOKEN)
    state_dir = tmp_path / "state"
    monkeypatch.setattr(store_module, "_default_state_dir", lambda: state_dir)
    store.state_dir = state_dir
    store.allowed_roots.clear()
    downloads.providers_override = None
    downloads.sources_override = None
    yield
    store.allowed_roots.clear()
    downloads.providers_override = None
    downloads.sources_override = None
    reset_session_token_for_tests(TEST_TOKEN)


@pytest.fixture
def client() -> TestClient:
    return TestClient(app)


def _make_entry(
    dataset_id: str = "zz20a",
    snapshot: str = "2026-09-01",
    files: list[tuple[str, bytes, bool]] | None = None,
) -> tuple[CatalogEntry, dict[str, bytes]]:
    """Build a catalog entry plus matching source bytes.

    Each file tuple is (path, bytes, with_checksum). Entries stay pending,
    exactly like provider-mapped metadata.
    """
    files = files if files is not None else [("data.bin", b"x" * 4096, True)]
    expected = [
        {
            "path": path,
            "byte_size": len(blob),
            "sha256": hashlib.sha256(blob).hexdigest() if hashed else None,
        }
        for path, blob, hashed in files
    ]
    total = sum(item["byte_size"] for item in expected)
    identity = catalog_entry_identity(
        provider="mock-archive",
        dataset_id=dataset_id,
        snapshot=snapshot,
        modality="EEG",
        task="rest",
        participants=1,
        formats=["BIDS"],
        approximate_total_bytes=total,
        expected_total_bytes=total,
        expected_files=expected,
        access="public",
        license_name="Mock public license",
        license_spdx=None,
        reuse_statement="Mock reuse statement.",
        citations=[{"title": "Mock citation.", "doi": None, "url": None}],
        landing_page=f"https://example.invalid/datasets/{dataset_id}",
        compatible_templates=[],
    )
    entry = CatalogEntry.model_validate(
        {
            "schema_version": "1.0",
            "catalog_identity": identity,
            "provider": "mock-archive",
            "dataset_id": dataset_id,
            "snapshot": snapshot,
            "title": f"Mock {dataset_id}",
            "modality": "EEG",
            "task": "rest",
            "participants": 1,
            "formats": ["BIDS"],
            "approximate_total_bytes": total,
            "expected_total_bytes": total,
            "expected_files": expected,
            "access": "public",
            "license_name": "Mock public license",
            "license_spdx": None,
            "reuse_statement": "Mock reuse statement.",
            "citations": [{"title": "Mock citation.", "doi": None, "url": None}],
            "landing_page": f"https://example.invalid/datasets/{dataset_id}",
            "compatible_templates": [],
            "curator": "",
            "review_status": "pending",
            "reviewed_at": None,
            "limitations": "Mock entry for download tests.",
        }
    )
    return entry, {path: blob for path, blob, _ in files}


def _make_project(tmp_path: Path, name: str = "downloads") -> Path:
    project = tmp_path / name
    project.mkdir(parents=True, exist_ok=True)
    (project / "brainlearn.project.json").write_text(
        json.dumps({"project_schema_version": "1.0", "id": name, "name": name}),
        encoding="utf-8",
    )
    store.register_root(project)
    return project


def _wire(
    entry: CatalogEntry,
    source_files: dict[str, bytes],
    **source_kwargs: Any,
) -> ScriptedDownloadSource:
    source = ScriptedDownloadSource(source_files, **source_kwargs)
    downloads.providers_override = {"mock-archive": MockDatasetProvider([entry])}
    downloads.sources_override = {"mock-archive": source}
    return source


def _start(
    client: TestClient,
    project: Path,
    dataset_id: str = "zz20a",
    snapshot: str = "2026-09-01",
    provider: str = "mock-archive",
) -> dict[str, Any]:
    response = client.post(
        "/api/datasets/downloads",
        json={
            "path": str(project),
            "provider": provider,
            "dataset_id": dataset_id,
            "snapshot": snapshot,
        },
        headers=AUTH_HEADERS,
    )
    assert response.status_code == 200, response.text
    return response.json()


def _wait_for_state(
    client: TestClient,
    project: Path,
    download_id: str,
    states: set[str],
    timeout: float = 20.0,
) -> dict[str, Any]:
    deadline = time.monotonic() + timeout
    body: dict[str, Any] = {}
    while time.monotonic() < deadline:
        response = client.get(
            f"/api/datasets/downloads/{download_id}",
            params={"path": str(project)},
            headers=AUTH_HEADERS,
        )
        assert response.status_code == 200, response.text
        body = response.json()
        if body["state"] in states:
            return body
        time.sleep(0.05)
    raise AssertionError(f"Download {download_id} never reached {states}; last: {body}")


def _final_dir(project: Path, dataset_id: str = "zz20a") -> Path:
    return project / "datasets" / "mock-archive" / dataset_id / "2026-09-01"


def test_record_fixture_round_trips_and_migrates() -> None:
    raw = json.loads((FIXTURES / "download-1.0.json").read_text(encoding="utf-8"))
    record = DownloadRecord.model_validate(migrate_download_dict(dict(raw)))
    assert record.schema_version == "1.0"
    assert record.state == "queued"
    assert DownloadRecord.model_validate_json(record.model_dump_json()) == record
    with pytest.raises(ValueError, match="Unsupported download schema_version"):
        migrate_download_dict({"schema_version": "9.9"})
    base = record.model_dump(mode="json")
    with pytest.raises(ValueError, match="must equal the sum over files"):
        DownloadRecord.model_validate({**base, "bytes_completed": 999})
    with pytest.raises(ValueError, match="must verify every file"):
        DownloadRecord.model_validate({**base, "state": "succeeded"})
    with pytest.raises(ValueError, match="must record its failure"):
        DownloadRecord.model_validate({**base, "state": "failed"})


def test_start_downloads_verifies_and_finalizes_with_lock(
    client: TestClient, tmp_path: Path
) -> None:
    project = _make_project(tmp_path)
    entry, source_files = _make_entry(
        files=[("a.bin", b"a" * 1000, True), ("sub/b.bin", b"b" * 500, False)]
    )
    _wire(entry, source_files)
    started = _start(client, project)
    assert started["state"] in ("queued", "downloading")
    assert started["attempt"] == 0
    finished = _wait_for_state(client, project, started["download_id"], {"succeeded"})
    assert finished["bytes_completed"] == 1500
    assert finished["lock_identity"] is not None
    assert all(item["verified"] for item in finished["files"])

    final = _final_dir(project)
    assert (final / "a.bin").read_bytes() == b"a" * 1000
    assert (final / "sub" / "b.bin").read_bytes() == b"b" * 500
    lock = json.loads((final / "dataset-lock-1.0.json").read_text(encoding="utf-8"))
    assert lock["dataset_identity"] == finished["lock_identity"]
    assert lock["catalog_identity"] == entry.catalog_identity
    partial = project / "datasets" / ".partial" / started["download_id"]
    assert partial.exists() is False
    assert "token" not in json.dumps(finished).lower()


def test_duplicate_and_completed_starts_conflict(client: TestClient, tmp_path: Path) -> None:
    project = _make_project(tmp_path)
    entry, source_files = _make_entry()
    gate = threading.Event()
    gate.set()
    _wire(entry, source_files, gate=gate, chunk_size=16)
    first = _start(client, project)
    duplicate = client.post(
        "/api/datasets/downloads",
        json={
            "path": str(project),
            "provider": "mock-archive",
            "dataset_id": "zz20a",
            "snapshot": "2026-09-01",
        },
        headers=AUTH_HEADERS,
    )
    assert duplicate.status_code == 409
    assert "already has" in duplicate.json()["detail"]
    _wait_for_state(client, project, first["download_id"], {"succeeded"})

    again = client.post(
        "/api/datasets/downloads",
        json={
            "path": str(project),
            "provider": "mock-archive",
            "dataset_id": "zz20a",
            "snapshot": "2026-09-01",
        },
        headers=AUTH_HEADERS,
    )
    assert again.status_code == 409
    assert "already downloaded" in again.json()["detail"]


def test_start_rejects_bad_requests(client: TestClient, tmp_path: Path) -> None:
    project = _make_project(tmp_path)
    entry, source_files = _make_entry()
    _wire(entry, source_files)
    base = {"path": str(project), "provider": "mock-archive"}

    missing = client.post(
        "/api/datasets/downloads",
        json={**base, "dataset_id": "zz99missing", "snapshot": "2026-09-01"},
        headers=AUTH_HEADERS,
    )
    assert missing.status_code == 404

    unknown = client.post(
        "/api/datasets/downloads",
        json={**base, "provider": "no-such-provider", "dataset_id": "x", "snapshot": "y"},
        headers=AUTH_HEADERS,
    )
    assert unknown.status_code == 404

    bad_id = client.post(
        "/api/datasets/downloads",
        json={**base, "dataset_id": "has space", "snapshot": "2026-09-01"},
        headers=AUTH_HEADERS,
    )
    assert bad_id.status_code == 422

    evil_provider = client.post(
        "/api/datasets/downloads",
        json={**base, "provider": "Evil Provider", "dataset_id": "x", "snapshot": "y"},
        headers=AUTH_HEADERS,
    )
    assert evil_provider.status_code == 422

    no_token = client.post(
        "/api/datasets/downloads",
        json={**base, "dataset_id": "zz20a", "snapshot": "2026-09-01"},
    )
    assert no_token.status_code == 401

    outside = client.post(
        "/api/datasets/downloads",
        json={
            "path": str(tmp_path / "elsewhere"),
            "provider": "mock-archive",
            "dataset_id": "zz20a",
            "snapshot": "2026-09-01",
        },
        headers=AUTH_HEADERS,
    )
    assert outside.status_code == 403


def test_start_disk_full_leaves_no_record(client: TestClient, tmp_path: Path) -> None:
    project = _make_project(tmp_path)
    entry, source_files = _make_entry()
    _wire(entry, source_files)
    real_disk_usage = shutil.disk_usage
    shutil.disk_usage = lambda path: real_disk_usage(path)._replace(free=0)  # type: ignore[assignment]
    try:
        response = client.post(
            "/api/datasets/downloads",
            json={
                "path": str(project),
                "provider": "mock-archive",
                "dataset_id": "zz20a",
                "snapshot": "2026-09-01",
            },
            headers=AUTH_HEADERS,
        )
    finally:
        shutil.disk_usage = real_disk_usage  # type: ignore[assignment]
    assert response.status_code == 507
    assert "safety margin" in response.json()["detail"]
    listed = client.get(
        "/api/datasets/downloads", params={"path": str(project)}, headers=AUTH_HEADERS
    )
    assert listed.json() == []


def test_checksum_mismatch_fails_without_final_output(client: TestClient, tmp_path: Path) -> None:
    project = _make_project(tmp_path)
    entry, _ = _make_entry(files=[("a.bin", b"a" * 100, True)])
    _wire(entry, {"a.bin": b"CORRUPT" + b"a" * 93})
    started = _start(client, project)
    finished = _wait_for_state(client, project, started["download_id"], {"failed"})
    assert finished["failure"]["code"] == "checksum_mismatch"
    assert finished["attempt"] == 3
    assert _final_dir(project).exists() is False
    assert (project / "datasets" / ".partial" / started["download_id"]).exists() is False
    assert "token" not in json.dumps(finished).lower()


def test_transient_timeout_recovers_then_succeeds(client: TestClient, tmp_path: Path) -> None:
    project = _make_project(tmp_path)
    entry, source_files = _make_entry(files=[("a.bin", b"a" * 200, True)])
    from brainlearn_core import ProviderTimeout

    source = _wire(
        entry, source_files, failures={"a.bin": [ProviderTimeout("slow"), ProviderTimeout("slow")]}
    )
    started = _start(client, project)
    finished = _wait_for_state(client, project, started["download_id"], {"succeeded"})
    assert finished["attempt"] == 3
    assert source.failures_remaining("a.bin") == 0


def test_retry_exhaustion_marks_failed(client: TestClient, tmp_path: Path) -> None:
    project = _make_project(tmp_path)
    entry, source_files = _make_entry(files=[("a.bin", b"a" * 200, True)])
    from brainlearn_core import ProviderError

    _wire(entry, source_files, failures={"a.bin": [ProviderError("down") for _ in range(9)]})
    started = _start(client, project)
    finished = _wait_for_state(client, project, started["download_id"], {"failed"})
    assert finished["failure"]["code"] == "connection_failed"
    assert finished["attempt"] == 3
    assert _final_dir(project).exists() is False


def test_short_read_fails_after_retries(client: TestClient, tmp_path: Path) -> None:
    project = _make_project(tmp_path)
    entry, source_files = _make_entry(files=[("a.bin", b"a" * 200, True)])
    _wire(entry, source_files, truncate_at={"a.bin": 50})
    started = _start(client, project)
    finished = _wait_for_state(client, project, started["download_id"], {"failed"})
    assert finished["failure"]["code"] == "incomplete_download"


def test_size_overrun_fails_fast_without_retry(client: TestClient, tmp_path: Path) -> None:
    project = _make_project(tmp_path)
    entry, _ = _make_entry(files=[("a.bin", b"a" * 100, False)])
    _wire(entry, {"a.bin": b"a" * 150})
    started = _start(client, project)
    finished = _wait_for_state(client, project, started["download_id"], {"failed"})
    assert finished["failure"]["code"] == "size_mismatch"
    assert finished["attempt"] == 1


class _BlockingSource(ScriptedDownloadSource):
    """Yields two chunks, then blocks until released (deterministic cancel)."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        self.started = threading.Event()
        self.release = threading.Event()
        self._count = 0
        super().__init__(*args, **kwargs)

    def stream_file(self, dataset_id: str, snapshot: str, path: str, offset: int) -> Any:
        for chunk in super().stream_file(dataset_id, snapshot, path, offset):
            self._count += 1
            if self._count == 2:
                self.started.set()
                assert self.release.wait(timeout=30.0)
            yield chunk


def test_cancel_mid_download_keeps_partial_and_resumes(client: TestClient, tmp_path: Path) -> None:
    project = _make_project(tmp_path)
    entry, source_files = _make_entry(files=[("a.bin", b"a" * 4096, True)])
    blocking = _BlockingSource(source_files, chunk_size=64)
    downloads.providers_override = {"mock-archive": MockDatasetProvider([entry])}
    downloads.sources_override = {"mock-archive": blocking}
    started = _start(client, project)
    assert blocking.started.wait(timeout=20.0)
    cancelled = client.post(
        f"/api/datasets/downloads/{started['download_id']}/cancel",
        json={"path": str(project)},
        headers=AUTH_HEADERS,
    )
    assert cancelled.status_code == 200
    blocking.release.set()
    state = _wait_for_state(client, project, started["download_id"], {"cancelled"})
    assert state["bytes_completed"] == 128
    assert state["failure"] is None
    partial_file = project / "datasets" / ".partial" / started["download_id"] / "a.bin"
    assert partial_file.stat().st_size == 128
    assert _final_dir(project).exists() is False

    resumed = client.post(
        f"/api/datasets/downloads/{started['download_id']}/resume",
        json={"path": str(project)},
        headers=AUTH_HEADERS,
    )
    assert resumed.status_code == 200
    assert resumed.json()["state"] == "queued"
    finished = _wait_for_state(client, project, started["download_id"], {"succeeded"})
    assert finished["bytes_completed"] == 4096
    assert (_final_dir(project) / "a.bin").read_bytes() == b"a" * 4096


def test_cancel_terminal_conflicts_and_resume_terminal_conflicts(
    client: TestClient, tmp_path: Path
) -> None:
    project = _make_project(tmp_path)
    entry, source_files = _make_entry(files=[("a.bin", b"a" * 64, True)])
    _wire(entry, source_files)
    started = _start(client, project)
    _wait_for_state(client, project, started["download_id"], {"succeeded"})
    assert (
        client.post(
            f"/api/datasets/downloads/{started['download_id']}/cancel",
            json={"path": str(project)},
            headers=AUTH_HEADERS,
        ).status_code
        == 409
    )
    assert (
        client.post(
            f"/api/datasets/downloads/{started['download_id']}/resume",
            json={"path": str(project)},
            headers=AUTH_HEADERS,
        ).status_code
        == 409
    )


def test_restart_recovery_requeues_with_bytes_intact(client: TestClient, tmp_path: Path) -> None:
    project = _make_project(tmp_path)
    entry, source_files = _make_entry(files=[("a.bin", b"a" * 4096, True)])
    blocking = _BlockingSource(source_files, chunk_size=64)
    downloads.providers_override = {"mock-archive": MockDatasetProvider([entry])}
    downloads.sources_override = {"mock-archive": blocking}
    started = _start(client, project)
    assert blocking.started.wait(timeout=20.0)

    # Stop the original driver cleanly first (a real restart would kill its
    # thread); then forge an unclean shutdown by flipping the settled
    # cancelled record back to downloading with bytes on disk.
    client.post(
        f"/api/datasets/downloads/{started['download_id']}/cancel",
        json={"path": str(project)},
        headers=AUTH_HEADERS,
    )
    blocking.release.set()
    _wait_for_state(client, project, started["download_id"], {"cancelled"})
    record_file = project / "downloads" / started["download_id"] / "transfer.json"
    raw = json.loads(record_file.read_text(encoding="utf-8"))
    assert raw["bytes_completed"] == 128
    raw["state"] = "downloading"
    raw["lock_identity"] = None
    record_file.write_text(json.dumps(raw), encoding="utf-8")

    # Simulate a service restart: a fresh service over the same project dir.
    from brainlearn_server.downloads import DownloadService

    restarted = DownloadService(store)
    restarted.providers_override = downloads.providers_override
    restarted.sources_override = downloads.sources_override
    recovered = restarted.recover_project(str(project))
    assert len(recovered) == 1
    assert recovered[0].state == "queued"
    assert recovered[0].bytes_completed == 128
    # Drive to completion through the public resume path.
    resumed = client.post(
        f"/api/datasets/downloads/{started['download_id']}/resume",
        json={"path": str(project)},
        headers=AUTH_HEADERS,
    )
    assert resumed.status_code == 200
    finished = _wait_for_state(client, project, started["download_id"], {"succeeded"})
    assert finished["bytes_completed"] == 4096


def test_recovery_never_invents_success(client: TestClient, tmp_path: Path) -> None:
    project = _make_project(tmp_path)
    entry, source_files = _make_entry(files=[("a.bin", b"a" * 128, True)])
    _wire(entry, source_files)
    started = _start(client, project)
    _wait_for_state(client, project, started["download_id"], {"succeeded"})

    # Corrupt the finalized tree and drop the lock: recovery must requeue,
    # never report success for unverified bytes.
    final_file = _final_dir(project) / "a.bin"
    final_file.write_bytes(b"tampered" + b"a" * 120)
    (final_file.parent / "dataset-lock-1.0.json").unlink()
    record_file = project / "downloads" / started["download_id"] / "transfer.json"
    raw = json.loads(record_file.read_text(encoding="utf-8"))
    raw["state"] = "downloading"
    raw["lock_identity"] = None
    record_file.write_text(json.dumps(raw), encoding="utf-8")

    from brainlearn_server.downloads import DownloadService

    restarted = DownloadService(store)
    recovered = restarted.recover_project(str(project))
    assert recovered[0].state == "queued"
    assert recovered[0].lock_identity is None


def test_recovery_adopts_verified_final_tree(client: TestClient, tmp_path: Path) -> None:
    project = _make_project(tmp_path)
    entry, source_files = _make_entry(files=[("a.bin", b"a" * 128, True)])
    _wire(entry, source_files)
    started = _start(client, project)
    before = _wait_for_state(client, project, started["download_id"], {"succeeded"})

    # Crash between finalize and record persist: lock and bytes exist while
    # the record still claims downloading.
    record_file = project / "downloads" / started["download_id"] / "transfer.json"
    raw = json.loads(record_file.read_text(encoding="utf-8"))
    raw["state"] = "downloading"
    raw["lock_identity"] = None
    raw["lock_identity"] = None
    record_file.write_text(json.dumps(raw), encoding="utf-8")

    from brainlearn_server.downloads import DownloadService

    restarted = DownloadService(store)
    recovered = restarted.recover_project(str(project))
    assert recovered[0].state == "succeeded"
    assert recovered[0].lock_identity == before["lock_identity"]


def test_range_unsupported_restarts_file_from_zero(client: TestClient, tmp_path: Path) -> None:
    project = _make_project(tmp_path)
    entry, source_files = _make_entry(files=[("a.bin", b"a" * 4096, True)])
    blocking = _BlockingSource(source_files, chunk_size=64)
    downloads.providers_override = {"mock-archive": MockDatasetProvider([entry])}
    downloads.sources_override = {"mock-archive": blocking}
    started = _start(client, project)
    assert blocking.started.wait(timeout=20.0)
    client.post(
        f"/api/datasets/downloads/{started['download_id']}/cancel",
        json={"path": str(project)},
        headers=AUTH_HEADERS,
    )
    blocking.release.set()
    _wait_for_state(client, project, started["download_id"], {"cancelled"})

    # The replacement source ignores Range: resume must truncate and stream
    # the whole file, recording a fresh zero-offset request.
    strict = ScriptedDownloadSource(source_files, honor_range=False, chunk_size=1024)
    downloads.sources_override = {"mock-archive": strict}
    resumed = client.post(
        f"/api/datasets/downloads/{started['download_id']}/resume",
        json={"path": str(project)},
        headers=AUTH_HEADERS,
    )
    assert resumed.status_code == 200
    finished = _wait_for_state(client, project, started["download_id"], {"succeeded"})
    assert finished["bytes_completed"] == 4096
    assert strict.requests[-1] == {
        "dataset_id": "zz20a",
        "snapshot": "2026-09-01",
        "path": "a.bin",
        "offset": 0,
    }
    assert (_final_dir(project) / "a.bin").read_bytes() == b"a" * 4096


def test_symlink_inside_partial_fails_safely(client: TestClient, tmp_path: Path) -> None:
    project = _make_project(tmp_path)
    entry, source_files = _make_entry(
        files=[("a.bin", b"a" * 512, True), ("b.bin", b"b" * 64, True)]
    )
    blocking = _BlockingSource(source_files, chunk_size=64)
    downloads.providers_override = {"mock-archive": MockDatasetProvider([entry])}
    downloads.sources_override = {"mock-archive": blocking}
    started = _start(client, project)
    # While the driver is deterministically blocked inside the first file,
    # plant a symlink for the second file's partial location.
    assert blocking.started.wait(timeout=20.0)
    partial_b = project / "datasets" / ".partial" / started["download_id"] / "b.bin"
    (tmp_path / "outside.txt").write_bytes(b"evil")
    partial_b.symlink_to(tmp_path / "outside.txt")
    blocking.release.set()
    finished = _wait_for_state(client, project, started["download_id"], {"failed"})
    assert finished["failure"]["code"] == "unexpected_symlink"
    assert _final_dir(project).exists() is False
    assert (tmp_path / "outside.txt").read_bytes() == b"evil"


def test_zero_size_files_verify(client: TestClient, tmp_path: Path) -> None:
    project = _make_project(tmp_path)
    entry, source_files = _make_entry(files=[("empty.bin", b"", True), ("a.bin", b"a" * 64, True)])
    _wire(entry, source_files)
    started = _start(client, project)
    _wait_for_state(client, project, started["download_id"], {"succeeded"})
    assert (_final_dir(project) / "empty.bin").read_bytes() == b""


def test_occupied_destination_is_refused(client: TestClient, tmp_path: Path) -> None:
    project = _make_project(tmp_path)
    entry, source_files = _make_entry()
    _wire(entry, source_files)
    final = _final_dir(project)
    final.mkdir(parents=True)
    (final / "junk.txt").write_text("obstruction", encoding="utf-8")
    response = client.post(
        "/api/datasets/downloads",
        json={
            "path": str(project),
            "provider": "mock-archive",
            "dataset_id": "zz20a",
            "snapshot": "2026-09-01",
        },
        headers=AUTH_HEADERS,
    )
    assert response.status_code == 409
    assert "without a valid lock" in response.json()["detail"]


def test_project_isolation_and_status_listing(client: TestClient, tmp_path: Path) -> None:
    first = _make_project(tmp_path, "first")
    second = _make_project(tmp_path, "second")
    entry, source_files = _make_entry()
    _wire(entry, source_files)
    started = _start(client, first)
    _wait_for_state(client, first, started["download_id"], {"succeeded"})

    listed = client.get(
        "/api/datasets/downloads", params={"path": str(second)}, headers=AUTH_HEADERS
    )
    assert listed.json() == []
    crossover = client.get(
        f"/api/datasets/downloads/{started['download_id']}",
        params={"path": str(second)},
        headers=AUTH_HEADERS,
    )
    assert crossover.status_code == 404
    cancel_other = client.post(
        f"/api/datasets/downloads/{started['download_id']}/cancel",
        json={"path": str(second)},
        headers=AUTH_HEADERS,
    )
    assert cancel_other.status_code in (404, 409)

    listed_first = client.get(
        "/api/datasets/downloads", params={"path": str(first)}, headers=AUTH_HEADERS
    )
    assert [item["download_id"] for item in listed_first.json()] == [started["download_id"]]
    assert listed_first.json()[0]["state"] == "succeeded"

    no_token = client.get("/api/datasets/downloads", params={"path": str(first)})
    assert no_token.status_code == 401


def test_concurrent_duplicate_starts_yield_one_transfer(tmp_path: Path) -> None:
    project = _make_project(tmp_path)
    entry, source_files = _make_entry(files=[("a.bin", b"a" * 64, True)])
    source = ScriptedDownloadSource(source_files)
    downloads.providers_override = {"mock-archive": MockDatasetProvider([entry])}
    downloads.sources_override = {"mock-archive": source}
    from brainlearn_server.downloads import DownloadConflictError, DownloadService

    service = DownloadService(store)
    service.providers_override = downloads.providers_override
    service.sources_override = downloads.sources_override
    outcomes: list[str] = []

    def _attempt() -> None:
        try:
            service.start_download(str(project), "mock-archive", "zz20a", "2026-09-01")
            outcomes.append("started")
        except DownloadConflictError:
            outcomes.append("conflict")

    threads = [threading.Thread(target=_attempt) for _ in range(4)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=20.0)
    assert outcomes.count("started") == 1
    assert outcomes.count("conflict") == 3
    # Join the winning driver before teardown clears the authorized roots;
    # otherwise its guarded writes would race the teardown.
    deadline = time.monotonic() + 20.0
    while time.monotonic() < deadline:
        records = service.list_downloads(str(project))
        if records and records[0].state in ("succeeded", "failed", "cancelled"):
            break
        time.sleep(0.05)
    else:
        raise AssertionError("Winning driver never reached a terminal state")
    key = f"{project}::" + records[0].download_id
    with service._guard:
        worker = service._threads.get(key)
    if worker is not None:
        worker.join(timeout=20.0)


def test_resume_hashes_prefix_in_bounded_chunks(
    client: TestClient, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A multi-mebibyte resume prefix must not be retained in memory.

    The recording hasher delegates to the real digest while tracking every
    update size: incremental prefix hashing keeps each update within one
    verify chunk, while buffering the whole prefix would show a single
    offset-sized update.
    """
    import hashlib

    from brainlearn_server.downloads import VERIFY_CHUNK_BYTES

    total = 4 * 1024 * 1024
    prefix_len = 5 * VERIFY_CHUNK_BYTES // 2
    assert prefix_len > VERIFY_CHUNK_BYTES
    blob = b"q" * total
    project = _make_project(tmp_path)
    entry, source_files = _make_entry(files=[("big.bin", blob, True)])
    gate = threading.Event()
    _wire(entry, source_files, gate=gate, chunk_size=65536)

    started = _start(client, project)
    download_id = started["download_id"]
    partial = project / "datasets" / ".partial" / download_id / "big.bin"
    deadline = time.monotonic() + 20.0
    while not partial.is_file():
        assert time.monotonic() < deadline, "driver never opened the partial file"
        time.sleep(0.01)
    cancelled = client.post(
        f"/api/datasets/downloads/{download_id}/cancel",
        json={"path": str(project)},
        headers=AUTH_HEADERS,
    )
    assert cancelled.status_code == 200
    gate.set()
    _wait_for_state(client, project, download_id, {"cancelled"})
    # Seed a multi-chunk verified-size prefix directly on disk.
    partial.write_bytes(blob[:prefix_len])

    real_sha256 = hashlib.sha256
    update_sizes: list[int] = []

    class _RecordingHash:
        def __init__(self, data: bytes = b"") -> None:
            self._inner = real_sha256(data)
            update_sizes.append(len(data))

        def update(self, data: bytes) -> None:
            update_sizes.append(len(data))
            self._inner.update(data)

        def hexdigest(self) -> str:
            return self._inner.hexdigest()

    monkeypatch.setattr(hashlib, "sha256", _RecordingHash)
    resumed = client.post(
        f"/api/datasets/downloads/{download_id}/resume",
        json={"path": str(project)},
        headers=AUTH_HEADERS,
    )
    assert resumed.status_code == 200
    finished = _wait_for_state(client, project, download_id, {"succeeded"})
    assert finished["bytes_completed"] == total
    assert (_final_dir(project) / "big.bin").read_bytes() == blob
    assert update_sizes, "expected the recorder to observe hashing"
    assert max(update_sizes) <= VERIFY_CHUNK_BYTES


def test_full_flow_stays_offline_and_leak_free(
    client: TestClient, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project = _make_project(tmp_path)
    entry, source_files = _make_entry(files=[("a.bin", b"a" * 128, True)])
    _wire(entry, source_files)

    def _forbidden(*args: Any, **kwargs: Any) -> Any:
        raise AssertionError("download tests must stay offline")

    monkeypatch.setattr(urllib.request, "urlopen", _forbidden)
    started = _start(client, project)
    finished = _wait_for_state(client, project, started["download_id"], {"succeeded"})
    assert "token" not in json.dumps(finished).lower()
    assert "secret" not in json.dumps(finished).lower()


# -- adversarial storage-boundary regressions (finding 1) --------------------


def test_symlinked_partial_ancestor_writes_nothing_outside(
    client: TestClient, tmp_path: Path
) -> None:
    project = _make_project(tmp_path)
    entry, source_files = _make_entry(files=[("a.bin", b"a" * 128, True)])
    _wire(entry, source_files)
    outside = tmp_path / "outside"
    outside.mkdir()
    sentinel = outside / "sentinel.txt"
    sentinel.write_bytes(b"do-not-touch")
    (project / "datasets").mkdir(exist_ok=True)
    (project / "datasets" / ".partial").symlink_to(outside, target_is_directory=True)

    started = _start(client, project)
    finished = _wait_for_state(client, project, started["download_id"], {"failed"})
    assert finished["failure"]["code"] in ("unsafe_storage", "unexpected_symlink")
    assert sentinel.read_bytes() == b"do-not-touch"
    assert sorted(path.name for path in outside.iterdir()) == ["sentinel.txt"]
    assert _final_dir(project).exists() is False


def test_symlinked_downloads_dir_blocks_start_and_list(client: TestClient, tmp_path: Path) -> None:
    project = _make_project(tmp_path)
    entry, source_files = _make_entry(files=[("a.bin", b"a" * 128, True)])
    _wire(entry, source_files)
    outside = tmp_path / "outside"
    outside.mkdir()
    (project / "downloads").symlink_to(outside, target_is_directory=True)

    started = client.post(
        "/api/datasets/downloads",
        json={
            "path": str(project),
            "provider": "mock-archive",
            "dataset_id": "zz20a",
            "snapshot": "2026-09-01",
        },
        headers=AUTH_HEADERS,
    )
    assert started.status_code == 409
    assert "blocked by a symlink" in started.json()["detail"]
    listed = client.get(
        "/api/datasets/downloads", params={"path": str(project)}, headers=AUTH_HEADERS
    )
    assert listed.status_code == 409
    assert list(outside.iterdir()) == []


def test_deep_parent_symlink_blocks_destination(client: TestClient, tmp_path: Path) -> None:
    project = _make_project(tmp_path)
    entry, source_files = _make_entry(files=[("a.bin", b"a" * 128, True)])
    _wire(entry, source_files)
    outside = tmp_path / "outside"
    outside.mkdir()
    sentinel = outside / "sentinel.txt"
    sentinel.write_bytes(b"do-not-touch")
    (project / "datasets").mkdir(exist_ok=True)
    (project / "datasets" / "mock-archive").symlink_to(outside, target_is_directory=True)

    started = client.post(
        "/api/datasets/downloads",
        json={
            "path": str(project),
            "provider": "mock-archive",
            "dataset_id": "zz20a",
            "snapshot": "2026-09-01",
        },
        headers=AUTH_HEADERS,
    )
    assert started.status_code == 409
    assert sentinel.read_bytes() == b"do-not-touch"
    assert sorted(path.name for path in outside.iterdir()) == ["sentinel.txt"]


# -- adversarial recovery regressions (finding 3) ----------------------------


def _forge_downloading(project: Path, download_id: str) -> None:
    record_file = project / "downloads" / download_id / "transfer.json"
    raw = json.loads(record_file.read_text(encoding="utf-8"))
    raw["state"] = "downloading"
    raw["lock_identity"] = None
    record_file.write_text(json.dumps(raw), encoding="utf-8")


def test_recovery_rejects_same_size_corruption_and_reconverges(
    client: TestClient, tmp_path: Path
) -> None:
    project = _make_project(tmp_path)
    entry, source_files = _make_entry(files=[("a.bin", b"a" * 128, True)])
    _wire(entry, source_files)
    started = _start(client, project)
    _wait_for_state(client, project, started["download_id"], {"succeeded"})

    final_file = _final_dir(project) / "a.bin"
    final_file.write_bytes(b"X" * 128)
    (final_file.parent / "dataset-lock-1.0.json").unlink()
    _forge_downloading(project, started["download_id"])

    from brainlearn_server.downloads import DownloadService

    restarted = DownloadService(store)
    recovered = restarted.recover_project(str(project))
    assert recovered[0].state == "queued"
    assert recovered[0].lock_identity is None
    assert all(not item.verified for item in recovered[0].files)

    resumed = client.post(
        f"/api/datasets/downloads/{started['download_id']}/resume",
        json={"path": str(project)},
        headers=AUTH_HEADERS,
    )
    assert resumed.status_code == 200
    _wait_for_state(client, project, started["download_id"], {"succeeded"})
    assert (_final_dir(project) / "a.bin").read_bytes() == b"a" * 128


def test_recovery_rejects_final_symlink(client: TestClient, tmp_path: Path) -> None:
    project = _make_project(tmp_path)
    entry, source_files = _make_entry(files=[("a.bin", b"a" * 128, True)])
    _wire(entry, source_files)
    started = _start(client, project)
    _wait_for_state(client, project, started["download_id"], {"succeeded"})

    final_file = _final_dir(project) / "a.bin"
    good = final_file.read_bytes()
    final_file.unlink()
    final_file.symlink_to(tmp_path / "outside-target.bin")
    (tmp_path / "outside-target.bin").write_bytes(good)
    (final_file.parent / "dataset-lock-1.0.json").unlink()
    _forge_downloading(project, started["download_id"])

    from brainlearn_server.downloads import DownloadService

    restarted = DownloadService(store)
    recovered = restarted.recover_project(str(project))
    assert recovered[0].state != "succeeded"
    assert recovered[0].lock_identity is None


def test_recovery_rejects_final_fifo(client: TestClient, tmp_path: Path) -> None:
    if not hasattr(os, "mkfifo"):
        pytest.skip(" FIFOs need os.mkfifo, unavailable on this platform")
    project = _make_project(tmp_path)
    entry, source_files = _make_entry(files=[("a.bin", b"a" * 128, True)])
    _wire(entry, source_files)
    started = _start(client, project)
    _wait_for_state(client, project, started["download_id"], {"succeeded"})

    final_file = _final_dir(project) / "a.bin"
    final_file.unlink()
    os.mkfifo(final_file)
    (final_file.parent / "dataset-lock-1.0.json").unlink()
    _forge_downloading(project, started["download_id"])

    from brainlearn_server.downloads import DownloadService

    restarted = DownloadService(store)
    recovered = restarted.recover_project(str(project))
    assert recovered[0].state != "succeeded"
    assert recovered[0].lock_identity is None


def test_recovery_rejects_final_directory_swap(client: TestClient, tmp_path: Path) -> None:
    project = _make_project(tmp_path)
    entry, source_files = _make_entry(files=[("a.bin", b"a" * 128, True)])
    _wire(entry, source_files)
    started = _start(client, project)
    _wait_for_state(client, project, started["download_id"], {"succeeded"})

    final_file = _final_dir(project) / "a.bin"
    final_file.unlink()
    final_file.mkdir()
    (final_file.parent / "dataset-lock-1.0.json").unlink()
    _forge_downloading(project, started["download_id"])

    from brainlearn_server.downloads import DownloadService

    restarted = DownloadService(store)
    recovered = restarted.recover_project(str(project))
    assert recovered[0].state != "succeeded"
    assert recovered[0].lock_identity is None


def test_recovery_rejects_extra_file_in_final_tree(client: TestClient, tmp_path: Path) -> None:
    project = _make_project(tmp_path)
    entry, source_files = _make_entry(files=[("a.bin", b"a" * 128, True)])
    _wire(entry, source_files)
    started = _start(client, project)
    _wait_for_state(client, project, started["download_id"], {"succeeded"})

    (_final_dir(project) / "stowaway.bin").write_bytes(b"unrecorded")
    _forge_downloading(project, started["download_id"])

    from brainlearn_server.downloads import DownloadService

    restarted = DownloadService(store)
    recovered = restarted.recover_project(str(project))
    # The valid lock still verifies the expected files, but the foreign
    # object blocks silent adoption: no success may be invented.
    assert (
        recovered[0].state != "succeeded"
        or (_final_dir(project) / "stowaway.bin").exists() is False
    )


# -- post-rename crash-window regressions (finding 4) ------------------------


def test_hash_failure_after_rename_parks_paused_and_resumes(
    client: TestClient, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from brainlearn_server.downloads import DownloadService

    project = _make_project(tmp_path)
    entry, source_files = _make_entry(files=[("a.bin", b"a" * 128, True)])
    _wire(entry, source_files)
    calls = {"count": 0}
    original = DownloadService._hash_regular

    def flaky(self: Any, *args: Any, **kwargs: Any) -> str:
        calls["count"] += 1
        if calls["count"] == 1:
            raise OSError("injected hash failure")
        return original(self, *args, **kwargs)

    monkeypatch.setattr(DownloadService, "_hash_regular", flaky)
    started = _start(client, project)
    parked = _wait_for_state(client, project, started["download_id"], {"paused"})
    assert parked["failure"]["code"] == "finalize_failed"
    assert _final_dir(project).exists() is False
    assert (project / "datasets" / ".partial" / started["download_id"]).is_dir()

    monkeypatch.undo()
    resumed = client.post(
        f"/api/datasets/downloads/{started['download_id']}/resume",
        json={"path": str(project)},
        headers=AUTH_HEADERS,
    )
    assert resumed.status_code == 200
    _wait_for_state(client, project, started["download_id"], {"succeeded"})
    assert (_final_dir(project) / "a.bin").read_bytes() == b"a" * 128


def test_lock_write_failure_after_rename_parks_paused_and_resumes(
    client: TestClient, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from brainlearn_server.downloads import DownloadService

    project = _make_project(tmp_path)
    entry, source_files = _make_entry(files=[("a.bin", b"a" * 128, True)])
    _wire(entry, source_files)
    calls = {"count": 0}
    original = DownloadService._publish_lock

    def flaky(self: Any, *args: Any, **kwargs: Any) -> None:
        calls["count"] += 1
        if calls["count"] == 1:
            raise OSError("injected lock-write failure")
        return original(self, *args, **kwargs)

    monkeypatch.setattr(DownloadService, "_publish_lock", flaky)
    started = _start(client, project)
    parked = _wait_for_state(client, project, started["download_id"], {"paused"})
    assert parked["failure"]["code"] == "finalize_failed"
    assert _final_dir(project).exists() is False
    assert not (_final_dir(project) / "dataset-lock-1.0.json").exists()

    monkeypatch.undo()
    resumed = client.post(
        f"/api/datasets/downloads/{started['download_id']}/resume",
        json={"path": str(project)},
        headers=AUTH_HEADERS,
    )
    assert resumed.status_code == 200
    _wait_for_state(client, project, started["download_id"], {"succeeded"})
    assert (_final_dir(project) / "a.bin").read_bytes() == b"a" * 128


def test_record_write_failure_after_lock_adopts_on_recovery(
    client: TestClient, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from brainlearn_server.downloads import DownloadService

    project = _make_project(tmp_path)
    entry, source_files = _make_entry(files=[("a.bin", b"a" * 128, True)])
    _wire(entry, source_files)

    def flaky(self: Any, *args: Any, **kwargs: Any) -> None:
        raise OSError("injected record-write failure")

    monkeypatch.setattr(DownloadService, "_persist_succeeded", flaky)
    started = _start(client, project)
    lock_path = _final_dir(project) / "dataset-lock-1.0.json"
    deadline = time.monotonic() + 20.0
    while time.monotonic() < deadline and not lock_path.is_file():
        time.sleep(0.05)
    # The lock is durable on disk while the record still claims downloading,
    # and crucially no failed record coexists with the finalized tree.
    assert lock_path.is_file()
    body = client.get(
        f"/api/datasets/downloads/{started['download_id']}",
        params={"path": str(project)},
        headers=AUTH_HEADERS,
    ).json()
    assert body["state"] != "failed"

    monkeypatch.undo()
    restarted = DownloadService(store)
    recovered = restarted.recover_project(str(project))
    assert recovered[0].state == "succeeded"
    assert recovered[0].lock_identity is not None


def test_tampered_persisted_record_is_rejected_on_read(client: TestClient, tmp_path: Path) -> None:
    project = _make_project(tmp_path)
    entry, source_files = _make_entry(files=[("a.bin", b"a" * 128, True)])
    _wire(entry, source_files)
    started = _start(client, project)
    _wait_for_state(client, project, started["download_id"], {"succeeded"})

    record_file = project / "downloads" / started["download_id"] / "transfer.json"
    raw = json.loads(record_file.read_text(encoding="utf-8"))
    raw["provider"] = "evil-archive"
    record_file.write_text(json.dumps(raw), encoding="utf-8")

    response = client.get(
        f"/api/datasets/downloads/{started['download_id']}",
        params={"path": str(project)},
        headers=AUTH_HEADERS,
    )
    assert response.status_code == 404


# -- snapshot-archive engine path (Step 5A.5) ---------------------------------


def _wire_archive(
    entry: CatalogEntry,
    members: dict[str, bytes],
    **source_kwargs: Any,
) -> ScriptedSnapshotArchive:
    source = ScriptedSnapshotArchive(members, **source_kwargs)
    downloads.providers_override = {"mock-archive": MockDatasetProvider([entry])}
    downloads.sources_override = {"mock-archive": source}
    return source


def _make_archive_entry(
    files: list[tuple[str, bytes, bool]],
) -> tuple[CatalogEntry, dict[str, bytes]]:
    entry, _ = _make_entry(files=files)
    members = {path: blob for path, blob, _ in files}
    return entry, members


def test_archive_snapshot_finalizes_with_lock(client: TestClient, tmp_path: Path) -> None:
    project = _make_project(tmp_path)
    entry, members = _make_archive_entry(
        [("a.bin", b"a" * 1000, True), ("sub/b.bin", b"b" * 500, False)]
    )
    _wire_archive(entry, members)
    started = _start(client, project)
    finished = _wait_for_state(client, project, started["download_id"], {"succeeded"})
    assert finished["bytes_completed"] == 1500
    assert finished["lock_identity"] is not None
    assert all(item["verified"] for item in finished["files"])

    final = _final_dir(project)
    assert (final / "a.bin").read_bytes() == b"a" * 1000
    assert (final / "sub" / "b.bin").read_bytes() == b"b" * 500
    lock = json.loads((final / "dataset-lock-1.0.json").read_text(encoding="utf-8"))
    assert lock["dataset_identity"] == finished["lock_identity"]
    assert lock["catalog_identity"] == entry.catalog_identity
    partial = project / "datasets" / ".partial" / started["download_id"]
    assert partial.exists() is False


def test_archive_tar_gz_snapshot_finalizes(client: TestClient, tmp_path: Path) -> None:
    project = _make_project(tmp_path)
    entry, members = _make_archive_entry([("a.bin", b"a" * 300, True)])
    _wire_archive(entry, members, format="tar.gz")
    started = _start(client, project)
    finished = _wait_for_state(client, project, started["download_id"], {"succeeded"})
    assert (_final_dir(project) / "a.bin").read_bytes() == b"a" * 300
    assert finished["lock_identity"] is not None


def test_archive_traversal_member_fails_without_escape(client: TestClient, tmp_path: Path) -> None:
    project = _make_project(tmp_path)
    entry, _ = _make_archive_entry([("a.bin", b"a" * 64, True)])
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("a.bin", b"a" * 64)
        archive.writestr("../evil.bin", b"escape")
    outside = tmp_path / "outside"
    outside.mkdir()
    sentinel = outside / "sentinel.txt"
    sentinel.write_bytes(b"do-not-touch")

    class _TraversalSource(ScriptedSnapshotArchive):
        def stream_snapshot_archive(self, dataset_id: str, snapshot: str) -> Any:
            self.requests.append({"dataset_id": dataset_id, "snapshot": snapshot})
            yield buffer.getvalue()

    downloads.providers_override = {"mock-archive": MockDatasetProvider([entry])}
    downloads.sources_override = {"mock-archive": _TraversalSource({"a.bin": b"a" * 64})}
    started = _start(client, project)
    finished = _wait_for_state(client, project, started["download_id"], {"failed"})
    assert finished["failure"]["code"] == "archive_rejected"
    assert _final_dir(project).exists() is False
    assert sentinel.read_bytes() == b"do-not-touch"
    assert sorted(path.name for path in outside.iterdir()) == ["sentinel.txt"]


def test_archive_symlink_member_fails_safely(client: TestClient, tmp_path: Path) -> None:
    project = _make_project(tmp_path)
    entry, _ = _make_archive_entry([("a.bin", b"a" * 64, True)])
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w") as archive:
        info = tarfile.TarInfo("a.bin")
        info.size = 64
        archive.addfile(info, io.BytesIO(b"a" * 64))
        link = tarfile.TarInfo("link.bin")
        link.type = tarfile.SYMTYPE
        link.linkname = "/etc/passwd"
        archive.addfile(link)

    class _LinkSource(ScriptedSnapshotArchive):
        def stream_snapshot_archive(self, dataset_id: str, snapshot: str) -> Any:
            self.requests.append({"dataset_id": dataset_id, "snapshot": snapshot})
            yield buffer.getvalue()

    downloads.providers_override = {"mock-archive": MockDatasetProvider([entry])}
    downloads.sources_override = {"mock-archive": _LinkSource({"a.bin": b"a" * 64})}
    started = _start(client, project)
    finished = _wait_for_state(client, project, started["download_id"], {"failed"})
    assert finished["failure"]["code"] == "archive_rejected"
    assert _final_dir(project).exists() is False


def test_archive_transient_fault_recovers_then_succeeds(client: TestClient, tmp_path: Path) -> None:
    from brainlearn_core import ProviderTimeout

    project = _make_project(tmp_path)
    entry, members = _make_archive_entry([("a.bin", b"a" * 200, True)])
    source = _wire_archive(
        entry, members, failures=[ProviderTimeout("slow"), ProviderTimeout("slow")]
    )
    started = _start(client, project)
    finished = _wait_for_state(client, project, started["download_id"], {"succeeded"})
    assert finished["attempt"] == 3
    assert source.failures_remaining() == 0
    assert (_final_dir(project) / "a.bin").read_bytes() == b"a" * 200


def test_archive_corrupt_bytes_fail_fast(client: TestClient, tmp_path: Path) -> None:
    project = _make_project(tmp_path)
    entry, members = _make_archive_entry([("a.bin", b"a" * 200, True)])

    class _GarbageSource(ScriptedSnapshotArchive):
        def stream_snapshot_archive(self, dataset_id: str, snapshot: str) -> Any:
            self.requests.append({"dataset_id": dataset_id, "snapshot": snapshot})
            yield b"this is not an archive at all"

    downloads.providers_override = {"mock-archive": MockDatasetProvider([entry])}
    downloads.sources_override = {"mock-archive": _GarbageSource(members)}
    assert members == {"a.bin": b"a" * 200}
    started = _start(client, project)
    finished = _wait_for_state(client, project, started["download_id"], {"failed"})
    assert finished["failure"]["code"] == "archive_rejected"
    # Unrecognizable bytes can never become an archive: no retry is spent.
    assert finished["attempt"] == 1
    assert _final_dir(project).exists() is False


def test_archive_truncated_bytes_retry_then_fail(client: TestClient, tmp_path: Path) -> None:
    import io
    import zipfile

    project = _make_project(tmp_path)
    entry, members = _make_archive_entry([("a.bin", b"a" * 200, True)])
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("a.bin", b"a" * 200)
    blob = buffer.getvalue()[: len(buffer.getvalue()) // 2]

    class _TruncatedSource(ScriptedSnapshotArchive):
        def stream_snapshot_archive(self, dataset_id: str, snapshot: str) -> Any:
            self.requests.append({"dataset_id": dataset_id, "snapshot": snapshot})
            yield blob

    downloads.providers_override = {"mock-archive": MockDatasetProvider([entry])}
    downloads.sources_override = {"mock-archive": _TruncatedSource(members)}
    started = _start(client, project)
    finished = _wait_for_state(client, project, started["download_id"], {"failed"})
    assert finished["failure"]["code"] == "archive_rejected"
    # A truncated stream might complete on retry, so attempts are spent.
    assert finished["attempt"] == 3
    assert _final_dir(project).exists() is False


def test_archive_cancel_mid_stream_keeps_partial_and_resumes(
    client: TestClient, tmp_path: Path
) -> None:
    project = _make_project(tmp_path)
    blob = b"b" * (256 * 1024)
    entry, members = _make_archive_entry([("big.bin", blob, True)])
    gate = threading.Event()
    source = _wire_archive(entry, members, gate=gate, chunk_size=8192)
    started = _start(client, project)
    deadline = time.monotonic() + 20.0
    partial_blob = project / "datasets" / ".partial" / started["download_id"] / ".snapshot-archive"
    while not partial_blob.is_file():
        assert time.monotonic() < deadline, "archive blob never started"
        time.sleep(0.02)
    cancelled = client.post(
        f"/api/datasets/downloads/{started['download_id']}/cancel",
        json={"path": str(project)},
        headers=AUTH_HEADERS,
    )
    assert cancelled.status_code == 200
    gate.set()
    state = _wait_for_state(client, project, started["download_id"], {"cancelled"})
    assert state["failure"] is None
    assert _final_dir(project).exists() is False

    resumed = client.post(
        f"/api/datasets/downloads/{started['download_id']}/resume",
        json={"path": str(project)},
        headers=AUTH_HEADERS,
    )
    assert resumed.status_code == 200
    _wait_for_state(client, project, started["download_id"], {"succeeded"})
    assert (_final_dir(project) / "big.bin").read_bytes() == blob
    assert len(source.requests) == 2


def test_archive_checksum_mismatch_fails(client: TestClient, tmp_path: Path) -> None:
    project = _make_project(tmp_path)
    entry, _ = _make_archive_entry([("a.bin", b"a" * 100, True)])
    _wire_archive(entry, {"a.bin": b"CORRUPT" + b"a" * 93})
    started = _start(client, project)
    finished = _wait_for_state(client, project, started["download_id"], {"failed"})
    assert finished["failure"]["code"] == "checksum_mismatch"
    assert _final_dir(project).exists() is False


def test_archive_mid_extract_disk_full_pauses(
    client: TestClient, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import brainlearn_server.downloads as downloads_module

    project = _make_project(tmp_path)
    entry, members = _make_archive_entry([("a.bin", b"a" * 512, True)])
    _wire_archive(entry, members)
    real_extract = downloads_module.extract_archive

    def flaky(*args: Any, **kwargs: Any) -> Any:
        from brainlearn_core.archives import ArchiveRejectedError

        raise ArchiveRejectedError("disk-full", "No space left for archive member.")

    monkeypatch.setattr(downloads_module, "extract_archive", flaky)
    started = _start(client, project)
    parked = _wait_for_state(client, project, started["download_id"], {"paused"})
    assert parked["failure"]["code"] == "disk_full"
    assert _final_dir(project).exists() is False
    monkeypatch.undo()
    assert real_extract is not None

    resumed = client.post(
        f"/api/datasets/downloads/{started['download_id']}/resume",
        json={"path": str(project)},
        headers=AUTH_HEADERS,
    )
    assert resumed.status_code == 200
    _wait_for_state(client, project, started["download_id"], {"succeeded"})
    assert (_final_dir(project) / "a.bin").read_bytes() == b"a" * 512


def test_archive_resume_skips_verified_members(client: TestClient, tmp_path: Path) -> None:
    project = _make_project(tmp_path)
    entry, members = _make_archive_entry(
        [("keep.bin", b"k" * 256, True), ("fresh.bin", b"f" * 256, True)]
    )
    gate = threading.Event()
    source = _wire_archive(entry, members, gate=gate, chunk_size=4096)
    started = _start(client, project)
    download_id = started["download_id"]
    partial_dir = project / "datasets" / ".partial" / download_id
    deadline = time.monotonic() + 20.0
    while not (partial_dir / ".snapshot-archive").is_file():
        assert time.monotonic() < deadline, "archive blob never started"
        time.sleep(0.02)
    cancelled = client.post(
        f"/api/datasets/downloads/{download_id}/cancel",
        json={"path": str(project)},
        headers=AUTH_HEADERS,
    )
    assert cancelled.status_code == 200
    gate.set()
    _wait_for_state(client, project, download_id, {"cancelled"})
    # Seed one member with exact bytes and a pinned mtime; resume must adopt
    # it by re-hashing instead of rewriting it.
    seeded = partial_dir / "keep.bin"
    seeded.write_bytes(b"k" * 256)
    marker = time.time() - 10_000
    os.utime(seeded, (marker, marker))
    seeded_ns = seeded.stat().st_mtime_ns
    requests_before = len(source.requests)

    resumed = client.post(
        f"/api/datasets/downloads/{download_id}/resume",
        json={"path": str(project)},
        headers=AUTH_HEADERS,
    )
    assert resumed.status_code == 200
    finished = _wait_for_state(client, project, download_id, {"succeeded"})
    assert (_final_dir(project) / "keep.bin").read_bytes() == b"k" * 256
    assert (_final_dir(project) / "fresh.bin").read_bytes() == b"f" * 256
    # Exactly one more archive fetch served the resume, and the adopted
    # member kept its bytes: it was verified, never rewritten.
    assert len(source.requests) == requests_before + 1
    assert (_final_dir(project) / "keep.bin").stat().st_mtime_ns == seeded_ns
    assert finished["bytes_completed"] == 512


def test_archive_resume_rejects_symlinked_member_parent(client: TestClient, tmp_path: Path) -> None:
    project = _make_project(tmp_path)
    entry, members = _make_archive_entry(
        [("sub/keep.bin", b"k" * 256, True), ("fresh.bin", b"f" * 256, True)]
    )
    gate = threading.Event()
    _wire_archive(entry, members, gate=gate, chunk_size=4096)
    started = _start(client, project)
    download_id = started["download_id"]
    partial_dir = project / "datasets" / ".partial" / download_id
    deadline = time.monotonic() + 20.0
    while not (partial_dir / ".snapshot-archive").is_file():
        assert time.monotonic() < deadline, "archive blob never started"
        time.sleep(0.02)
    cancelled = client.post(
        f"/api/datasets/downloads/{download_id}/cancel",
        json={"path": str(project)},
        headers=AUTH_HEADERS,
    )
    assert cancelled.status_code == 200
    gate.set()
    _wait_for_state(client, project, download_id, {"cancelled"})
    # Swap the member parent for a symlink to attacker bytes of the exact
    # expected size and checksum input shape: adoption must refuse to hash
    # through it instead of blessing outside bytes as verified.
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "keep.bin").write_bytes(b"k" * 256)
    (partial_dir / "sub").mkdir(exist_ok=True)
    (partial_dir / "sub" / "keep.bin").write_bytes(b"k" * 256)
    (partial_dir / "sub" / "keep.bin").unlink()
    (partial_dir / "sub").rmdir()
    (partial_dir / "sub").symlink_to(outside, target_is_directory=True)

    resumed = client.post(
        f"/api/datasets/downloads/{download_id}/resume",
        json={"path": str(project)},
        headers=AUTH_HEADERS,
    )
    assert resumed.status_code == 200
    finished = _wait_for_state(client, project, download_id, {"failed", "succeeded"})
    assert finished["state"] == "failed"
    assert _final_dir(project).exists() is False
    assert (outside / "keep.bin").read_bytes() == b"k" * 256


def test_archive_oversized_blob_fails(client: TestClient, tmp_path: Path) -> None:
    project = _make_project(tmp_path)
    entry, members = _make_archive_entry([("a.bin", b"a" * 100, True)])

    class _FloodSource(ScriptedSnapshotArchive):
        def stream_snapshot_archive(self, dataset_id: str, snapshot: str) -> Any:
            self.requests.append({"dataset_id": dataset_id, "snapshot": snapshot})
            while True:
                yield b"\x00" * (1024 * 1024)

    downloads.providers_override = {"mock-archive": MockDatasetProvider([entry])}
    downloads.sources_override = {"mock-archive": _FloodSource(members)}
    started = _start(client, project)
    finished = _wait_for_state(client, project, started["download_id"], {"failed"})
    assert finished["failure"]["code"] == "archive_too_large"
    assert _final_dir(project).exists() is False


def test_archive_recovery_preserves_cancelled_transfer(client: TestClient, tmp_path: Path) -> None:
    project = _make_project(tmp_path)
    entry, members = _make_archive_entry([("a.bin", b"a" * 1024, True)])
    gate = threading.Event()
    _wire_archive(entry, members, gate=gate, chunk_size=1024)
    started = _start(client, project)
    deadline = time.monotonic() + 20.0
    partial_blob = project / "datasets" / ".partial" / started["download_id"] / ".snapshot-archive"
    while not partial_blob.is_file():
        assert time.monotonic() < deadline, "archive blob never started"
        time.sleep(0.02)
    cancelled = client.post(
        f"/api/datasets/downloads/{started['download_id']}/cancel",
        json={"path": str(project)},
        headers=AUTH_HEADERS,
    )
    assert cancelled.status_code == 200
    gate.set()
    _wait_for_state(client, project, started["download_id"], {"cancelled"})

    from brainlearn_server.downloads import DownloadService

    restarted = DownloadService(store)
    restarted.providers_override = downloads.providers_override
    restarted.sources_override = downloads.sources_override
    recovered = restarted.recover_project(str(project))
    # A deliberate cancellation is a stable user decision, not an
    # interruption: recovery leaves it alone with bytes intact.
    assert recovered[0].state == "cancelled"
    assert recovered[0].bytes_completed == 0

    resumed = client.post(
        f"/api/datasets/downloads/{started['download_id']}/resume",
        json={"path": str(project)},
        headers=AUTH_HEADERS,
    )
    assert resumed.status_code == 200
    _wait_for_state(client, project, started["download_id"], {"succeeded"})
    assert (_final_dir(project) / "a.bin").read_bytes() == b"a" * 1024


def test_archive_reserved_staging_name_refused_at_start(client: TestClient, tmp_path: Path) -> None:
    project = _make_project(tmp_path)
    entry, members = _make_archive_entry([(".snapshot-archive", b"x" * 16, True)])
    _wire_archive(entry, members)
    response = client.post(
        "/api/datasets/downloads",
        json={
            "path": str(project),
            "provider": "mock-archive",
            "dataset_id": "zz20a",
            "snapshot": "2026-09-01",
        },
        headers=AUTH_HEADERS,
    )
    assert response.status_code == 409
    assert "reserved staging name" in response.json()["detail"]
    records = project / "downloads"
    assert not records.exists() or list(records.iterdir()) == []
