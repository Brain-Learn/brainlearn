"""Loopback safety and workflow setup for the interactive cohort CLI."""

from __future__ import annotations

from pathlib import Path

import pytest
from brainlearn_server.cohort_cli import (
    CohortCliError,
    LocalApi,
    _choose_recordings,
    _loopback_base_url,
    _read_workflow,
)

WORKFLOW = Path(__file__).parents[1] / "examples" / "eeg-first-look.workflow.json"


def test_cli_restricts_http_to_loopback_and_disallows_redirect_destinations() -> None:
    assert _loopback_base_url("http://127.0.0.1:8000/") == "http://127.0.0.1:8000"
    assert _loopback_base_url("http://[::1]:8000") == "http://[::1]:8000"
    with pytest.raises(CohortCliError, match="loopback"):
        _loopback_base_url("https://research.example/api")
    with pytest.raises(CohortCliError, match="credentials"):
        _loopback_base_url("http://user:secret@localhost:8000")


def test_cli_sets_only_the_dataset_root_parameter() -> None:
    workflow = _read_workflow(WORKFLOW, "datasets/openneuro/ds005284")
    node = next(item for item in workflow.nodes if item.type == "input.bids_eeg")
    root = next(item for item in node.parameters if item.id == "root")
    assert root.value == "datasets/openneuro/ds005284"
    assert any(
        item.id == "event" and item.value == "condition 54"
        for item in next(item for item in workflow.nodes if item.type == "eeg.epochs").parameters
    )


def test_cli_reference_selection_uses_only_ready_first_five_subjects(monkeypatch) -> None:
    discovery = {
        "recordings": [
            {"path": f"sub-{subject}/eeg/run.edf", "subject": subject, "status": "ready"}
            for subject in ("001", "002", "003", "004", "005", "006")
        ]
    }
    monkeypatch.setattr("builtins.input", lambda _prompt: "")
    selected = _choose_recordings(discovery, "datasets/openneuro/ds005284")
    assert selected == [
        f"sub-{subject}/eeg/run.edf" for subject in ("001", "002", "003", "004", "005")
    ]


def test_cli_rejects_invalid_recording_selection(monkeypatch) -> None:
    discovery = {
        "recordings": [{"path": "sub-001/eeg/run.edf", "subject": "001", "status": "ready"}]
    }
    monkeypatch.setattr("builtins.input", lambda _prompt: "2")
    with pytest.raises(CohortCliError, match="valid numbers"):
        _choose_recordings(discovery, "local-study")


class _FakeResponse:
    def __init__(self, payload: bytes) -> None:
        self.payload = payload

    def __enter__(self):
        return self

    def __exit__(self, *_args) -> None:
        return None

    def read(self, limit: int) -> bytes:
        return self.payload[:limit]


class _FakeOpener:
    def __init__(self, payload: bytes) -> None:
        self.payload = payload

    def open(self, _request, timeout: int) -> _FakeResponse:
        assert timeout == 30
        return _FakeResponse(self.payload)


def test_cli_accepts_large_ica_diagnostics_but_keeps_a_size_limit(monkeypatch) -> None:
    diagnostic = b'{"components":"' + b"x" * 1_600_000 + b'"}'
    monkeypatch.setattr("brainlearn_server.cohort_cli._LOCAL_OPENER", _FakeOpener(diagnostic))
    api = LocalApi("http://127.0.0.1:8000", "test-token")
    assert api.artifact("/tmp/project", "run-id", "artifact-id") == diagnostic

    oversized = b"x" * 10_000_001
    monkeypatch.setattr("brainlearn_server.cohort_cli._LOCAL_OPENER", _FakeOpener(oversized))
    with pytest.raises(CohortCliError, match="10 MB"):
        api.artifact("/tmp/project", "run-id", "artifact-id")
