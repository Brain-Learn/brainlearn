"""Read-only BIDS EEG discovery and essential metadata validation."""

from __future__ import annotations

import hashlib
import json
import os
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from brainlearn_server import bids_eeg as bids_eeg_module
from brainlearn_server import project_store as store_module
from brainlearn_server.app import app, store
from brainlearn_server.auth import reset_session_token_for_tests
from brainlearn_server.bids_eeg import (
    BidsDatasetPathError,
    BidsEegDiscoveryService,
    BidsSignalInspectionError,
    discover_bids_eeg,
)
from fastapi.testclient import TestClient

TEST_TOKEN = "step5a11-test-token-0123456789abcdef"
AUTH_HEADERS = {"Authorization": f"Bearer {TEST_TOKEN}"}


@pytest.fixture(autouse=True)
def _isolated_store(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    reset_session_token_for_tests(TEST_TOKEN)
    monkeypatch.setattr(store_module, "_default_state_dir", lambda: tmp_path / "state")
    store.state_dir = tmp_path / "state"
    store.allowed_roots.clear()
    yield
    store.allowed_roots.clear()
    reset_session_token_for_tests(TEST_TOKEN)


def _project(tmp_path: Path) -> tuple[Path, Path]:
    project = tmp_path / "project"
    project.mkdir()
    (project / "brainlearn.project.json").write_text("{}", encoding="utf-8")
    (project / "workflow.json").write_text("{}", encoding="utf-8")
    store.allowed_roots.add(project.resolve())
    return project, project / "raw-data" / "study"


def _write_valid_dataset(root: Path, *, extension: str = ".edf") -> Path:
    eeg_dir = root / "sub-01" / "eeg"
    eeg_dir.mkdir(parents=True, exist_ok=True)
    (root / "dataset_description.json").write_text(
        json.dumps({"Name": "Example EEG", "BIDSVersion": "1.2.0"}),
        encoding="utf-8",
    )
    recording = eeg_dir / f"sub-01_task-Rest_eeg{extension}"
    recording.write_bytes(b"raw-signal-bytes-must-not-be-read-or-changed")
    (eeg_dir / "sub-01_task-Rest_eeg.json").write_text(
        json.dumps(
            {
                "TaskName": "Rest",
                "SamplingFrequency": 500,
                "EEGReference": "average",
                "PowerLineFrequency": 50,
                "SoftwareFilters": "n/a",
                "EEGChannelCount": 2,
            }
        ),
        encoding="utf-8",
    )
    (eeg_dir / "sub-01_task-Rest_channels.tsv").write_text(
        "name\ttype\tunits\nCz\tEEG\tuV\nPz\tEEG\tuV\n",
        encoding="utf-8",
    )
    (eeg_dir / "sub-01_task-Rest_events.tsv").write_text(
        "onset\tduration\ttrial_type\n0\t0.5\tstandard\n1.5\t0.2\tdeviant\n",
        encoding="utf-8",
    )
    return recording


def test_discovers_recording_and_reports_metadata_without_changing_source(tmp_path: Path):
    project, root = _project(tmp_path)
    recording = _write_valid_dataset(root)
    before = hashlib.sha256(recording.read_bytes()).hexdigest()

    result = discover_bids_eeg(root, "raw-data/study")

    assert result.status == "ready"
    assert result.dataset_name == "Example EEG"
    assert result.bids_version == "1.2.0"
    assert result.inspection_scope == "metadata_only"
    assert len(result.recordings) == 1
    found = result.recordings[0]
    assert found.path == "sub-01/eeg/sub-01_task-Rest_eeg.edf"
    assert found.subject == "01"
    assert found.task == "Rest"
    assert found.format == "edf"
    assert found.sampling_frequency_hz == 500
    assert found.eeg_reference == "average"
    assert found.eeg_channel_count == 2
    assert found.channel_names == ("Cz", "Pz")
    assert found.event_count == 2
    assert found.event_types == ("deviant", "standard")
    assert hashlib.sha256(recording.read_bytes()).hexdigest() == before
    assert project.is_dir()


def test_applies_bids_inherited_task_sidecars(tmp_path: Path):
    _, root = _project(tmp_path)
    eeg_dir = root / "sub-01" / "eeg"
    eeg_dir.mkdir(parents=True)
    (root / "dataset_description.json").write_text(
        '{"Name":"Inherited","BIDSVersion":"1.2.0"}', encoding="utf-8"
    )
    (eeg_dir / "sub-01_task-Rest_eeg.edf").write_bytes(b"opaque")
    (root / "task-Rest_eeg.json").write_text(
        json.dumps(
            {
                "TaskName": "Rest",
                "SamplingFrequency": 250,
                "EEGReference": "Cz",
                "PowerLineFrequency": "n/a",
                "SoftwareFilters": {},
            }
        ),
        encoding="utf-8",
    )

    result = discover_bids_eeg(root, "raw-data/study")

    assert result.status == "ready"
    assert result.recordings[0].sampling_frequency_hz == 250
    assert result.recordings[0].eeg_reference == "Cz"


def test_reports_missing_and_malformed_required_metadata(tmp_path: Path):
    _, root = _project(tmp_path)
    _write_valid_dataset(root)
    sidecar = root / "sub-01" / "eeg" / "sub-01_task-Rest_eeg.json"
    sidecar.write_text(
        json.dumps(
            {
                "TaskName": "Other",
                "SamplingFrequency": -1,
                "PowerLineFrequency": None,
                "SoftwareFilters": [],
            }
        ),
        encoding="utf-8",
    )

    result = discover_bids_eeg(root, "raw-data/study")

    assert result.status == "incomplete_metadata"
    codes = {issue.code for issue in result.recordings[0].issues}
    assert {
        "invalid_sampling_frequency",
        "missing_eeg_reference",
        "invalid_power_line_frequency",
        "invalid_software_filters",
    } <= codes


def test_accepts_unknown_event_timing_and_independent_task_name(tmp_path: Path):
    _, root = _project(tmp_path)
    _write_valid_dataset(root)
    sidecar = root / "sub-01" / "eeg" / "sub-01_task-Rest_eeg.json"
    metadata = json.loads(sidecar.read_text(encoding="utf-8"))
    metadata["TaskName"] = "Eyes Closed Rest"
    sidecar.write_text(json.dumps(metadata), encoding="utf-8")
    events = root / "sub-01" / "eeg" / "sub-01_task-Rest_events.tsv"
    events.write_text(
        "onset\tduration\ttrial_type\nn/a\tn/a\tunknown\n",
        encoding="utf-8",
    )

    result = discover_bids_eeg(root, "raw-data/study")

    assert result.status == "ready"
    assert result.recordings[0].event_count == 1
    assert result.recordings[0].event_types == ("unknown",)
    assert "task_name_mismatch" not in {issue.code for issue in result.recordings[0].issues}


def test_rejects_uppercase_bids_recording_extension(tmp_path: Path):
    _, root = _project(tmp_path)
    _write_valid_dataset(root, extension=".EDF")

    result = discover_bids_eeg(root, "raw-data/study")

    assert result.status == "unsupported"
    assert result.recordings[0].status == "unsupported"
    assert result.recordings[0].format is None
    assert "unsupported_format" in {issue.code for issue in result.recordings[0].issues}


def test_missing_task_entity_is_incomplete_not_unsupported(tmp_path: Path):
    _, root = _project(tmp_path)
    recording = _write_valid_dataset(root)
    recording.rename(recording.with_name("sub-01_eeg.edf"))

    result = discover_bids_eeg(root, "raw-data/study")

    assert result.status == "incomplete_metadata"
    assert result.recordings[0].format == "edf"
    assert result.recordings[0].status == "incomplete_metadata"
    assert "missing_task_entity" in {issue.code for issue in result.recordings[0].issues}


def test_directory_entry_limit_returns_clear_incomplete_status(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    _, root = _project(tmp_path)
    root.mkdir(parents=True)
    (root / "dataset_description.json").write_text(
        '{"Name":"Bounded","BIDSVersion":"1.2.0"}', encoding="utf-8"
    )
    (root / "sub-01").mkdir()
    (root / "sub-01" / "eeg").mkdir()
    monkeypatch.setattr(bids_eeg_module, "MAX_DISCOVERY_ENTRIES", 2)

    result = discover_bids_eeg(root, "raw-data/study")

    assert result.status == "incomplete_metadata"
    assert "unsafe_dataset_tree" in {issue.code for issue in result.issues}


def test_metadata_query_limit_is_reported_without_failing_the_api_shape(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    _, root = _project(tmp_path)
    _write_valid_dataset(root)
    monkeypatch.setattr(bids_eeg_module, "MAX_METADATA_QUERY_MULTIPLIER", 0)

    result = discover_bids_eeg(root, "raw-data/study")

    assert result.status == "incomplete_metadata"
    codes = {issue.code for issue in result.recordings[0].issues}
    assert {
        "invalid_eeg_sidecar",
        "invalid_channels_tsv",
        "invalid_events_tsv",
    } <= codes


def test_reports_unsupported_format_and_incomplete_brainvision_set(tmp_path: Path):
    _, root = _project(tmp_path)
    unsupported = _write_valid_dataset(root, extension=".fif")

    result = discover_bids_eeg(root, "raw-data/study")

    assert result.status == "unsupported"
    assert result.recordings[0].status == "unsupported"
    assert result.recordings[0].format is None
    assert "unsupported_format" in {issue.code for issue in result.recordings[0].issues}

    unsupported.unlink()
    brainvision = _write_valid_dataset(root, extension=".vhdr")
    result = discover_bids_eeg(root, "raw-data/study")
    assert result.status == "incomplete_metadata"
    assert "incomplete_brainvision_set" in {issue.code for issue in result.recordings[0].issues}
    (brainvision.parent / brainvision.with_suffix(".vmrk").name).write_text(
        "marker", encoding="utf-8"
    )
    (brainvision.parent / brainvision.with_suffix(".eeg").name).write_bytes(b"opaque")
    assert discover_bids_eeg(root, "raw-data/study").status == "ready"


def test_reports_non_bids_and_bids_without_recordings(tmp_path: Path):
    _, root = _project(tmp_path)
    root.mkdir(parents=True)
    assert discover_bids_eeg(root, "raw-data/study").status == "not_bids"

    root.mkdir(parents=True, exist_ok=True)
    (root / "dataset_description.json").write_text(
        '{"Name":"Empty","BIDSVersion":"1.2.0"}', encoding="utf-8"
    )
    assert discover_bids_eeg(root, "raw-data/study").status == "no_recordings"


def test_service_requires_open_project_and_rejects_symlinked_dataset(tmp_path: Path):
    project, root = _project(tmp_path)
    _write_valid_dataset(root)
    service = BidsEegDiscoveryService(store)
    assert service.discover(str(project), "raw-data/study").status == "ready"
    with pytest.raises(BidsDatasetPathError, match="project-relative"):
        service.discover(str(project), str(root))
    root.rename(project / "elsewhere")
    (project / "raw-data").rmdir()
    (project / "raw-data").symlink_to(tmp_path, target_is_directory=True)
    with pytest.raises(BidsDatasetPathError, match="symlink"):
        service.discover(str(project), "raw-data/study")


def test_api_requires_session_token_and_returns_bounded_discovery(tmp_path: Path):
    project, root = _project(tmp_path)
    _write_valid_dataset(root)
    client = TestClient(app)

    unauthenticated = client.post(
        "/api/datasets/bids-eeg/discover",
        json={"path": str(project), "relative_dir": "raw-data/study"},
    )
    assert unauthenticated.status_code == 401
    response = client.post(
        "/api/datasets/bids-eeg/discover",
        headers=AUTH_HEADERS,
        json={"path": str(project), "relative_dir": "raw-data/study"},
    )
    assert response.status_code == 200, response.text
    payload: dict[str, Any] = response.json()
    assert payload["status"] == "ready"
    assert payload["inspection_scope"] == "metadata_only"
    assert payload["recordings"][0]["path"] == "sub-01/eeg/sub-01_task-Rest_eeg.edf"


def test_signal_inspection_uses_lazy_mne_read_and_reports_measured_properties(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    project, root = _project(tmp_path)
    recording = _write_valid_dataset(root, extension=".set")
    calls: dict[str, Any] = {}

    class FakeAnnotations:
        description = ["stimulus/a", "stimulus/b"]

        def __len__(self) -> int:
            return len(self.description)

    class FakeRaw:
        info = {"sfreq": 500.0, "bads": ["Pz"], "highpass": 1.0, "lowpass": 100.0}
        n_times = 2_000
        ch_names = ["Cz", "Pz"]
        annotations = FakeAnnotations()

        def get_channel_types(self) -> list[str]:
            return ["eeg", "eeg"]

        def close(self) -> None:
            calls["closed"] = True

    bids_path = SimpleNamespace(root=None)

    def get_bids_path_from_fname(path: Path, *, check: bool) -> SimpleNamespace:
        calls["bids_path"] = path
        assert check is False
        return bids_path

    def read_raw_bids(path: SimpleNamespace, **kwargs: Any) -> FakeRaw:
        calls["read_path"] = path
        calls["kwargs"] = kwargs
        return FakeRaw()

    monkeypatch.setitem(
        sys.modules,
        "mne_bids",
        SimpleNamespace(
            get_bids_path_from_fname=get_bids_path_from_fname,
            read_raw_bids=read_raw_bids,
        ),
    )
    inspection = BidsEegDiscoveryService(store).inspect_signal(
        str(project), "raw-data/study", recording.relative_to(root).as_posix()
    )

    assert bids_path.root == root.resolve()
    assert calls["kwargs"] == {"verbose": "ERROR", "extra_params": {"preload": False}}
    assert calls["closed"] is True
    assert inspection.sampling_frequency_hz == 500
    assert inspection.sample_count == 2_000
    assert inspection.duration_seconds == 4
    assert inspection.channel_count == 2
    assert inspection.channel_types == {"eeg": 2}
    assert inspection.bad_channel_count == 1
    assert inspection.annotation_count == 2
    assert inspection.inspection_scope == "read_only_signal_metadata"


def test_signal_inspection_endpoint_requires_auth_and_returns_schema(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    project, root = _project(tmp_path)
    recording = _write_valid_dataset(root)
    from brainlearn_server import app as app_module

    monkeypatch.setattr(
        app_module.bids_eeg,
        "inspect_signal",
        lambda path, relative_dir, recording_path: bids_eeg_module.BidsEegSignalInspection(
            recording_path=recording_path,
            format="edf",
            sampling_frequency_hz=500,
            sample_count=1_000,
            duration_seconds=2,
            channel_count=2,
            channel_types={"eeg": 2},
            bad_channel_count=0,
            annotation_count=0,
        ),
    )
    payload = {
        "path": str(project),
        "relative_dir": "raw-data/study",
        "recording_path": recording.relative_to(root).as_posix(),
    }
    client = TestClient(app)
    assert client.post("/api/datasets/bids-eeg/inspect", json=payload).status_code == 401
    response = client.post("/api/datasets/bids-eeg/inspect", headers=AUTH_HEADERS, json=payload)
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["sample_count"] == 1_000
    assert body["channel_types"] == {"eeg": 2}
    assert body["inspection_scope"] == "read_only_signal_metadata"


def test_signal_inspection_rejects_unsupported_and_incomplete_recordings(
    tmp_path: Path,
):
    project, root = _project(tmp_path)
    unsupported = _write_valid_dataset(root, extension=".xyz")
    service = BidsEegDiscoveryService(store)
    with pytest.raises(BidsSignalInspectionError, match="supports EDF, BDF"):
        service.inspect_signal(
            str(project), "raw-data/study", unsupported.relative_to(root).as_posix()
        )

    unsupported.unlink()
    incomplete = _write_valid_dataset(root)
    (incomplete.parent / "sub-01_task-Rest_eeg.json").unlink()
    with pytest.raises(BidsDatasetPathError, match="complete supported BIDS EEG metadata"):
        service.inspect_signal(
            str(project), "raw-data/study", incomplete.relative_to(root).as_posix()
        )


def test_input_identity_hashes_signal_and_effective_sidecars_without_copying(tmp_path: Path):
    project, root = _project(tmp_path)
    recording_path = _write_valid_dataset(root)
    before_files = sorted(
        path.relative_to(root).as_posix() for path in root.rglob("*") if path.is_file()
    )
    client = TestClient(app)

    response = client.post(
        "/api/datasets/bids-eeg/identity",
        headers=AUTH_HEADERS,
        json={
            "path": str(project),
            "relative_dir": "raw-data/study",
            "recording_path": "sub-01/eeg/sub-01_task-Rest_eeg.edf",
        },
    )
    assert response.status_code == 200, response.text
    payload: dict[str, Any] = response.json()
    expected_paths = [
        "sub-01/eeg/sub-01_task-Rest_channels.tsv",
        "sub-01/eeg/sub-01_task-Rest_eeg.edf",
        "sub-01/eeg/sub-01_task-Rest_eeg.json",
        "sub-01/eeg/sub-01_task-Rest_events.tsv",
    ]
    assert payload["status"] == "ready"
    assert payload["inspection_scope"] == "bounded_source_hashes"
    assert [item["path"] for item in payload["files"]] == expected_paths
    for item in payload["files"]:
        source = root / item["path"]
        assert item["byte_size"] == source.stat().st_size
        assert item["sha256"] == hashlib.sha256(source.read_bytes()).hexdigest()
    assert payload["content_identity"].startswith("brainlearn-v1:artifact:")
    assert (
        sorted(path.relative_to(root).as_posix() for path in root.rglob("*") if path.is_file())
        == before_files
    )

    again = client.post(
        "/api/datasets/bids-eeg/identity",
        headers=AUTH_HEADERS,
        json={
            "path": str(project),
            "relative_dir": "raw-data/study",
            "recording_path": "sub-01/eeg/sub-01_task-Rest_eeg.edf",
        },
    )
    assert again.json()["content_identity"] == payload["content_identity"]
    recording_path.write_bytes(recording_path.read_bytes() + b"changed")
    changed = client.post(
        "/api/datasets/bids-eeg/identity",
        headers=AUTH_HEADERS,
        json={
            "path": str(project),
            "relative_dir": "raw-data/study",
            "recording_path": "sub-01/eeg/sub-01_task-Rest_eeg.edf",
        },
    )
    assert changed.status_code == 200
    assert changed.json()["content_identity"] != payload["content_identity"]


def test_input_identity_requires_auth_and_reports_resource_limit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    project, root = _project(tmp_path)
    _write_valid_dataset(root)
    client = TestClient(app)
    request = {
        "path": str(project),
        "relative_dir": "raw-data/study",
        "recording_path": "sub-01/eeg/sub-01_task-Rest_eeg.edf",
    }
    assert client.post("/api/datasets/bids-eeg/identity", json=request).status_code == 401

    monkeypatch.setattr(bids_eeg_module, "MAX_INPUT_IDENTITY_BYTES", 1)
    response = client.post("/api/datasets/bids-eeg/identity", headers=AUTH_HEADERS, json=request)
    assert response.status_code == 200, response.text
    assert response.json()["status"] == "resource_limit"
    assert response.json()["content_identity"] is None
    assert "10 GiB" in response.json()["message"]


def test_input_identity_refuses_symlink_source(tmp_path: Path):
    project, root = _project(tmp_path)
    recording = _write_valid_dataset(root)
    target = recording.with_suffix(".target")
    recording.rename(target)
    recording.symlink_to(target)
    service = BidsEegDiscoveryService(store)
    with pytest.raises(BidsDatasetPathError, match="symlink"):
        service.identify(str(project), "raw-data/study", "sub-01/eeg/sub-01_task-Rest_eeg.edf")


def test_brainvision_identity_includes_required_companions_and_inherited_sidecars(
    tmp_path: Path,
):
    project, root = _project(tmp_path)
    recording = _write_valid_dataset(root, extension=".vhdr")
    (recording.with_suffix(".vmrk")).write_text("marker", encoding="utf-8")
    (recording.with_suffix(".eeg")).write_bytes(b"binary signal")
    eeg_dir = recording.parent
    for sidecar in (
        eeg_dir / "sub-01_task-Rest_eeg.json",
        eeg_dir / "sub-01_task-Rest_channels.tsv",
        eeg_dir / "sub-01_task-Rest_events.tsv",
    ):
        sidecar.unlink()
    (root / "task-Rest_eeg.json").write_text(
        json.dumps(
            {
                "TaskName": "Rest",
                "SamplingFrequency": 500,
                "EEGReference": "average",
                "PowerLineFrequency": 50,
                "SoftwareFilters": "n/a",
                "EEGChannelCount": 2,
            }
        ),
        encoding="utf-8",
    )
    (root / "sub-01_channels.tsv").write_text(
        "name\ttype\tunits\nCz\tEEG\tuV\nPz\tEEG\tuV\n", encoding="utf-8"
    )
    (root / "sub-01_events.tsv").write_text(
        "onset\tduration\ttrial_type\n0\t0.5\tstandard\n", encoding="utf-8"
    )

    identity = BidsEegDiscoveryService(store).identify(
        str(project), "raw-data/study", "sub-01/eeg/sub-01_task-Rest_eeg.vhdr"
    )
    assert identity.status == "ready"
    assert {item.path for item in identity.files} == {
        "sub-01/eeg/sub-01_task-Rest_eeg.vhdr",
        "sub-01/eeg/sub-01_task-Rest_eeg.vmrk",
        "sub-01/eeg/sub-01_task-Rest_eeg.eeg",
        "task-Rest_eeg.json",
        "sub-01_channels.tsv",
        "sub-01_events.tsv",
    }


def test_identity_hash_refuses_a_file_changed_during_streaming(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    source = tmp_path / "recording.edf"
    source.write_bytes(b"a sufficiently long source payload")
    expected = os.lstat(source)
    monkeypatch.setattr(bids_eeg_module, "INPUT_IDENTITY_CHUNK_BYTES", 4)
    original_read = os.read
    mutated = False

    def read_then_mutate(descriptor: int, amount: int) -> bytes:
        nonlocal mutated
        chunk = original_read(descriptor, amount)
        if chunk and not mutated:
            mutated = True
            source.write_bytes(b"replacement payload with same length")
        return chunk

    monkeypatch.setattr(os, "read", read_then_mutate)
    with pytest.raises(BidsDatasetPathError, match="changed during hashing"):
        bids_eeg_module._hash_identity_file(source, expected, "recording.edf")


def test_identity_refuses_an_earlier_file_changed_after_its_hash(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    project, root = _project(tmp_path)
    _write_valid_dataset(root)
    channels = root / "sub-01/eeg/sub-01_task-Rest_channels.tsv"
    original_hash = bids_eeg_module._hash_identity_file
    changed = False

    def hash_then_mutate(path: Path, expected: os.stat_result, relative_path: str) -> str:
        nonlocal changed
        digest = original_hash(path, expected, relative_path)
        if path == channels and not changed:
            changed = True
            channels.write_text("name\ttype\tunits\nCz\tEEG\tmV\n", encoding="utf-8")
        return digest

    monkeypatch.setattr(bids_eeg_module, "_hash_identity_file", hash_then_mutate)
    with pytest.raises(BidsDatasetPathError, match="changed while the input identity"):
        BidsEegDiscoveryService(store).identify(
            str(project), "raw-data/study", "sub-01/eeg/sub-01_task-Rest_eeg.edf"
        )
