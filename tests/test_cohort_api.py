"""API path for persisted cohort selection and partial recording failures."""

from __future__ import annotations

import json
import time
from pathlib import Path

import pytest
from brainlearn_core import Workflow, migrate_workflow_dict
from brainlearn_server import project_store as project_store_module
from brainlearn_server.app import app, store
from brainlearn_server.auth import reset_session_token_for_tests
from fastapi.testclient import TestClient

TOKEN = "step8-cohort-api-test-token-0123456789"
HEADERS = {"Authorization": f"Bearer {TOKEN}"}
EXAMPLE_PATH = Path(__file__).parents[1] / "examples" / "eeg-first-look.workflow.json"


@pytest.fixture
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    reset_session_token_for_tests(TOKEN)
    monkeypatch.setattr(project_store_module, "_default_state_dir", lambda: tmp_path / "state")
    store.state_dir = tmp_path / "state"
    store.allowed_roots.clear()
    yield TestClient(app)
    store.allowed_roots.clear()
    reset_session_token_for_tests(TOKEN)


def _write_invalid_signal_dataset(root: Path) -> list[str]:
    root.mkdir(parents=True, exist_ok=True)
    (root / "dataset_description.json").write_text(
        json.dumps({"Name": "Synthetic cohort", "BIDSVersion": "1.8.0"}),
        encoding="utf-8",
    )
    paths = []
    for subject in ("001", "002"):
        eeg = root / f"sub-{subject}" / "eeg"
        eeg.mkdir(parents=True)
        stem = f"sub-{subject}_task-reading_run-01"
        recording = eeg / f"{stem}_eeg.edf"
        recording.write_bytes(f"not an EEG signal fixture: {subject}".encode())
        (eeg / f"{stem}_eeg.json").write_text(
            json.dumps(
                {
                    "TaskName": "reading",
                    "SamplingFrequency": 250,
                    "EEGReference": "n/a",
                    "PowerLineFrequency": 50,
                    "SoftwareFilters": "n/a",
                    "EEGChannelCount": 2,
                }
            ),
            encoding="utf-8",
        )
        (eeg / f"{stem}_channels.tsv").write_text(
            "name\ttype\tunits\nCz\tEEG\tuV\nPz\tEEG\tuV\n", encoding="utf-8"
        )
        (eeg / f"{stem}_events.tsv").write_text(
            "onset\tduration\ttrial_type\n1\t0\tstimulus\n", encoding="utf-8"
        )
        paths.append(f"sub-{subject}/eeg/{stem}_eeg.edf")
    return paths


def test_cohort_api_continues_after_record_failure_and_persists_counts(
    client: TestClient, tmp_path: Path
) -> None:
    project = tmp_path / "project"
    created = client.post(
        "/api/projects/create",
        headers=HEADERS,
        json={"path": str(project), "name": "Cohort API test"},
    )
    assert created.status_code == 200, created.text
    recordings = _write_invalid_signal_dataset(project / "datasets" / "testset")
    workflow_payload = migrate_workflow_dict(json.loads(EXAMPLE_PATH.read_text(encoding="utf-8")))
    workflow = Workflow.model_validate(workflow_payload)
    for node in workflow.nodes:
        if node.type == "input.bids_eeg":
            node.parameters[:] = [
                item.model_copy(update={"value": "datasets/testset"}) if item.id == "root" else item
                for item in node.parameters
            ]

    started = client.post(
        "/api/cohorts/start",
        headers=HEADERS,
        json={
            "path": str(project),
            "workflow": workflow.model_dump(mode="json"),
            "recording_paths": recordings,
            "seed": 19,
        },
    )
    assert started.status_code == 200, started.text
    cohort = started.json()
    assert cohort["schema_version"] == "1.2"
    assert cohort["workflow"]["id"] == workflow.id

    deadline = time.monotonic() + 20
    while time.monotonic() < deadline:
        response = client.post(
            "/api/cohorts/open",
            headers=HEADERS,
            json={"path": str(project), "cohort_id": cohort["id"]},
        )
        assert response.status_code == 200, response.text
        cohort = response.json()
        if cohort["finished_at"]:
            break
        time.sleep(0.05)
    assert cohort["state"] == "failed"
    assert cohort["selected_count"] == 2
    assert cohort["processed_count"] == 0
    assert cohort["excluded_count"] == 0
    assert cohort["failed_count"] == 2
    assert cohort["cancelled_count"] == 0
    assert all(item["child_run_id"] for item in cohort["recordings"])
    assert all(item["failure_reason"] for item in cohort["recordings"])
    assert "No recording succeeded" in cohort["failure_reason"]

    recovered = client.post("/api/cohorts/recover", headers=HEADERS, json={"path": str(project)})
    assert recovered.status_code == 200, recovered.text
    assert recovered.json()[0]["id"] == cohort["id"]
