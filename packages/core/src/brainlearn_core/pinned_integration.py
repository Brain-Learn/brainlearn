"""Pinned integration dataset manifest and upstream drift diagnosis (Step 5A.7).

This module defines:
1. The pinned integration manifest for the single tiny, openly accessible
   public dataset snapshot (OpenNeuro ds001037:00001) used by the scheduled
   integration smoke test.
2. The fail-closed upstream drift diagnostic taxonomy distinguishing
   availability, schema, identity, and checksum failures.
3. Secret-free diagnostic reporting and exception classification.

No dataset bytes are committed to Git or stored here.
"""

from __future__ import annotations

import re
import urllib.error
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from brainlearn_core.datasets import (
    CatalogEntry,
    DatasetCitation,
    DatasetLock,
    validate_https_url,
    validate_relative_path,
)
from brainlearn_core.openneuro import OPENNEURO_PROVIDER
from brainlearn_core.providers import (
    ProviderError,
    ProviderMalformed,
    ProviderNotFound,
    ProviderTimeout,
)

PINNED_INTEGRATION_SCHEMA_VERSION: Literal["1.0"] = "1.0"
_SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")


class PinnedFile(BaseModel):
    """One expected file in the pinned snapshot with verified checksum and URL."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    path: str = Field(min_length=1)
    byte_size: int = Field(ge=0)
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    url: str = Field(min_length=1)

    @field_validator("path")
    @classmethod
    def _path_must_be_portable(cls, value: str) -> str:
        return validate_relative_path(value, "Pinned file path")

    @field_validator("url")
    @classmethod
    def _url_must_be_https(cls, value: str) -> str:
        return validate_https_url(value, "Pinned file URL")


class PinnedLimits(BaseModel):
    """Strict resource and network limits for the integration smoke download."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    timeout_s: float = Field(default=30.0, gt=0)
    max_total_bytes: int = Field(default=10 * 1024 * 1024, gt=0)
    max_files: int = Field(default=10, gt=0)
    max_redirect_hops: int = Field(default=5, ge=0)


class PinnedDatasetManifest(BaseModel):
    """Immutable reviewed manifest for the tiny integration dataset smoke."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal["1.0"] = PINNED_INTEGRATION_SCHEMA_VERSION
    provider: str = Field(min_length=1)
    dataset_id: str = Field(min_length=1)
    snapshot: str = Field(min_length=1)
    title: str = Field(min_length=1)
    landing_page: str = Field(min_length=1)
    license_name: str = Field(min_length=1)
    citations: tuple[DatasetCitation, ...] = Field(default_factory=tuple)
    expected_files: tuple[PinnedFile, ...] = Field(min_length=1)
    expected_total_bytes: int = Field(ge=0)
    expected_catalog_identity: str = Field(pattern=r"^brainlearn-v1:dataset:[0-9a-f]{64}$")
    expected_lock_identity: str = Field(pattern=r"^brainlearn-v1:dataset:[0-9a-f]{64}$")
    limits: PinnedLimits = Field(default_factory=PinnedLimits)

    @field_validator("landing_page")
    @classmethod
    def _landing_page_must_be_https(cls, value: str) -> str:
        return validate_https_url(value, "Landing page")

    @model_validator(mode="after")
    def _validate_totals_and_files(self) -> PinnedDatasetManifest:
        sum_bytes = sum(f.byte_size for f in self.expected_files)
        if sum_bytes != self.expected_total_bytes:
            raise ValueError(
                f"Expected total bytes ({self.expected_total_bytes}) does not match "
                f"the sum of file sizes ({sum_bytes})."
            )
        paths = [f.path for f in self.expected_files]
        if len(paths) != len(set(paths)):
            raise ValueError("Pinned files must not have duplicate paths.")
        return self


# Canonical reviewed snapshot pin for OpenNeuro ds001037:00001 ("The brain of Chris").
# Published 2018-07-14; contains exactly 2 root files (367 bytes total).
_PINNED_DS001037_DATA: dict[str, Any] = {
    "schema_version": "1.0",
    "provider": OPENNEURO_PROVIDER,
    "dataset_id": "ds001037",
    "snapshot": "00001",
    "title": "The brain of Chris",
    "landing_page": "https://openneuro.org/datasets/ds001037/versions/00001",
    "license_name": "Unverified OpenNeuro license (pending curator review)",
    "citations": [
        {"title": "The brain of Chris", "doi": None, "url": None},
    ],
    "expected_files": [
        {
            "path": ".gitattributes",
            "byte_size": 284,
            "sha256": "9476689a1190b1b79c7a65a128f992a551d86f55236c939a74218973423fcdd1",
            "url": "https://openneuro.org/crn/datasets/ds001037/snapshots/00001/files/.gitattributes",
        },
        {
            "path": "dataset_description.json",
            "byte_size": 83,
            "sha256": "8cef746e8df99ef7a3efaf4b7f1cea7313f2d732b22abf609733fdf38584f400",
            "url": "https://openneuro.org/crn/datasets/ds001037/snapshots/00001/files/dataset_description.json",
        },
    ],
    "expected_total_bytes": 367,
    "expected_catalog_identity": (
        "brainlearn-v1:dataset:4a7fbd09beef515a1dba89c2fea81f82fbfa13c66c787a7233de6515c056a369"
    ),
    "expected_lock_identity": (
        "brainlearn-v1:dataset:020cab5e89f2633bf536d2c6e72e04361972991b3b69f3714d359f5a99d09251"
    ),
    "limits": {
        "timeout_s": 30.0,
        "max_total_bytes": 10485760,
        "max_files": 10,
        "max_redirect_hops": 5,
    },
}


def get_pinned_integration_manifest() -> PinnedDatasetManifest:
    """Return the validated immutable manifest for ds001037:00001."""
    return PinnedDatasetManifest.model_validate(_PINNED_DS001037_DATA)


# ── Drift taxonomy and diagnostics ───────────────────────────────────────────


class DriftCategory(StrEnum):
    """Category of upstream divergence or retrieval failure."""

    AVAILABILITY = "availability"
    SCHEMA = "schema"
    IDENTITY = "identity"
    CHECKSUM = "checksum"


class DriftDiagnostic(BaseModel):
    """Structured, secret-free diagnostic record for upstream drift."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal["1.0"] = PINNED_INTEGRATION_SCHEMA_VERSION
    category: DriftCategory
    code: str
    message: str
    details: dict[str, Any] = Field(default_factory=dict)
    timestamp: str = Field(default_factory=lambda: datetime.now(UTC).isoformat())

    def format_diagnostic(self) -> str:
        """Format a concise, secret-free one-line summary for logs and CI."""
        return f"[DRIFT:{self.category.value}:{self.code}] {self.message}"


class UpstreamDriftError(Exception):
    """Raised when upstream drift is detected during integration verification."""

    def __init__(self, diagnostic: DriftDiagnostic) -> None:
        super().__init__(diagnostic.format_diagnostic())
        self.diagnostic = diagnostic


def _sanitize_text(text: str) -> str:
    """Strip any accidental URLs with credentials or query strings."""
    # Remove query strings and fragments
    cleaned = re.sub(r"(\?[^ \t\r\n\)\"']*)", "", text)
    cleaned = re.sub(r"(#[^ \t\r\n\)\"']*)", "", cleaned)
    # Strip whitespace
    return " ".join(cleaned.split())


def classify_upstream_exception(exc: Exception, manifest: PinnedDatasetManifest) -> DriftDiagnostic:
    """Map any upstream exception to a concise, secret-free drift diagnostic."""
    if isinstance(exc, UpstreamDriftError):
        return exc.diagnostic

    dataset_ref = f"{manifest.dataset_id}:{manifest.snapshot}"
    raw_msg = _sanitize_text(str(exc)) if str(exc) else type(exc).__name__

    # 1. Availability failures: timeout, not found, HTTP 5xx, connection drop
    if isinstance(exc, ProviderTimeout) or isinstance(exc, TimeoutError):
        return DriftDiagnostic(
            category=DriftCategory.AVAILABILITY,
            code="timeout",
            message=f"Upstream provider timed out resolving {dataset_ref}.",
            details={"provider": manifest.provider, "dataset": dataset_ref},
        )
    if isinstance(exc, ProviderNotFound):
        return DriftDiagnostic(
            category=DriftCategory.AVAILABILITY,
            code="not_found",
            message=f"Upstream snapshot {dataset_ref} was not found or is no longer public.",
            details={"provider": manifest.provider, "dataset": dataset_ref},
        )
    if isinstance(exc, urllib.error.HTTPError):
        code = exc.code
        if code in (404, 410):
            return DriftDiagnostic(
                category=DriftCategory.AVAILABILITY,
                code="not_found",
                message=f"Upstream snapshot {dataset_ref} returned HTTP {code}.",
                details={
                    "provider": manifest.provider,
                    "dataset": dataset_ref,
                    "http_status": code,
                },
            )
        if code >= 500:
            return DriftDiagnostic(
                category=DriftCategory.AVAILABILITY,
                code="server_error",
                message=f"Upstream provider returned HTTP {code} for {dataset_ref}.",
                details={
                    "provider": manifest.provider,
                    "dataset": dataset_ref,
                    "http_status": code,
                },
            )
    if isinstance(exc, (urllib.error.URLError, ConnectionError, OSError)):
        return DriftDiagnostic(
            category=DriftCategory.AVAILABILITY,
            code="connection_failed",
            message=f"Failed to connect to upstream provider for {dataset_ref}: {raw_msg[:120]}.",
            details={"provider": manifest.provider, "dataset": dataset_ref},
        )

    # 2. Schema failures: malformed GraphQL, invalid JSON, unexpected types
    if isinstance(exc, ProviderMalformed) or isinstance(exc, (KeyError, TypeError, ValueError)):
        return DriftDiagnostic(
            category=DriftCategory.SCHEMA,
            code="malformed_payload",
            message=(
                f"Upstream payload for {dataset_ref} violated schema expectations: {raw_msg[:120]}."
            ),
            details={"provider": manifest.provider, "dataset": dataset_ref},
        )

    # 3. Default fallback: generic availability failure
    if isinstance(exc, ProviderError):
        return DriftDiagnostic(
            category=DriftCategory.AVAILABILITY,
            code="provider_error",
            message=f"Upstream provider error for {dataset_ref}: {raw_msg[:120]}.",
            details={"provider": manifest.provider, "dataset": dataset_ref},
        )

    return DriftDiagnostic(
        category=DriftCategory.AVAILABILITY,
        code="unexpected_error",
        message=f"Unexpected error retrieving {dataset_ref}: {raw_msg[:120]}.",
        details={
            "provider": manifest.provider,
            "dataset": dataset_ref,
            "exception_type": type(exc).__name__,
        },
    )


def check_catalog_entry_drift(manifest: PinnedDatasetManifest, entry: CatalogEntry) -> None:
    """Verify that a resolved catalog entry matches the pinned expectation."""
    dataset_ref = f"{manifest.dataset_id}:{manifest.snapshot}"
    if entry.catalog_identity != manifest.expected_catalog_identity:
        raise UpstreamDriftError(
            DriftDiagnostic(
                category=DriftCategory.IDENTITY,
                code="catalog_identity_mismatch",
                message=(
                    f"Catalog identity for {dataset_ref} diverged from pinned expectation. "
                    f"Expected {manifest.expected_catalog_identity}, "
                    f"observed {entry.catalog_identity}."
                ),
                details={
                    "expected_identity": manifest.expected_catalog_identity,
                    "observed_identity": entry.catalog_identity,
                    "dataset": dataset_ref,
                },
            )
        )

    # Verify file count and paths
    expected_paths = {f.path: f.byte_size for f in manifest.expected_files}
    observed_paths = {f.path: f.byte_size for f in entry.expected_files}
    if set(expected_paths) != set(observed_paths):
        raise UpstreamDriftError(
            DriftDiagnostic(
                category=DriftCategory.SCHEMA,
                code="file_manifest_drift",
                message=(
                    f"Catalog files for {dataset_ref} differ from pinned list. "
                    f"Missing: {sorted(set(expected_paths) - set(observed_paths))}, "
                    f"Unexpected: {sorted(set(observed_paths) - set(expected_paths))}."
                ),
                details={
                    "dataset": dataset_ref,
                    "missing": sorted(set(expected_paths) - set(observed_paths)),
                    "unexpected": sorted(set(observed_paths) - set(expected_paths)),
                },
            )
        )


def check_lock_drift(manifest: PinnedDatasetManifest, lock: DatasetLock) -> None:
    """Verify that a finalized dataset lock matches the pinned expectation."""
    dataset_ref = f"{manifest.dataset_id}:{manifest.snapshot}"
    pinned_by_path = {f.path: f for f in manifest.expected_files}
    observed_by_path = {f.path: f for f in lock.expected_files}

    # 1. Check file set
    if set(pinned_by_path) != set(observed_by_path):
        raise UpstreamDriftError(
            DriftDiagnostic(
                category=DriftCategory.SCHEMA,
                code="locked_files_mismatch",
                message=(
                    f"Finalized lock for {dataset_ref} holds files differing from pin. "
                    f"Missing: {sorted(set(pinned_by_path) - set(observed_by_path))}, "
                    f"Unexpected: {sorted(set(observed_by_path) - set(pinned_by_path))}."
                ),
                details={"dataset": dataset_ref},
            )
        )

    # 2. Check byte sizes and checksums per file
    for path, pinned_file in pinned_by_path.items():
        observed = observed_by_path[path]
        if observed.byte_size != pinned_file.byte_size:
            raise UpstreamDriftError(
                DriftDiagnostic(
                    category=DriftCategory.CHECKSUM,
                    code="byte_size_mismatch",
                    message=(
                        f"File {path!r} byte size diverged: "
                        f"expected {pinned_file.byte_size}, observed {observed.byte_size}."
                    ),
                    details={
                        "path": path,
                        "expected_size": pinned_file.byte_size,
                        "observed_size": observed.byte_size,
                    },
                )
            )
        if observed.sha256 != pinned_file.sha256:
            raise UpstreamDriftError(
                DriftDiagnostic(
                    category=DriftCategory.CHECKSUM,
                    code="sha256_mismatch",
                    message=(
                        f"File {path!r} sha256 diverged: "
                        f"expected {pinned_file.sha256}, observed {observed.sha256}."
                    ),
                    details={
                        "path": path,
                        "expected_sha256": pinned_file.sha256,
                        "observed_sha256": observed.sha256,
                    },
                )
            )

    # 3. Check lock identity
    if lock.dataset_identity != manifest.expected_lock_identity:
        raise UpstreamDriftError(
            DriftDiagnostic(
                category=DriftCategory.IDENTITY,
                code="lock_identity_mismatch",
                message=(
                    f"Lock identity for {dataset_ref} diverged from pinned expectation. "
                    f"Expected {manifest.expected_lock_identity}, observed {lock.dataset_identity}."
                ),
                details={
                    "expected_identity": manifest.expected_lock_identity,
                    "observed_identity": lock.dataset_identity,
                    "dataset": dataset_ref,
                },
            )
        )
