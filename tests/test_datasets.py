"""Step 5A.1: versioned curated-dataset contract, no networking."""

import json
from pathlib import Path
from typing import Any

import pytest
from brainlearn_core import (
    CatalogEntry,
    DatasetLock,
    catalog_entry_identity,
    dataset_lock_identity,
    migrate_catalog_entry_dict,
    migrate_dataset_lock_dict,
    project_lock_from_catalog,
)
from brainlearn_core.datasets import CatalogFile, DatasetCitation, VerifiedFile
from pydantic import ValidationError

FIXTURES = Path(__file__).parent / "fixtures"


def _catalog_raw() -> list[dict[str, Any]]:
    return json.loads((FIXTURES / "dataset-catalog-1.0.json").read_text(encoding="utf-8"))


def _lock_raw() -> dict[str, Any]:
    return json.loads((FIXTURES / "dataset-lock-1.0.json").read_text(encoding="utf-8"))


def _normalize_citation(item: Any) -> Any:
    """Best-effort canonical form; hostile values pass through to validation."""
    try:
        return DatasetCitation.model_validate(item).model_dump(mode="json")
    except ValidationError:
        return dict(item) if isinstance(item, dict) else item


def _normalize_catalog_file(item: Any) -> Any:
    """Best-effort canonical form; hostile values pass through to validation."""
    try:
        return CatalogFile.model_validate(item).model_dump(mode="json")
    except ValidationError:
        return dict(item) if isinstance(item, dict) else item


def _normalize_verified_file(item: Any) -> Any:
    """Best-effort canonical form; hostile values pass through to validation."""
    try:
        return VerifiedFile.model_validate(item).model_dump(mode="json")
    except ValidationError:
        return dict(item) if isinstance(item, dict) else item


def _reidentify_catalog(entry: dict[str, Any]) -> dict[str, Any]:
    """Recompute a catalog dict's identity so probes test validation, not staleness."""
    recalculated = dict(entry)
    recalculated["catalog_identity"] = catalog_entry_identity(
        provider=entry["provider"],
        dataset_id=entry["dataset_id"],
        snapshot=entry["snapshot"],
        modality=entry["modality"],
        task=entry["task"],
        participants=entry["participants"],
        formats=entry["formats"],
        approximate_total_bytes=entry["approximate_total_bytes"],
        expected_total_bytes=entry["expected_total_bytes"],
        expected_files=[_normalize_catalog_file(item) for item in entry["expected_files"]],
        access=entry["access"],
        license_name=entry["license_name"],
        license_spdx=entry["license_spdx"],
        reuse_statement=entry["reuse_statement"],
        citations=[_normalize_citation(item) for item in entry["citations"]],
        landing_page=entry["landing_page"],
        compatible_templates=entry["compatible_templates"],
    )
    return recalculated


def _reidentify_lock(lock: dict[str, Any]) -> dict[str, Any]:
    """Recompute a lock dict's identity so probes test validation, not staleness."""
    recalculated = dict(lock)
    recalculated["dataset_identity"] = dataset_lock_identity(
        provider=lock["provider"],
        dataset_id=lock["dataset_id"],
        snapshot=lock["snapshot"],
        access=lock["access"],
        license_name=lock["license_name"],
        license_spdx=lock["license_spdx"],
        reuse_statement=lock["reuse_statement"],
        title=lock["title"],
        modality=lock["modality"],
        task=lock["task"],
        participants=lock["participants"],
        formats=lock["formats"],
        citations=[_normalize_citation(item) for item in lock["citations"]],
        compatible_templates=lock["compatible_templates"],
        landing_page=lock["landing_page"],
        limitations=lock["limitations"],
        expected_total_bytes=lock["expected_total_bytes"],
        expected_files=[_normalize_verified_file(item) for item in lock["expected_files"]],
    )
    return recalculated


def test_catalog_fixture_migrates_round_trips_and_stays_pending() -> None:
    raw = _catalog_raw()
    assert len(raw) == 2
    entries = [CatalogEntry.model_validate(migrate_catalog_entry_dict(dict(item))) for item in raw]
    for entry, original in zip(entries, raw):
        assert entry.schema_version == "1.0"
        assert entry.review_status == "pending"
        assert entry.reviewed_at is None
        assert "unverified" in entry.limitations or "not a real dataset" in entry.limitations
        assert CatalogEntry.model_validate_json(entry.model_dump_json()) == entry
        assert original["catalog_identity"] == entry.catalog_identity
    assert {entry.dataset_id for entry in entries} == {
        "zz00synthetic-eeg",
        "zz01restricted-eeg",
    }
    by_id = {entry.dataset_id: entry for entry in entries}
    assert "zz01restricted-eeg" in by_id["zz01restricted-eeg"].landing_page
    assert by_id["zz01restricted-eeg"].access == "restricted"
    assert list(entries[0].formats) == ["BIDS"]


def test_lock_fixture_migrates_round_trips_and_recomputes() -> None:
    raw = _lock_raw()
    lock = DatasetLock.model_validate(migrate_dataset_lock_dict(dict(raw)))
    assert lock.schema_version == "1.0"
    assert DatasetLock.model_validate_json(lock.model_dump_json()) == lock
    assert lock.dataset_identity == dataset_lock_identity(
        provider=lock.provider,
        dataset_id=lock.dataset_id,
        snapshot=lock.snapshot,
        access=lock.access.value,
        license_name=lock.license_name,
        license_spdx=lock.license_spdx,
        reuse_statement=lock.reuse_statement,
        title=lock.title,
        modality=lock.modality,
        task=lock.task,
        participants=lock.participants,
        formats=list(lock.formats),
        citations=[item.model_dump(mode="json") for item in lock.citations],
        compatible_templates=list(lock.compatible_templates),
        landing_page=lock.landing_page,
        limitations=lock.limitations,
        expected_total_bytes=lock.expected_total_bytes,
        expected_files=[item.model_dump(mode="json") for item in lock.expected_files],
    )
    assert lock.catalog_identity == _catalog_raw()[0]["catalog_identity"]
    assert lock.title and lock.modality and lock.formats and lock.citations
    assert lock.landing_page and lock.task is not None


def test_models_are_deeply_immutable() -> None:
    entry = CatalogEntry.model_validate(_catalog_raw()[0])
    lock = DatasetLock.model_validate(_lock_raw())
    with pytest.raises(AttributeError):
        entry.formats.append("MEG")  # type: ignore[attr-defined]
    with pytest.raises(AttributeError):
        lock.expected_files.append(lock.expected_files[0])  # type: ignore[attr-defined]
    with pytest.raises(ValidationError, match="frozen"):
        entry.citations[0].title = "mutated-after-validation"
    with pytest.raises(ValidationError, match="frozen"):
        lock.snapshot = "mutated-after-validation"  # type: ignore[misc]
    assert entry.snapshot == _catalog_raw()[0]["snapshot"]
    assert lock.snapshot == _lock_raw()["snapshot"]


def test_reordered_inputs_dump_identically() -> None:
    base = _catalog_raw()[0]
    reordered = dict(
        base,
        formats=list(reversed(base["formats"])),
        expected_files=list(reversed(base["expected_files"])),
        citations=list(reversed(base["citations"])),
    )
    first = CatalogEntry.model_validate(_reidentify_catalog(dict(base)))
    second = CatalogEntry.model_validate(_reidentify_catalog(reordered))
    assert first.model_dump_json() == second.model_dump_json()
    assert first.catalog_identity == second.catalog_identity == base["catalog_identity"]
    lock_base = _lock_raw()
    lock_reordered = dict(
        lock_base,
        expected_files=list(reversed(lock_base["expected_files"])),
        formats=list(reversed(lock_base["formats"])),
    )
    lock_first = DatasetLock.model_validate(_reidentify_lock(dict(lock_base)))
    lock_second = DatasetLock.model_validate(_reidentify_lock(lock_reordered))
    assert lock_first.model_dump_json() == lock_second.model_dump_json()
    assert lock_first.dataset_identity == lock_second.dataset_identity


@pytest.mark.parametrize("version", ["9.9", "2.0", "", None])
def test_migrations_reject_unsupported_versions(version: object) -> None:
    with pytest.raises(ValueError, match="Unsupported dataset schema_version"):
        migrate_catalog_entry_dict({"schema_version": version})
    with pytest.raises(ValueError, match="Unsupported dataset schema_version"):
        migrate_dataset_lock_dict({"schema_version": version})


@pytest.mark.parametrize(
    "field,value",
    [
        ("provider", "Has Space"),
        ("provider", "UPPER"),
        ("provider", ""),
        ("provider", ".."),
        ("provider", "a/b"),
        ("dataset_id", "has space"),
        ("dataset_id", ""),
        ("dataset_id", ".."),
        ("dataset_id", "."),
        ("dataset_id", "a/b"),
        ("snapshot", "../escape"),
        ("snapshot", "has space"),
        ("snapshot", "."),
        ("snapshot", ".."),
        ("snapshot", ""),
        ("snapshot", "a/b"),
        ("snapshot", "C:"),
        ("snapshot", "snapshot."),
        ("snapshot", " snapshot"),
        ("snapshot", "CON"),
        ("snapshot", "com1.txt"),
        ("license_name", ""),
        ("license_name", "   "),
        ("reuse_statement", ""),
        ("title", "   "),
        ("modality", "   "),
    ],
)
def test_catalog_rejects_malformed_fields(field: str, value: str) -> None:
    raw = _reidentify_catalog(dict(_catalog_raw()[0], **{field: value}))
    with pytest.raises(ValidationError):
        CatalogEntry.model_validate(raw)


@pytest.mark.parametrize(
    "url",
    [
        "http://example.invalid/x",
        "ftp://example.invalid/x",
        "https://user:pass@example.invalid/x",
        "https://user@example.invalid/x",
        "https://example.invalid/x?X-Amz-Signature=secret",
        "https://example.invalid/x?token=abc",
        "https://example.invalid/x#section",
        "https://example.invalid:99999/x",
        "https://example.invalid/white space",
        "https://bad_host.invalid/x",
        "https://-leading.invalid/x",
        "https://trailing-.invalid/x",
        "https://",
        "",
    ],
)
def test_catalog_rejects_hostile_landing_urls(url: str) -> None:
    raw = _reidentify_catalog(dict(_catalog_raw()[0], landing_page=url))
    with pytest.raises(ValidationError):
        CatalogEntry.model_validate(raw)


@pytest.mark.parametrize("url", ["https://example.invalid/x", "https://[::1]:8443/x"])
def test_catalog_accepts_valid_landing_urls(url: str) -> None:
    raw = _reidentify_catalog(dict(_catalog_raw()[0], landing_page=url))
    assert CatalogEntry.model_validate(raw).landing_page == url


@pytest.mark.parametrize(
    "citation",
    [
        {"title": "Has creds", "url": "https://user:pass@example.invalid/x"},
        {"title": "Has creds", "url": "https://example.invalid/x?token=secret"},
        {"title": "Has creds", "url": "https://example.invalid/x#frag"},
        {"title": "Bad DOI", "doi": "not-a-doi"},
        {"title": "Bad DOI", "doi": "10.1234/has space"},
        {"title": "Bad DOI", "doi": "10.1234/trailing\n"},
        {"title": "   "},
    ],
)
def test_catalog_rejects_hostile_nested_citations(citation: dict[str, Any]) -> None:
    raw = _reidentify_catalog(dict(_catalog_raw()[0], citations=[citation]))
    with pytest.raises(ValidationError):
        CatalogEntry.model_validate(raw)


def test_catalog_accepts_valid_doi_and_https_citation() -> None:
    raw = _reidentify_catalog(
        dict(
            _catalog_raw()[0],
            citations=[{"title": "Real shape", "doi": "10.18112/openneuro.ds000001.v1.0.0"}],
        )
    )
    assert CatalogEntry.model_validate(raw).citations[0].doi is not None


def test_catalog_stores_normalized_citations_before_dedup() -> None:
    base = _catalog_raw()[0]
    duplicate = dict(base["citations"][0])
    duplicate["title"] = f"  {duplicate['title']}  "
    raw = _reidentify_catalog(dict(base, citations=[base["citations"][0], duplicate]))
    with pytest.raises(ValidationError, match="unique"):
        CatalogEntry.model_validate(raw)
    normalized = _reidentify_catalog(dict(base, citations=[duplicate]))
    assert CatalogEntry.model_validate(normalized).citations[0].title == duplicate["title"].strip()


@pytest.mark.parametrize("field,value", [("participants", -1), ("approximate_total_bytes", -1)])
def test_catalog_rejects_negative_counts(field: str, value: int) -> None:
    raw = dict(_catalog_raw()[0])
    raw[field] = value
    with pytest.raises(ValidationError):
        CatalogEntry.model_validate(raw)


def test_catalog_canonical_list_rules() -> None:
    base = _catalog_raw()[0]
    assert list(CatalogEntry.model_validate(_reidentify_catalog(dict(base))).formats) == ["BIDS"]
    with pytest.raises(ValidationError):
        CatalogEntry.model_validate(_reidentify_catalog(dict(base, formats=[])))
    with pytest.raises(ValidationError):
        CatalogEntry.model_validate(_reidentify_catalog(dict(base, formats=["BIDS", "BIDS"])))
    with pytest.raises(ValidationError):
        CatalogEntry.model_validate(_reidentify_catalog(dict(base, formats=["BIDS", "  "])))
    with pytest.raises(ValidationError):
        CatalogEntry.model_validate(_reidentify_catalog(dict(base, citations=[])))
    with pytest.raises(ValidationError):
        CatalogEntry.model_validate(
            _reidentify_catalog(dict(base, citations=[base["citations"][0], base["citations"][0]]))
        )


def test_catalog_requires_nonempty_expectations() -> None:
    base = _catalog_raw()[0]
    assert CatalogEntry.model_validate(_reidentify_catalog(dict(base)))
    with pytest.raises(ValidationError):
        CatalogEntry.model_validate(_reidentify_catalog(dict(base, expected_files=[])))
    with pytest.raises(ValidationError, match="expected_total_bytes"):
        CatalogEntry.model_validate(_reidentify_catalog(dict(base, expected_total_bytes=10)))
    with pytest.raises(ValidationError, match="expected_total_bytes"):
        CatalogEntry.model_validate(_reidentify_catalog(dict(base, expected_total_bytes=0)))


@pytest.mark.parametrize("path", ["a/b\revil.txt", "a/b\nvil.txt"])
def test_catalog_file_paths_reject_control_characters(path: str) -> None:
    base = _catalog_raw()[0]
    files = [dict(base["expected_files"][0], path=path), base["expected_files"][1]]
    total = sum(item["byte_size"] for item in files)
    raw = _reidentify_catalog(dict(base, expected_files=files, expected_total_bytes=total))
    with pytest.raises(ValidationError, match="control characters"):
        CatalogEntry.model_validate(raw)


def test_catalog_review_state_must_be_consistent() -> None:
    verified_without_date = dict(_catalog_raw()[0], review_status="verified")
    with pytest.raises(ValidationError, match="reviewed_at"):
        CatalogEntry.model_validate(_reidentify_catalog(verified_without_date))
    pending_with_date = dict(_catalog_raw()[0], reviewed_at="2026-09-14T00:00:00+00:00")
    with pytest.raises(ValidationError, match="reviewed_at"):
        CatalogEntry.model_validate(pending_with_date)
    verified_without_curator = dict(
        _catalog_raw()[0],
        review_status="verified",
        reviewed_at="2026-09-14T00:00:00+00:00",
    )
    with pytest.raises(ValidationError, match="curator"):
        CatalogEntry.model_validate(_reidentify_catalog(verified_without_curator))
    verified = dict(
        _catalog_raw()[0],
        review_status="verified",
        curator="contract-test",
        reviewed_at="2026-09-14T00:00:00+00:00",
    )
    assert CatalogEntry.model_validate(_reidentify_catalog(verified)).review_status == "verified"


@pytest.mark.parametrize(
    "key",
    [
        "token",
        "access_token",
        "password",
        "cookie",
        "cookies",
        "authorization",
        "signature",
        "signed_url",
        "secret",
        "api_key",
        "session",
    ],
)
def test_contract_never_persists_credentials(key: str) -> None:
    catalog_raw = dict(_catalog_raw()[0], **{key: "hunter2"})
    with pytest.raises(ValidationError):
        CatalogEntry.model_validate(catalog_raw)
    lock_raw = dict(_lock_raw(), **{key: "hunter2"})
    with pytest.raises(ValidationError):
        DatasetLock.model_validate(lock_raw)


@pytest.mark.parametrize(
    "field,value",
    [
        ("local_path", "/absolute/path"),
        ("local_path", "../escape"),
        ("local_path", "a/../b"),
        ("local_path", ""),
        ("retrieved_at", "2026-09-14 00:00:00"),
        ("retrieved_at", "not-a-timestamp"),
        ("expected_total_bytes", -1),
        ("snapshot", "../escape"),
        ("snapshot", "C:"),
        ("provider", ".."),
        ("title", "   "),
        ("landing_page", "https://example.invalid/x?sig=abc"),
    ],
)
def test_lock_rejects_malformed_fields(field: str, value: object) -> None:
    raw = _reidentify_lock(dict(_lock_raw(), **{field: value}))
    with pytest.raises(ValidationError):
        DatasetLock.model_validate(raw)


def test_lock_requires_nonempty_hashed_files_with_exact_total() -> None:
    base = _lock_raw()
    with pytest.raises(ValidationError):
        DatasetLock.model_validate(_reidentify_lock(dict(base, expected_files=[])))
    unhashed = dict(base)
    unhashed["expected_files"] = [dict(base["expected_files"][0])]
    del unhashed["expected_files"][0]["sha256"]
    with pytest.raises(ValidationError):
        DatasetLock.model_validate(_reidentify_lock(unhashed))
    slack = dict(base, expected_total_bytes=base["expected_total_bytes"] + 1)
    with pytest.raises(ValidationError, match="exactly"):
        DatasetLock.model_validate(_reidentify_lock(slack))


def test_lock_is_frozen_after_validation() -> None:
    lock = DatasetLock.model_validate(_lock_raw())
    with pytest.raises(ValidationError, match="frozen"):
        lock.snapshot = "mutated-after-validation"  # type: ignore[misc]
    assert lock.snapshot == _lock_raw()["snapshot"]


def test_lock_ordering_is_identity_invariant() -> None:
    base = _lock_raw()
    assert DatasetLock.model_validate(_reidentify_lock(dict(base)))
    reordered = dict(
        base,
        expected_files=list(reversed(base["expected_files"])),
        formats=list(reversed(base["formats"])),
    )
    assert (
        DatasetLock.model_validate(_reidentify_lock(reordered)).dataset_identity
        == (base["dataset_identity"])
    )


def test_catalog_ordering_is_identity_invariant() -> None:
    base = _catalog_raw()[0]
    assert CatalogEntry.model_validate(_reidentify_catalog(dict(base)))
    reordered = dict(
        base,
        expected_files=list(reversed(base["expected_files"])),
        formats=list(reversed(base["formats"])),
    )
    assert (
        CatalogEntry.model_validate(_reidentify_catalog(reordered)).catalog_identity
        == (base["catalog_identity"])
    )


def test_stale_identities_are_rejected() -> None:
    catalog_raw = dict(_catalog_raw()[0], snapshot="2026-09-02")
    with pytest.raises(ValidationError, match="catalog_identity"):
        CatalogEntry.model_validate(catalog_raw)
    lock_raw = dict(_lock_raw(), snapshot="2026-09-02")
    with pytest.raises(ValidationError, match="dataset_identity"):
        DatasetLock.model_validate(lock_raw)


def test_catalog_identity_ignores_review_workflow_but_binds_description() -> None:
    base = _catalog_raw()[0]
    identity = base["catalog_identity"]
    review_only = dict(
        base,
        review_status="verified",
        curator="reviewer",
        reviewed_at="2026-09-14T00:00:00+00:00",
    )
    assert (
        CatalogEntry.model_validate(_reidentify_catalog(review_only)).catalog_identity == identity
    )
    retitled = dict(base, title="Different display title.")
    assert CatalogEntry.model_validate(_reidentify_catalog(retitled)).catalog_identity == identity
    for field, value in [
        ("snapshot", "2026-09-02"),
        ("reuse_statement", "Different terms."),
        ("license_name", "Different license."),
        ("modality", "MEG"),
        ("landing_page", "https://example.invalid/other"),
        ("task", "different"),
        ("participants", 99),
    ]:
        rebound = _reidentify_catalog(dict(base, **{field: value}))
        assert rebound["catalog_identity"] != identity
        assert CatalogEntry.model_validate(rebound).catalog_identity != identity


@pytest.mark.parametrize(
    "field,value",
    [
        ("title", "Projected title"),
        ("modality", "MEG"),
        ("task", "projected"),
        ("participants", 99),
        ("landing_page", "https://example.invalid/projected"),
        ("limitations", "Projected limits."),
        ("snapshot", "2026-09-02"),
        ("reuse_statement", "Different terms."),
    ],
)
def test_lock_stale_identity_per_field(field: str, value: object) -> None:
    base = _lock_raw()
    mutated = dict(base, **{field: value})
    with pytest.raises(ValidationError, match="dataset_identity"):
        DatasetLock.model_validate(mutated)


def test_lock_identity_ignores_retrieval_details_but_binds_contract() -> None:
    base = _lock_raw()
    identity = base["dataset_identity"]
    relocated = dict(base, retrieved_at="2027-01-01T00:00:00+00:00", local_path="elsewhere/copy")
    assert DatasetLock.model_validate(_reidentify_lock(relocated)).dataset_identity == identity
    assert (
        DatasetLock.model_validate(
            _reidentify_lock(dict(base, catalog_identity=None))
        ).dataset_identity
        == identity
    )
    for field, value in [
        ("snapshot", "2026-09-02"),
        ("reuse_statement", "Different terms."),
        ("title", "Different title."),
        ("modality", "MEG"),
        ("license_name", "Different license."),
        ("landing_page", "https://example.invalid/other"),
        ("limitations", "Different limits."),
    ]:
        rebound = _reidentify_lock(dict(base, **{field: value}))
        assert rebound["dataset_identity"] != identity
        assert DatasetLock.model_validate(rebound).dataset_identity != identity


def test_catalog_to_lock_projection_preserves_provenance() -> None:

    entry = CatalogEntry.model_validate(_catalog_raw()[0])
    lock = project_lock_from_catalog(
        entry,
        retrieved_at="2026-09-15T00:00:00+00:00",
        local_path="datasets/synthetic-archive/zz00synthetic-eeg/2026-09-01",
        expected_files=[
            {
                "path": "sub-01/eeg/sub-01_task-rest_eeg.edf",
                "byte_size": 131072,
                "sha256": "a" * 64,
            },
            {"path": "dataset_description.json", "byte_size": 512, "sha256": "b" * 64},
        ],
    )
    for field in (
        "provider",
        "dataset_id",
        "snapshot",
        "access",
        "title",
        "modality",
        "license_name",
        "license_spdx",
        "reuse_statement",
        "landing_page",
        "limitations",
    ):
        assert getattr(lock, field) == getattr(entry, field)
    assert list(lock.formats) == list(entry.formats)
    assert [c.model_dump(mode="json") for c in lock.citations] == [
        c.model_dump(mode="json") for c in entry.citations
    ]
    assert list(lock.compatible_templates) == list(entry.compatible_templates)
    assert lock.catalog_identity == entry.catalog_identity
    assert lock.expected_total_bytes == 131584
    assert sum(item.byte_size for item in lock.expected_files) == 131584


def test_identity_helpers_are_deterministic() -> None:
    base = _catalog_raw()[0]
    citations = [
        DatasetCitation.model_validate(item).model_dump(mode="json") for item in base["citations"]
    ]
    files = [
        CatalogFile.model_validate(item).model_dump(mode="json") for item in base["expected_files"]
    ]
    kwargs = dict(
        provider=base["provider"],
        dataset_id=base["dataset_id"],
        snapshot=base["snapshot"],
        modality=base["modality"],
        task=base["task"],
        participants=base["participants"],
        formats=base["formats"],
        approximate_total_bytes=base["approximate_total_bytes"],
        expected_total_bytes=base["expected_total_bytes"],
        expected_files=files,
        access=base["access"],
        license_name=base["license_name"],
        license_spdx=base["license_spdx"],
        reuse_statement=base["reuse_statement"],
        citations=citations,
        landing_page=base["landing_page"],
        compatible_templates=base["compatible_templates"],
    )
    assert catalog_entry_identity(**kwargs) == base["catalog_identity"]
    assert catalog_entry_identity(**kwargs) == catalog_entry_identity(**kwargs)


def test_projection_canonicalizes_reversed_inputs() -> None:
    from brainlearn_core.datasets import VerifiedFile

    entry = CatalogEntry.model_validate(_catalog_raw()[0])
    forward = [
        {
            "path": "sub-01/eeg/sub-01_task-rest_eeg.edf",
            "byte_size": 131072,
            "sha256": "a" * 64,
        },
        {"path": "dataset_description.json", "byte_size": 512, "sha256": "b" * 64},
    ]
    kwargs: dict[str, Any] = dict(
        retrieved_at="2026-09-15T00:00:00+00:00",
        local_path="datasets/synthetic-archive/zz00synthetic-eeg/2026-09-01",
    )
    from_dicts = project_lock_from_catalog(entry, expected_files=forward, **kwargs)
    from_reversed_dicts = project_lock_from_catalog(
        entry, expected_files=list(reversed(forward)), **kwargs
    )
    assert from_reversed_dicts.dataset_identity == from_dicts.dataset_identity
    assert from_reversed_dicts.model_dump_json() == from_dicts.model_dump_json()
    models = tuple(VerifiedFile.model_validate(item) for item in reversed(forward))
    from_models = project_lock_from_catalog(entry, expected_files=models, **kwargs)
    assert from_models.dataset_identity == from_dicts.dataset_identity
    assert from_models.model_dump_json() == from_dicts.model_dump_json()
    assert [item.path for item in from_dicts.expected_files] == [
        "dataset_description.json",
        "sub-01/eeg/sub-01_task-rest_eeg.edf",
    ]


def _catalog_with_files(base: dict[str, Any], files: list[dict[str, Any]]) -> dict[str, Any]:
    total = sum(item["byte_size"] for item in files)
    return _reidentify_catalog(dict(base, expected_files=files, expected_total_bytes=total))


@pytest.mark.parametrize("value", ["a:b", "a<b", "a|b", 'a"b', "a?b", "a*b"])
def test_components_reject_portable_forbidden_characters(value: str) -> None:
    with pytest.raises(ValidationError, match="portable"):
        CatalogEntry.model_validate(_reidentify_catalog(dict(_catalog_raw()[0], snapshot=value)))
    with pytest.raises(ValidationError, match="portable"):
        CatalogEntry.model_validate(_reidentify_catalog(dict(_catalog_raw()[0], dataset_id=value)))


@pytest.mark.parametrize(
    "path",
    [
        "folder/AUX.txt",
        "folder/NUL.edf",
        "folder/name?.edf",
        "folder/trailing.",
        "folder/name:stream",
        "folder/ lead.txt",
        "folder/com1.txt",
        "folder/a*b.bin",
    ],
)
def test_relative_paths_reject_nonportable_segments(path: str) -> None:
    base = _catalog_raw()[0]
    files = [dict(base["expected_files"][0], path=path), base["expected_files"][1]]
    with pytest.raises(ValidationError, match="portable|reserved|trailing|leading"):
        CatalogEntry.model_validate(_catalog_with_files(base, files))
    lock_base = _lock_raw()
    lock_files = [dict(lock_base["expected_files"][0], path=path), lock_base["expected_files"][1]]
    with pytest.raises(ValidationError, match="portable|reserved|trailing|leading"):
        DatasetLock.model_validate(_reidentify_lock(dict(lock_base, expected_files=lock_files)))


def test_relative_paths_accept_leading_dotfiles() -> None:
    base = _catalog_raw()[0]
    files = [dict(base["expected_files"][0], path="folder/.hidden-ok.txt")]
    assert CatalogEntry.model_validate(_catalog_with_files(base, files))


def test_lock_local_path_rejects_nonportable_segments() -> None:
    raw = _reidentify_lock(dict(_lock_raw(), local_path="datasets/bad /x"))
    with pytest.raises(ValidationError, match="trailing|leading"):
        DatasetLock.model_validate(raw)


@pytest.mark.parametrize("doi", ["10.1234/\x00abc", "10.1234/abc\x7f", "10.1234/\x01"])
def test_dois_reject_controls(doi: str) -> None:
    raw = _reidentify_catalog(
        dict(_catalog_raw()[0], citations=[{"title": "Controls", "doi": doi}])
    )
    with pytest.raises(ValidationError, match="whitespace or controls"):
        CatalogEntry.model_validate(raw)


def test_direct_model_tuples_canonicalize() -> None:
    from brainlearn_core import dataset_lock_identity
    from brainlearn_core.datasets import DatasetCitation, VerifiedFile

    base = _lock_raw()
    models = tuple(VerifiedFile.model_validate(item) for item in base["expected_files"])
    assert len(models) > 1
    citations = [
        DatasetCitation.model_validate(item).model_dump(mode="json") for item in base["citations"]
    ]

    def _identify(files: tuple[VerifiedFile, ...]) -> str:
        return dataset_lock_identity(
            provider=base["provider"],
            dataset_id=base["dataset_id"],
            snapshot=base["snapshot"],
            access=base["access"],
            license_name=base["license_name"],
            license_spdx=base["license_spdx"],
            reuse_statement=base["reuse_statement"],
            title=base["title"],
            modality=base["modality"],
            task=base["task"],
            participants=base["participants"],
            formats=base["formats"],
            citations=citations,
            compatible_templates=base["compatible_templates"],
            landing_page=base["landing_page"],
            limitations=base["limitations"],
            expected_total_bytes=base["expected_total_bytes"],
            expected_files=[item.model_dump(mode="json") for item in files],
        )

    forward = DatasetLock.model_validate(
        dict(
            base,
            expected_files=models,
            dataset_identity=_identify(models),
        )
    )
    reversed_models = DatasetLock.model_validate(
        dict(
            base,
            expected_files=tuple(reversed(models)),
            dataset_identity=_identify(tuple(reversed(models))),
        )
    )
    assert reversed_models.dataset_identity == forward.dataset_identity
    assert reversed_models.model_dump_json() == forward.model_dump_json()


def test_direct_citation_and_string_tuples_canonicalize() -> None:
    from brainlearn_core.datasets import DatasetCitation

    base = _catalog_raw()[0]
    citations = tuple(DatasetCitation.model_validate(item) for item in base["citations"])
    multi = dict(
        base,
        formats=("rest", "BIDS"),
        citations=citations + citations[:1],
    )
    with pytest.raises(ValidationError, match="unique"):
        CatalogEntry.model_validate(_reidentify_catalog(multi))
    ordered = CatalogEntry.model_validate(_reidentify_catalog(dict(base, formats=("rest", "BIDS"))))
    assert list(ordered.formats) == ["BIDS", "rest"]
    assert (
        ordered.model_dump_json()
        == CatalogEntry.model_validate(
            _reidentify_catalog(dict(base, formats=["BIDS", "rest"]))
        ).model_dump_json()
    )
