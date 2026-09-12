"""Fault-logger layout, middleware behavior, and redaction rules."""

import re
from datetime import UTC, datetime
from pathlib import Path

import pytest
from brainlearn_server.error_log import (
    ErrorLoggingMiddleware,
    error_log_dir,
    log_fault,
    log_file_for,
)
from fastapi import FastAPI
from fastapi.testclient import TestClient


@pytest.fixture
def log_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    target = tmp_path / "faults"
    monkeypatch.setenv("BRAINLEARN_ERROR_LOG_DIR", str(target))
    return target


def test_log_files_use_date_folders_and_hour_names(log_dir: Path) -> None:
    moment = datetime(2026, 9, 12, 14, 5, tzinfo=UTC)
    assert log_file_for(moment) == log_dir / "2026-09-12" / "14.log"


def test_fault_entries_record_time_request_and_traceback(log_dir: Path) -> None:
    try:
        raise RuntimeError("disk exploded for testing")
    except RuntimeError as exc:
        target = log_fault(
            summary="probe failure",
            method="POST",
            path="/api/runs/save",
            client="127.0.0.1",
            content_length="128",
            exc=exc,
            moment=datetime(2026, 9, 12, 14, 5, tzinfo=UTC),
        )

    assert target == log_dir / "2026-09-12" / "14.log"
    text = target.read_text(encoding="utf-8")
    assert "FAULT probe failure" in text
    assert "POST /api/runs/save" in text
    assert "RuntimeError: disk exploded for testing" in text
    assert "Traceback" in text


def test_fault_entries_never_store_tokens_or_bodies(log_dir: Path) -> None:
    target = log_fault(
        summary="boom",
        method="POST",
        path="/api/projects/open",
        exc=ValueError("bad"),
        moment=datetime(2026, 9, 12, 3, 0, tzinfo=UTC),
    )
    text = target.read_text(encoding="utf-8")
    assert "secret-token-value" not in text
    assert "research payload bytes" not in text


def test_middleware_logs_unhandled_failures_before_500(log_dir: Path) -> None:
    app = FastAPI()

    @app.get("/boom")
    def boom() -> dict[str, str]:
        raise RuntimeError("unexpected worker fault")

    app.add_middleware(ErrorLoggingMiddleware)
    client = TestClient(app, raise_server_exceptions=False)

    response = client.get("/boom")
    assert response.status_code == 500

    hour = datetime.now(UTC).strftime("%H")
    day = datetime.now(UTC).strftime("%Y-%m-%d")
    logged = log_dir / day / f"{hour}.log"
    text = logged.read_text(encoding="utf-8")
    assert "RuntimeError: unexpected worker fault" in text
    assert "GET /boom" in text
    assert re.search(r"Traceback", text) is not None


def test_error_log_dir_defaults_below_state_dir(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("BRAINLEARN_ERROR_LOG_DIR", raising=False)
    monkeypatch.setenv("BRAINLEARN_STATE_DIR", str(tmp_path / "state"))
    assert error_log_dir() == tmp_path / "state" / "error-log"
