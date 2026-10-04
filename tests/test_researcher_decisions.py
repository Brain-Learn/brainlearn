"""Persistence and API checks for explicit researcher EEG decisions."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

import pytest
from brainlearn_server import project_store as store_module
from brainlearn_server.app import app, store
from brainlearn_server.auth import reset_session_token_for_tests
from brainlearn_server.bids_eeg import BidsEegDiscoveryService
from brainlearn_server.researcher_decisions import (
    ResearcherDecisionCreate,
    ResearcherDecisionStore,
)
from fastapi.testclient import TestClient

TOKEN = "researcher-decision-test-token-0123456789"
HEADERS = {"Authorization": f"Bearer {TOKEN}"}


@pytest.fixture(autouse=True)
def isolated_store(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    reset_session_token_for_tests(TOKEN)
    monkeypatch.setattr(store_module, "_default_state_dir", lambda: tmp_path / "state")
    store.state_dir = tmp_path / "state"
    store.allowed_roots.clear()
    yield
    store.allowed_roots.clear()
    reset_session_token_for_tests(TOKEN)


def project_and_dataset(tmp_path: Path) -> tuple[Path, Path, Path]:
    project = tmp_path / "project"
    project.mkdir()
    (project / "brainlearn.project.json").write_text("{}", encoding="utf-8")
    (project / "workflow.json").write_text("{}", encoding="utf-8")
    store.allowed_roots.add(project.resolve())
    dataset = project / "raw-data" / "study"
    eeg = dataset / "sub-01" / "eeg"
    eeg.mkdir(parents=True)
    (dataset / "dataset_description.json").write_text(
        '{"Name":"Example","BIDSVersion":"1.2.0"}', encoding="utf-8"
    )
    recording = eeg / "sub-01_task-Rest_eeg.edf"
    recording.write_bytes(b"original raw EEG bytes")
    (eeg / "sub-01_task-Rest_eeg.json").write_text(
        '{"TaskName":"Rest","SamplingFrequency":500,"EEGReference":"average",'
        '"PowerLineFrequency":50,"SoftwareFilters":"n/a","EEGChannelCount":2}',
        encoding="utf-8",
    )
    (eeg / "sub-01_task-Rest_channels.tsv").write_text(
        "name\ttype\tunits\nCz\tEEG\tuV\nPz\tEEG\tuV\n", encoding="utf-8"
    )
    (eeg / "sub-01_task-Rest_events.tsv").write_text(
        "onset\tduration\ttrial_type\n0\t0.5\tstandard\n", encoding="utf-8"
    )
    return project, dataset, recording


def decision_payload(identity: str) -> ResearcherDecisionCreate:
    return ResearcherDecisionCreate(
        researcher="Dr. Example",
        dataset_path="raw-data/study",
        recording_path="sub-01/eeg/sub-01_task-Rest_eeg.edf",
        source_content_identity=identity,
        decision="needs_review",
        note="Inspect blink artifact around onset.",
        time_start_seconds=1.25,
        time_end_seconds=2.0,
        channel_names=("Cz",),
    )


def test_decisions_round_trip_append_only_and_preserve_source(tmp_path: Path):
    project, dataset, recording = project_and_dataset(tmp_path)
    before = hashlib.sha256(recording.read_bytes()).hexdigest()
    identity = (
        BidsEegDiscoveryService(store)
        .identify(str(project), "raw-data/study", "sub-01/eeg/sub-01_task-Rest_eeg.edf")
        .content_identity
    )
    assert identity is not None
    service = ResearcherDecisionStore(store)

    first = service.create(str(project), decision_payload(identity))
    second_payload = decision_payload(identity).model_copy(update={"decision": "accepted"})
    second = service.create(str(project), second_payload)
    saved = service.list(
        str(project), "raw-data/study", "sub-01/eeg/sub-01_task-Rest_eeg.edf", identity
    )

    assert len(saved.records) == 2
    assert saved.records[0] == first
    assert first.created_at == first.updated_at
    assert first.researcher == "Dr. Example"
    assert first.channel_names == ("Cz",)
    assert first.time_start_seconds == 1.25
    assert second.id != first.id
    assert (project / "annotations" / "eeg-researcher-decisions.json").is_file()
    assert hashlib.sha256(recording.read_bytes()).hexdigest() == before
    assert dataset.is_dir()


def test_decisions_require_an_authorized_open_project(tmp_path: Path):
    project, _, _ = project_and_dataset(tmp_path)
    service = ResearcherDecisionStore(store)
    payload = decision_payload("brainlearn-v1:artifact:" + "a" * 64)

    with pytest.raises(PermissionError):
        service.create(str(tmp_path / "outside"), payload)
    (project / "annotations").mkdir()
    (project / "annotations").rmdir()
    (project / "annotations").symlink_to(tmp_path / "outside", target_is_directory=True)
    with pytest.raises(OSError, match="symlink"):
        service.create(str(project), payload)


def test_api_refuses_decision_when_source_identity_changed(tmp_path: Path):
    project, _, recording = project_and_dataset(tmp_path)
    identity_service = BidsEegDiscoveryService(store)
    identity = identity_service.identify(
        str(project), "raw-data/study", "sub-01/eeg/sub-01_task-Rest_eeg.edf"
    ).content_identity
    assert identity is not None
    recording.write_bytes(b"changed source bytes")
    response = TestClient(app).post(
        "/api/datasets/bids-eeg/decisions/create",
        headers=HEADERS,
        json={"path": str(project), **decision_payload(identity).model_dump()},
    )

    assert response.status_code == 409, response.text
    assert not (project / "annotations").exists()


def test_api_saves_only_with_authenticated_explicit_decision(tmp_path: Path):
    project, _, recording = project_and_dataset(tmp_path)
    before = hashlib.sha256(recording.read_bytes()).hexdigest()
    identity = (
        BidsEegDiscoveryService(store)
        .identify(str(project), "raw-data/study", "sub-01/eeg/sub-01_task-Rest_eeg.edf")
        .content_identity
    )
    assert identity is not None
    client = TestClient(app)
    payload: dict[str, Any] = {"path": str(project), **decision_payload(identity).model_dump()}

    assert client.post("/api/datasets/bids-eeg/decisions/create", json=payload).status_code == 401
    without_decision = dict(payload)
    del without_decision["decision"]
    assert (
        client.post(
            "/api/datasets/bids-eeg/decisions/create",
            headers=HEADERS,
            json=without_decision,
        ).status_code
        == 422
    )
    response = client.post("/api/datasets/bids-eeg/decisions/create", headers=HEADERS, json=payload)
    assert response.status_code == 201, response.text
    assert response.json()["decision"] == "needs_review"
    assert response.json()["researcher"] == "Dr. Example"
    assert hashlib.sha256(recording.read_bytes()).hexdigest() == before
    collection = json.loads((project / "annotations" / "eeg-researcher-decisions.json").read_text())
    assert collection["schema_version"] == "1.0"
    assert len(collection["records"]) == 1
    list_payload = {
        "path": str(project),
        "dataset_path": "raw-data/study",
        "recording_path": "sub-01/eeg/sub-01_task-Rest_eeg.edf",
        "source_content_identity": identity,
    }
    listed = client.post("/api/datasets/bids-eeg/decisions", headers=HEADERS, json=list_payload)
    assert listed.status_code == 200, listed.text
    assert len(listed.json()["records"]) == 1
    recording.write_bytes(b"a different source identity")
    stale = client.post("/api/datasets/bids-eeg/decisions", headers=HEADERS, json=list_payload)
    assert stale.status_code == 409
