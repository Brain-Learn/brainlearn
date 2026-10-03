"""Read-only BIDS EEG discovery and essential metadata validation."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

import pytest
from brainlearn_server import project_store as store_module
from brainlearn_server.app import app, store
from brainlearn_server.auth import reset_session_token_for_tests
from brainlearn_server.bids_eeg import (
    BidsDatasetPathError,
    BidsEegDiscoveryService,
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
    (project / "project.json").write_text("{}", encoding="utf-8")
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
        "task_name_mismatch",
        "invalid_power_line_frequency",
        "invalid_software_filters",
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
