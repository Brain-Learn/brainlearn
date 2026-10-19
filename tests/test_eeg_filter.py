"""Step 6: executable MNE filter, bounded comparison, and Python export."""

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
from brainlearn_core import CanvasPosition, Workflow, migrate_workflow_dict
from brainlearn_server import project_store as store_module
from brainlearn_server.app import app, store
from brainlearn_server.auth import reset_session_token_for_tests
from brainlearn_server.bids_eeg import BidsEegDiscoveryService
from brainlearn_server.demo_nodes import ControlledFailure, NodeContext, eeg_filter_adapter
from brainlearn_server.python_export import CodeRenderError, render_eeg_filter_python
from brainlearn_server.registry import instantiate_registered_node
from fastapi.testclient import TestClient

FIXTURE = Path(__file__).parents[1] / "examples" / "synthetic-eeg-bids"
FIXTURE_GENERATOR = Path(__file__).parents[1] / "examples" / "create_synthetic_eeg_bids.py"
FIXTURE_MANIFEST = Path(__file__).parents[1] / "examples" / "synthetic-eeg-bids-manifest.json"
EXAMPLE = Path(__file__).parents[1] / "examples" / "eeg-filter-demo.workflow.json"
TEST_TOKEN = "step6-filter-test-token-0123456789"
AUTH_HEADERS = {"Authorization": f"Bearer {TEST_TOKEN}"}
RECORDING_RELATIVE = "sub-01/eeg/sub-01_task-Demo_eeg.vhdr"
MAX_ABSOLUTE_DIFFERENCE_UV = 2e-5


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


def _workflow() -> Workflow:
    return Workflow.model_validate(
        migrate_workflow_dict(json.loads(EXAMPLE.read_text(encoding="utf-8")))
    )


def _set_filter_parameters(workflow: Workflow, **values: object) -> None:
    node = next(item for item in workflow.nodes if item.type == "eeg.filter")
    parameters = {parameter.id: parameter for parameter in node.parameters}
    for name, value in values.items():
        parameters[name].value = value


def _create_project(client: TestClient, path: Path) -> Path:
    response = client.post(
        "/api/projects/create",
        json={
            "path": str(path),
            "name": "EEG filter test",
            "workflow": _workflow().model_dump(mode="json"),
        },
        headers=AUTH_HEADERS,
    )
    assert response.status_code == 200, response.text
    dataset = path / "synthetic" / "example-bids"
    shutil.copytree(FIXTURE, dataset)
    return path


def _source_snapshot(root: Path) -> dict[str, tuple[int, str]]:
    return {
        item.relative_to(root).as_posix(): (
            item.stat().st_size,
            hashlib.sha256(item.read_bytes()).hexdigest(),
        )
        for item in sorted(root.rglob("*"))
        if item.is_file()
    }


def test_synthetic_bids_fixture_generator_is_deterministic(tmp_path: Path) -> None:
    generated = tmp_path / "synthetic-bids"
    result = subprocess.run(
        [sys.executable, str(FIXTURE_GENERATOR), str(generated)],
        check=False,
        capture_output=True,
        text=True,
        timeout=20,
    )

    assert result.returncode == 0, result.stderr
    assert _source_snapshot(generated) == _source_snapshot(FIXTURE)
    manifest = json.loads(FIXTURE_MANIFEST.read_text(encoding="utf-8"))
    assert manifest["license"] == "CC0-1.0"
    assert (
        manifest["generator_sha256"] == hashlib.sha256(FIXTURE_GENERATOR.read_bytes()).hexdigest()
    )
    expected_files = manifest["files"]
    assert set(expected_files) == set(_source_snapshot(FIXTURE))
    assert {
        relative_path: digest for relative_path, (_, digest) in _source_snapshot(FIXTURE).items()
    } == expected_files


def _filter_context(
    project: Path,
    tmp_path: Path,
    *,
    low_hz: object = 1.0,
    high_hz: object = 40.0,
    notch_hz: object = "",
    notch_width_hz: object = 1.0,
    staging_name: str = "staging",
) -> tuple[NodeContext, Path]:
    dataset_relative = "synthetic/example-bids"
    identity = BidsEegDiscoveryService(store).identify(
        str(project), dataset_relative, RECORDING_RELATIVE
    )
    assert identity.status == "ready" and identity.content_identity is not None
    reference = tmp_path / "recording-reference.json"
    reference.write_text(
        json.dumps(
            {
                "schema_version": "1.0",
                "dataset_path": dataset_relative,
                "recording_path": RECORDING_RELATIVE,
                "source_content_identity": identity.content_identity,
            }
        ),
        encoding="utf-8",
    )
    context = NodeContext(
        node_id="filter",
        node_type="eeg.filter",
        parameters={
            "low_hz": low_hz,
            "high_hz": high_hz,
            "notch_hz": notch_hz,
            "notch_width_hz": notch_width_hz,
        },
        inputs={"raw": reference},
        staging_dir=tmp_path / staging_name,
        project_path=str(project),
        project_store=store,
    )
    return context, project / dataset_relative


def test_adapter_direct_mne_and_generated_script_agree_and_preserve_source(
    client: TestClient, tmp_path: Path
) -> None:
    project = _create_project(client, tmp_path / "project")
    context, dataset = _filter_context(
        project, tmp_path, low_hz=1.0, high_hz=70.0, notch_hz="50", notch_width_hz=2.0
    )
    before_files = _source_snapshot(dataset)

    staged = eeg_filter_adapter(context)
    by_port = {item.port_id: item for item in staged}
    assert set(by_port) == {"filtered", "preview", "provenance"}
    adapter_path = context.staging_dir / by_port["filtered"].relative_path
    adapter_raw = mne.io.read_raw_fif(adapter_path, preload=True, verbose="ERROR")
    adapter_values = adapter_raw.get_data(units="uV")
    adapter_channels = adapter_raw.ch_names.copy()
    adapter_raw.close()

    preview = json.loads((context.staging_dir / by_port["preview"].relative_path).read_text())
    assert preview["schema_version"] == "1.0"
    assert preview["channels"] == ["Cz", "Pz"]
    assert preview["before"]["traces"] and preview["after"]["traces"]
    assert preview["before"]["spectrum_traces"] and preview["after"]["spectrum_traces"]
    assert len(preview["before"]["traces"][0]["bins"]) <= 240
    assert len(preview["before"]["frequencies_hz"]) <= 513
    assert preview["before"]["spectrum_remove_dc"] is True

    provenance = json.loads((context.staging_dir / by_port["provenance"].relative_path).read_text())
    assert provenance["parameters"] == {
        "high_hz": 70.0,
        "low_hz": 1.0,
        "notch_frequencies_hz": [50.0],
        "notch_width_hz": 2.0,
        "unit": "Hz",
    }
    assert provenance["settings"]["phase"] == "zero"
    assert provenance["settings"]["fir_design"] == "firwin"
    assert provenance["software"] == {"mne": "1.13.2", "mne-bids": "0.20.0"}
    assert provenance["plugin_version"] == "0.2.0"
    assert provenance["citations"]
    assert {
        "dataset_description.json",
        "participants.tsv",
        "sub-01/eeg/sub-01_task-Demo_events.json",
    } <= {item["path"] for item in provenance["source_files"]}

    service = BidsEegDiscoveryService(store)
    root, recording_relative, recording = service._resolve_signal_recording(
        str(project), "synthetic/example-bids", RECORDING_RELATIVE
    )
    direct = service._open_signal_raw(root, recording_relative, recording.format or "EEG")
    direct.load_data(verbose="ERROR")
    direct.filter(
        l_freq=1.0,
        h_freq=70.0,
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
    direct.notch_filter(
        freqs=[50.0],
        picks=["Cz", "Pz"],
        filter_length="auto",
        notch_widths=2.0,
        trans_bandwidth=1.0,
        method="fir",
        phase="zero",
        fir_window="hamming",
        fir_design="firwin",
        skip_by_annotation=("edge", "bad_acq_skip"),
        pad="reflect_limited",
        verbose="ERROR",
    )
    direct_values = direct.get_data(units="uV")
    direct.close()
    np.testing.assert_allclose(
        adapter_values,
        direct_values,
        rtol=1e-6,
        atol=MAX_ABSOLUTE_DIFFERENCE_UV,
    )

    workflow = _workflow()
    _set_filter_parameters(workflow, low_hz=1.0, high_hz=70.0, notch_hz="50", notch_width_hz=2.0)
    rendered = render_eeg_filter_python(workflow)
    assert rendered.schema_version == "1.0"
    assert rendered.renderer_version == "1.2.1"
    assert "NOTCH_HZ = [50.0]" in rendered.source
    assert "filtered.notch_filter(" in rendered.source
    assert rendered.dependencies == ["mne==1.13.2", "mne-bids==0.20.0"]
    compile(rendered.source, rendered.filename, "exec")
    script_path = tmp_path / rendered.filename
    script_path.write_text(rendered.source, encoding="utf-8")
    generated_path = tmp_path / "generated-filtered-raw.fif"
    script_command = [
        sys.executable,
        str(script_path),
        "--dataset-root",
        str(dataset),
        "--recording-path",
        RECORDING_RELATIVE,
        "--output",
        str(generated_path),
    ]
    completed = subprocess.run(
        script_command,
        check=False,
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert completed.returncode == 0, completed.stderr
    generated = mne.io.read_raw_fif(generated_path, preload=True, verbose="ERROR")
    generated_values = generated.get_data(units="uV")
    assert generated.ch_names == adapter_channels
    generated.close()
    generated_provenance = json.loads(
        generated_path.with_suffix(".provenance.json").read_text(encoding="utf-8")
    )
    assert {
        "dataset_description.json",
        "participants.tsv",
        "sub-01/eeg/sub-01_task-Demo_events.json",
    } <= set(generated_provenance["source_file_sha256"])
    np.testing.assert_allclose(
        adapter_values,
        generated_values,
        rtol=1e-6,
        atol=MAX_ABSOLUTE_DIFFERENCE_UV,
    )

    baseline_context, _ = _filter_context(
        project,
        tmp_path,
        low_hz=1.0,
        high_hz=70.0,
        staging_name="baseline-staging",
    )
    baseline_artifacts = eeg_filter_adapter(baseline_context)
    baseline_filtered = next(item for item in baseline_artifacts if item.port_id == "filtered")
    baseline_raw = mne.io.read_raw_fif(
        baseline_context.staging_dir / baseline_filtered.relative_path,
        preload=True,
        verbose="ERROR",
    )
    baseline_values = baseline_raw.get_data(units="uV")
    baseline_raw.close()
    assert not np.allclose(adapter_values, baseline_values, rtol=1e-6, atol=1e-3)
    assert _source_snapshot(dataset) == before_files
    provenance_path = generated_path.with_suffix(".provenance.json")
    assert provenance_path.is_file()
    output_hashes = {
        generated_path: hashlib.sha256(generated_path.read_bytes()).hexdigest(),
        provenance_path: hashlib.sha256(provenance_path.read_bytes()).hexdigest(),
    }
    refused = subprocess.run(
        script_command, check=False, capture_output=True, text=True, timeout=60
    )
    assert refused.returncode != 0 and "Output already exists" in refused.stderr
    assert {
        path: hashlib.sha256(path.read_bytes()).hexdigest() for path in output_hashes
    } == output_hashes
    overwritten = subprocess.run(
        [*script_command, "--overwrite"],
        check=False,
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert overwritten.returncode == 0, overwritten.stderr
    assert not list(tmp_path.glob(".*.backup"))


@pytest.mark.parametrize(
    ("low_hz", "high_hz", "message"),
    [
        (0.0, 40.0, "between 0.1 and 100 Hz"),
        (1.0, 250.0, "between 0.5 and 200 Hz"),
        (40.0, 40.0, "lower than the high cutoff"),
        (float("nan"), 40.0, "must be finite"),
        (1.0, float("inf"), "must be finite"),
        (1.0, 130.0, "Nyquist frequency"),
    ],
)
def test_filter_rejects_out_of_range_parameters(
    client: TestClient,
    tmp_path: Path,
    low_hz: object,
    high_hz: object,
    message: str,
) -> None:
    project = _create_project(client, tmp_path / "project")
    context, _ = _filter_context(project, tmp_path, low_hz=low_hz, high_hz=high_hz)

    with pytest.raises(ControlledFailure, match=message):
        eeg_filter_adapter(context)


@pytest.mark.parametrize(
    ("notch_hz", "notch_width_hz", "high_hz", "message"),
    [
        ("50,50", 1.0, 70.0, "unique and listed in ascending order"),
        ("51,50", 1.0, 70.0, "unique and listed in ascending order"),
        ("NaN", 1.0, 70.0, "finite numbers"),
        ("50", 1.0, 40.0, "inside the configured pass band"),
        ("50", 11.0, 70.0, "Notch width must be between"),
        ("1,2,3,4,5,6,7,8,9,10,11,12,13,14,15,16,17", 1.0, 70.0, "up to 16"),
    ],
)
def test_filter_rejects_invalid_notch_parameters(
    client: TestClient,
    tmp_path: Path,
    notch_hz: str,
    notch_width_hz: float,
    high_hz: float,
    message: str,
) -> None:
    project = _create_project(client, tmp_path / "project")
    context, _ = _filter_context(
        project,
        tmp_path,
        high_hz=high_hz,
        notch_hz=notch_hz,
        notch_width_hz=notch_width_hz,
    )

    with pytest.raises(ControlledFailure, match=message):
        eeg_filter_adapter(context)


def test_filter_refuses_source_changed_since_inspection(client: TestClient, tmp_path: Path) -> None:
    project = _create_project(client, tmp_path / "project")
    context, dataset = _filter_context(project, tmp_path)
    source = dataset / RECORDING_RELATIVE
    source.write_bytes(source.read_bytes() + b"changed")

    with pytest.raises(ControlledFailure, match="changed after inspection"):
        eeg_filter_adapter(context)


def test_filter_refuses_participant_metadata_changed_since_inspection(
    client: TestClient, tmp_path: Path
) -> None:
    project = _create_project(client, tmp_path / "project")
    context, dataset = _filter_context(project, tmp_path)
    participants = dataset / "participants.tsv"
    participants.write_text("participant_id\nsub-01\n# changed\n", encoding="utf-8")

    with pytest.raises(ControlledFailure, match="changed after inspection"):
        eeg_filter_adapter(context)


def test_generated_script_refuses_participant_metadata_changed_after_mne_read(
    client: TestClient, tmp_path: Path
) -> None:
    project = _create_project(client, tmp_path / "project")
    dataset = project / "synthetic" / "example-bids"
    script_path = tmp_path / "generated-filter.py"
    script_path.write_text(render_eeg_filter_python(_workflow()).source, encoding="utf-8")
    patch_directory = tmp_path / "python-patch"
    patch_directory.mkdir()
    (patch_directory / "sitecustomize.py").write_text(
        "import os\n"
        "import mne_bids\n"
        "_read_raw_bids = mne_bids.read_raw_bids\n"
        "def _read_then_mutate(*args, **kwargs):\n"
        "    raw = _read_raw_bids(*args, **kwargs)\n"
        "    with open(os.environ['BRAINLEARN_MUTATE_PARTICIPANTS'], 'a', encoding='utf-8') as f:\n"
        "        f.write('# changed after MNE-BIDS read\\n')\n"
        "    return raw\n"
        "mne_bids.read_raw_bids = _read_then_mutate\n",
        encoding="utf-8",
    )
    output_path = tmp_path / "mutated-filtered-raw.fif"
    environment = os.environ.copy()
    environment["BRAINLEARN_MUTATE_PARTICIPANTS"] = str(dataset / "participants.tsv")
    environment["PYTHONPATH"] = os.pathsep.join(
        filter(None, [str(patch_directory), environment.get("PYTHONPATH", "")])
    )
    completed = subprocess.run(
        [
            sys.executable,
            str(script_path),
            "--dataset-root",
            str(dataset),
            "--recording-path",
            RECORDING_RELATIVE,
            "--output",
            str(output_path),
        ],
        check=False,
        capture_output=True,
        text=True,
        timeout=60,
        env=environment,
    )

    assert completed.returncode != 0
    assert "Source files changed during filtering" in completed.stderr
    assert not output_path.exists()
    assert not output_path.with_suffix(".provenance.json").exists()


@pytest.mark.parametrize("race_target", ["output", "provenance"])
def test_generated_script_preserves_destination_created_during_filtering(
    client: TestClient, tmp_path: Path, race_target: str
) -> None:
    project = _create_project(client, tmp_path / "project")
    dataset = project / "synthetic" / "example-bids"
    script_path = tmp_path / "generated-filter.py"
    script_path.write_text(render_eeg_filter_python(_workflow()).source, encoding="utf-8")
    patch_directory = tmp_path / "python-patch"
    patch_directory.mkdir()
    (patch_directory / "sitecustomize.py").write_text(
        "import os\n"
        "from pathlib import Path\n"
        "import mne\n"
        "_save = mne.io.BaseRaw.save\n"
        "def _save_then_create_destination(self, fname, *args, **kwargs):\n"
        "    result = _save(self, fname, *args, **kwargs)\n"
        "    Path(os.environ['BRAINLEARN_RACE_PATH']).write_bytes(b'race sentinel')\n"
        "    return result\n"
        "mne.io.BaseRaw.save = _save_then_create_destination\n",
        encoding="utf-8",
    )
    output_path = tmp_path / "raced-filtered-raw.fif"
    provenance_path = output_path.with_suffix(".provenance.json")
    raced_path = output_path if race_target == "output" else provenance_path
    environment = os.environ.copy()
    environment["BRAINLEARN_RACE_PATH"] = str(raced_path)
    environment["PYTHONPATH"] = os.pathsep.join(
        filter(None, [str(patch_directory), environment.get("PYTHONPATH", "")])
    )

    completed = subprocess.run(
        [
            sys.executable,
            str(script_path),
            "--dataset-root",
            str(dataset),
            "--recording-path",
            RECORDING_RELATIVE,
            "--output",
            str(output_path),
        ],
        check=False,
        capture_output=True,
        text=True,
        timeout=60,
        env=environment,
    )

    assert completed.returncode != 0
    assert "An output appeared during filtering" in completed.stderr
    assert raced_path.read_bytes() == b"race sentinel"
    other_path = provenance_path if raced_path == output_path else output_path
    assert not other_path.exists()
    assert not list(tmp_path.glob(".brainlearn-filter-*"))
    assert not list(tmp_path.glob(".brainlearn-provenance-*"))
    assert not list(tmp_path.glob(".*.backup"))


def test_python_renderer_uses_current_parameters_and_escapes_literals() -> None:
    workflow = _workflow()
    workflow.nodes[0].parameters[0].value = "synthetic/quoted'\\dataset"
    workflow.nodes[1].parameters[0].value = "sub-01/eeg/quoted'\\file.vhdr"
    workflow.nodes[2].parameters[0].value = 2.5
    rendered = render_eeg_filter_python(workflow)

    assert "LOW_HZ = 2.5" in rendered.source
    assert json.dumps("synthetic/quoted'\\dataset") in rendered.source
    assert json.dumps("sub-01/eeg/quoted'\\file.vhdr") in rendered.source
    compile(rendered.source, rendered.filename, "exec")


@pytest.mark.parametrize(
    ("notch_hz", "low_hz", "high_hz", "message"),
    [
        ("50,50", 1.0, 70.0, "unique and listed in ascending order"),
        ("51,50", 1.0, 70.0, "unique and listed in ascending order"),
        ("NaN", 1.0, 70.0, "finite numbers"),
        ("50", 1.0, 40.0, "inside the configured pass band"),
        ("50", 1.0, 70.0, "Notch width must"),
    ],
)
def test_python_renderer_validates_explicit_notch_parameters(
    notch_hz: str, low_hz: float, high_hz: float, message: str
) -> None:
    workflow = _workflow()
    _set_filter_parameters(
        workflow,
        low_hz=low_hz,
        high_hz=high_hz,
        notch_hz=notch_hz,
        notch_width_hz=11.0 if message == "Notch width must" else 1.0,
    )

    with pytest.raises(CodeRenderError, match=message):
        render_eeg_filter_python(workflow)


def test_python_renderer_rejects_unsupported_graph() -> None:
    workflow = _workflow()
    extra = instantiate_registered_node("eeg.ica_review", "ica", CanvasPosition(x=900, y=180))
    workflow.nodes.append(extra)

    with pytest.raises(CodeRenderError, match="supports one BIDS EEG input"):
        render_eeg_filter_python(workflow)


def test_filter_node_runs_through_local_worker_with_three_artifacts(
    client: TestClient, tmp_path: Path
) -> None:
    project = _create_project(client, tmp_path / "project")
    workflow = json.loads(EXAMPLE.read_text(encoding="utf-8"))
    for node in workflow["nodes"]:
        if node["id"] == "inspect":
            node["parameters"][0]["value"] = RECORDING_RELATIVE
    started = client.post(
        "/api/runs/start",
        json={"path": str(project), "workflow": workflow, "seed": 0},
        headers=AUTH_HEADERS,
    )
    assert started.status_code == 200, started.text
    run_id = started.json()["run_id"]
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        response = client.post(
            "/api/runs/open",
            json={"path": str(project), "run_id": run_id},
            headers=AUTH_HEADERS,
        )
        assert response.status_code == 200, response.text
        run = response.json()["run"]
        if run["state"] in {"succeeded", "failed", "cancelled"}:
            break
        time.sleep(0.05)
    assert run["state"] == "succeeded", run
    by_node = {node["node_id"]: node for node in run["node_runs"]}
    filtered = by_node["filter"]
    assert {artifact["port_id"] for artifact in filtered["artifacts"]} == {
        "filtered",
        "preview",
        "provenance",
    }
    assert not (project / "cache").exists()


def test_python_export_route_requires_auth_and_returns_versioned_code(client: TestClient) -> None:
    workflow = json.loads(EXAMPLE.read_text(encoding="utf-8"))
    denied = client.post("/api/workflows/render/python", json=workflow)
    response = client.post("/api/workflows/render/python", json=workflow, headers=AUTH_HEADERS)

    assert denied.status_code in {401, 403}
    assert response.status_code == 200, response.text
    payload: dict[str, Any] = response.json()
    assert payload["schema_version"] == "1.0"
    assert payload["renderer_id"] == "brainlearn.eeg.mne-filter.python"
    assert payload["renderer_version"] == "1.2.1"
    assert payload["filename"] == "brainlearn_eeg_filter.py"
    assert "filtered.filter(" in payload["source"]
