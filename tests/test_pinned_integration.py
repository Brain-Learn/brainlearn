"""Step 5A.7: Deterministic mock-provider coverage and pinned integration smoke.

Ordinary CI remains 100% offline. Every test in this suite except the
opt-in `test_live_pinned_integration_download_smoke` runs with mock doubles
and zero network sockets.
"""

from __future__ import annotations

import json
import os
import urllib.error
from pathlib import Path

import pytest
from brainlearn_core import (
    CatalogEntry,
    DatasetLock,
    DatasetSearch,
    DriftCategory,
    MockDatasetProvider,
    PinnedDatasetManifest,
    ProviderError,
    ProviderMalformed,
    ProviderNotFound,
    ProviderTimeout,
    ScriptedDownloadSource,
    UpstreamDriftError,
    VerifiedFile,
    check_catalog_entry_drift,
    check_lock_drift,
    classify_upstream_exception,
    get_pinned_integration_manifest,
)
from brainlearn_core.datasets import project_lock_from_catalog
from brainlearn_core.openneuro import map_openneuro_snapshot_to_catalog
from brainlearn_server.integration_smoke import run_integration_smoke

FIXTURES = Path(__file__).parent / "fixtures"
LIVE_SMOKE_ENV_VAR = "BRAINLEARN_INTEGRATION_SMOKE"
needs_live_smoke = pytest.mark.skipif(
    not os.environ.get(LIVE_SMOKE_ENV_VAR),
    reason=f"Run only when {LIVE_SMOKE_ENV_VAR}=1; ordinary CI stays offline.",
)


def _make_sample_entry(dataset_id: str = "ds001037", snapshot: str = "00001") -> CatalogEntry:
    raw_snapshot = {
        "id": f"{dataset_id}:{snapshot}",
        "tag": snapshot,
        "created": "2018-07-14T03:16:56.000Z",
        "hexsha": "f996ba587ae398f93ba24c34ece68d686ec66d0b",
        "size": 8600815,
        "dataset": {"id": dataset_id, "public": True, "name": "The brain of Chris"},
        "description": {
            "Name": "The brain of Chris",
            "DatasetDOI": None,
            "License": None,
            "Authors": ["Chris Gorgolewski"],
            "ReferencesAndLinks": None,
            "BIDSVersion": "1.0.2",
        },
        "summary": {
            "modalities": ["mri"],
            "primaryModality": "mri",
            "tasks": [],
            "size": 8600815,
            "totalFiles": 2,
            "subjects": ["CG"],
        },
        "files": [
            {"filename": "dataset_description.json", "size": 83, "directory": False},
            {"filename": ".gitattributes", "size": 284, "directory": False},
        ],
    }
    return map_openneuro_snapshot_to_catalog(raw_snapshot)


def _make_sample_lock(manifest: PinnedDatasetManifest) -> DatasetLock:
    entry = _make_sample_entry()
    return project_lock_from_catalog(
        entry,
        local_path="datasets/openneuro/ds001037/00001",
        retrieved_at="2026-09-17T12:00:00Z",
        expected_files=[
            {"path": f.path, "byte_size": f.byte_size, "sha256": f.sha256}
            for f in manifest.expected_files
        ],
    )


def test_mock_provider_queued_failures() -> None:
    """MockDatasetProvider must support queued failures that recover on retry."""
    import asyncio

    async def _run() -> None:
        entry = _make_sample_entry()
        failures = [
            ProviderTimeout("Simulated timeout"),
            ProviderError("Simulated transport error"),
        ]
        provider = MockDatasetProvider(
            entries=[entry],
            provider_name="openneuro",
            failures=failures,
        )

        assert provider.failures_remaining() == 2

        # First attempt: timeout
        with pytest.raises(ProviderTimeout, match="Simulated timeout"):
            await provider.resolve_snapshot("ds001037", "00001")
        assert provider.failures_remaining() == 1

        # Second attempt: transport error
        with pytest.raises(ProviderError, match="Simulated transport error"):
            await provider.list_datasets(DatasetSearch(first=10))
        assert provider.failures_remaining() == 0

        # Third attempt: succeeds normally
        resolved = await provider.resolve_snapshot("ds001037", "00001")
        assert resolved.dataset_id == "ds001037"

        page = await provider.list_datasets(DatasetSearch(first=10))
        assert len(page.items) == 1
        assert page.items[0].dataset_id == "ds001037"

    asyncio.run(_run())


def test_drift_classification_availability() -> None:
    """Availability errors must map to DriftCategory.AVAILABILITY."""
    manifest = get_pinned_integration_manifest()

    # Timeout
    diag = classify_upstream_exception(ProviderTimeout("Gateway timeout"), manifest)
    assert diag.category == DriftCategory.AVAILABILITY
    assert diag.code == "timeout"
    assert "timed out" in diag.message

    # Not found
    diag = classify_upstream_exception(ProviderNotFound("No such snapshot"), manifest)
    assert diag.category == DriftCategory.AVAILABILITY
    assert diag.code == "not_found"

    # HTTP 502 / 503
    http_503 = urllib.error.HTTPError(
        "https://openneuro.org/crn", 503, "Service Unavailable", {}, None
    )  # type: ignore[arg-type]
    diag = classify_upstream_exception(http_503, manifest)
    assert diag.category == DriftCategory.AVAILABILITY
    assert diag.code == "server_error"

    # Connection error
    diag = classify_upstream_exception(ConnectionResetError("Connection dropped"), manifest)
    assert diag.category == DriftCategory.AVAILABILITY
    assert diag.code == "connection_failed"


def test_drift_classification_schema() -> None:
    """Schema violations must map to DriftCategory.SCHEMA."""
    manifest = get_pinned_integration_manifest()

    # Malformed payload
    diag = classify_upstream_exception(ProviderMalformed("Missing files array"), manifest)
    assert diag.category == DriftCategory.SCHEMA
    assert diag.code == "malformed_payload"

    # KeyError
    diag = classify_upstream_exception(KeyError("latestSnapshot"), manifest)
    assert diag.category == DriftCategory.SCHEMA
    assert diag.code == "malformed_payload"


def test_drift_classification_identity() -> None:
    """Identity divergence must fail closed as DriftCategory.IDENTITY."""
    manifest = get_pinned_integration_manifest()
    entry = _make_sample_entry()

    # Catalog identity mismatch
    altered_entry = entry.model_copy(
        update={"catalog_identity": "brainlearn-v1:dataset:" + "0" * 64}
    )
    with pytest.raises(UpstreamDriftError) as exc_info:
        check_catalog_entry_drift(manifest, altered_entry)
    diag = exc_info.value.diagnostic
    assert diag.category == DriftCategory.IDENTITY
    assert diag.code == "catalog_identity_mismatch"
    assert "diverged" in diag.message

    valid_lock = _make_sample_lock(manifest)
    altered_lock = valid_lock.model_copy(
        update={"dataset_identity": "brainlearn-v1:dataset:" + "1" * 64}
    )
    with pytest.raises(UpstreamDriftError) as exc_info:
        check_lock_drift(manifest, altered_lock)
    assert exc_info.value.diagnostic.category == DriftCategory.IDENTITY
    assert exc_info.value.diagnostic.code == "lock_identity_mismatch"


def test_drift_classification_checksum() -> None:
    """Checksum divergence must fail closed as DriftCategory.CHECKSUM."""
    manifest = get_pinned_integration_manifest()
    valid_lock = _make_sample_lock(manifest)

    # Altered sha256
    corrupted_lock = valid_lock.model_copy(
        update={
            "expected_files": (
                VerifiedFile(
                    path=".gitattributes",
                    byte_size=284,
                    sha256="0000000000000000000000000000000000000000000000000000000000000000",
                ),
                valid_lock.expected_files[1],
            )
        }
    )

    with pytest.raises(UpstreamDriftError) as exc_info:
        check_lock_drift(manifest, corrupted_lock)
    diag = exc_info.value.diagnostic
    assert diag.category == DriftCategory.CHECKSUM
    assert diag.code == "sha256_mismatch"
    assert "sha256 diverged" in diag.message

    # Altered byte size
    corrupted_size_lock = valid_lock.model_copy(
        update={
            "expected_files": (
                VerifiedFile(
                    path=".gitattributes",
                    byte_size=999,
                    sha256=valid_lock.expected_files[0].sha256,
                ),
                valid_lock.expected_files[1],
            )
        }
    )

    with pytest.raises(UpstreamDriftError) as exc_info:
        check_lock_drift(manifest, corrupted_size_lock)
    diag = exc_info.value.diagnostic
    assert diag.category == DriftCategory.CHECKSUM
    assert diag.code == "byte_size_mismatch"
    assert "byte size diverged" in diag.message


def test_deterministic_mock_smoke_flow_and_offline_reopening(tmp_path: Path) -> None:
    """Run the smoke runner using deterministic doubles with zero network calls."""
    manifest = get_pinned_integration_manifest()
    entry = _make_sample_entry()

    gitattributes_bytes = (
        b"* annex.backend=MD5E\n"
        b"**/.git* annex.largefiles=nothing\n"
        b"*.tsv annex.largefiles=nothing\n"
        b"*.json annex.largefiles=nothing\n"
        b"*.bvec annex.largefiles=nothing\n"
        b"*.bval annex.largefiles=nothing\n"
        b"README annex.largefiles=nothing\n"
        b"CHANGES annex.largefiles=nothing\n"
        b".bidsignore annex.largefiles=nothing\n"
    )
    dataset_description_bytes = (
        b'{"Name":"The brain of Chris","BIDSVersion":"1.0.2","Authors":["Chris Gorgolewski"]}'
    )

    assert len(gitattributes_bytes) == 284
    assert len(dataset_description_bytes) == 83

    provider = MockDatasetProvider([entry], provider_name="openneuro")
    source = ScriptedDownloadSource(
        {
            ".gitattributes": gitattributes_bytes,
            "dataset_description.json": dataset_description_bytes,
        },
        source_name="openneuro",
    )

    project_dir = tmp_path / "smoke-proj"

    # Run smoke flow with doubles: verifies download, lock, and offline reopening
    run_integration_smoke(
        project_root=project_dir,
        manifest=manifest,
        provider=provider,
        source=source,
        max_wait_s=5.0,
    )

    # Verify that the dataset exists and has the expected files on disk
    dest = project_dir / "datasets" / "openneuro" / "ds001037" / "00001"
    assert (dest / ".gitattributes").is_file()
    assert (dest / "dataset_description.json").is_file()
    assert (dest / "dataset-lock-1.0.json").is_file()

    lock = DatasetLock.model_validate_json((dest / "dataset-lock-1.0.json").read_bytes())
    assert lock.dataset_identity == manifest.expected_lock_identity


def test_deterministic_mock_smoke_detects_drift_and_writes_log(tmp_path: Path) -> None:
    """Smoke runner must fail closed and write secret-free diagnostic JSON on drift."""
    manifest = get_pinned_integration_manifest()
    entry = _make_sample_entry()

    # Return modified bytes for dataset_description.json
    provider = MockDatasetProvider([entry], provider_name="openneuro")
    source = ScriptedDownloadSource(
        {
            ".gitattributes": b"x" * 284,
            "dataset_description.json": b"y" * 83,
        },
        source_name="openneuro",
    )

    project_dir = tmp_path / "smoke-proj"
    log_file = tmp_path / "diagnostic.json"

    with pytest.raises(UpstreamDriftError) as exc_info:
        run_integration_smoke(
            project_root=project_dir,
            log_file=log_file,
            manifest=manifest,
            provider=provider,
            source=source,
            max_wait_s=5.0,
        )

    diag = exc_info.value.diagnostic
    assert diag.category == DriftCategory.CHECKSUM
    assert log_file.is_file()

    log_data = json.loads(log_file.read_text(encoding="utf-8"))
    assert log_data["category"] == DriftCategory.CHECKSUM
    assert log_data["schema_version"] == "1.0"
    assert "token" not in json.dumps(log_data).lower()
    assert "secret" not in json.dumps(log_data).lower()


@needs_live_smoke
def test_live_pinned_integration_download_smoke() -> None:
    """Opt-in live smoke test: resolve, download, verify ds001037:00001 and reopen offline."""
    run_integration_smoke(max_wait_s=60.0)
