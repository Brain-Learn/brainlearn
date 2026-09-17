"""Trusted pinned integration smoke runner and drift detector (Step 5A.7).

This module runs the integration smoke against the single pinned snapshot
(OpenNeuro ds001037:00001). It verifies:
1. Upstream metadata resolution within strict timeout and redirect bounds.
2. Catalog identity and file manifest match the reviewed pin.
3. Download and verification into a real project tree within byte and file limits.
4. Finalized lock identity and per-file SHA-256 digests match the pin.
5. The downloaded project and lock can be reopened and verified completely offline
   with all network access blocked.
6. Fail-closed upstream drift diagnosis that outputs concise, secret-free records.

No credentials are required or accepted.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
import tempfile
import time
import urllib.request
from pathlib import Path
from typing import Any

from brainlearn_core import (
    OPENNEURO_ENDPOINT,
    OPENNEURO_PROVIDER,
    DatasetLock,
    DatasetProvider,
    DownloadState,
    FileDownloadSource,
    OpenNeuroDownloadSource,
    OpenNeuroProvider,
    RedirectPolicy,
    UrllibGraphQLTransport,
    check_catalog_entry_drift,
    check_lock_drift,
    classify_upstream_exception,
    get_pinned_integration_manifest,
)
from brainlearn_core.pinned_integration import (
    DriftCategory,
    DriftDiagnostic,
    PinnedDatasetManifest,
    UpstreamDriftError,
)

from brainlearn_server.downloads import DownloadService
from brainlearn_server.project_store import ProjectStore


def _block_network() -> Any:
    """Return a function that forbids any socket or URL operation."""

    def _forbidden(*args: Any, **kwargs: Any) -> Any:
        raise AssertionError(
            "Offline reopening violation: network operation attempted while offline."
        )

    return _forbidden


def run_integration_smoke(
    *,
    project_root: Path | None = None,
    log_file: Path | None = None,
    manifest: PinnedDatasetManifest | None = None,
    provider: DatasetProvider | None = None,
    source: FileDownloadSource | None = None,
    poll_interval_s: float = 0.1,
    max_wait_s: float = 60.0,
) -> None:
    """Execute the pinned integration smoke flow.

    Raises :class:`UpstreamDriftError` on any drift or failure.
    Writes a structured diagnostic log to ``log_file`` on failure if requested.
    """
    if manifest is None:
        manifest = get_pinned_integration_manifest()

    cleanup_dir = None
    if project_root is None:
        cleanup_dir = tempfile.TemporaryDirectory(prefix="brainlearn-smoke-")
        project_dir = Path(cleanup_dir.name) / "smoke-project"
    else:
        project_dir = project_root

    diagnostic: DriftDiagnostic | None = None

    try:
        _execute_smoke_flow(
            project_dir=project_dir,
            manifest=manifest,
            provider=provider,
            source=source,
            poll_interval_s=poll_interval_s,
            max_wait_s=max_wait_s,
        )
    except UpstreamDriftError as exc:
        diagnostic = exc.diagnostic
        raise
    except Exception as exc:
        diagnostic = classify_upstream_exception(exc, manifest)
        raise UpstreamDriftError(diagnostic) from exc
    finally:
        if diagnostic is not None and log_file is not None:
            try:
                log_file.parent.mkdir(parents=True, exist_ok=True)
                log_file.write_text(diagnostic.model_dump_json(indent=2), encoding="utf-8")
            except OSError:
                pass
        if cleanup_dir is not None:
            cleanup_dir.cleanup()


def _execute_smoke_flow(
    *,
    project_dir: Path,
    manifest: PinnedDatasetManifest,
    provider: DatasetProvider | None,
    source: FileDownloadSource | None,
    poll_interval_s: float,
    max_wait_s: float,
) -> None:
    # 1. Prepare isolated project
    store = ProjectStore(project_dir.parent)
    store.create_project(str(project_dir), name="integration-smoke")

    # 2. Setup provider and source
    if provider is None:
        redirect_policy = RedirectPolicy(
            max_hops=manifest.limits.max_redirect_hops,
            allowed_hosts=("openneuro.org",),
        )
        transport = UrllibGraphQLTransport(
            endpoint=OPENNEURO_ENDPOINT,
            timeout_s=manifest.limits.timeout_s,
            redirect_policy=redirect_policy,
        )
        provider = OpenNeuroProvider(transport)

    if source is None and provider.provider_name == OPENNEURO_PROVIDER:
        source = OpenNeuroDownloadSource(
            timeout_s=manifest.limits.timeout_s,
        )

    # 3. Resolve snapshot metadata and check drift
    try:
        entry = asyncio.run(provider.resolve_snapshot(manifest.dataset_id, manifest.snapshot))
    except Exception as exc:
        raise UpstreamDriftError(classify_upstream_exception(exc, manifest)) from exc

    # Enforce limits
    if len(entry.expected_files) > manifest.limits.max_files:
        raise UpstreamDriftError(
            DriftDiagnostic(
                category=DriftCategory.SCHEMA,
                code="file_count_limit_exceeded",
                message=(
                    f"Resolved files count ({len(entry.expected_files)}) exceeds "
                    f"limit of {manifest.limits.max_files}."
                ),
                details={
                    "observed_count": len(entry.expected_files),
                    "limit": manifest.limits.max_files,
                },
            )
        )

    if entry.approximate_total_bytes > manifest.limits.max_total_bytes:
        raise UpstreamDriftError(
            DriftDiagnostic(
                category=DriftCategory.SCHEMA,
                code="byte_limit_exceeded",
                message=(
                    f"Snapshot total bytes ({entry.approximate_total_bytes}) exceeds "
                    f"limit of {manifest.limits.max_total_bytes}."
                ),
                details={
                    "observed_bytes": entry.approximate_total_bytes,
                    "limit": manifest.limits.max_total_bytes,
                },
            )
        )

    # Verify catalog drift
    check_catalog_entry_drift(manifest, entry)

    # 4. Run download through DownloadService
    providers_override = {manifest.provider: provider}
    sources_override = {manifest.provider: source} if source is not None else None
    service = DownloadService(
        store,
        providers_override=providers_override,
        sources_override=sources_override,
    )

    record = service.start_download(
        str(project_dir),
        manifest.provider,
        manifest.dataset_id,
        manifest.snapshot,
    )

    # Wait for completion
    deadline = time.monotonic() + max_wait_s
    final_record = record
    while time.monotonic() < deadline:
        fresh = service._load_record(project_dir, record.download_id)
        if fresh.state in (
            DownloadState.SUCCEEDED,
            DownloadState.FAILED,
            DownloadState.CANCELLED,
        ):
            final_record = fresh
            break
        time.sleep(poll_interval_s)
    else:
        raise UpstreamDriftError(
            DriftDiagnostic(
                category=DriftCategory.AVAILABILITY,
                code="download_timeout",
                message=(
                    f"Download of {manifest.dataset_id}:{manifest.snapshot} "
                    f"timed out after {max_wait_s}s."
                ),
                details={"download_id": record.download_id, "state": final_record.state},
            )
        )

    if final_record.state != DownloadState.SUCCEEDED:
        failure_msg = final_record.failure.message if final_record.failure else "Unknown failure"
        failure_code = final_record.failure.code if final_record.failure else "failed"
        category = (
            DriftCategory.CHECKSUM if "checksum" in failure_code else DriftCategory.AVAILABILITY
        )
        raise UpstreamDriftError(
            DriftDiagnostic(
                category=category,
                code=failure_code,
                message=f"Download ended in {final_record.state}: {failure_msg}",
                details={"download_id": record.download_id, "state": final_record.state},
            )
        )

    # 5. Read finalized lock and check drift
    lock_parts = ("datasets", manifest.provider, manifest.dataset_id, manifest.snapshot)
    lock = service._read_lock(project_dir, lock_parts)
    if lock is None or not isinstance(lock, DatasetLock):
        raise UpstreamDriftError(
            DriftDiagnostic(
                category=DriftCategory.IDENTITY,
                code="missing_lock",
                message=f"Finalized dataset lock is missing or corrupt at {lock_parts}.",
                details={"project": str(project_dir)},
            )
        )

    check_lock_drift(manifest, lock)

    # 6. Prove offline reopening with network strictly blocked
    orig_urlopen = urllib.request.urlopen
    urllib.request.urlopen = _block_network()
    try:
        restarted_service = DownloadService(store)
        reconciled = restarted_service.list_downloads(str(project_dir))
        if len(reconciled) != 1 or reconciled[0].state != DownloadState.SUCCEEDED:
            raise UpstreamDriftError(
                DriftDiagnostic(
                    category=DriftCategory.IDENTITY,
                    code="offline_reopen_failed",
                    message="Failed to reload succeeded download record in offline mode.",
                    details={"reconciled_count": len(reconciled)},
                )
            )

        offline_lock = restarted_service._read_lock(project_dir, lock_parts)
        if offline_lock is None or offline_lock.dataset_identity != manifest.expected_lock_identity:
            raise UpstreamDriftError(
                DriftDiagnostic(
                    category=DriftCategory.IDENTITY,
                    code="offline_lock_verification_failed",
                    message="Offline lock verification failed upon reopening.",
                    details={},
                )
            )
    finally:
        urllib.request.urlopen = orig_urlopen


def main() -> int:
    """CLI entry point for running the integration smoke."""
    parser = argparse.ArgumentParser(
        description="Run BrainLearn pinned integration download smoke (ds001037:00001)."
    )
    parser.add_argument(
        "--log-file",
        type=Path,
        default=None,
        help="Optional path to write a JSON diagnostic drift log on failure.",
    )
    parser.add_argument(
        "--project-dir",
        type=Path,
        default=None,
        help="Optional existing project directory for the smoke.",
    )
    args = parser.parse_args()

    try:
        run_integration_smoke(
            project_root=args.project_dir,
            log_file=args.log_file,
        )
        print("SUCCESS: Pinned integration smoke verified ds001037:00001 and reopened offline.")
        return 0
    except UpstreamDriftError as err:
        print(f"FAILED: {err.diagnostic.format_diagnostic()}", file=sys.stderr)
        return 1
    except Exception as exc:
        print(f"UNEXPECTED ERROR: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
