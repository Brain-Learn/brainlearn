"""Versioned curated-dataset contract (provider-neutral, no networking).

This module fixes the persisted shapes for the Step 5A dataset library: a
curated catalog entry describing an immutable dataset snapshot, and an
immutable local lock record claiming what was retrieved into a project. There
is no downloading, extraction, or inspection here; provider adapters and the
UI arrive in later units and must consume these exact shapes.

Immutability is structural, not just a frozen flag: every persisted
collection is stored as a tuple in canonical sorted order, citations are an
immutable dataset-specific value, and descriptive strings are normalized
before uniqueness and identity checks. Two logically identical records
therefore serialize identically however their inputs were enumerated.

Security posture: the contract can never persist credentials, tokens,
cookies, or signed URLs. Catalog entries carry no transfer endpoints at all
(adapters derive them ephemerally from provider, dataset ID, and snapshot);
every persisted URL must be a plain HTTPS link with a valid host and no
userinfo, query, or fragment; every model forbids unknown fields; and
artifact/cache paths and media types reject control characters and
header-unsafe values.
"""

import ipaddress
import re
import urllib.parse
from collections.abc import Sequence
from datetime import datetime
from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from brainlearn_core.identity import content_identity

DATASET_SCHEMA_VERSION: Literal["1.0"] = "1.0"
SUPPORTED_DATASET_VERSIONS: tuple[str, ...] = ("1.0",)

# Provider tag for researcher-selected local/private directories.  The value
# matches _PROVIDER_PATTERN and is stable across schema versions.
LOCAL_PROVIDER: str = "local"


_DATASET_IDENTITY_PATTERN = r"^brainlearn-v1:dataset:[0-9a-f]{64}$"
_PROVIDER_PATTERN = r"^[a-z0-9][a-z0-9-]*$"
_DATASET_ID_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9._-]*$"
_SHA256_PATTERN = r"^[0-9a-f]{64}$"
_DRIVE_QUALIFIED_PATTERN = r"^[A-Za-z]:"
_DOI_PATTERN = r"^10\.\d{4,}/\S+$"
_NOTES_MAX_LENGTH = 2000
# Windows reserved basenames, matched case-insensitively without extension.
_RESERVED_BASENAMES = frozenset(
    ["con", "prn", "aux", "nul"]
    + [f"com{i}" for i in range(1, 10)]
    + [f"lpt{i}" for i in range(1, 10)]
)


class DatasetAccess(StrEnum):
    """Who may retrieve the dataset without further approval."""

    PUBLIC = "public"
    RESTRICTED = "restricted"
    CREDENTIALED = "credentialed"


class DatasetReviewStatus(StrEnum):
    """Whether a curator has verified the entry against its provider."""

    PENDING = "pending"
    VERIFIED = "verified"


def _require_iso_timestamp(value: str | None, field_name: str) -> str | None:
    if value is None:
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as exc:
        raise ValueError(f"{field_name} must be an ISO-8601 timestamp, got {value!r}.") from exc
    if parsed.tzinfo is None:
        raise ValueError(
            f"{field_name} must include a timezone offset, got {value!r}. "
            "Naive timestamps cannot be ordered reliably across platforms."
        )
    return value


def _stripped_text(value: str, field_name: str, *, allow_empty: bool) -> str:
    """Normalize surrounding whitespace on descriptive strings.

    Blank-but-nonempty input is rejected; a truly empty value is allowed
    only where the field documents it as unstated.
    """

    trimmed = value.strip()
    if trimmed == "" and (not allow_empty or value != ""):
        raise ValueError(f"{field_name} must not be blank.")
    return trimmed


_PORTABLE_FORBIDDEN_CHARS = frozenset(':<>"|?*')


def _check_portable_segment(
    segment: str, field_name: str, value: str, *, allow_leading_dot: bool
) -> None:
    """Enforce the conservative cross-platform segment grammar.

    Beyond separators and controls (checked by callers), Windows forbids
    ``: < > " | ? *`` anywhere, reserves DOS basenames regardless of
    extension or case, and strips trailing dots/spaces and leading spaces,
    so such names cannot materialize consistently on every target.
    """

    if any(char in _PORTABLE_FORBIDDEN_CHARS for char in segment):
        raise ValueError(
            f'{field_name} must use portable path characters (no :<>"|?*), got {value!r}.'
        )
    if segment.split(".")[0].lower() in _RESERVED_BASENAMES:
        raise ValueError(f"{field_name} must not use reserved names, got {value!r}.")
    if segment != segment.rstrip(". ") or segment.startswith(" "):
        raise ValueError(
            f"{field_name} must not use trailing dots/spaces or leading spaces, got {value!r}."
        )
    if not allow_leading_dot and segment.startswith("."):
        raise ValueError(f"{field_name} must not use leading dots, got {value!r}.")


def _check_path_component(value: str, field_name: str) -> str:
    """Validate one canonical, cross-platform path-safe identifier component."""

    if (
        not value
        or value in (".", "..")
        or "/" in value
        or "\\" in value
        or re.match(_DRIVE_QUALIFIED_PATTERN, value) is not None
        or any(char.isspace() or ord(char) < 0x20 or ord(char) == 0x7F for char in value)
    ):
        raise ValueError(
            f"{field_name} must be a single portable path component "
            f"(no separators, whitespace, dot segments, drive qualifiers, "
            f"reserved names, or trailing dots/spaces), got {value!r}."
        )
    _check_portable_segment(value, field_name, value, allow_leading_dot=False)
    return value


def _check_relative_path(value: str, field_name: str) -> str:
    canonical = value.replace("\\", "/")
    if (
        not canonical
        or canonical.startswith("/")
        or re.match(_DRIVE_QUALIFIED_PATTERN, canonical) is not None
    ):
        raise ValueError(
            f"{field_name} must be project-relative with no drive or root qualifier, got {value!r}."
        )
    segments = canonical.split("/")
    if ".." in segments or "." in segments or "" in segments:
        raise ValueError(
            f"{field_name} must not contain empty, '.', or '..' segments, got {value!r}."
        )
    if any(ord(char) < 0x20 or ord(char) == 0x7F for char in canonical):
        raise ValueError(f"{field_name} must not contain control characters, got {value!r}.")
    for segment in segments:
        _check_portable_segment(segment, field_name, value, allow_leading_dot=True)
    return canonical


def validate_relative_path(value: str, field_name: str) -> str:
    """Public form of the portable project-relative path grammar.

    Other dataset records (transfers, locks) reuse this so one conservative
    cross-platform rule set decides what may be written inside a project.
    """

    return _check_relative_path(value, field_name)


def _check_hostname(hostname: str, field_name: str, value: str) -> str:
    """Validate a DNS hostname, or a documented literal IP address."""

    try:
        ipaddress.ip_address(hostname)
        return hostname
    except ValueError:
        pass
    labels = hostname.split(".")
    if not labels or len(hostname) > 253:
        raise ValueError(f"{field_name} names an invalid host, got {value!r}.")
    for label in labels:
        if (
            not label
            or len(label) > 63
            or label.startswith("-")
            or label.endswith("-")
            or any(not (char.isascii() and (char.isalnum() or char == "-")) for char in label)
        ):
            raise ValueError(f"{field_name} names an invalid host, got {value!r}.")
    return hostname


def _check_https_url(value: str, field_name: str) -> str:
    """Validate a persisted plain HTTPS link with no credential surface."""

    if not isinstance(value, str) or any(
        char.isspace() or ord(char) < 0x20 or ord(char) == 0x7F for char in value
    ):
        raise ValueError(f"{field_name} must not contain whitespace or controls.")
    try:
        parts = urllib.parse.urlsplit(value)
    except ValueError as exc:
        raise ValueError(f"{field_name} must be a valid HTTPS URL: {exc}.") from exc
    if parts.scheme != "https":
        raise ValueError(f"{field_name} must use the https scheme, got {value!r}.")
    if not parts.hostname:
        raise ValueError(f"{field_name} must name a valid host, got {value!r}.")
    try:
        parts.port
    except ValueError as exc:
        raise ValueError(f"{field_name} names an invalid port: {exc}.") from exc
    if parts.username is not None or parts.password is not None:
        raise ValueError(f"{field_name} must not embed userinfo or credentials, got {value!r}.")
    if parts.query or parts.fragment:
        raise ValueError(
            f"{field_name} must not carry query or fragment data such as "
            f"signed or credential-bearing URLs, got {value!r}."
        )
    _check_hostname(parts.hostname, field_name, value)
    return value


def _check_doi(value: str | None, field_name: str) -> str | None:
    if value is None:
        return None
    if (
        any(ord(char) < 0x20 or ord(char) == 0x7F for char in value)
        or re.fullmatch(_DOI_PATTERN, value) is None
    ):
        raise ValueError(
            f"{field_name} must be a DOI of the form '10.<registrant>/<suffix>' "
            f"with no whitespace or controls, got {value!r}."
        )
    return value


def _file_sort_key(item: Any) -> str:
    """Order key matching stored file normalization (raw dicts or models)."""

    path = item.get("path") if isinstance(item, dict) else getattr(item, "path", None)
    return path if isinstance(path, str) else ""


def _sort_raw_files(value: Any) -> Any:
    """Order file inputs canonically across lists, tuples, dicts, and models."""

    if isinstance(value, (list, tuple)):
        return sorted(value, key=_file_sort_key)
    return value


def _citation_sort_key(item: Any) -> tuple[str, str, str]:
    """Order key matching stored citation normalization (raw dicts or models)."""

    if isinstance(item, dict):
        title, doi, url = item.get("title"), item.get("doi"), item.get("url")
    else:
        title, doi, url = (
            getattr(item, "title", None),
            getattr(item, "doi", None),
            getattr(item, "url", None),
        )
    return (
        title.strip() if isinstance(title, str) else "",
        doi if isinstance(doi, str) else "",
        url if isinstance(url, str) else "",
    )


def _sort_raw_citations(value: Any) -> Any:
    """Order citation inputs canonically across lists, tuples, dicts, and models."""

    if isinstance(value, (list, tuple)):
        return sorted(value, key=_citation_sort_key)
    return value


def _sort_raw_strings(value: Any) -> Any:
    """Strip and order raw string collections canonically; pass anything else through."""

    if isinstance(value, (list, tuple)) and all(isinstance(item, str) for item in value):
        return sorted(item.strip() for item in value)
    return value


def _sorted_json_dump(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return sorted(items, key=lambda item: json_dumps_canonical(item))


def json_dumps_canonical(value: Any) -> str:
    """Stable string form for ordering identity-bearing mappings."""

    import json

    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def catalog_entry_identity(
    *,
    provider: str,
    dataset_id: str,
    snapshot: str,
    modality: str,
    task: str,
    participants: int,
    formats: list[str],
    approximate_total_bytes: int,
    expected_total_bytes: int,
    expected_files: list[dict[str, Any]],
    access: str,
    license_name: str,
    license_spdx: str | None,
    reuse_statement: str,
    citations: list[dict[str, Any]],
    landing_page: str,
    compatible_templates: list[str],
) -> str:
    """Identity for the dataset snapshot a catalog entry describes.

    Set-like inputs (formats, templates, citations, expected files) are
    sorted into canonical order, so enumeration order never changes the
    identity. Review workflow metadata (status, curator, review timestamp)
    is excluded by construction, so verifying an entry never changes the
    identity of the dataset it describes.
    """

    return content_identity(
        "dataset",
        {
            "schema_version": "1.0",
            "provider": provider,
            "dataset_id": dataset_id,
            "snapshot": snapshot,
            "modality": modality,
            "task": task,
            "participants": participants,
            "formats": sorted(formats),
            "approximate_total_bytes": approximate_total_bytes,
            "expected_total_bytes": expected_total_bytes,
            "expected_files": _sorted_json_dump([dict(item) for item in expected_files]),
            "access": access,
            "license_name": license_name,
            "license_spdx": license_spdx,
            "reuse_statement": reuse_statement,
            "citations": _sorted_json_dump([dict(item) for item in citations]),
            "landing_page": landing_page,
            "compatible_templates": sorted(compatible_templates),
        },
    )


def dataset_lock_identity(
    *,
    provider: str,
    dataset_id: str,
    snapshot: str,
    access: str,
    license_name: str | None,
    license_spdx: str | None,
    reuse_statement: str | None,
    title: str,
    modality: str,
    task: str,
    participants: int,
    formats: list[str],
    citations: list[dict[str, Any]],
    compatible_templates: list[str],
    landing_page: str | None,
    limitations: str,
    expected_total_bytes: int,
    expected_files: list[dict[str, Any]],
) -> str:
    """Identity for one immutable local retrieval claim.

    Only the retrieval contract enters the hash: what was requested, under
    which terms, the dataset context needed for offline reopening, and which
    exact bytes are expected. Retrieval time, local path, and the optional
    catalog link are excluded so the same snapshot verifies identically on
    another machine or project layout. File and citation order is canonical.
    """

    return content_identity(
        "dataset",
        {
            "schema_version": "1.0",
            "provider": provider,
            "dataset_id": dataset_id,
            "snapshot": snapshot,
            "access": access,
            "license_name": license_name,
            "license_spdx": license_spdx,
            "reuse_statement": reuse_statement,
            "title": title,
            "modality": modality,
            "task": task,
            "participants": participants,
            "formats": sorted(formats),
            "citations": _sorted_json_dump([dict(item) for item in citations]),
            "compatible_templates": sorted(compatible_templates),
            "landing_page": landing_page,
            "limitations": limitations,
            "expected_total_bytes": expected_total_bytes,
            "expected_files": _sorted_json_dump([dict(item) for item in expected_files]),
        },
    )


class DatasetCitation(BaseModel):
    """One immutable, strictly validated dataset citation."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    title: str = Field(min_length=1)
    doi: str | None = None
    url: str | None = None

    @field_validator("title", mode="before")
    @classmethod
    def _title_must_be_nonblank(cls, value: Any) -> Any:
        if isinstance(value, str):
            trimmed = value.strip()
            if trimmed == "":
                raise ValueError("Dataset citation titles must not be blank.")
            return trimmed
        return value

    @field_validator("doi")
    @classmethod
    def _doi_must_be_valid(cls, value: str | None) -> str | None:
        return _check_doi(value, "Dataset citation DOI")

    @field_validator("url")
    @classmethod
    def _url_must_be_https(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return _check_https_url(value, "Dataset citation URL")


class CatalogFile(BaseModel):
    """One file a catalog snapshot is expected to contain.

    Unlike a lock record, the provider may not publish checksums, so the
    hash stays optional here. Downloader units must treat a missing hash as
    unverified, never as verified.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    path: str = Field(min_length=1)
    byte_size: int = Field(ge=0)
    sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")

    @field_validator("path")
    @classmethod
    def _path_must_be_relative(cls, value: str) -> str:
        return _check_relative_path(value, "Expected file path")


class VerifiedFile(BaseModel):
    """One file a retrieved snapshot must contain, with its content hash."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    path: str = Field(min_length=1)
    byte_size: int = Field(ge=0)
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")

    @field_validator("path")
    @classmethod
    def _path_must_be_relative(cls, value: str) -> str:
        return _check_relative_path(value, "Expected file path")


class CatalogEntry(BaseModel):
    """One curated catalog record describing an immutable dataset snapshot."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal["1.0"] = DATASET_SCHEMA_VERSION
    catalog_identity: str = Field(pattern=r"^brainlearn-v1:dataset:[0-9a-f]{64}$")
    provider: str = Field(min_length=1, pattern=_PROVIDER_PATTERN)
    dataset_id: str = Field(min_length=1, pattern=_DATASET_ID_PATTERN)
    snapshot: str = Field(min_length=1)
    title: str = Field(min_length=1)
    modality: str = Field(min_length=1)
    task: str = ""
    participants: int = Field(ge=0, default=0)
    formats: tuple[str, ...] = ()
    approximate_total_bytes: int = Field(ge=0, default=0)
    expected_total_bytes: int = Field(gt=0)
    expected_files: tuple[CatalogFile, ...] = Field(min_length=1)
    access: DatasetAccess = DatasetAccess.PUBLIC
    license_name: str = Field(min_length=1)
    license_spdx: str | None = None
    reuse_statement: str = Field(min_length=1)
    citations: tuple[DatasetCitation, ...] = Field(min_length=1)
    landing_page: str = Field(min_length=1)
    compatible_templates: tuple[str, ...] = ()
    curator: str = ""
    review_status: DatasetReviewStatus = DatasetReviewStatus.PENDING
    reviewed_at: str | None = None
    limitations: str = Field(default="", max_length=_NOTES_MAX_LENGTH)

    @field_validator("provider", "dataset_id", "snapshot", mode="before")
    @classmethod
    def _components_must_be_path_safe(cls, value: Any) -> Any:
        if not isinstance(value, str):
            return value
        return _check_path_component(value, "Dataset identifier")

    @field_validator("title", "modality", "license_name", "reuse_statement")
    @classmethod
    def _descriptive_text_must_be_nonblank(cls, value: str, info: Any) -> str:
        return _stripped_text(value, info.field_name or "field", allow_empty=False)

    @field_validator("task", "curator", "limitations")
    @classmethod
    def _optional_text_must_not_be_blank(cls, value: str, info: Any) -> str:
        return _stripped_text(value, info.field_name or "field", allow_empty=True)

    @field_validator("formats", "compatible_templates", mode="before")
    @classmethod
    def _string_lists_must_be_canonical(cls, value: Any) -> Any:
        cleaned = _sort_raw_strings(value)
        if isinstance(cleaned, list) and any(item == "" for item in cleaned):
            raise ValueError("List entries must be non-blank strings.")
        return cleaned

    @field_validator("expected_files", mode="before")
    @classmethod
    def _files_must_be_canonical(cls, value: Any) -> Any:
        return _sort_raw_files(value)

    @field_validator("citations", mode="before")
    @classmethod
    def _citations_must_be_canonical(cls, value: Any) -> Any:
        return _sort_raw_citations(value)

    @field_validator("landing_page")
    @classmethod
    def _landing_page_must_be_https(cls, value: str) -> str:
        return _check_https_url(value, "Landing page")

    @field_validator("reviewed_at")
    @classmethod
    def _reviewed_at_must_be_iso(cls, value: str | None) -> str | None:
        return _require_iso_timestamp(value, "reviewed_at")

    @model_validator(mode="after")
    def _review_state_must_be_consistent(self) -> "CatalogEntry":
        if self.review_status == DatasetReviewStatus.VERIFIED:
            if self.reviewed_at is None:
                raise ValueError("A verified entry must record reviewed_at.")
            if self.curator.strip() == "":
                raise ValueError("A verified entry must name its curator.")
        if self.review_status == DatasetReviewStatus.PENDING and self.reviewed_at is not None:
            raise ValueError("A pending entry must not record reviewed_at.")
        return self

    @model_validator(mode="after")
    def _lists_must_be_unique_and_covered(self) -> "CatalogEntry":
        if not self.formats:
            raise ValueError("A catalog entry must name at least one format.")
        if len(set(self.formats)) != len(self.formats):
            raise ValueError("Catalog formats must be unique.")
        if len(set(self.compatible_templates)) != len(self.compatible_templates):
            raise ValueError("Compatible templates must be unique.")
        seen = {_stripped_citation_key(item) for item in self.citations}
        if len(seen) != len(self.citations):
            raise ValueError("Catalog citations must be unique.")
        paths = [item.path for item in self.expected_files]
        if len(set(paths)) != len(paths):
            raise ValueError("Catalog expected file paths must be unique.")
        if sum(item.byte_size for item in self.expected_files) > self.expected_total_bytes:
            raise ValueError("Catalog expected files must not sum past expected_total_bytes.")
        return self

    @model_validator(mode="after")
    def _identity_must_match_description(self) -> "CatalogEntry":
        expected = catalog_entry_identity(
            provider=self.provider,
            dataset_id=self.dataset_id,
            snapshot=self.snapshot,
            modality=self.modality,
            task=self.task,
            participants=self.participants,
            formats=list(self.formats),
            approximate_total_bytes=self.approximate_total_bytes,
            expected_total_bytes=self.expected_total_bytes,
            expected_files=[item.model_dump(mode="json") for item in self.expected_files],
            access=self.access.value,
            license_name=self.license_name,
            license_spdx=self.license_spdx,
            reuse_statement=self.reuse_statement,
            citations=[item.model_dump(mode="json") for item in self.citations],
            landing_page=self.landing_page,
            compatible_templates=list(self.compatible_templates),
        )
        if expected != self.catalog_identity:
            raise ValueError(
                "Catalog entry catalog_identity does not match its described "
                "dataset snapshot. Recompute the identity instead of reusing "
                "a stale one."
            )
        return self


class DatasetLock(BaseModel):
    """Immutable local claim that a snapshot was retrieved into a project."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal["1.0"] = DATASET_SCHEMA_VERSION
    dataset_identity: str = Field(pattern=r"^brainlearn-v1:dataset:[0-9a-f]{64}$")
    catalog_identity: str | None = Field(
        default=None, pattern=r"^brainlearn-v1:dataset:[0-9a-f]{64}$"
    )
    provider: str = Field(min_length=1, pattern=_PROVIDER_PATTERN)
    dataset_id: str = Field(min_length=1, pattern=_DATASET_ID_PATTERN)
    snapshot: str = Field(min_length=1)
    access: DatasetAccess = DatasetAccess.PUBLIC
    title: str = Field(min_length=1)
    modality: str = ""
    task: str = ""
    participants: int = Field(ge=0, default=0)
    formats: tuple[str, ...] = ()
    citations: tuple[DatasetCitation, ...] = ()
    compatible_templates: tuple[str, ...] = ()
    landing_page: str | None = None
    limitations: str = Field(default="", max_length=_NOTES_MAX_LENGTH)
    retrieved_at: str = Field(min_length=1)
    local_path: str = Field(min_length=1)
    expected_total_bytes: int = Field(ge=0)
    expected_files: tuple[VerifiedFile, ...] = Field(min_length=1)
    license_name: str | None = None
    license_spdx: str | None = None
    reuse_statement: str | None = None

    @field_validator("provider", "dataset_id", "snapshot", mode="before")
    @classmethod
    def _components_must_be_path_safe(cls, value: Any) -> Any:
        if not isinstance(value, str):
            return value
        return _check_path_component(value, "Dataset identifier")

    @field_validator("title")
    @classmethod
    def _title_must_be_nonblank(cls, value: str) -> str:
        return _stripped_text(value, "title", allow_empty=False)

    @field_validator("license_name", "reuse_statement")
    @classmethod
    def _optional_license_must_be_stripped(cls, value: str | None, info: Any) -> str | None:
        if value is None:
            return None
        return _stripped_text(value, info.field_name or "field", allow_empty=False)

    @field_validator("modality", "task", "limitations")
    @classmethod
    def _optional_text_must_not_be_blank(cls, value: str, info: Any) -> str:
        return _stripped_text(value, info.field_name or "field", allow_empty=True)

    @field_validator("formats", "compatible_templates", mode="before")
    @classmethod
    def _string_lists_must_be_canonical(cls, value: Any) -> Any:
        cleaned = _sort_raw_strings(value)
        if isinstance(cleaned, list) and any(item == "" for item in cleaned):
            raise ValueError("List entries must be non-blank strings.")
        return cleaned

    @field_validator("expected_files", mode="before")
    @classmethod
    def _files_must_be_canonical(cls, value: Any) -> Any:
        return _sort_raw_files(value)

    @field_validator("citations", mode="before")
    @classmethod
    def _citations_must_be_canonical(cls, value: Any) -> Any:
        return _sort_raw_citations(value)

    @field_validator("landing_page")
    @classmethod
    def _landing_page_must_be_https(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return _check_https_url(value, "Landing page")

    @field_validator("retrieved_at")
    @classmethod
    def _retrieved_at_must_be_iso(cls, value: str) -> str:
        checked = _require_iso_timestamp(value, "retrieved_at")
        assert checked is not None
        return checked

    @field_validator("local_path")
    @classmethod
    def _local_path_must_be_relative(cls, value: str) -> str:
        return _check_relative_path(value, "Local dataset path")

    @model_validator(mode="after")
    def _files_must_be_consistent(self) -> "DatasetLock":
        if self.provider != LOCAL_PROVIDER:
            if not self.formats:
                raise ValueError("A public dataset lock must name at least one format.")
            if not self.citations:
                raise ValueError("A public dataset lock must name at least one citation.")
            if self.landing_page is None:
                raise ValueError("A public dataset lock must record a landing page.")
            if self.license_name is None:
                raise ValueError("A public dataset lock must record license_name.")
            if self.reuse_statement is None:
                raise ValueError("A public dataset lock must record reuse_statement.")
            if not self.modality:
                raise ValueError("A public dataset lock must record modality.")
        if len(set(self.formats)) != len(self.formats):
            raise ValueError("Dataset lock formats must be unique.")
        seen = {_stripped_citation_key(item) for item in self.citations}
        if len(seen) != len(self.citations):
            raise ValueError("Dataset lock citations must be unique.")
        paths = [item.path for item in self.expected_files]
        if len(set(paths)) != len(paths):
            raise ValueError("Expected file paths must be unique within a lock record.")
        if sum(item.byte_size for item in self.expected_files) != self.expected_total_bytes:
            raise ValueError("Expected files must sum exactly to expected_total_bytes.")
        return self

    @model_validator(mode="after")
    def _identity_must_match_contract(self) -> "DatasetLock":
        expected = dataset_lock_identity(
            provider=self.provider,
            dataset_id=self.dataset_id,
            snapshot=self.snapshot,
            access=self.access.value,
            license_name=self.license_name,
            license_spdx=self.license_spdx,
            reuse_statement=self.reuse_statement,
            title=self.title,
            modality=self.modality,
            task=self.task,
            participants=self.participants,
            formats=list(self.formats),
            citations=[item.model_dump(mode="json") for item in self.citations],
            compatible_templates=list(self.compatible_templates),
            landing_page=self.landing_page,
            limitations=self.limitations,
            expected_total_bytes=self.expected_total_bytes,
            expected_files=[item.model_dump(mode="json") for item in self.expected_files],
        )
        if expected != self.dataset_identity:
            raise ValueError(
                "Dataset lock dataset_identity does not match its retrieval "
                "contract. Recompute the identity instead of reusing a stale one."
            )
        return self


def _stripped_citation_key(item: DatasetCitation) -> str:
    return json_dumps_canonical(item.model_dump(mode="json"))


def project_lock_from_catalog(
    entry: CatalogEntry,
    *,
    retrieved_at: str,
    local_path: str,
    expected_files: list[dict[str, Any]] | tuple[dict[str, Any] | VerifiedFile, ...],
    catalog_identity: str | None = None,
) -> DatasetLock:
    """Project a catalog entry into an offline lock record.

    Descriptive, license, citation, and compatibility context copy over so the
    lock stays self-contained when the catalog is absent or changes. The
    verified file list comes from the caller (a downloader passes what it
    actually verified) as raw dicts, tuples, or validated models in any order;
    files are validated and sorted canonically before identity calculation
    and construction, so equivalent projections serialize identically. The
    identity is recomputed from the projected record.
    """

    verified = sorted(
        (
            item if isinstance(item, VerifiedFile) else VerifiedFile.model_validate(item)
            for item in expected_files
        ),
        key=lambda item: item.path,
    )
    identity = dataset_lock_identity(
        provider=entry.provider,
        dataset_id=entry.dataset_id,
        snapshot=entry.snapshot,
        access=entry.access.value,
        license_name=entry.license_name,
        license_spdx=entry.license_spdx,
        reuse_statement=entry.reuse_statement,
        title=entry.title,
        modality=entry.modality,
        task=entry.task,
        participants=entry.participants,
        formats=list(entry.formats),
        citations=[item.model_dump(mode="json") for item in entry.citations],
        compatible_templates=list(entry.compatible_templates),
        landing_page=entry.landing_page,
        limitations=entry.limitations,
        expected_total_bytes=sum(item.byte_size for item in verified),
        expected_files=[item.model_dump(mode="json") for item in verified],
    )
    return DatasetLock(
        schema_version="1.0",
        dataset_identity=identity,
        catalog_identity=catalog_identity
        if catalog_identity is not None
        else entry.catalog_identity,
        provider=entry.provider,
        dataset_id=entry.dataset_id,
        snapshot=entry.snapshot,
        access=entry.access,
        title=entry.title,
        modality=entry.modality,
        task=entry.task,
        participants=entry.participants,
        formats=tuple(entry.formats),
        citations=tuple(entry.citations),
        compatible_templates=tuple(entry.compatible_templates),
        landing_page=entry.landing_page,
        limitations=entry.limitations,
        retrieved_at=retrieved_at,
        local_path=local_path,
        expected_total_bytes=sum(item.byte_size for item in verified),
        expected_files=tuple(verified),
        license_name=entry.license_name,
        license_spdx=entry.license_spdx,
        reuse_statement=entry.reuse_statement,
    )


def local_import_lock(
    *,
    dataset_id: str,
    title: str,
    limitations: str = "",
    citations: Sequence[dict[str, Any] | DatasetCitation] = (),
    formats: Sequence[str] = (),
    retrieved_at: str,
    local_path: str,
    verified_files: Sequence[dict[str, Any] | VerifiedFile],
) -> DatasetLock:
    """Build an immutable DatasetLock for a researcher-selected local directory.

    No CatalogEntry is required: the caller supplies explicit title,
    limitations, citations, and formats. The provider is always
    ``LOCAL_PROVIDER`` (``"local"``), the snapshot is always ``"local"``,
    and the access class is always ``RESTRICTED`` — the most conservative
    choice for private research data. Unassessed or absent metadata fields
    (modality, landing_page, license_name, reuse_statement) remain None or
    empty rather than inventing synthetic placeholder values.

    The ``dataset_id`` must be a single portable path component derived from
    the directory name (validated by the DatasetLock field validator).

    Identity is derived deterministically from the canonical verified file list,
    total bytes, and explicit user-declared metadata (title, limitations,
    formats, citations), excluding local path and scan timestamp.
    """

    clean_formats = tuple(sorted(set(f.strip() for f in formats if f.strip())))
    clean_citations = tuple(
        sorted(
            (
                c if isinstance(c, DatasetCitation) else DatasetCitation.model_validate(c)
                for c in citations
            ),
            key=_citation_sort_key,
        )
    )
    verified = sorted(
        (
            item if isinstance(item, VerifiedFile) else VerifiedFile.model_validate(item)
            for item in verified_files
        ),
        key=lambda item: item.path,
    )
    total_bytes = sum(item.byte_size for item in verified)
    clean_limitations = limitations.strip()
    clean_title = title.strip()
    identity = dataset_lock_identity(
        provider=LOCAL_PROVIDER,
        dataset_id=dataset_id,
        snapshot="local",
        access=DatasetAccess.RESTRICTED.value,
        license_name=None,
        license_spdx=None,
        reuse_statement=None,
        title=clean_title,
        modality="",
        task="",
        participants=0,
        formats=list(clean_formats),
        citations=[item.model_dump(mode="json") for item in clean_citations],
        compatible_templates=[],
        landing_page=None,
        limitations=clean_limitations,
        expected_total_bytes=total_bytes,
        expected_files=[item.model_dump(mode="json") for item in verified],
    )
    return DatasetLock(
        schema_version="1.0",
        dataset_identity=identity,
        catalog_identity=None,
        provider=LOCAL_PROVIDER,
        dataset_id=dataset_id,
        snapshot="local",
        access=DatasetAccess.RESTRICTED,
        title=clean_title,
        modality="",
        task="",
        participants=0,
        formats=clean_formats,
        citations=clean_citations,
        compatible_templates=(),
        landing_page=None,
        limitations=clean_limitations,
        retrieved_at=retrieved_at,
        local_path=local_path,
        expected_total_bytes=total_bytes,
        expected_files=tuple(verified),
        license_name=None,
        license_spdx=None,
        reuse_statement=None,
    )


class LocalImportFailure(BaseModel):
    """Structured failure record for a failed or interrupted local import."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    code: str = Field(min_length=1)
    message: str = Field(min_length=1)


class LocalImportRecord(BaseModel):
    """Persisted record of one local/private dataset import."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal["1.0"] = DATASET_SCHEMA_VERSION
    import_id: str = Field(pattern=r"^li-[0-9a-f]{12}$")
    state: Literal["scanning", "ready", "failed", "cancelled"]
    local_path: str = Field(min_length=1)
    lock: DatasetLock | None = None
    failure: LocalImportFailure | None = None
    created_at: str = Field(min_length=1)
    updated_at: str = Field(min_length=1)

    @field_validator("created_at", "updated_at")
    @classmethod
    def _timestamps_must_be_iso(cls, value: str, info: Any) -> str:
        checked = _require_iso_timestamp(value, info.field_name or "timestamp")
        assert checked is not None
        return checked

    @field_validator("local_path")
    @classmethod
    def _local_path_must_be_relative(cls, value: str) -> str:
        return _check_relative_path(value, "Local import path")

    @model_validator(mode="after")
    def _state_consistency(self) -> "LocalImportRecord":
        if self.state == "ready":
            if self.lock is None:
                raise ValueError("A ready local import record must include its DatasetLock.")
            if self.failure is not None:
                raise ValueError("A ready local import record must not record a failure.")
        elif self.state == "failed":
            if self.failure is None:
                raise ValueError("A failed local import record must describe its failure.")
            if self.lock is not None:
                raise ValueError("A failed local import record must not include a DatasetLock.")
        elif self.state in ("scanning", "cancelled"):
            if self.lock is not None:
                raise ValueError(
                    f"A {self.state} local import record cannot include a DatasetLock."
                )
        return self


def migrate_catalog_entry_dict(data: dict[str, Any]) -> dict[str, Any]:
    """Migrate a raw catalog entry dict to the current dataset schema."""

    version = data.get("schema_version")
    if version not in SUPPORTED_DATASET_VERSIONS:
        raise ValueError(
            f"Unsupported dataset schema_version {version!r}. "
            f"Supported versions: {', '.join(SUPPORTED_DATASET_VERSIONS)}. "
            "Open the catalog with a compatible BrainLearn release."
        )
    return data


def migrate_dataset_lock_dict(data: dict[str, Any]) -> dict[str, Any]:
    """Migrate a raw dataset lock dict to the current dataset schema."""

    version = data.get("schema_version")
    if version not in SUPPORTED_DATASET_VERSIONS:
        raise ValueError(
            f"Unsupported dataset schema_version {version!r}. "
            f"Supported versions: {', '.join(SUPPORTED_DATASET_VERSIONS)}. "
            "Open the lock record with a compatible BrainLearn release."
        )
    return data


def migrate_local_import_dict(data: dict[str, Any]) -> dict[str, Any]:
    """Migrate a raw local import record dict to the current dataset schema."""

    version = data.get("schema_version")
    if version not in SUPPORTED_DATASET_VERSIONS:
        raise ValueError(
            f"Unsupported local import schema_version {version!r}. "
            f"Supported versions: {', '.join(SUPPORTED_DATASET_VERSIONS)}. "
            "Open the import record with a compatible BrainLearn release."
        )
    return data
