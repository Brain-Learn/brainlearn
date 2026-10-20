"""Step 7: deterministic ICA fitting, explicit review, and export agreement."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

import mne
import numpy as np
import pytest
from brainlearn_core import (
    ArtifactRecord,
    CanvasPosition,
    QCReviewArtifactIdentity,
    QCReviewInputIdentity,
    QCReviewRecord,
    Workflow,
    content_identity,
)
from brainlearn_server import project_store as store_module
from brainlearn_server.app import app, runs, store, workers
from brainlearn_server.auth import reset_session_token_for_tests
from brainlearn_server.demo_nodes import ControlledFailure, NodeContext
from brainlearn_server.mne_ica import (
    _verified_copy,
    apply_reviewed_ica_eeg,
    is_current_converged_fit_provenance,
)
from brainlearn_server.python_export import render_eeg_ica_python
from brainlearn_server.registry import instantiate_registered_node
from fastapi.testclient import TestClient

FIXTURE = Path(__file__).parents[1] / "examples" / "synthetic-eeg-bids"
FILTER_WORKFLOW = Path(__file__).parents[1] / "examples" / "eeg-filter-demo.workflow.json"
RECORDING_RELATIVE = "sub-01/eeg/sub-01_task-Demo_eeg.vhdr"
PUBLIC_FIXTURE_MANIFEST = (
    Path(__file__).parents[1] / "docs" / "fixtures" / "ds007338-sub-EP10-1.0.0.json"
)
PUBLIC_RECORDING_RELATIVE = "sub-EP10/ses-01/eeg/sub-EP10_ses-01_task-dots_run-01_eeg.edf"
PUBLIC_FIXTURE_ENV = "BRAINLEARN_PUBLIC_BIDS_EEG_FIXTURE"
TEST_TOKEN = "step7-ica-test-token-0123456789"
AUTH_HEADERS = {"Authorization": f"Bearer {TEST_TOKEN}"}
MAX_ABSOLUTE_DIFFERENCE_UV = 3e-5


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


def _ica_workflow(
    *,
    dataset_root: str = "synthetic/example-bids",
    recording_path: str = RECORDING_RELATIVE,
    high_hz: float = 40.0,
) -> Workflow:
    payload = json.loads(FILTER_WORKFLOW.read_text(encoding="utf-8"))
    bids = next(node for node in payload["nodes"] if node["id"] == "bids")
    dataset_parameter = next(
        parameter for parameter in bids["parameters"] if parameter["id"] == "root"
    )
    dataset_parameter["value"] = dataset_root
    inspect = next(node for node in payload["nodes"] if node["id"] == "inspect")
    recording_parameter = next(
        parameter for parameter in inspect["parameters"] if parameter["id"] == "recording_path"
    )
    recording_parameter["value"] = recording_path
    filter_node = next(node for node in payload["nodes"] if node["id"] == "filter")
    high_parameter = next(
        parameter for parameter in filter_node["parameters"] if parameter["id"] == "high_hz"
    )
    high_parameter["value"] = high_hz
    fit = instantiate_registered_node("eeg.ica_fit", "ica-fit", CanvasPosition(x=850, y=80))
    review = instantiate_registered_node(
        "eeg.ica_review", "ica-review", CanvasPosition(x=1050, y=180)
    )
    payload["nodes"].extend([fit.model_dump(mode="json"), review.model_dump(mode="json")])
    payload["edges"].extend(
        [
            {
                "id": "filter-fit",
                "source": {"node_id": "filter", "port_id": "filtered"},
                "target": {"node_id": "ica-fit", "port_id": "raw"},
            },
            {
                "id": "filter-review",
                "source": {"node_id": "filter", "port_id": "filtered"},
                "target": {"node_id": "ica-review", "port_id": "raw"},
            },
            {
                "id": "fit-review",
                "source": {"node_id": "ica-fit", "port_id": "ica"},
                "target": {"node_id": "ica-review", "port_id": "fit"},
            },
        ]
    )
    return Workflow.model_validate(payload)


def _create_project(
    client: TestClient,
    path: Path,
    *,
    dataset_source: Path = FIXTURE,
    dataset_root: str = "synthetic/example-bids",
    workflow: Workflow | None = None,
) -> Path:
    workflow = workflow or _ica_workflow(dataset_root=dataset_root)
    response = client.post(
        "/api/projects/create",
        json={
            "path": str(path),
            "name": "ICA integration",
            "workflow": workflow.model_dump(mode="json"),
        },
        headers=AUTH_HEADERS,
    )
    assert response.status_code == 200, response.text
    dataset = path / dataset_root
    dataset.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(dataset_source, dataset)
    return path


def _wait_for_state(
    client: TestClient, project: Path, run_id: str, states: set[str], timeout: float = 30.0
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
        if body["state"] in {"failed", "cancelled"}:
            return body
        time.sleep(0.05)
    raise AssertionError(f"Run {run_id} did not reach {states}; last: {body.get('state')}")


def _wait_for_worker_idle(project: Path, run_id: str) -> None:
    deadline = time.monotonic() + 5.0
    while time.monotonic() < deadline:
        with workers._guard:
            thread = workers._threads.get((str(project), run_id))
        if thread is None or not thread.is_alive():
            return
        time.sleep(0.01)
    raise AssertionError(f"Worker for {run_id} did not become idle.")


def _replace_fit_provenance_with_legacy_record(project: Path, run_id: str) -> Any:
    record = runs.get_run(str(project), run_id)
    fit = next(item for item in record.node_runs if item.node_type == "eeg.ica_fit")
    provenance_artifact = next(item for item in fit.artifacts if item.port_id == "provenance")
    provenance_path = project / provenance_artifact.path
    provenance = json.loads(provenance_path.read_text(encoding="utf-8"))
    provenance["schema_version"] = "1.0"
    provenance["converged"] = False
    encoded = json.dumps(provenance, ensure_ascii=False, sort_keys=True, indent=2) + "\n"
    data = encoded.encode("utf-8")
    provenance_path.write_bytes(data)
    updated_artifact = provenance_artifact.model_copy(
        update={"byte_size": len(data), "sha256": hashlib.sha256(data).hexdigest()}
    )
    updated_fit = fit.model_copy(
        update={
            "artifacts": [
                updated_artifact if item.port_id == "provenance" else item for item in fit.artifacts
            ]
        }
    )
    updated_record = record.model_copy(
        update={
            "node_runs": [updated_fit if item.id == fit.id else item for item in record.node_runs]
        }
    )
    return runs.save_run(str(project), updated_record)


def _source_snapshot(root: Path) -> dict[str, tuple[int, str]]:
    return {
        path.relative_to(root).as_posix(): (
            path.stat().st_size,
            hashlib.sha256(path.read_bytes()).hexdigest(),
        )
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


def _bad_channel_raw() -> mne.io.BaseRaw:
    sampling_frequency = 100.0
    sample_count = 5000
    time = np.arange(sample_count) / sampling_frequency
    sources = np.vstack(
        [
            np.sin(2 * np.pi * 3.1 * time) + 0.2 * np.sin(2 * np.pi * 7.3 * time),
            np.sign(np.sin(2 * np.pi * 0.7 * time)) + 0.13 * np.sin(2 * np.pi * 11.0 * time),
        ]
    )
    mixing = np.array([[1.0, 0.3], [0.2, 1.0], [0.8, -0.5]])
    info = mne.create_info(["Cz", "Pz", "Oz"], sampling_frequency, ch_types="eeg")
    info["bads"] = ["Pz"]
    return mne.io.RawArray(mixing @ sources * 1e-6, info, verbose="ERROR")


def test_ica_fit_fails_closed_on_nonconvergence_and_preexisting_outputs(
    tmp_path: Path,
) -> None:
    from brainlearn_mne.ica import MneIcaError, fit_ica

    info = mne.create_info(["Cz", "Pz", "Oz"], 100, ch_types="eeg")
    info["bads"] = ["Pz"]
    unstable = mne.io.RawArray(
        np.random.default_rng(0).normal(size=(3, 2000)) * 1e-6, info, verbose="ERROR"
    )
    nonconverged_dir = tmp_path / "nonconverged"
    with pytest.raises(MneIcaError, match="FastICA did not converge"):
        fit_ica(
            unstable,
            random_state=0,
            staging_dir=nonconverged_dir,
            source_identity="test-source",
            input_artifact_id="test-input",
            cancel_check=lambda: None,
        )
    unstable.close()
    assert list(nonconverged_dir.iterdir()) == []

    raw = _bad_channel_raw()
    staging = tmp_path / "symlink-output"
    staging.mkdir()
    outside = tmp_path / "outside-diagnostics.json"
    outside.write_text("leave this file alone", encoding="utf-8")
    (staging / "ica-diagnostics.json").symlink_to(outside)
    with pytest.raises(MneIcaError, match="already exists or is unsafe"):
        fit_ica(
            raw,
            random_state=0,
            staging_dir=staging,
            source_identity="test-source",
            input_artifact_id="test-input",
            cancel_check=lambda: None,
        )
    raw.close()
    assert outside.read_text(encoding="utf-8") == "leave this file alone"
    assert not (staging / "ica-model-ica.fif").exists()


def test_ica_provenance_requires_current_schema_and_convergence() -> None:
    valid = {
        "schema_version": "1.1",
        "operation": "fit_ica",
        "input_artifact_id": "raw-artifact",
        "method": "fastica",
        "converged": True,
        "max_iter": 1000,
        "iterations": 999,
        "fitted_components": 2,
        "channels": ["Cz", "Oz"],
        "picks": ["Cz", "Oz"],
        "excluded_bad_channels": ["Pz"],
    }
    assert is_current_converged_fit_provenance(valid, "raw-artifact")
    for field, invalid_value in (
        ("schema_version", "1.0"),
        ("converged", False),
        ("iterations", 0),
        ("iterations", 1000),
        ("iterations", True),
    ):
        invalid = {**valid, field: invalid_value}
        assert not is_current_converged_fit_provenance(invalid, "raw-artifact")


def test_ica_input_sample_limit_counts_bad_channels_before_preload(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    import brainlearn_server.mne_ica as adapter_module

    class LargeRaw:
        ch_names = [f"EEG{index:03}" for index in range(512)]
        n_times = 49_000
        info = {"bads": ch_names[2:]}
        loaded = False

        def copy(self) -> LargeRaw:
            return self

        def pick(self, picks: str) -> LargeRaw:
            assert picks == "eeg"
            return self

        def get_channel_types(self) -> list[str]:
            return ["eeg"] * len(self.ch_names)

        def load_data(self, *, verbose: str) -> LargeRaw:
            self.loaded = True
            raise AssertionError(f"load_data must not run under the sample-limit guard: {verbose}")

        def close(self) -> None:
            return None

    raw = LargeRaw()
    monkeypatch.setattr(adapter_module, "_load_raw", lambda *_args: (raw, None))
    staging = tmp_path / "staging"
    staging.mkdir()
    artifact = ArtifactRecord(
        artifact_id="brainlearn-v1:artifact:" + "a" * 64,
        path="runs/run/artifacts/source/raw.fif",
        byte_size=1,
        sha256="b" * 64,
        produced_by_node="source",
        port_id="raw",
    )
    context = NodeContext(
        node_id="ica-fit",
        node_type="eeg.ica_fit",
        parameters={},
        inputs={},
        staging_dir=staging,
        input_artifacts={"raw": artifact},
    )
    with pytest.raises(ControlledFailure, match="bounded ICA input sample limit"):
        adapter_module.fit_ica_eeg(context)
    assert not raw.loaded


def test_ica_fit_records_only_good_eeg_channels_and_explicit_bad_exclusions(
    tmp_path: Path,
) -> None:
    from brainlearn_mne.ica import fit_ica

    raw = _bad_channel_raw()
    staging = tmp_path / "bad-channel-fit"
    result = fit_ica(
        raw,
        random_state=0,
        staging_dir=staging,
        source_identity="test-source",
        input_artifact_id="test-input",
        cancel_check=lambda: None,
    )
    raw.close()
    provenance = json.loads(result.provenance_path.read_text("utf-8"))
    diagnostics = json.loads(result.diagnostics_path.read_text("utf-8"))
    ica = mne.preprocessing.read_ica(result.model_path, verbose="ERROR")
    assert ica.ch_names == ["Cz", "Oz"]
    assert provenance["channels"] == ["Cz", "Oz"]
    assert provenance["picks"] == ["Cz", "Oz"]
    assert provenance["excluded_bad_channels"] == ["Pz"]
    assert diagnostics["channels"] == ["Cz", "Oz"]
    assert diagnostics["excluded_bad_channels"] == ["Pz"]
    assert provenance["iterations"] == diagnostics["fit_iterations"]
    assert provenance["converged"] is diagnostics["fit_converged"] is True


@pytest.mark.parametrize("work_dir", ["ica-work", "ica-review-work"])
def test_ica_verified_copy_refuses_symlinked_work_directory(tmp_path: Path, work_dir: str) -> None:
    staging = tmp_path / "staging"
    staging.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    sentinel = outside / "sentinel"
    sentinel.write_text("untouched", encoding="utf-8")
    (staging / work_dir).symlink_to(outside, target_is_directory=True)
    artifact = ArtifactRecord(
        artifact_id="brainlearn-v1:artifact:" + "a" * 64,
        path="runs/run/artifacts/source/raw.fif",
        byte_size=1,
        sha256="b" * 64,
        produced_by_node="source",
        port_id="raw",
    )
    context = NodeContext(
        node_id="ica",
        node_type="eeg.ica_fit",
        parameters={},
        inputs={},
        staging_dir=staging,
        input_artifacts={"raw": artifact},
        project_path=str(tmp_path),
        run_store=object(),
        run_id="run",
    )
    with pytest.raises(ControlledFailure, match="unsafe directory"):
        _verified_copy(context, "raw", staging / work_dir / "raw-input_raw.fif")
    assert sentinel.read_text(encoding="utf-8") == "untouched"
    assert list(outside.iterdir()) == [sentinel]


def _direct_mne_reviewed_values(
    dataset: Path,
    scratch: Path,
    excluded: list[int],
    *,
    recording_path: str = RECORDING_RELATIVE,
    high_hz: float = 40.0,
) -> np.ndarray:
    from mne_bids import get_bids_path_from_fname, read_raw_bids

    recording = dataset / recording_path
    bids_path = get_bids_path_from_fname(recording, check=False)
    bids_path.root = dataset
    raw = read_raw_bids(bids_path, verbose="ERROR", extra_params={"preload": False})
    raw.load_data(verbose="ERROR")
    filtered = raw.copy()
    raw.close()
    eeg_names = [
        name
        for name, kind in zip(filtered.ch_names, filtered.get_channel_types(), strict=True)
        if kind == "eeg"
    ]
    filtered.filter(
        l_freq=1.0,
        h_freq=high_hz,
        picks=eeg_names,
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
    intermediate = scratch / "direct-filtered-raw.fif"
    filtered.save(str(intermediate), fmt="single", overwrite=False, verbose="ERROR")
    filtered.close()
    fit_raw = mne.io.read_raw_fif(intermediate, preload=True, verbose="ERROR")
    ica = mne.preprocessing.ICA(
        n_components=None,
        method="fastica",
        random_state=0,
        max_iter=1000,
        fit_params=None,
        allow_ref_meg=False,
        verbose="ERROR",
    )
    ica.fit(
        fit_raw.copy().pick("eeg"),
        picks="eeg",
        reject_by_annotation=False,
        decim=1,
        verbose="ERROR",
    )
    ica.apply(fit_raw, exclude=excluded, verbose="ERROR")
    values = fit_raw.get_data(units="uV")
    fit_raw.close()
    return values


def test_ica_pauses_persists_review_and_matches_direct_mne_and_export(
    client: TestClient, tmp_path: Path
) -> None:
    project = _create_project(client, tmp_path / "project")
    dataset = project / "synthetic" / "example-bids"
    before = _source_snapshot(dataset)
    response = client.post(
        "/api/runs/start",
        json={"path": str(project), "workflow": _ica_workflow().model_dump(mode="json"), "seed": 0},
        headers=AUTH_HEADERS,
    )
    assert response.status_code == 200, response.text
    run_id = response.json()["run_id"]
    parked = _wait_for_state(client, project, run_id, {"waiting_for_review"})
    assert parked["state"] == "waiting_for_review", parked
    by_type = {node["node_type"]: node for node in parked["node_runs"]}
    fit = by_type["eeg.ica_fit"]
    review = by_type["eeg.ica_review"]
    assert fit["state"] == "succeeded"
    assert review["state"] == "waiting_for_review"
    assert {item["port_id"] for item in fit["artifacts"]} == {
        "ica",
        "diagnostics",
        "provenance",
    }
    diagnostics_artifact = next(
        item for item in fit["artifacts"] if item["port_id"] == "diagnostics"
    )
    diagnostics = json.loads((project / diagnostics_artifact["path"]).read_text("utf-8"))
    provenance_artifact = next(item for item in fit["artifacts"] if item["port_id"] == "provenance")
    fit_provenance = json.loads((project / provenance_artifact["path"]).read_text("utf-8"))
    assert diagnostics["component_count"] == 2
    assert len(diagnostics["component_maps"]) == 2
    assert len(diagnostics["time_courses"]) == 2
    assert len(diagnostics["spectra"]) == 2
    assert diagnostics["topography_available"] is False
    assert diagnostics["fit_converged"] is True
    assert diagnostics["fit_iterations"] == fit_provenance["iterations"]
    assert fit_provenance["converged"] is True
    assert fit_provenance["channels"] == diagnostics["channels"]
    assert fit_provenance["picks"] == diagnostics["channels"]
    assert fit_provenance["excluded_bad_channels"] == []
    assert not any(item["port_id"] == "reviewed" for item in review["artifacts"])

    invalid = client.post(
        "/api/runs/review",
        json={
            "path": str(project),
            "run_id": run_id,
            "node_run_id": review["id"],
            "decision": "approved",
            "reviewer": "Researcher A",
            "excluded_ica_components": [2],
        },
        headers=AUTH_HEADERS,
    )
    assert invalid.status_code == 409
    assert "component indices" in invalid.json()["detail"]

    decided = client.post(
        "/api/runs/review",
        json={
            "path": str(project),
            "run_id": run_id,
            "node_run_id": review["id"],
            "decision": "approved",
            "reviewer": "Researcher A",
            "excluded_ica_components": [0],
            "note": "Explicit component decision for the engineering fixture.",
        },
        headers=AUTH_HEADERS,
    )
    assert decided.status_code == 200, decided.text
    assert decided.json()["qc_reviews"][0]["excluded_ica_components"] == [0]
    assert len(decided.json()["qc_reviews"][0]["input_artifact_identities"]) == 2

    finished = _wait_for_state(client, project, run_id, {"succeeded"})
    assert finished["state"] == "succeeded", [
        (node["id"], node["state"], node["failure"])
        for node in finished["node_runs"]
        if node["failure"] is not None
    ]
    review = next(node for node in finished["node_runs"] if node["node_type"] == "eeg.ica_review")
    reviewed_artifact = next(item for item in review["artifacts"] if item["port_id"] == "reviewed")
    brainlearn = mne.io.read_raw_fif(
        project / reviewed_artifact["path"], preload=True, verbose="ERROR"
    )
    brainlearn_values = brainlearn.get_data(units="uV")
    brainlearn.close()

    direct_values = _direct_mne_reviewed_values(dataset, tmp_path, [0])
    np.testing.assert_allclose(
        brainlearn_values,
        direct_values,
        rtol=1e-6,
        atol=MAX_ABSOLUTE_DIFFERENCE_UV,
    )

    rendered = client.post(
        "/api/runs/render/python",
        json={
            "path": str(project),
            "run_id": run_id,
            "node_run_id": review["id"],
            "workflow": _ica_workflow().model_dump(mode="json"),
        },
        headers=AUTH_HEADERS,
    )
    assert rendered.status_code == 200, rendered.text
    payload = rendered.json()
    assert payload["renderer_id"] == "brainlearn.eeg.mne-ica.python"
    assert payload["filename"] == "brainlearn_eeg_ica.py"
    assert "ICA_EXCLUDED_COMPONENTS = [0]" in payload["source"]
    compile(payload["source"], payload["filename"], "exec")
    script = tmp_path / payload["filename"]
    script.write_text(payload["source"], encoding="utf-8")
    generated_path = tmp_path / "generated-reviewed-raw.fif"
    generated = subprocess.run(
        [
            sys.executable,
            str(script),
            "--dataset-root",
            str(dataset),
            "--recording-path",
            RECORDING_RELATIVE,
            "--output",
            str(generated_path),
        ],
        check=False,
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert generated.returncode == 0, generated.stderr
    exported = mne.io.read_raw_fif(generated_path, preload=True, verbose="ERROR")
    exported_values = exported.get_data(units="uV")
    exported.close()
    np.testing.assert_allclose(
        brainlearn_values,
        exported_values,
        rtol=1e-6,
        atol=MAX_ABSOLUTE_DIFFERENCE_UV,
    )
    exported_provenance = json.loads(
        generated_path.with_suffix(".provenance.json").read_text("utf-8")
    )
    assert exported_provenance["ica_review"]["reviewer"] == "Researcher A"
    assert exported_provenance["ica_review"]["excluded_components"] == [0]
    assert exported_provenance["source_content_identity"]
    assert (
        exported_provenance["source_content_identity"]
        == exported_provenance["ica_review"]["source_content_identity"]
    )

    changed_dataset = tmp_path / "changed-bids"
    shutil.copytree(dataset, changed_dataset)
    eeg_file = changed_dataset / RECORDING_RELATIVE.replace(".vhdr", ".eeg")
    changed_bytes = bytearray(eeg_file.read_bytes())
    changed_bytes[0] ^= 1
    eeg_file.write_bytes(changed_bytes)
    changed_output = tmp_path / "changed-source-reviewed.fif"
    changed_run = subprocess.run(
        [
            sys.executable,
            str(script),
            "--dataset-root",
            str(changed_dataset),
            "--recording-path",
            RECORDING_RELATIVE,
            "--output",
            str(changed_output),
        ],
        check=False,
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert changed_run.returncode != 0
    assert "differs from the source approved" in changed_run.stderr
    assert not changed_output.exists()
    assert not changed_output.with_suffix(".provenance.json").exists()
    assert _source_snapshot(dataset) == before


@pytest.mark.skipif(
    not os.environ.get(PUBLIC_FIXTURE_ENV),
    reason="set BRAINLEARN_PUBLIC_BIDS_EEG_FIXTURE to run the pinned public-data comparison",
)
def test_public_bids_eeg_ica_matches_direct_mne_and_generated_export(
    client: TestClient, tmp_path: Path
) -> None:
    fixture_root = Path(os.environ[PUBLIC_FIXTURE_ENV]).expanduser().resolve(strict=True)
    manifest = json.loads(PUBLIC_FIXTURE_MANIFEST.read_text(encoding="utf-8"))
    dataset_relative = "datasets/public-eegeyenet"
    workflow = _ica_workflow(
        dataset_root=dataset_relative,
        recording_path=PUBLIC_RECORDING_RELATIVE,
        high_hz=40.0,
    )
    project = _create_project(
        client,
        tmp_path / "public-eegeyenet-project",
        dataset_source=fixture_root,
        dataset_root=dataset_relative,
        workflow=workflow,
    )
    dataset = project / dataset_relative
    before = _source_snapshot(dataset)
    assert len(before) == len(manifest["fixture"]["files"])

    started = client.post(
        "/api/runs/start",
        json={"path": str(project), "workflow": workflow.model_dump(mode="json"), "seed": 0},
        headers=AUTH_HEADERS,
    )
    assert started.status_code == 200, started.text
    run_id = started.json()["run_id"]
    parked = _wait_for_state(client, project, run_id, {"waiting_for_review"}, timeout=180.0)
    assert parked["state"] == "waiting_for_review", parked
    _wait_for_worker_idle(project, run_id)
    by_type = {node["node_type"]: node for node in parked["node_runs"]}
    fit = by_type["eeg.ica_fit"]
    review = by_type["eeg.ica_review"]
    assert fit["state"] == "succeeded"
    diagnostics_artifact = next(
        item for item in fit["artifacts"] if item["port_id"] == "diagnostics"
    )
    provenance_artifact = next(item for item in fit["artifacts"] if item["port_id"] == "provenance")
    diagnostics = json.loads((project / diagnostics_artifact["path"]).read_text("utf-8"))
    fit_provenance = json.loads((project / provenance_artifact["path"]).read_text("utf-8"))
    assert len(diagnostics["channels"]) == 129
    assert diagnostics["fit_converged"] is True
    assert diagnostics["fit_iterations"] < 1000
    assert fit_provenance["channels"] == diagnostics["channels"]
    assert len(fit_provenance["channels"]) == 129
    assert fit_provenance["converged"] is True
    assert not any(item["port_id"] == "reviewed" for item in review["artifacts"])

    decision = client.post(
        "/api/runs/review",
        json={
            "path": str(project),
            "run_id": run_id,
            "node_run_id": review["id"],
            "decision": "approved",
            "reviewer": "Engineering fixture review",
            "excluded_ica_components": [],
            "note": (
                "Explicit keep-all decision for numerical engineering comparison; "
                "no component interpretation is asserted."
            ),
        },
        headers=AUTH_HEADERS,
    )
    assert decision.status_code == 200, decision.text
    assert decision.json()["qc_reviews"][0]["excluded_ica_components"] == []
    finished = _wait_for_state(client, project, run_id, {"succeeded"}, timeout=180.0)
    assert finished["state"] == "succeeded", [
        (node["id"], node["state"], node["failure"])
        for node in finished["node_runs"]
        if node["failure"] is not None
    ]
    review = next(node for node in finished["node_runs"] if node["node_type"] == "eeg.ica_review")
    reviewed_artifact = next(item for item in review["artifacts"] if item["port_id"] == "reviewed")
    brainlearn = mne.io.read_raw_fif(
        project / reviewed_artifact["path"], preload=True, verbose="ERROR"
    )
    brainlearn_values = brainlearn.get_data(units="uV")
    brainlearn.close()

    direct_output = tmp_path / "public-direct-mne-raw.fif"
    reference_script = Path(__file__).parents[1] / "scripts" / "reference_public_eeg_mne.py"
    direct = subprocess.run(
        [sys.executable, str(reference_script), str(dataset), str(direct_output)],
        check=False,
        capture_output=True,
        text=True,
        timeout=180,
    )
    assert direct.returncode == 0, direct.stderr
    summary_start = direct.stdout.find("{")
    assert summary_start >= 0, direct.stdout
    direct_summary = json.loads(direct.stdout[summary_start:])
    assert direct_summary["method"]["sampling_frequency_hz"] == 500.0
    assert direct_summary["method"]["sample_count"] == 162000
    assert direct_summary["method"]["ica"]["converged"] is True
    assert direct_summary["method"]["ica"]["excluded_components"] == []
    direct_raw = mne.io.read_raw_fif(direct_output, preload=True, verbose="ERROR")
    direct_values = direct_raw.get_data(units="uV")
    direct_raw.close()
    np.testing.assert_allclose(
        brainlearn_values,
        direct_values,
        rtol=1e-6,
        atol=MAX_ABSOLUTE_DIFFERENCE_UV,
    )

    rendered = client.post(
        "/api/runs/render/python",
        json={
            "path": str(project),
            "run_id": run_id,
            "node_run_id": review["id"],
            "workflow": workflow.model_dump(mode="json"),
        },
        headers=AUTH_HEADERS,
    )
    assert rendered.status_code == 200, rendered.text
    payload = rendered.json()
    assert payload["renderer_id"] == "brainlearn.eeg.mne-ica.python"
    assert "ICA_EXCLUDED_COMPONENTS = []" in payload["source"]
    compile(payload["source"], payload["filename"], "exec")
    script = tmp_path / payload["filename"]
    script.write_text(payload["source"], encoding="utf-8")
    generated_path = tmp_path / "public-generated-raw.fif"
    generated = subprocess.run(
        [
            sys.executable,
            str(script),
            "--dataset-root",
            str(dataset),
            "--recording-path",
            PUBLIC_RECORDING_RELATIVE,
            "--output",
            str(generated_path),
        ],
        check=False,
        capture_output=True,
        text=True,
        timeout=180,
    )
    assert generated.returncode == 0, generated.stderr
    exported = mne.io.read_raw_fif(generated_path, preload=True, verbose="ERROR")
    exported_values = exported.get_data(units="uV")
    exported.close()
    np.testing.assert_allclose(
        brainlearn_values,
        exported_values,
        rtol=1e-6,
        atol=MAX_ABSOLUTE_DIFFERENCE_UV,
    )
    exported_provenance = json.loads(
        generated_path.with_suffix(".provenance.json").read_text("utf-8")
    )
    assert exported_provenance["ica_review"]["excluded_components"] == []
    assert exported_provenance["source_content_identity"]
    assert _source_snapshot(dataset) == before


def test_public_mne_reference_refuses_symlinked_output_parent(tmp_path: Path) -> None:
    target = tmp_path / "target"
    target.mkdir()
    linked_parent = tmp_path / "output-link"
    try:
        linked_parent.symlink_to(target, target_is_directory=True)
    except (NotImplementedError, OSError) as exc:
        pytest.skip(f"the platform cannot create a test symlink: {exc}")
    script = Path(__file__).parents[1] / "scripts" / "reference_public_eeg_mne.py"

    result = subprocess.run(
        [
            sys.executable,
            str(script),
            str(tmp_path / "missing-dataset"),
            str(linked_parent / "reviewed.fif"),
        ],
        check=False,
        capture_output=True,
        text=True,
        timeout=20,
    )

    assert result.returncode != 0
    assert "symlinked directory" in result.stderr
    assert list(target.iterdir()) == []


def test_legacy_nonconverged_fit_cannot_be_reviewed_or_applied(
    client: TestClient, tmp_path: Path
) -> None:
    project = _create_project(client, tmp_path / "legacy-ica-project")
    response = client.post(
        "/api/runs/start",
        json={"path": str(project), "workflow": _ica_workflow().model_dump(mode="json"), "seed": 0},
        headers=AUTH_HEADERS,
    )
    assert response.status_code == 200, response.text
    run_id = response.json()["run_id"]
    parked = _wait_for_state(client, project, run_id, {"waiting_for_review"})
    assert parked["state"] == "waiting_for_review", parked
    _wait_for_worker_idle(project, run_id)

    record = _replace_fit_provenance_with_legacy_record(project, run_id)
    review = next(item for item in record.node_runs if item.node_type == "eeg.ica_review")
    decision = client.post(
        "/api/runs/review",
        json={
            "path": str(project),
            "run_id": run_id,
            "node_run_id": review.id,
            "decision": "approved",
            "reviewer": "Researcher A",
            "excluded_ica_components": [0],
        },
        headers=AUTH_HEADERS,
    )
    assert decision.status_code == 409
    assert "legacy, non-converged, or invalid" in decision.json()["detail"]
    assert runs.list_qc_reviews(str(project), run_id) == []

    context = NodeContext(
        node_id=review.node_id,
        node_type=review.node_type,
        parameters=review.parameters,
        inputs={},
        staging_dir=tmp_path / "legacy-ica-apply-staging",
        input_artifacts=workers._resolve_input_artifacts(str(project), record, review),
        project_path=str(project),
        project_store=store,
        run_store=runs,
        run_id=run_id,
        node_run_id=review.id,
        run_seed=record.seed,
    )
    context.staging_dir.mkdir()
    with pytest.raises(ControlledFailure, match="legacy, non-converged, or invalid"):
        apply_reviewed_ica_eeg(context)
    assert runs.list_qc_reviews(str(project), run_id) == []
    review_after = runs.get_run(str(project), run_id).node_runs
    review_after = next(item for item in review_after if item.id == review.id)
    assert not any(item.port_id == "reviewed" for item in review_after.artifacts)


def test_ica_rejection_persists_decision_and_never_emits_reviewed_eeg(
    client: TestClient, tmp_path: Path
) -> None:
    project = _create_project(client, tmp_path / "project")
    started = client.post(
        "/api/runs/start",
        json={"path": str(project), "workflow": _ica_workflow().model_dump(mode="json")},
        headers=AUTH_HEADERS,
    ).json()
    parked = _wait_for_state(client, project, started["run_id"], {"waiting_for_review"})
    review = next(node for node in parked["node_runs"] if node["node_type"] == "eeg.ica_review")
    rejected = client.post(
        "/api/runs/review",
        json={
            "path": str(project),
            "run_id": started["run_id"],
            "node_run_id": review["id"],
            "decision": "rejected",
            "reviewer": "Researcher B",
            "excluded_ica_components": [],
            "note": "Rejected for this workflow run.",
        },
        headers=AUTH_HEADERS,
    )
    assert rejected.status_code == 200, rejected.text
    failed = _wait_for_state(client, project, started["run_id"], {"failed"})
    review = next(node for node in failed["node_runs"] if node["node_type"] == "eeg.ica_review")
    assert review["state"] == "failed"
    assert not any(item["port_id"] == "reviewed" for item in review["artifacts"])
    assert rejected.json()["qc_reviews"][0]["decision"] == "rejected"


@pytest.mark.parametrize("changed_port", ["raw", "fit"])
def test_saved_ica_decision_is_hidden_when_an_input_artifact_changes(
    client: TestClient,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    changed_port: str,
) -> None:
    project = _create_project(client, tmp_path / "project")
    started = client.post(
        "/api/runs/start",
        json={"path": str(project), "workflow": _ica_workflow().model_dump(mode="json")},
        headers=AUTH_HEADERS,
    ).json()
    parked = _wait_for_state(client, project, started["run_id"], {"waiting_for_review"})
    review = next(node for node in parked["node_runs"] if node["node_type"] == "eeg.ica_review")
    monkeypatch.setattr(workers, "_ensure_driver_locked", lambda *_args: None)
    saved = client.post(
        "/api/runs/review",
        json={
            "path": str(project),
            "run_id": started["run_id"],
            "node_run_id": review["id"],
            "decision": "approved",
            "reviewer": "Researcher A",
            "excluded_ica_components": [0],
        },
        headers=AUTH_HEADERS,
    )
    assert saved.status_code == 200, saved.text
    assert len(saved.json()["qc_reviews"]) == 1

    record = runs.get_run(str(project), started["run_id"])
    review_record = next(item for item in record.node_runs if item.id == review["id"])
    source_spec = review_record.settings["input_sources"][changed_port]
    source_node = next(item for item in record.node_runs if item.node_id == source_spec["node"])
    target_artifact = next(
        item for item in source_node.artifacts if item.port_id == source_spec["port"]
    )
    changed_artifact = target_artifact.model_copy(
        update={"artifact_id": "brainlearn-v1:artifact:" + "f" * 64}
    )
    changed_source = source_node.model_copy(
        update={
            "artifacts": [
                changed_artifact if item.port_id == target_artifact.port_id else item
                for item in source_node.artifacts
            ]
        }
    )
    changed_run = record.model_copy(
        update={
            "node_runs": [
                changed_source if item.id == source_node.id else item for item in record.node_runs
            ]
        }
    )
    runs.save_run(
        str(project),
        record.__class__.model_validate(changed_run.model_dump(mode="json")),
    )
    reopened = client.post(
        "/api/runs/open",
        json={"path": str(project), "run_id": started["run_id"]},
        headers=AUTH_HEADERS,
    )
    assert reopened.status_code == 200, reopened.text
    assert reopened.json()["qc_reviews"] == []
    assert len(list((project / "runs" / started["run_id"] / "qc-reviews").glob("*.json"))) == 1


def test_ica_python_renderer_requires_approved_review_for_its_graph() -> None:
    workflow = _ica_workflow()
    review_node = next(node for node in workflow.nodes if node.type == "eeg.ica_review")
    review = QCReviewRecord(
        id="review-ica-1",
        run_id="run-1",
        node_run_id="ica-review",
        node_id=review_node.id,
        node_content_identity="brainlearn-v1:node:" + "a" * 64,
        input_identities=(
            QCReviewInputIdentity(port_id="raw", content_identity="brainlearn-v1:node:" + "b" * 64),
            QCReviewInputIdentity(port_id="fit", content_identity="brainlearn-v1:node:" + "c" * 64),
        ),
        input_artifact_identities=(
            QCReviewArtifactIdentity(
                port_id="raw", artifact_id="brainlearn-v1:artifact:" + "1" * 64
            ),
            QCReviewArtifactIdentity(
                port_id="fit", artifact_id="brainlearn-v1:artifact:" + "2" * 64
            ),
        ),
        reviewer="Researcher A",
        decision="approved",
        excluded_ica_components=(0,),
        note="Keep the reviewed choice with the exported code.",
        created_at="2026-10-19T00:00:00+00:00",
    )
    source_files = [
        {
            "path": RECORDING_RELATIVE,
            "byte_size": 1,
            "sha256": "a" * 64,
        }
    ]
    source_identity = content_identity(
        "artifact",
        {
            "schema_version": "bids-eeg-input-1.1",
            "recording_path": RECORDING_RELATIVE,
            "files": source_files,
        },
    )
    rendered = render_eeg_ica_python(
        workflow,
        review,
        seed=0,
        filter_provenance={
            "operation": "mne.raw.filter",
            "recording_path": RECORDING_RELATIVE,
            "source_content_identity": source_identity,
            "source_files": source_files,
        },
        fit_provenance={
            "schema_version": "1.1",
            "operation": "fit_ica",
            "input_artifact_id": "brainlearn-v1:artifact:" + "1" * 64,
            "method": "fastica",
            "converged": True,
            "fitted_components": 2,
            "random_state": 0,
            "max_iter": 1000,
            "reject_by_annotation": False,
            "decim": 1,
            "channels": ["Cz", "Pz"],
            "picks": ["Cz", "Pz"],
            "excluded_bad_channels": [],
            "iterations": 17,
        },
    )
    assert rendered.renderer_id == "brainlearn.eeg.mne-ica.python"
    assert rendered.renderer_version == "0.2.0"
    assert "ICA_REVIEW_JSON" in rendered.source
    assert "ica.apply(filtered, exclude=ICA_EXCLUDED_COMPONENTS" in rendered.source
    assert "current_source_identity != ICA_SOURCE_CONTENT_IDENTITY" in rendered.source
    assert "FastICA did not converge within 1000 iterations" in rendered.source
    compile(rendered.source, rendered.filename, "exec")
