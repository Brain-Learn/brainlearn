"""Download transfer records stay bound to their embedded catalog entry.

Every persisted transfer field that names the dataset, its size, or its
file set must equal the embedded catalog contract; otherwise a locally
corrupted record could fetch a different snapshot or verify a different
file set while retaining the original catalog identity.
"""

import hashlib
import json
from pathlib import Path
from typing import Any

import pytest
from brainlearn_core import (
    CatalogEntry,
    DownloadRecord,
    catalog_entry_identity,
    migrate_download_dict,
)

FIXTURES = Path(__file__).parent / "fixtures"


def _make_entry() -> CatalogEntry:
    blobs = {"b.bin": b"b" * 64, "a.bin": b"a" * 128, "sub/c.bin": b"c" * 32}
    expected = [
        {"path": path, "byte_size": len(blob), "sha256": hashlib.sha256(blob).hexdigest()}
        for path, blob in blobs.items()
    ]
    total = sum(item["byte_size"] for item in expected)
    identity = catalog_entry_identity(
        provider="mock-archive",
        dataset_id="zz20a",
        snapshot="2026-09-01",
        modality="EEG",
        task="rest",
        participants=1,
        formats=["BIDS"],
        approximate_total_bytes=total,
        expected_total_bytes=total,
        expected_files=expected,
        access="public",
        license_name="Mock public license",
        license_spdx=None,
        reuse_statement="Mock reuse statement.",
        citations=[{"title": "Mock citation.", "doi": None, "url": None}],
        landing_page="https://example.invalid/datasets/zz20a",
        compatible_templates=[],
    )
    return CatalogEntry.model_validate(
        {
            "schema_version": "1.0",
            "catalog_identity": identity,
            "provider": "mock-archive",
            "dataset_id": "zz20a",
            "snapshot": "2026-09-01",
            "title": "Mock zz20a",
            "modality": "EEG",
            "task": "rest",
            "participants": 1,
            "formats": ["BIDS"],
            "approximate_total_bytes": total,
            "expected_total_bytes": total,
            "expected_files": expected,
            "access": "public",
            "license_name": "Mock public license",
            "license_spdx": None,
            "reuse_statement": "Mock reuse statement.",
            "citations": [{"title": "Mock citation.", "doi": None, "url": None}],
            "landing_page": "https://example.invalid/datasets/zz20a",
            "compatible_templates": [],
            "curator": "",
            "review_status": "pending",
            "reviewed_at": None,
            "limitations": "Mock entry for download tests.",
        }
    )


def _make_record(entry: CatalogEntry | None = None) -> dict[str, Any]:
    entry = entry or _make_entry()
    return {
        "schema_version": "1.0",
        "download_id": "dl-0123456789ab",
        "provider": entry.provider,
        "dataset_id": entry.dataset_id,
        "snapshot": entry.snapshot,
        "catalog_identity": entry.catalog_identity,
        "catalog_entry": entry.model_dump(mode="json"),
        "expected_total_bytes": entry.expected_total_bytes,
        "files": [
            {
                "path": item.path,
                "byte_size": item.byte_size,
                "sha256": item.sha256,
                "bytes_completed": 0,
                "verified": False,
            }
            for item in entry.expected_files
        ],
        "bytes_completed": 0,
        "state": "queued",
        "attempt": 0,
        "max_attempts": 3,
        "created_at": "2026-09-14T00:00:00+00:00",
        "updated_at": "2026-09-14T00:00:00+00:00",
        "failure": None,
        "lock_identity": None,
    }


def test_fixture_record_binds_to_its_catalog() -> None:
    raw = json.loads((FIXTURES / "download-1.0.json").read_text(encoding="utf-8"))
    record = DownloadRecord.model_validate(migrate_download_dict(dict(raw)))
    assert record.catalog_identity == record.catalog_entry.catalog_identity
    assert [item.path for item in record.files] == sorted(
        item.path for item in record.catalog_entry.expected_files
    )


def test_valid_record_round_trips() -> None:
    record = DownloadRecord.model_validate(_make_record())
    assert DownloadRecord.model_validate_json(record.model_dump_json()) == record


@pytest.mark.parametrize(
    "field,value",
    [
        ("provider", "other-archive"),
        ("dataset_id", "zz99other"),
        ("snapshot", "1999-01-01"),
        ("expected_total_bytes", 999999),
        ("catalog_identity", "brainlearn-v1:dataset:" + "0" * 64),
    ],
)
def test_top_level_tamper_is_rejected(field: str, value: object) -> None:
    raw = _make_record()
    raw[field] = value
    with pytest.raises(ValueError, match="Transfer|transfer|catalog"):
        DownloadRecord.model_validate(raw)


def test_file_path_tamper_is_rejected() -> None:
    raw = _make_record()
    raw["files"][0]["path"] = "evil.bin"
    with pytest.raises(ValueError, match="canonical projection"):
        DownloadRecord.model_validate(raw)


def test_file_size_tamper_is_rejected() -> None:
    raw = _make_record()
    raw["files"][0]["byte_size"] += 1
    with pytest.raises(ValueError, match="canonical projection"):
        DownloadRecord.model_validate(raw)


def test_file_checksum_tamper_is_rejected() -> None:
    raw = _make_record()
    raw["files"][0]["sha256"] = "f" * 64
    with pytest.raises(ValueError, match="canonical projection"):
        DownloadRecord.model_validate(raw)


def test_reordered_files_canonicalize_identically() -> None:
    raw = _make_record()
    raw["files"] = list(reversed(raw["files"]))
    reordered = DownloadRecord.model_validate(raw)
    assert [item.path for item in reordered.files] == ["a.bin", "b.bin", "sub/c.bin"]
    expected = DownloadRecord.model_validate(_make_record()).model_dump_json()
    assert reordered.model_dump_json() == expected


def test_duplicate_files_are_rejected() -> None:
    raw = _make_record()
    raw["files"] = raw["files"] + [dict(raw["files"][0])]
    with pytest.raises(ValueError, match="canonical projection"):
        DownloadRecord.model_validate(raw)


def test_missing_file_is_rejected() -> None:
    raw = _make_record()
    raw["files"] = raw["files"][1:]
    with pytest.raises(ValueError, match="canonical projection"):
        DownloadRecord.model_validate(raw)


def test_tuple_and_model_inputs_are_accepted() -> None:
    raw = _make_record()
    record = DownloadRecord.model_validate(raw)
    via_tuple = DownloadRecord.model_validate({**raw, "files": tuple(raw["files"])})
    assert via_tuple == record
    via_models = DownloadRecord.model_validate({**raw, "files": tuple(record.files)})
    assert via_models == record


def test_non_portable_file_path_is_rejected() -> None:
    raw = _make_record()
    raw["files"][0]["path"] = "../escape.bin"
    with pytest.raises(ValueError, match=r"segments|portable|canonical projection"):
        DownloadRecord.model_validate(raw)


def test_embedded_catalog_tamper_breaks_the_binding() -> None:
    raw = _make_record()
    raw["catalog_entry"]["modality"] = "MEG"
    with pytest.raises(ValueError, match="catalog_identity|does not match"):
        DownloadRecord.model_validate(raw)
