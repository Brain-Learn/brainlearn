"""API path for persisted cohort selection and partial recording failures."""

from __future__ import annotations

import builtins
import hashlib
import importlib.util
import json
import shutil
import subprocess
import sys
import time
from importlib.machinery import SourceFileLoader
from io import BytesIO
from pathlib import Path
from typing import Any
from zipfile import ZipFile

import pytest
from brainlearn_core import Workflow, content_identity, migrate_workflow_dict
from brainlearn_server import project_store as project_store_module
from brainlearn_server.app import app, store
from brainlearn_server.auth import reset_session_token_for_tests
from brainlearn_server.cohort_export import (
    CohortExportError,
    _check_no_secrets,
    _relative_path,
    _runner_script,
    _workflow_script,
)
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

    exported = client.post(
        "/api/cohorts/export",
        headers=HEADERS,
        json={"path": str(project), "cohort_id": cohort["id"]},
    )
    assert exported.status_code == 200, exported.text
    assert exported.headers["content-type"].startswith("application/zip")
    with ZipFile(BytesIO(exported.content)) as archive:
        names = set(archive.namelist())
        assert names == {
            "README.md",
            "analysis.ipynb",
            "citations.json",
            "config.json",
            "environment.lock",
            "expected_outputs.md",
            "provenance.json",
            "replay_brainlearn.py",
            "workflow.json",
            "workflow.py",
        }
        files = {name: archive.read(name) for name in names}
    assert str(project).encode() not in b"".join(files.values())
    assert b"BRAINLEARN_SESSION_TOKEN" not in b"".join(files.values())
    assert b"mne==1.13.2" in files["environment.lock"]
    assert b"Direct MNE replay" in files["workflow.py"]
    assert b"interactively again" in files["replay_brainlearn.py"]
    assert b"no session token" in files["replay_brainlearn.py"]
    assert b"from workflow import" not in files["replay_brainlearn.py"]
    compile(files["workflow.py"].decode(), "workflow.py", "exec")
    compile(files["replay_brainlearn.py"].decode(), "replay_brainlearn.py", "exec")
    notebook = json.loads(files["analysis.ipynb"])
    headings = ["".join(cell.get("source", [])) for cell in notebook["cells"]]
    assert any("1. Setup" in cell for cell in headings)
    assert any("2. Inspect inputs" in cell for cell in headings)
    assert any("3. Quality-control decisions" in cell for cell in headings)
    assert any("4. Processing" in cell for cell in headings)
    assert any("5. Results" in cell for cell in headings)
    exported_project = tmp_path / "exported-project"
    exported_project.mkdir()
    for name, content in files.items():
        (exported_project / name).write_bytes(content)
    replay = subprocess.run(
        [
            sys.executable,
            str(exported_project / "workflow.py"),
            "--dataset-root",
            str(project / "datasets" / "testset"),
            "--output-dir",
            str(exported_project / "direct-output"),
        ],
        check=False,
        capture_output=True,
        text=True,
    )
    assert replay.returncode == 1
    assert "No recording succeeded" in replay.stdout
    direct_summary = json.loads(
        (exported_project / "direct-output" / "summary.json").read_text(encoding="utf-8")
    )
    assert direct_summary["counts"] == {
        "selected": 2,
        "processed": 0,
        "excluded": 0,
        "failed": 2,
        "cancelled": 0,
        "processed_participants": 0,
    }
    assert len(direct_summary["failure_reasons"]) == 2
    assert "Denominators" in (exported_project / "direct-output" / "report.html").read_text(
        encoding="utf-8"
    )
    repeated = client.post(
        "/api/cohorts/export",
        headers=HEADERS,
        json={"path": str(project), "cohort_id": cohort["id"]},
    )
    assert repeated.status_code == 200, repeated.text
    assert repeated.content == exported.content


def test_cohort_export_rejects_secrets_and_unsafe_paths() -> None:
    with pytest.raises(CohortExportError, match="session token"):
        _check_no_secrets({"notes.json": b'{"Authorization": "Bearer ghp_12345678901234567890"}'})
    with pytest.raises(CohortExportError, match="absolute personal path"):
        _check_no_secrets({"notes.json": b'{"note": "/Users/researcher/private data"}'})
    with pytest.raises(CohortExportError, match="absolute personal path"):
        _check_no_secrets({"notes.json": b'{"note": "/Volumes/Research/private data"}'})
    with pytest.raises(CohortExportError, match="absolute personal path"):
        _check_no_secrets(
            {"notes.json": json.dumps({"note": r"\\lab-files\users\alice\study\raw.edf"}).encode()}
        )
    with pytest.raises(CohortExportError, match="project-relative"):
        _relative_path("../outside/dataset", "Dataset path")


def test_exported_source_path_guard_and_brainlearn_runner_loopback_guard(
    tmp_path: Path,
) -> None:
    direct_script = tmp_path / "workflow.py"
    direct_script.write_text(_workflow_script(), encoding="utf-8")
    direct_namespace: dict[str, Any] = {
        "__file__": str(direct_script),
        "__name__": "brainlearn_export_test",
    }
    exec(
        compile(direct_script.read_text(encoding="utf-8"), str(direct_script), "exec"),
        direct_namespace,
    )
    for unsafe in ("../outside/file.edf", r"..\outside\file.edf", r"C:\data\file.edf"):
        with pytest.raises(ValueError, match="stay inside"):
            direct_namespace["_safe_source"](tmp_path, unsafe)

    runner_script = tmp_path / "replay_brainlearn.py"
    runner_script.write_text(_runner_script(), encoding="utf-8")
    runner_namespace: dict[str, Any] = {
        "__file__": str(runner_script),
        "__name__": "brainlearn_runner_test",
    }
    exec(
        compile(runner_script.read_text(encoding="utf-8"), str(runner_script), "exec"),
        runner_namespace,
    )
    validate = runner_namespace["_loopback_service_url"]
    assert validate("http://127.0.0.1:8000/") == "http://127.0.0.1:8000"
    assert validate("http://[::1]:8000") == "http://[::1]:8000"
    assert validate("https://localhost:8443") == "https://localhost:8443"
    for unsafe in (
        "https://example.org",
        "http://192.168.1.10:8000",
        "http://user:secret@localhost:8000",
        "http://localhost:invalid",
    ):
        with pytest.raises(ValueError, match="loopback"):
            validate(unsafe)
    dataset = tmp_path / "project" / "datasets" / "sample"
    dataset.mkdir(parents=True)
    source = dataset / "sub-01_task-demo_eeg.edf"
    source.write_bytes(b"test-only EEG bytes")
    relative = source.relative_to(dataset).as_posix()
    input_files = [
        {
            "path": relative,
            "byte_size": source.stat().st_size,
            "sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
        }
    ]
    recording_path = "sub-01/eeg/sub-01_task-demo_eeg.edf"
    identity = content_identity(
        "artifact",
        {
            "schema_version": "bids-eeg-input-1.1",
            "recording_path": recording_path,
            "files": input_files,
        },
    )
    records = [
        {
            "recording_path": recording_path,
            "input_files": input_files,
            "input_identity": identity,
        }
    ]
    runner_namespace["_verify_source_identities"](dataset, records)
    preflight_excluded = {
        "recording_path": "sub-02/eeg/sub-02_task-demo_eeg.edf",
        "original_state": "excluded",
        "input_files": [],
        "input_identity": None,
    }
    runner_namespace["_verify_source_identities"](dataset, [*records, preflight_excluded])
    with pytest.raises(ValueError, match="No pinned input-file identity"):
        runner_namespace["_verify_source_identities"](
            dataset,
            [
                {
                    "recording_path": "sub-03/eeg/sub-03_task-demo_eeg.edf",
                    "original_state": "selected",
                    "input_files": [],
                }
            ],
        )
    with pytest.raises(ValueError, match="checksum changed"):
        source.write_bytes(b"changed bytes")
        runner_namespace["_verify_source_identities"](dataset, records)
    with pytest.raises(ValueError, match="project-relative"):
        runner_namespace["_dataset_root"](tmp_path / "project", "../outside")

    shutil.copy(
        Path(__file__).parents[1] / "packages/server/src/brainlearn_server/eeg-cohort.lock",
        tmp_path / "environment.lock",
    )
    with pytest.raises(RuntimeError, match="does not match the recorded SHA-256"):
        direct_namespace["_locked_versions"](
            {"direct_replay_environment": {"lockfile_sha256": "0" * 64}}
        )


def test_cohort_project_export_requires_the_local_session_token(client: TestClient) -> None:
    response = client.post(
        "/api/cohorts/export",
        json={"path": "/tmp/project", "cohort_id": "cohort-test-001"},
    )
    assert response.status_code == 401


def test_exported_direct_mne_replay_matches_separate_synthetic_reference(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    mne = pytest.importorskip("mne")
    mne_bids = pytest.importorskip("mne_bids")
    np = pytest.importorskip("numpy")
    source_root = Path(__file__).parents[1] / "examples" / "synthetic-eeg-bids"
    manifest = json.loads(
        (Path(__file__).parents[1] / "examples" / "synthetic-eeg-bids-manifest.json").read_text(
            encoding="utf-8"
        )
    )
    dataset_root = tmp_path / "dataset"
    shutil.copytree(source_root, dataset_root)
    recording_path = "sub-01/eeg/sub-01_task-Demo_eeg.vhdr"
    input_files = []
    for relative, expected_sha256 in sorted(manifest["files"].items()):
        path = dataset_root / relative
        input_files.append(
            {
                "path": relative,
                "byte_size": path.stat().st_size,
                "sha256": expected_sha256,
            }
        )
    identity = content_identity(
        "artifact",
        {
            "schema_version": "bids-eeg-input-1.1",
            "recording_path": recording_path,
            "files": input_files,
        },
    )
    workflow = json.loads(EXAMPLE_PATH.read_text(encoding="utf-8"))
    for node in workflow["nodes"]:
        parameters = {item["id"]: item for item in node["parameters"]}
        if node["type"] == "eeg.epochs":
            parameters["event"]["value"] = "stimulus"
        if node["type"] == "eeg.psd":
            parameters["n_fft"]["value"] = 128
            parameters["n_per_seg"]["value"] = 128
            parameters["n_overlap"]["value"] = 64
    parameters_by_node = {
        node["id"]: {item["id"]: item["value"] for item in node["parameters"]}
        for node in workflow["nodes"]
    }
    config: dict[str, Any] = {
        "source_cohort": {"cohort_id": "cohort-synthetic-replay", "seed": 19},
        "workflow": workflow,
        "parameters": parameters_by_node,
        "citations": [],
        "recordings": [
            {
                "recording_path": recording_path,
                "participant": "sub-01",
                "session": None,
                "run": None,
                "original_state": "selected",
                "input_identity": identity,
                "input_files": input_files,
                "failure_reason": None,
                "qc_decisions": {
                    "eeg.bad_channel_review": {
                        "decision": "approved",
                        "reviewer": "Synthetic replay test",
                        "note": "Explicit test-only decision for the synthetic fixture.",
                        "created_at": "2026-11-14T00:00:00+00:00",
                        "review_id": "qc-synthetic-source-bound",
                        "source_content_identity": identity,
                        "excluded_channel_names": [],
                    }
                },
                "node_seeds": {"eeg.ica_fit": 19},
            }
        ],
    }
    export_dir = tmp_path / "exported"
    export_dir.mkdir()
    script = export_dir / "workflow.py"
    script.write_text(_workflow_script(), encoding="utf-8")
    lockfile = Path(__file__).parents[1] / "packages/server/src/brainlearn_server/eeg-cohort.lock"
    lock_bytes = lockfile.read_bytes()
    config["direct_replay_environment"] = {
        "lockfile_sha256": hashlib.sha256(lock_bytes).hexdigest()
    }
    (export_dir / "config.json").write_text(json.dumps(config), encoding="utf-8")
    shutil.copy(lockfile, export_dir / "environment.lock")
    loader = SourceFileLoader("exported_workflow_for_test", str(script))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    answers = iter(["approve", "Synthetic replay test", ""])
    monkeypatch.setattr(builtins, "input", lambda _prompt="": next(answers))
    output_dir = tmp_path / "direct-output"
    replay = module.run_workflow(
        config,
        dataset_root,
        reuse_recorded_bad_channel_qc=True,
        show_ica_diagnostics=False,
        output_dir=output_dir,
    )
    assert replay["summary"]["counts"] == {
        "selected": 1,
        "processed": 1,
        "excluded": 0,
        "failed": 0,
        "cancelled": 0,
        "processed_participants": 1,
    }
    participant_summary = replay["summary"]["participant_level"][0]
    assert participant_summary["recording_count"] == 1
    participant_path = output_dir / participant_summary["output_files"][0]
    participant = json.loads(participant_path.read_text(encoding="utf-8"))
    assert participant["bad_channel_review"]["reviewer"] == "Synthetic replay test"
    assert participant["bad_channel_review"]["reused_after_explicit_acknowledgement"] is True
    assert participant["finished_at"]
    assert replay["summary"]["environment"]["packages"]["mne"] == "1.13.2"
    report = (output_dir / "report.html").read_text(encoding="utf-8")
    assert "Cohort ERP (µV; participant mean and SD)" in report
    assert "cohort_erp_mean_uv" not in report
    assert "Cohort PSD (µV²/Hz; participant mean and SD)" in report
    assert "mean_psd_uv2_per_hz" in report

    raw = mne_bids.read_raw_bids(
        mne_bids.get_bids_path_from_fname(dataset_root / recording_path), verbose="ERROR"
    )
    try:
        raw.load_data(verbose="ERROR")
        raw.filter(
            l_freq=1.0,
            h_freq=40.0,
            picks=["Cz", "Pz"],
            method="fir",
            phase="zero",
            fir_design="firwin",
            fir_window="hamming",
            filter_length="auto",
            l_trans_bandwidth="auto",
            h_trans_bandwidth="auto",
            skip_by_annotation=("edge", "bad_acq_skip"),
            pad="reflect_limited",
            verbose="ERROR",
        )
        fit_raw = raw.copy().pick("eeg")
        ica = mne.preprocessing.ICA(
            n_components=None,
            method="fastica",
            random_state=19,
            max_iter=1000,
            fit_params=None,
            allow_ref_meg=False,
            verbose="ERROR",
        )
        ica.fit(
            fit_raw,
            picks=["Cz", "Pz"],
            reject_by_annotation=False,
            decim=1,
            verbose="ERROR",
        )
        reviewed = raw.copy()
        ica.apply(reviewed, exclude=[], verbose="ERROR")
        events, event_id = mne.events_from_annotations(
            reviewed, event_id={"stimulus": 1}, regexp=None, verbose="ERROR"
        )
        epochs = mne.Epochs(
            reviewed,
            events,
            event_id={"stimulus": event_id["stimulus"]},
            tmin=-0.2,
            tmax=0.8,
            baseline=(-0.2, 0.0),
            picks="eeg",
            preload=True,
            reject=None,
            reject_by_annotation=True,
            event_repeated="error",
            on_missing="raise",
            verbose="ERROR",
        )
        direct_uv = epochs.get_data(picks="eeg", units="uV", verbose="ERROR")
        spectrum = epochs.compute_psd(
            method="welch",
            fmin=1.0,
            fmax=40.0,
            picks="eeg",
            n_fft=128,
            n_per_seg=128,
            n_overlap=64,
            window="hamming",
            average="mean",
            remove_dc=True,
            verbose="ERROR",
        )
        np.testing.assert_allclose(
            participant["erp_mean_uv"], direct_uv.mean(axis=0), rtol=1e-7, atol=1e-12
        )
        np.testing.assert_allclose(
            participant["psd_mean_uv2_per_hz"],
            spectrum.get_data().mean(axis=0) * 1e12,
            rtol=1e-7,
            atol=1e-12,
        )
        np.testing.assert_allclose(
            participant_summary["mean_erp_uv"], direct_uv.mean(axis=0), rtol=1e-7, atol=1e-12
        )
        np.testing.assert_allclose(
            participant_summary["mean_psd_uv2_per_hz"],
            spectrum.get_data().mean(axis=0) * 1e12,
            rtol=1e-7,
            atol=1e-12,
        )
        assert participant["retained_epoch_count"] == len(epochs) == 1
    finally:
        raw.close()
    for item in input_files:
        current = hashlib.sha256((dataset_root / item["path"]).read_bytes()).hexdigest()
        assert current == item["sha256"]


def test_exported_cohort_means_weight_participants_not_recordings(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from importlib.resources import files

    script_path = tmp_path / "workflow.py"
    script_path.write_text(_workflow_script(), encoding="utf-8")
    lock_bytes = files("brainlearn_server").joinpath("eeg-cohort.lock").read_bytes()
    script_path.with_name("environment.lock").write_bytes(lock_bytes)
    loader = SourceFileLoader("exported_workflow_aggregation_test", str(script_path))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    recordings = [
        {"recording_path": "sub-01/eeg/run-01.edf", "participant": "sub-01"},
        {"recording_path": "sub-01/eeg/run-02.edf", "participant": "sub-01"},
        {"recording_path": "sub-02/eeg/run-01.edf", "participant": "sub-02"},
    ]
    monkeypatch.setattr(
        module,
        "inspect_inputs",
        lambda _config, _root, *, recording_paths: [
            {"recording_path": path, "status": "ready"} for path in recording_paths
        ],
    )
    values = {
        recordings[0]["recording_path"]: 2.0,
        recordings[1]["recording_path"]: 4.0,
        recordings[2]["recording_path"]: 8.0,
    }

    def run_recording(
        _config: Any, entry: dict[str, Any], *_args: Any, **_kwargs: Any
    ) -> dict[str, Any]:
        value = values[entry["recording_path"]]
        return {
            "participant": entry["participant"],
            "recording_path": entry["recording_path"],
            "retained_epoch_count": 1,
            "channel_names": ["Cz"],
            "times_seconds": [0.0],
            "frequencies_hz": [1.0],
            "erp_mean_uv": [[value]],
            "psd_mean_uv2_per_hz": [[value * 2]],
            "bad_channel_review": {"excluded_channel_names": []},
            "fresh_ica_review": {"excluded_components": []},
        }

    monkeypatch.setattr(module, "_run_recording", run_recording)
    config: dict[str, Any] = {
        "source_cohort": {"cohort_id": "cohort-equal-participants"},
        "recordings": [dict(item, original_state="selected") for item in recordings],
        "citations": [],
        "parameters": {},
        "direct_replay_environment": {"lockfile_sha256": hashlib.sha256(lock_bytes).hexdigest()},
    }
    result = module.run_workflow(
        config,
        tmp_path,
        reuse_recorded_bad_channel_qc=True,
        output_dir=tmp_path / "aggregate-output",
    )
    summary = result["summary"]
    assert summary["counts"]["processed"] == 3
    assert summary["counts"]["processed_participants"] == 2
    assert [item["mean_erp_uv"][0][0] for item in summary["participant_level"]] == [3.0, 8.0]
    assert summary["cohort_erp_mean_uv"][0][0] == 5.5
    assert summary["cohort_psd_mean_uv2_per_hz"][0][0] == 11.0
