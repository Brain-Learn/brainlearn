# Curated-dataset contract, schema 1.0 (Step 5A.1)

This document defines the provider-neutral persisted contract for the Step 5A
dataset library. It covers metadata shapes, validation, identity, storage
layout, and licensing only. Downloading, provider networking, archive
extraction, dataset UI, and BIDS/MNE inspection arrive in later Step 5A units
and must consume these exact shapes.

## Records

| Record | Purpose |
|---|---|
| `CatalogEntry` | One curated record describing an immutable dataset snapshot, including provider expectations for verification. |
| `DatasetLock` | Immutable local claim that a snapshot was retrieved into a project, self-contained for offline reopening. |
| `CatalogFile` | One expected catalog file; the checksum may be unavailable upstream. |
| `VerifiedFile` | One locked file; the content hash is always required. |
| `DatasetCitation` | One immutable citation with strict title/DOI/URL validation. |

Every record carries `schema_version: "1.0"` and is frozen after validation:
post-validation mutation raises instead of silently changing data under a
stored identity. Unknown versions fail through the
`migrate_catalog_entry_dict` / `migrate_dataset_lock_dict` entry points
with an actionable message; stored records are never silently coerced.
`tests/fixtures/dataset-catalog-1.0.json` (two synthetic entries) and
`tests/fixtures/dataset-lock-1.0.json` prove the round trip.

## Trust boundary

- Catalog metadata is **untrusted until verified**: every static entry ships
  with `review_status: "pending"`, an empty curator, no review timestamp, and
  a limitations note. Nothing may present a catalog entry as approved based on
  recalled facts; only a curator check against the live provider flips an
  entry to `verified` with a timestamp and a named curator.
- The contract stores **no transfer endpoints and no secrets**. Catalog
  entries carry no download URLs; models reject unknown fields, so
  credentials, tokens, cookies, signatures, and signed URLs cannot be
  persisted even if supplied. Every persisted URL must be a plain HTTPS link
  with a valid hostname: userinfo, invalid ports, query strings, and
  fragments are rejected, including inside nested citation URLs, and DOI
  values must match `10.<registrant>/<suffix>` syntax.
- Provider, dataset ID, and snapshot are canonical single path-safe
  components, since the documented layout uses them as directory names: no
  separators or whitespace, no dot segments, no drive qualifiers, none of
  the Windows-forbidden `:<>\"|?*` characters, no reserved basenames, and no
  trailing dots/spaces or leading dots. Every segment of persisted relative
  paths follows the same portable grammar (leading dots stay legal there, so
  dotfiles keep working). Hostnames validate per-label (or documented IP
  literals), DOIs full-match with explicit C0/DEL rejection, and citation
  titles are stored stripped before deduplication.
- A lock record is a **local claim, not proof of bytes**: download
  verification (sizes, checksums, BIDS structure) belongs to the retrieval
  units, which must re-derive the lock identity from what actually landed.

## Identity rules

Identities are `brainlearn-v1:dataset:<sha256>` over canonical JSON.
Set-like inputs (formats, templates, citations, file lists) are sorted into
canonical order during model validation for every sequence shape — lists,
tuples, raw dicts, or already-validated models — so equivalent records
serialize byte-identically and enumeration order never changes an identity.

- `catalog_entry_identity` covers the described snapshot: provider, dataset
  ID, snapshot, modality/task/participants/formats, approximate and expected
  sizes, the expected file list, access class, license fields, citations,
  landing page, and compatible templates. Display titles and review workflow
  metadata (status, curator, review timestamp) are excluded, so verifying or
  retitling an entry never re-identifies its dataset.
- `dataset_lock_identity` covers the retrieval contract: everything above
  except approximate size, plus the dataset title, modality, task,
  participants, formats, citations, templates, landing page, and limitations
  that make the lock self-contained offline. Retrieval time, local path, and
  the optional catalog link are excluded so the same snapshot verifies
  identically on another machine or layout.
- `project_lock_from_catalog` projects a catalog entry into a lock, copying
  descriptive, license, citation, and compatibility context so the offline
  record preserves them when the catalog is absent or changes. Supplied files
  may be raw dicts, tuples, or validated models in any order; the projection
  validates and sorts them canonically before identity calculation and
  construction, so equivalent projections serialize byte-identically.
- Both models recompute their stored identity on load and reject stale ones,
  mirroring cache entries and node-run records.

## Verification semantics

- Catalog expectations describe what the provider publishes: file checksums
  may be absent, but a file list requires a positive expected total covering
  at least the summed sizes.
- A lock proves immutable local bytes: its file list must be nonempty, every
  file must carry a content hash, and sizes must sum exactly to the expected
  total. Descriptive strings are stripped and blanks rejected; formats and
  citations must be nonempty and unique; a verified review requires both a
  timestamp and a named curator.

## Filesystem layout (reserved)

Retrieved snapshots live under the owning project at

```text
<project-root>/datasets/<provider>/<dataset-id>/<snapshot>/
```

with the lock record persisted alongside the bytes it describes. Retrieval
units must create the snapshot directory atomically, refuse symlinks and
path escapes, and never write outside the selected project dataset root.

## Licensing requirements

- Every entry and lock carries the exact license name, an SPDX identifier
  when one exists, and the full reuse statement. Display and persist all
  three before any download affordance is enabled.
- `public` means retrievable without credentials. `restricted` and
  `credentialed` entries must open provider instructions instead of an
  automatic downloader until their access design is approved; BrainLearn must
  never bypass an account, credentialing, or data-use agreement.
- “Free to download” is never treated as permission to redistribute. Keep
  each dataset's own license and citation on the record.

## Provider-adapter consumption (later units)

Adapters resolve a catalog entry to transfer endpoints at call time from
`(provider, dataset_id, snapshot)`, fetch into a staging directory, verify
expected sizes and checksums plus BIDS structure, then write the lock record
with an identity recomputed from verified facts. The OpenNeuro-first
integration pins exact snapshots and records checksums where the provider
exposes them; ordinary CI must not fetch large datasets.
