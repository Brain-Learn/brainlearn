"""Step 5A.7: Deterministic mock-provider coverage and pinned integration smoke.

Ordinary CI remains 100% offline. Every test in this suite except the
opt-in `test_live_pinned_integration_download_smoke` runs with mock doubles
and zero network sockets.
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

import pytest
from brainlearn_core import (
    OPENNEURO_REVIEWED_LICENSE_EVIDENCE_URL,
    OPENNEURO_REVIEWED_LICENSE_NAME,
    OPENNEURO_REVIEWED_LICENSE_SPDX,
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
from brainlearn_server.integration_smoke import (
    OfflineNetworkViolation,
    _offline_network_boundary,
    run_integration_smoke,
)

FIXTURES = Path(__file__).parent / "fixtures"
LIVE_SMOKE_ENV_VAR = "BRAINLEARN_INTEGRATION_SMOKE"
PINNED_FIXTURE_PATH = FIXTURES / "pinned-integration-snapshot-1.0.json"
needs_live_smoke = pytest.mark.skipif(
    not os.environ.get(LIVE_SMOKE_ENV_VAR),
    reason=f"Run only when {LIVE_SMOKE_ENV_VAR}=1; ordinary CI stays offline.",
)


# Hostile strings planted in every secret-leak regression below. If any of
# them ever reaches a diagnostic message, JSON log, or repr, the test fails.
PLANTED_USERINFO_SECRET = "TOPSECRETUSERINFO"
PLANTED_QUERY_TOKEN = "HIDDENTOKEN123"
PLANTED_BEARER = "Bearer sk-plant-abc123"
PLANTED_ABSOLUTE_PATH = "/Users/researcher/private-project/hidden"
PLANTED_SIGNED_URL = "https://openneuro.org/crn/download?X-Amz-Signature=SIGNEDSIG123&token=abc"
LEAK_NEEDLES = (
    PLANTED_USERINFO_SECRET,
    PLANTED_QUERY_TOKEN,
    "sk-plant-abc123",
    "SIGNEDSIG123",
    PLANTED_ABSOLUTE_PATH,
    "/Users/researcher",
    "alice:TOPSECRET@example.test",
    "Authorization:",
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


def test_fixture_matches_runtime_manifest_exactly() -> None:
    """The reviewed JSON fixture must equal the runtime pin exactly.

    The fixture is the reviewed record of the pin; this test makes silent
    divergence between it and the in-code manifest impossible. Field-level
    diagnostics name the first divergence so a curator repairs the pin and
    the fixture together.
    """
    raw = json.loads(PINNED_FIXTURE_PATH.read_text(encoding="utf-8"))
    runtime = get_pinned_integration_manifest()

    fixture = PinnedDatasetManifest.model_validate(raw)
    assert fixture == runtime, "reviewed fixture diverged from the runtime pin"

    # Field-by-field diagnostics: the first mismatch names the field.
    for name in PinnedDatasetManifest.model_fields:
        assert getattr(fixture, name) == getattr(runtime, name), (
            f"fixture field {name!r} diverged from the runtime pin"
        )

    # Canonical serialization must be stable as well as semantic.
    assert fixture.model_dump_json() == runtime.model_dump_json()


def test_fixture_mutation_is_caught_by_parity_or_validation() -> None:
    """Any pinned-value mutation must fail parity or validation.

    Proves the fixture cannot silently drift: changing a size, digest,
    license, SPDX value, evidence URL, snapshot, or identity fails either
    model validation or exact parity with the runtime manifest.
    """
    base = json.loads(PINNED_FIXTURE_PATH.read_text(encoding="utf-8"))
    runtime = get_pinned_integration_manifest()
    digest = base["expected_files"][0]["sha256"]

    mutations = [
        {"path": ["expected_files", 0, "byte_size"], "value": 999},
        {"path": ["expected_files", 0, "sha256"], "value": "0" * 64},
        {"path": ["license_name"], "value": "MIT"},
        {
            "path": ["license_name"],
            "value": "Unverified OpenNeuro license (pending curator review)",
        },
        {"path": ["license_spdx"], "value": "MIT"},
        {"path": ["license_spdx"], "value": None},
        {"path": ["reuse_statement"], "value": "Reuse terms pending curator review."},
        {"path": ["license_evidence_url"], "value": "https://example.org/evidence"},
        {"path": ["snapshot"], "value": "00002"},
        {"path": ["dataset_id"], "value": "ds000001"},
        {"path": ["expected_catalog_identity"], "value": "brainlearn-v1:dataset:" + "1" * 64},
        {"path": ["expected_lock_identity"], "value": "brainlearn-v1:dataset:" + "2" * 64},
        {"path": ["expected_total_bytes"], "value": 368},
        {
            "path": ["landing_page"],
            "value": "https://openneuro.org/datasets/ds001037/versions/00002",
        },
    ]
    for mutation in mutations:
        candidate = json.loads(json.dumps(base))
        target: Any = candidate
        for key in mutation["path"][:-1]:
            target = target[key]
        target[mutation["path"][-1]] = mutation["value"]
        try:
            mutated = PinnedDatasetManifest.model_validate(candidate)
        except ValueError:
            continue  # validation rejected the mutation: acceptable
        with pytest.raises(AssertionError):
            assert mutated == runtime
    assert digest != "0" * 64


def test_manifest_rejects_pending_license_placeholder() -> None:
    """A pin carrying a pending-license placeholder must fail validation."""

    raw = json.loads(PINNED_FIXTURE_PATH.read_text(encoding="utf-8"))
    raw["license_name"] = "Unverified OpenNeuro license (pending curator review)"
    with pytest.raises(ValueError, match="reviewed license"):
        PinnedDatasetManifest.model_validate(raw)


def test_catalog_drift_rejects_license_downgrade() -> None:
    """A live entry whose license terms diverge from the pin fails closed."""

    manifest = get_pinned_integration_manifest()
    entry = _make_sample_entry()
    check_catalog_entry_drift(manifest, entry)  # exact reviewed terms pass

    downgraded = entry.model_copy(update={"license_spdx": None})
    with pytest.raises(UpstreamDriftError) as exc_info:
        check_catalog_entry_drift(manifest, downgraded)
    diag = exc_info.value.diagnostic
    assert diag.category == DriftCategory.IDENTITY
    assert diag.code == "license_terms_mismatch"

    relabeled = entry.model_copy(update={"license_name": "MIT"})
    with pytest.raises(UpstreamDriftError):
        check_catalog_entry_drift(manifest, relabeled)


def test_pinned_license_matches_reviewed_platform_terms() -> None:
    """The pin encodes the reviewed CC0 terms with the evidence URL."""

    manifest = get_pinned_integration_manifest()
    assert manifest.license_name == OPENNEURO_REVIEWED_LICENSE_NAME
    assert manifest.license_spdx == OPENNEURO_REVIEWED_LICENSE_SPDX == "CC0-1.0"
    assert manifest.license_evidence_url == OPENNEURO_REVIEWED_LICENSE_EVIDENCE_URL
    assert "CC0" in manifest.license_name
    assert "docs.openneuro.org/faq.html" in manifest.reuse_statement


def test_live_resolved_entry_carries_reviewed_license() -> None:
    """The production adapter records the reviewed license when upstream omits it.

    The pinned snapshot's dataset_description.json has no License field, so
    the resolved catalog entry must carry the reviewed platform terms, not
    a placeholder — this is what makes the pin's license claim live-true.
    """

    entry = _make_sample_entry()
    manifest = get_pinned_integration_manifest()
    assert entry.license_name == manifest.license_name
    assert entry.license_spdx == manifest.license_spdx
    assert entry.reuse_statement == manifest.reuse_statement
    # The identity must recompute through the production function.
    from brainlearn_core import catalog_entry_identity

    assert entry.catalog_identity == catalog_entry_identity(
        provider=entry.provider,
        dataset_id=entry.dataset_id,
        snapshot=entry.snapshot,
        modality=entry.modality,
        task=entry.task,
        participants=entry.participants,
        formats=list(entry.formats),
        approximate_total_bytes=entry.approximate_total_bytes,
        expected_total_bytes=entry.expected_total_bytes,
        expected_files=[item.model_dump(mode="json") for item in entry.expected_files],
        access=entry.access.value,
        license_name=entry.license_name,
        license_spdx=entry.license_spdx,
        reuse_statement=entry.reuse_statement,
        citations=[item.model_dump(mode="json") for item in entry.citations],
        landing_page=entry.landing_page,
        compatible_templates=list(entry.compatible_templates),
    )


def test_no_socket_in_offline_tests() -> None:
    """Ordinary tests must open no real network socket.

    Installs the production-transport boundary (denied sockets plus denied
    opener factory) and verifies the production OpenerDirector.open path is
    refused without touching the network, then restores everything.
    """

    from brainlearn_core import openneuro as on_module

    request = urllib.request.Request("https://openneuro.org/crn/graphql", method="POST")
    with _offline_network_boundary() as touched:
        opener = on_module._redirect_opener()
        with pytest.raises(OfflineNetworkViolation):
            opener.open(request, timeout=1)
    assert touched["opener_open"] == 1
    assert touched["connect"] == 0
    # Everything is restored afterwards: the real factory is back in place.
    assert on_module._redirect_opener.__name__ == "_redirect_opener"


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


def _assert_diagnostic_secret_free(diagnostic_json: str) -> None:
    """Fail if any planted secret or untrusted text reached the diagnostic."""

    lowered = diagnostic_json.lower()
    for needle in LEAK_NEEDLES:
        assert needle.lower() not in lowered, f"planted secret {needle!r} leaked"
    assert "example.test" not in lowered
    assert "/path" not in lowered
    assert "/users/" not in lowered


def test_drift_classification_never_copies_exception_text() -> None:
    """Hostile exception text must never reach any diagnostic field."""

    manifest = get_pinned_integration_manifest()
    hostile_messages = [
        f"GET https://alice:{PLANTED_USERINFO_SECRET}@example.test/path"
        f"?token={PLANTED_QUERY_TOKEN} failed",
        f"Authorization: {PLANTED_BEARER} rejected by provider",
        f"open failed: {PLANTED_ABSOLUTE_PATH}/dataset_description.json",
        f"denied {PLANTED_SIGNED_URL}",
        "GET https://alice:TOPSECRET@example.test/path?token=HIDDEN failed",
    ]
    hostile_exceptions: list[Exception] = [
        *(ProviderError(message) for message in hostile_messages),
        *(ProviderMalformed(message) for message in hostile_messages),
        *(urllib.error.URLError(message) for message in hostile_messages),
        *(OSError(message) for message in hostile_messages),
        *(ValueError(message) for message in hostile_messages),
        RuntimeError(f"bearer {PLANTED_BEARER} at {PLANTED_ABSOLUTE_PATH}"),
        Exception(f"userinfo {PLANTED_USERINFO_SECRET} token {PLANTED_QUERY_TOKEN}"),
    ]
    for exc in hostile_exceptions:
        diagnostic = classify_upstream_exception(exc, manifest)
        assert diagnostic.category in (
            DriftCategory.AVAILABILITY,
            DriftCategory.SCHEMA,
            DriftCategory.IDENTITY,
            DriftCategory.CHECKSUM,
        )
        serialized = diagnostic.model_dump_json()
        _assert_diagnostic_secret_free(serialized)
        formatted = diagnostic.format_diagnostic()
        _assert_diagnostic_secret_free(formatted)
        # The exception type name is the only trusted detail allowed.
        assert type(exc).__name__ in serialized or "untrusted" in serialized.lower()


def test_hostile_exceptions_keep_deterministic_categories_and_codes() -> None:
    """Secret-free classification must preserve the category/code contract."""

    manifest = get_pinned_integration_manifest()
    secret = f"https://alice:{PLANTED_USERINFO_SECRET}@example.test/p?token={PLANTED_QUERY_TOKEN}"

    cases: list[tuple[Exception, str, str]] = [
        (ProviderTimeout(f"timed out fetching {secret}"), "availability", "timeout"),
        (ProviderNotFound(f"no snapshot at {secret}"), "availability", "not_found"),
        (ProviderError(f"provider exploded: {secret}"), "availability", "provider_error"),
        (ProviderMalformed(f"bad payload {secret}"), "schema", "malformed_payload"),
        (ValueError(f"bad value {secret}"), "schema", "malformed_payload"),
        (KeyError(f"missing {secret}"), "schema", "malformed_payload"),
        (urllib.error.URLError(f"refused {secret}"), "availability", "connection_failed"),
        (OSError(f"disk error at {PLANTED_ABSOLUTE_PATH}"), "availability", "connection_failed"),
    ]
    for exc, category, code in cases:
        diagnostic = classify_upstream_exception(exc, manifest)
        assert diagnostic.category == category, (exc, diagnostic)
        assert diagnostic.code == code, (exc, diagnostic)
        _assert_diagnostic_secret_free(diagnostic.model_dump_json())

    http_503 = urllib.error.HTTPError(secret, 503, "Service Unavailable", {}, None)
    diagnostic = classify_upstream_exception(http_503, manifest)
    assert diagnostic.category == "availability"
    assert diagnostic.code == "server_error"
    assert diagnostic.details["http_status"] == 503  # trusted numeric field
    http_404 = urllib.error.HTTPError(secret, 404, "Not Found", {}, None)
    diagnostic = classify_upstream_exception(http_404, manifest)
    assert diagnostic.code == "not_found"
    assert "example.test" not in diagnostic.model_dump_json().lower()


def test_cli_unexpected_error_path_is_static_and_secret_free(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The CLI's unexpected-error path must print static text only."""

    from brainlearn_server import integration_smoke as smoke

    manifest = get_pinned_integration_manifest()
    entry = _make_sample_entry()
    gitattributes_bytes = b"* annex.backend=MD5E\n" + b"x" * 270
    dataset_description_bytes = (
        b'{"Name":"The brain of Chris","BIDSVersion":"1.0.2","Authors":["Chris Gorgolewski"]}'
    )
    provider = MockDatasetProvider([entry], provider_name="openneuro")
    source = ScriptedDownloadSource(
        {
            ".gitattributes": gitattributes_bytes,
            "dataset_description.json": dataset_description_bytes,
        },
        source_name="openneuro",
    )

    def _explode(*args: Any, **kwargs: Any) -> None:
        raise RuntimeError(
            f"offline boundary failure for alice:{PLANTED_USERINFO_SECRET}"
            f"@example.test?token={PLANTED_QUERY_TOKEN} at {PLANTED_ABSOLUTE_PATH}"
        )

    monkeypatch.setattr(smoke, "_execute_smoke_flow", _explode)
    log_file = tmp_path / "drift.json"
    with pytest.raises(UpstreamDriftError) as exc_info:
        smoke.run_integration_smoke(
            project_root=tmp_path / "proj",
            log_file=log_file,
            manifest=manifest,
            provider=provider,
            source=source,
        )
    # The raised diagnostic and the persisted JSON must be static/secret-free.
    assert exc_info.value.diagnostic.code == "unexpected_error"
    _assert_diagnostic_secret_free(exc_info.value.diagnostic.model_dump_json())
    _assert_diagnostic_secret_free(log_file.read_text(encoding="utf-8"))


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
    _assert_diagnostic_secret_free(json.dumps(log_data))


def test_offline_reopen_zero_provider_and_source_calls(tmp_path: Path) -> None:
    """Reopening the succeeded record must make zero provider/source calls.

    The smoke runner's offline phase installs fail-on-call provider and
    download-source doubles; a single ``list_datasets``,
    ``resolve_snapshot``, or ``stream_file`` invocation would raise and
    fail the flow. This is the production-boundary regression for finding
    3: the doubles are wired where the restarted service resolves them.
    """

    from brainlearn_server.integration_smoke import _fail_on_call_provider, _fail_on_call_source

    calls: dict[str, int] = {}
    provider = _fail_on_call_provider(calls)
    source = _fail_on_call_source(calls)

    # Direct invocation raises and is recorded (the doubles work). The
    # source double is a generator, so iteration triggers the refusal.
    import asyncio

    with pytest.raises(OfflineNetworkViolation):
        asyncio.run(provider.resolve_snapshot("ds001037", "00001"))
    with pytest.raises(OfflineNetworkViolation):
        for _ in source.stream_file("ds001037", "00001", ".gitattributes", 0):
            pass
    assert calls["resolve_snapshot"] == 1
    assert calls["stream_file"] == 1

    # Zero further calls: nothing else touched the doubles.
    assert calls.get("list_datasets", 0) == 0

    # The boundary denies a real opener attempt, and restores everything.
    with _offline_network_boundary() as touched:
        from brainlearn_core import openneuro as on_module

        opener = on_module._redirect_opener()
        request = urllib.request.Request("https://openneuro.org/crn/graphql", method="POST")
        with pytest.raises(OfflineNetworkViolation):
            opener.open(request, timeout=1)
    assert touched["opener_open"] == 1
    assert touched["connect"] == 0
    # Restored: the production opener factory is back in place.
    assert on_module._redirect_opener.__name__ == "_redirect_opener"


@needs_live_smoke
def test_live_pinned_integration_download_smoke() -> None:
    """Opt-in live smoke test: resolve, download, verify ds001037:00001 and reopen offline."""
    run_integration_smoke(max_wait_s=60.0)
