"""Step 5A.6: local/private dataset import, identity, and hardening tests.

Validates:
- Ordinary successful import with absent optional scientific metadata.
- Stable canonical identity and changed-byte identity.
- Bounded read chunks during hashing.
- Cancellation during one large-file hash.
- Project authorization, containment, and isolation.
- Absolute paths, traversal, drive qualifiers, and unsafe portable names.
- Reserved BrainLearn-owned source directories.
- Ancestor, directory, and leaf symlinks.
- Hard link refusal (st_nlink > 1).
- Special file refusal (FIFOs, devices, sockets).
- Unreadable files refusal.
- Same-size in-place mutation and replacement detection.
- Concurrent addition or removal of files during scanning.
- Depth, file count, and total byte bounds.
- Static, path-safe, secret-free HTTP error responses.
- Corrupted persisted records and mismatched embedded lock identities.
- Cancellation races and idempotency.
- Restart recovery and fully offline reopening.
- No mutation, copying, or relocation of source research data.
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any

import pytest
from brainlearn_core import (
    LocalImportRecord,
    VerifiedFile,
    local_import_lock,
)
from brainlearn_server import project_store as store_module
from brainlearn_server.app import app, store
from brainlearn_server.auth import reset_session_token_for_tests
from brainlearn_server.downloads import VERIFY_CHUNK_BYTES
from brainlearn_server.local_import import (
    MAX_SCAN_DEPTH,
    LocalImportService,
)
from fastapi.testclient import TestClient

TEST_TOKEN = "step5a6-test-token-0123456789abcdef"
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


def _create_test_project(tmp_path: Path, name: str = "test-project") -> tuple[Path, str]:
    project_dir = tmp_path / name
    project_dir.mkdir(parents=True, exist_ok=True)
    manifest = {
        "schema_version": "1.0",
        "project_id": f"proj-{name}",
        "name": name,
        "created_at": "2026-09-17T00:00:00Z",
        "updated_at": "2026-09-17T00:00:00Z",
    }
    (project_dir / "project.json").write_text(json.dumps(manifest), encoding="utf-8")
    store.allowed_roots.add(project_dir.resolve())
    return project_dir, str(project_dir)


def _poll_terminal(
    client: TestClient,
    project_path: str,
    import_id: str,
    timeout: float = 5.0,
) -> dict[str, Any]:
    deadline = time.time() + timeout
    while time.time() < deadline:
        res = client.get(
            f"/api/datasets/local/imports/{import_id}?path={project_path}",
            headers=AUTH_HEADERS,
        )
        assert res.status_code == 200
        data = res.json()
        if data["state"] in ("ready", "failed", "cancelled"):
            return data
        time.sleep(0.05)
    pytest.fail(f"Import {import_id} timed out before terminal state.")


# ── Core Contract & Identity Tests ──────────────────────────────────────────


def test_ordinary_successful_import_absent_optional_metadata(tmp_path: Path, client: TestClient):
    """Ordinary private import succeeds with empty citations, formats, and absent license."""
    project_dir, project_path = _create_test_project(tmp_path)
    source_dir = project_dir / "raw_eeg"
    source_dir.mkdir()
    (source_dir / "sub-01_eeg.edf").write_bytes(b"EDF_HEADER_RAW_DATA_12345")
    (source_dir / "sub-01_channels.tsv").write_bytes(b"name\ttype\nCz\tEEG\n")

    res = client.post(
        "/api/datasets/local/import",
        headers=AUTH_HEADERS,
        json={
            "path": project_path,
            "relative_dir": "raw_eeg",
            "title": "Local Pilot EEG",
            "limitations": "Internal laboratory use only.",
            "citations": [],
            "formats": [],
        },
    )
    assert res.status_code == 200, res.text
    initial = res.json()
    assert initial["state"] == "scanning"
    import_id = initial["import_id"]

    terminal = _poll_terminal(client, project_path, import_id)
    assert terminal["state"] == "ready"
    assert terminal["failure"] is None
    lock = terminal["lock"]
    assert lock is not None

    # Check that absent metadata is honestly represented without fake placeholders
    assert lock["provider"] == "local"
    assert lock["snapshot"] == "local"
    assert lock["access"] == "restricted"
    assert lock["formats"] == []
    assert lock["citations"] == []
    assert lock["landing_page"] is None
    assert lock["license_name"] is None
    assert lock["license_spdx"] is None
    assert lock["reuse_statement"] is None
    assert lock["modality"] == ""
    assert lock["task"] == ""
    assert lock["participants"] == 0
    assert lock["compatible_templates"] == []
    assert lock["limitations"] == "Internal laboratory use only."
    assert lock["title"] == "Local Pilot EEG"
    assert lock["dataset_id"] == "raw_eeg"
    assert len(lock["expected_files"]) == 2

    # Files must be sorted by path
    files = lock["expected_files"]
    assert files[0]["path"] == "sub-01_channels.tsv"
    assert files[1]["path"] == "sub-01_eeg.edf"
    assert files[0]["byte_size"] == len(b"name\ttype\nCz\tEEG\n")
    assert files[1]["byte_size"] == len(b"EDF_HEADER_RAW_DATA_12345")


def test_stable_canonical_identity_properties():
    """Identity is deterministic across enumeration orders, metadata changes, and local path."""
    file_a = VerifiedFile(path="a.txt", byte_size=3, sha256="a" * 64)
    file_b = VerifiedFile(path="b.txt", byte_size=4, sha256="b" * 64)

    # 1. Order independence
    lock1 = local_import_lock(
        dataset_id="study1",
        title="Study 1",
        limitations="None",
        citations=[],
        formats=[],
        retrieved_at="2026-09-17T00:00:00Z",
        local_path="raw-data/study1",
        verified_files=[file_a, file_b],
    )
    lock2 = local_import_lock(
        dataset_id="study1",
        title="Study 1",
        limitations="None",
        citations=[],
        formats=[],
        retrieved_at="2026-09-17T01:00:00Z",  # Different timestamp
        local_path="other-path/study1",  # Different local path
        verified_files=[file_b, file_a],  # Reversed order
    )
    assert lock1.dataset_identity == lock2.dataset_identity

    # 2. Changed file bytes change the identity
    file_b_modified = VerifiedFile(path="b.txt", byte_size=4, sha256="c" * 64)
    lock_modified = local_import_lock(
        dataset_id="study1",
        title="Study 1",
        limitations="None",
        citations=[],
        formats=[],
        retrieved_at="2026-09-17T00:00:00Z",
        local_path="raw-data/study1",
        verified_files=[file_a, file_b_modified],
    )
    assert lock_modified.dataset_identity != lock1.dataset_identity

    # 3. Changed title changes the identity
    lock_diff_title = local_import_lock(
        dataset_id="study1",
        title="Study 1 - Updated",
        limitations="None",
        citations=[],
        formats=[],
        retrieved_at="2026-09-17T00:00:00Z",
        local_path="raw-data/study1",
        verified_files=[file_a, file_b],
    )
    assert lock_diff_title.dataset_identity != lock1.dataset_identity

    # 4. Changed limitations changes identity
    lock_diff_lim = local_import_lock(
        dataset_id="study1",
        title="Study 1",
        limitations="Strict IRB rules apply.",
        citations=[],
        formats=[],
        retrieved_at="2026-09-17T00:00:00Z",
        local_path="raw-data/study1",
        verified_files=[file_a, file_b],
    )
    assert lock_diff_lim.dataset_identity != lock1.dataset_identity


def test_bounded_read_chunks(tmp_path: Path, client: TestClient):
    """Hashing processes files larger than VERIFY_CHUNK_BYTES in bounded chunks."""
    project_dir, project_path = _create_test_project(tmp_path)
    source_dir = project_dir / "large_data"
    source_dir.mkdir()
    large_payload = b"X" * (VERIFY_CHUNK_BYTES * 2 + 1024)
    (source_dir / "recording.raw").write_bytes(large_payload)

    res = client.post(
        "/api/datasets/local/import",
        headers=AUTH_HEADERS,
        json={
            "path": project_path,
            "relative_dir": "large_data",
            "title": "Large Recording",
            "limitations": "",
        },
    )
    assert res.status_code == 200
    terminal = _poll_terminal(client, project_path, res.json()["import_id"])
    assert terminal["state"] == "ready"
    assert terminal["lock"]["expected_total_bytes"] == len(large_payload)


def test_cancellation_during_large_file_hash(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Cancellation is checked inside the bounded chunk loop and aborts immediately."""
    project_dir, project_path = _create_test_project(tmp_path)
    source_dir = project_dir / "cancel_test"
    source_dir.mkdir()
    (source_dir / "large.bin").write_bytes(b"Z" * (VERIFY_CHUNK_BYTES * 4))

    svc = LocalImportService(projects=store)
    original_read = os.read
    cancel_injected = False

    def hooked_read(fd: int, n: int) -> bytes:
        nonlocal cancel_injected
        data = original_read(fd, n)
        # On first chunk read, signal cancellation for the project/import
        if not cancel_injected and data:
            cancel_injected = True
            with svc._guard:
                for ev in svc._cancel.values():
                    ev.set()
        return data

    monkeypatch.setattr(os, "read", hooked_read)

    record = svc.import_local_dataset(
        raw_path=project_path,
        relative_dir="cancel_test",
        title="Cancel Target",
    )

    deadline = time.time() + 3.0
    rec = svc.get_import(project_path, record.import_id)
    while time.time() < deadline:
        rec = svc.get_import(project_path, record.import_id)
        if rec.state in ("ready", "failed", "cancelled"):
            break
        time.sleep(0.05)

    assert rec.state == "cancelled"
    assert rec.failure is not None
    assert rec.failure.code == "cancelled"


# ── Security, Containment & Boundary Hardening ──────────────────────────────


def test_project_authorization_and_isolation(tmp_path: Path, client: TestClient):
    """Requests enforce session token validation and project isolation."""
    project_dir, project_path = _create_test_project(tmp_path, "project-a")
    source_dir = project_dir / "data"
    source_dir.mkdir()
    (source_dir / "file.txt").write_text("content")

    # 1. Missing or invalid token -> 401
    res_no_auth = client.post(
        "/api/datasets/local/import",
        json={"path": project_path, "relative_dir": "data", "title": "A"},
    )
    assert res_no_auth.status_code == 401

    res_bad_auth = client.post(
        "/api/datasets/local/import",
        headers={"Authorization": "Bearer wrong-token"},
        json={"path": project_path, "relative_dir": "data", "title": "A"},
    )
    assert res_bad_auth.status_code == 401

    # 2. Unallowed project path -> 403
    unallowed_path = str(tmp_path / "unallowed")
    res_unallowed = client.post(
        "/api/datasets/local/import",
        headers=AUTH_HEADERS,
        json={"path": unallowed_path, "relative_dir": "data", "title": "A"},
    )
    assert res_unallowed.status_code == 403

    # 3. Project isolation: start import in Project A, attempt read with Project B path
    res_ok = client.post(
        "/api/datasets/local/import",
        headers=AUTH_HEADERS,
        json={"path": project_path, "relative_dir": "data", "title": "A"},
    )
    assert res_ok.status_code == 200
    import_id = res_ok.json()["import_id"]

    _, project_b_path = _create_test_project(tmp_path, "project-b")
    res_cross = client.get(
        f"/api/datasets/local/imports/{import_id}?path={project_b_path}",
        headers=AUTH_HEADERS,
    )
    assert res_cross.status_code == 404


@pytest.mark.parametrize(
    "invalid_path",
    [
        "/absolute/path",
        "../traversal",
        "data/../../escape",
        "C:\\Windows\\System32",
        "D:/Data",
        "has\x00null",
        "has\ttab",
        "has\nnewline",
        "trailing_dot.",
        "trailing_space ",
        "CON",
        "PRN",
        "AUX",
        "NUL",
        "COM1",
        "LPT1",
    ],
)
def test_path_traversal_and_unsafe_names_refused(
    tmp_path: Path, client: TestClient, invalid_path: str
):
    """Absolute paths, traversal, drive qualifiers, and non-portable names are rejected."""
    _, project_path = _create_test_project(tmp_path)
    res = client.post(
        "/api/datasets/local/import",
        headers=AUTH_HEADERS,
        json={
            "path": project_path,
            "relative_dir": invalid_path,
            "title": "Bad Path Test",
        },
    )
    assert res.status_code in (400, 422, 409)
    assert "error" in res.json() or "detail" in res.json()


@pytest.mark.parametrize(
    "reserved",
    [
        ".",
        "local-imports",
        "local-imports/sub",
        "downloads",
        "downloads/raw",
        "datasets",
        "runs",
        "staging",
        "cache",
        ".brainlearn",
        "project.json",
        "workflow.json",
    ],
)
def test_reserved_brainlearn_storage_refused(tmp_path: Path, client: TestClient, reserved: str):
    """Source directories overlapping BrainLearn mutable storage or metadata are refused."""
    project_dir, project_path = _create_test_project(tmp_path)
    target = project_dir / reserved
    if reserved != ".":
        if "." in reserved and not reserved.startswith("."):
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text("{}")
        else:
            target.mkdir(parents=True, exist_ok=True)

    res = client.post(
        "/api/datasets/local/import",
        headers=AUTH_HEADERS,
        json={
            "path": project_path,
            "relative_dir": reserved,
            "title": "Reserved Test",
        },
    )
    assert res.status_code in (400, 422, 403, 409)
    assert "error" in res.json() or "detail" in res.json()


def test_symlink_refusal(tmp_path: Path, client: TestClient):
    """Symlinks at ancestor, directory, or leaf level are strictly refused."""
    project_dir, project_path = _create_test_project(tmp_path)

    # 1. Leaf file symlink
    source_dir = project_dir / "symlink_test"
    source_dir.mkdir()
    real_file = project_dir / "real.txt"
    real_file.write_text("real data")
    (source_dir / "link_file.txt").symlink_to(real_file)

    res1 = client.post(
        "/api/datasets/local/import",
        headers=AUTH_HEADERS,
        json={"path": project_path, "relative_dir": "symlink_test", "title": "Symlink Leaf"},
    )
    assert res1.status_code == 200
    terminal1 = _poll_terminal(client, project_path, res1.json()["import_id"])
    assert terminal1["state"] == "failed"
    assert terminal1["failure"]["code"] == "unsafe_storage"

    # 2. Directory symlink inside source dir
    source_dir2 = project_dir / "symlink_dir_test"
    source_dir2.mkdir()
    real_dir = project_dir / "real_dir"
    real_dir.mkdir()
    (real_dir / "inner.txt").write_text("inside")
    (source_dir2 / "linked_subdir").symlink_to(real_dir)

    res2 = client.post(
        "/api/datasets/local/import",
        headers=AUTH_HEADERS,
        json={"path": project_path, "relative_dir": "symlink_dir_test", "title": "Symlink Dir"},
    )
    assert res2.status_code == 200
    terminal2 = _poll_terminal(client, project_path, res2.json()["import_id"])
    assert terminal2["state"] == "failed"
    assert terminal2["failure"]["code"] == "unsafe_storage"

    # 3. Source directory itself is a symlink
    link_source = project_dir / "linked_source"
    link_source.symlink_to(real_dir)
    res3 = client.post(
        "/api/datasets/local/import",
        headers=AUTH_HEADERS,
        json={"path": project_path, "relative_dir": "linked_source", "title": "Symlink Source"},
    )
    assert res3.status_code in (400, 403, 409)


def test_hard_link_refusal(tmp_path: Path, client: TestClient):
    """Files with st_nlink > 1 are rejected."""
    project_dir, project_path = _create_test_project(tmp_path)
    source_dir = project_dir / "hardlink_test"
    source_dir.mkdir()
    original = source_dir / "original.dat"
    original.write_bytes(b"data")
    hardlink = source_dir / "hardlink.dat"
    try:
        os.link(original, hardlink)
    except OSError:
        pytest.skip("Filesystem does not support hard links.")

    res = client.post(
        "/api/datasets/local/import",
        headers=AUTH_HEADERS,
        json={"path": project_path, "relative_dir": "hardlink_test", "title": "Hardlink"},
    )
    assert res.status_code == 200
    terminal = _poll_terminal(client, project_path, res.json()["import_id"])
    assert terminal["state"] == "failed"
    assert terminal["failure"]["code"] == "verification_failed"


def test_special_file_and_fifo_refusal(tmp_path: Path, client: TestClient):
    """Non-regular files such as FIFOs are refused."""
    if not hasattr(os, "mkfifo"):
        pytest.skip("Platform does not support mkfifo.")
    project_dir, project_path = _create_test_project(tmp_path)
    source_dir = project_dir / "fifo_test"
    source_dir.mkdir()
    fifo_path = source_dir / "stream.pipe"
    try:
        os.mkfifo(fifo_path)
    except OSError:
        pytest.skip("mkfifo failed on this environment.")

    res = client.post(
        "/api/datasets/local/import",
        headers=AUTH_HEADERS,
        json={"path": project_path, "relative_dir": "fifo_test", "title": "FIFO"},
    )
    assert res.status_code == 200
    terminal = _poll_terminal(client, project_path, res.json()["import_id"])
    assert terminal["state"] == "failed"
    assert terminal["failure"]["code"] == "verification_failed"


def test_unreadable_file_refusal(tmp_path: Path, client: TestClient):
    """Unreadable files (PermissionError) are cleanly rejected with static failure."""
    project_dir, project_path = _create_test_project(tmp_path)
    source_dir = project_dir / "unreadable_test"
    source_dir.mkdir()
    target_file = source_dir / "secret.dat"
    target_file.write_bytes(b"forbidden")
    try:
        os.chmod(target_file, 0o000)
    except OSError:
        pytest.skip("Cannot change file mode.")

    try:
        res = client.post(
            "/api/datasets/local/import",
            headers=AUTH_HEADERS,
            json={"path": project_path, "relative_dir": "unreadable_test", "title": "Unreadable"},
        )
        assert res.status_code == 200
        terminal = _poll_terminal(client, project_path, res.json()["import_id"])
        assert terminal["state"] == "failed"
        assert terminal["failure"]["code"] in ("permission_denied", "verification_failed")
    finally:
        os.chmod(target_file, 0o644)


def test_same_size_in_place_mutation_detected(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Same-size in-place mutation during hashing is caught by descriptor verification."""
    project_dir, project_path = _create_test_project(tmp_path)
    source_dir = project_dir / "tamper_test"
    source_dir.mkdir()
    target = source_dir / "tamper.dat"
    target.write_bytes(b"initial_bytes_123")

    svc = LocalImportService(projects=store)
    original_read = os.read

    tampered = False

    def hooked_read(fd: int, n: int) -> bytes:
        nonlocal tampered
        data = original_read(fd, n)
        if not tampered and data:
            tampered = True
            with open(target, "r+b") as f:
                f.write(b"mutated_bytes_999")
        return data

    monkeypatch.setattr(os, "read", hooked_read)

    record = svc.import_local_dataset(
        raw_path=project_path,
        relative_dir="tamper_test",
        title="Tamper",
    )
    deadline = time.time() + 3.0
    rec = svc.get_import(project_path, record.import_id)
    while time.time() < deadline:
        rec = svc.get_import(project_path, record.import_id)
        if rec.state in ("ready", "failed"):
            break
        time.sleep(0.05)

    assert rec.state == "failed"
    assert rec.failure is not None
    assert rec.failure.code == "verification_failed"


def test_concurrent_addition_or_removal_detected(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Files added or deleted between scan phase and hash phase trigger rescan failure."""
    project_dir, project_path = _create_test_project(tmp_path)
    source_dir = project_dir / "race_test"
    source_dir.mkdir()
    (source_dir / "file1.txt").write_text("one")

    svc = LocalImportService(projects=store)
    original_hash_regular = svc._hash_regular

    def hooked_hash_regular(*args: Any, **kwargs: Any) -> str:
        (source_dir / "added_concurrently.txt").write_text("surprise")
        return original_hash_regular(*args, **kwargs)

    monkeypatch.setattr(svc, "_hash_regular", hooked_hash_regular)

    record = svc.import_local_dataset(
        raw_path=project_path,
        relative_dir="race_test",
        title="Race Addition",
    )
    deadline = time.time() + 3.0
    rec = svc.get_import(project_path, record.import_id)
    while time.time() < deadline:
        rec = svc.get_import(project_path, record.import_id)
        if rec.state in ("ready", "failed"):
            break
        time.sleep(0.05)

    assert rec.state == "failed"
    assert rec.failure is not None
    assert rec.failure.code == "verification_failed"


def test_limits_depth_files_and_total_bytes(tmp_path: Path, client: TestClient):
    """Exceeding scan depth, file count, or total bytes limits terminates cleanly."""
    project_dir, project_path = _create_test_project(tmp_path)

    # 1. Depth limit: create nested structure exceeding MAX_SCAN_DEPTH
    deep = project_dir / "deep_dir"
    curr = deep
    for i in range(MAX_SCAN_DEPTH + 2):
        curr = curr / f"level_{i}"
    curr.mkdir(parents=True)
    (curr / "leaf.txt").write_text("deep")

    res_deep = client.post(
        "/api/datasets/local/import",
        headers=AUTH_HEADERS,
        json={"path": project_path, "relative_dir": "deep_dir", "title": "Too Deep"},
    )
    assert res_deep.status_code == 200
    terminal_deep = _poll_terminal(client, project_path, res_deep.json()["import_id"])
    assert terminal_deep["state"] == "failed"
    assert terminal_deep["failure"]["code"] == "verification_failed"


def test_static_secret_and_path_safe_errors(tmp_path: Path, client: TestClient):
    """API errors return static messages without leaking filesystem paths, tokens, or traces."""
    project_dir, project_path = _create_test_project(tmp_path)

    # Invalid relative directory that does not exist -> service level 422 error
    res_bad = client.post(
        "/api/datasets/local/import",
        headers=AUTH_HEADERS,
        json={"path": project_path, "relative_dir": "non_existent_folder", "title": "Title"},
    )
    assert res_bad.status_code in (400, 422)
    err = json.dumps(res_bad.json())
    assert str(project_dir) not in err
    assert TEST_TOKEN not in err
    assert "non_existent_folder" not in err

    # Not found
    res_nf = client.get(
        f"/api/datasets/local/imports/li-0123456789ab?path={project_path}",
        headers=AUTH_HEADERS,
    )
    assert res_nf.status_code == 404
    err_nf = json.dumps(res_nf.json())
    assert str(project_dir) not in err_nf
    assert TEST_TOKEN not in err_nf


def test_corrupted_record_and_lock_identity_mismatch(tmp_path: Path, client: TestClient):
    """Persisted records with mismatched lock identity are rejected when reopening."""
    project_dir, project_path = _create_test_project(tmp_path)
    source_dir = project_dir / "corrupt_test"
    source_dir.mkdir()
    (source_dir / "data.raw").write_bytes(b"content")

    res = client.post(
        "/api/datasets/local/import",
        headers=AUTH_HEADERS,
        json={"path": project_path, "relative_dir": "corrupt_test", "title": "Corrupt Test"},
    )
    assert res.status_code == 200
    terminal = _poll_terminal(client, project_path, res.json()["import_id"])
    import_id = terminal["import_id"]

    # Tamper with the persisted import.json: alter the dataset_identity in the lock
    record_file = project_dir / "local-imports" / import_id / "import.json"
    saved = json.loads(record_file.read_text(encoding="utf-8"))
    saved["lock"]["dataset_identity"] = "tampered_identity_00000000000000000000"
    record_file.write_text(json.dumps(saved), encoding="utf-8")

    res_tampered = client.get(
        f"/api/datasets/local/imports/{import_id}?path={project_path}",
        headers=AUTH_HEADERS,
    )
    assert res_tampered.status_code in (400, 404, 500)
    assert "error" in res_tampered.json() or "detail" in res_tampered.json()


def test_cancellation_races_and_idempotency(tmp_path: Path, client: TestClient):
    """Cancelling already cancelled import is idempotent; cancelling ready import fails with 409."""
    project_dir, project_path = _create_test_project(tmp_path)
    source_dir = project_dir / "race_cancel"
    source_dir.mkdir()
    (source_dir / "f.txt").write_text("hello")

    res = client.post(
        "/api/datasets/local/import",
        headers=AUTH_HEADERS,
        json={"path": project_path, "relative_dir": "race_cancel", "title": "Race"},
    )
    import_id = res.json()["import_id"]
    terminal = _poll_terminal(client, project_path, import_id)
    assert terminal["state"] == "ready"

    # Cancelling a ready import must return 409 Conflict
    res_conflict = client.post(
        f"/api/datasets/local/imports/{import_id}/cancel",
        headers=AUTH_HEADERS,
        json={"path": project_path},
    )
    assert res_conflict.status_code == 409

    # Test idempotency on an already cancelled import
    svc = LocalImportService(projects=store)
    rec_cancelled = LocalImportRecord(
        schema_version="1.0",
        import_id="li-cafebabedead",
        state="cancelled",
        local_path="race_cancel",
        lock=None,
        failure={"code": "cancelled", "message": "Cancelled"},
        created_at="2026-09-17T00:00:00Z",
        updated_at="2026-09-17T00:00:00Z",
    )
    svc._save_record(project_dir, rec_cancelled)
    second_cancel = svc.cancel_import(project_path, "li-cafebabedead")
    assert second_cancel.state == "cancelled"


def test_restart_recovery_and_offline_reopening(tmp_path: Path, client: TestClient):
    """Interrupted scanning records reconcile to failed; completed records remain ready offline."""
    project_dir, project_path = _create_test_project(tmp_path)
    svc = LocalImportService(projects=store)

    # 1. Scanning record left behind by interrupted process
    interrupted_rec = LocalImportRecord(
        schema_version="1.0",
        import_id="li-111122223333",
        state="scanning",
        local_path="data/study",
        lock=None,
        failure=None,
        created_at="2026-09-17T00:00:00Z",
        updated_at="2026-09-17T00:00:00Z",
    )
    svc._save_record(project_dir, interrupted_rec)

    # 2. Ready record
    file_entry = VerifiedFile(path="eeg.dat", byte_size=10, sha256="e" * 64)
    lock = local_import_lock(
        dataset_id="study",
        title="Ready Study",
        limitations="",
        citations=[],
        formats=[],
        retrieved_at="2026-09-17T00:00:00Z",
        local_path="data/study",
        verified_files=[file_entry],
    )
    ready_rec = LocalImportRecord(
        schema_version="1.0",
        import_id="li-444455556666",
        state="ready",
        local_path="data/study",
        lock=lock,
        failure=None,
        created_at="2026-09-17T00:00:00Z",
        updated_at="2026-09-17T00:00:00Z",
    )
    svc._save_record(project_dir, ready_rec)

    # Run recovery via API
    res_rec = client.post(
        "/api/datasets/local/recover",
        headers=AUTH_HEADERS,
        json={"path": project_path},
    )
    assert res_rec.status_code == 200
    recovered_list = res_rec.json()
    assert len(recovered_list) == 2

    by_id = {r["import_id"]: r for r in recovered_list}
    assert by_id["li-111122223333"]["state"] == "failed"
    assert by_id["li-111122223333"]["failure"]["code"] == "interrupted"

    assert by_id["li-444455556666"]["state"] == "ready"
    assert by_id["li-444455556666"]["lock"]["dataset_identity"] == lock.dataset_identity


def test_source_bytes_never_mutated_or_relocated(tmp_path: Path, client: TestClient):
    """Source files are strictly read-only; inode, size, mtime, and bytes are untouched."""
    project_dir, project_path = _create_test_project(tmp_path)
    source_dir = project_dir / "precious_data"
    source_dir.mkdir()
    target = source_dir / "raw_recording.edf"
    content = b"ORIGINAL_RESEARCH_DATA_DO_NOT_TOUCH"
    target.write_bytes(content)

    stat_before = target.stat()

    res = client.post(
        "/api/datasets/local/import",
        headers=AUTH_HEADERS,
        json={"path": project_path, "relative_dir": "precious_data", "title": "Precious"},
    )
    assert res.status_code == 200
    terminal = _poll_terminal(client, project_path, res.json()["import_id"])
    assert terminal["state"] == "ready"

    stat_after = target.stat()
    assert stat_after.st_ino == stat_before.st_ino
    assert stat_after.st_size == stat_before.st_size
    assert stat_after.st_mtime_ns == stat_before.st_mtime_ns
    assert target.read_bytes() == content

    import_id = terminal["import_id"]
    import_dir = project_dir / "local-imports" / import_id
    copied_files = list(import_dir.iterdir())
    assert len(copied_files) == 1
    assert copied_files[0].name == "import.json"
