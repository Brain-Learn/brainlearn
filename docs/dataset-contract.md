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

## Local/private offline datasets (Step 5A.6)

Local/private imports allow a researcher to import private data from a
subdirectory inside an authorized project without copying, relocating, uploading,
or modifying source files.

### 1. The local/private boundary

- **Provider**: `provider: "local"`, `dataset_id: "local"`, `snapshot: "local"`, `access: "restricted"`.
- **Honest metadata**: Unlike public catalog datasets, private local datasets
  often have no DOI, published paper, public license, or landing page. BrainLearn
  does not invent fake citations, licenses, modalities, BIDS status, or landing
  pages. Citations and formats default to empty lists (`()`), while
  `license_name`, `license_spdx`, `reuse_statement`, and `landing_page` default
  to `None`. Modality and task default to unassessed (`""`).
- **Invariants enforced at model boundary**: `DatasetLock` enforces that any
  local provider lock strictly has `dataset_id="local"`, `snapshot="local"`,
  `access="restricted"`, `catalog_identity=None`, empty modality/task, 0
  participants, no template compatibility claims, and no license or landing
  page metadata. `LocalImportRecord` binds its `local_path` strictly to its
  embedded `lock.local_path`.
- Public dataset locks continue to require nonempty formats, citations,
  landing page, license, reuse statement, and catalog identity.

### 2. Identity inputs

Local dataset identity is computed deterministically via `dataset_lock_identity`:
- Included: `provider="local"`, `dataset_id="local"` (constant for all local
  datasets), `snapshot="local"`, `access="restricted"`, `title`, `modality=""`,
  `task=""`, `participants=0`, sorted `formats`, canonical sorted `citations`,
  `compatible_templates=[]`, `landing_page=None`, `limitations`,
  `expected_total_bytes`, and canonically sorted `expected_files` (path, byte
  size, SHA-256 digest).
- Excluded: `local_path` and `retrieved_at`. Two projects importing the same
  directory contents with the same metadata produce the exact same identity
  regardless of local filesystem location, folder directory name, or scan
  timestamp. Enumeration order is irrelevant because paths are sorted
  canonically.

### 3. Hardened filesystem protections

- **Read-only**: Source research data is never opened with write permissions,
  moved, deleted, renamed, or modified.
- **Storage containment**: Source paths must be project-relative subdirectories.
  Absolute paths, path traversal (`..`), drive qualifiers, Windows-forbidden
  characters, and BrainLearn-owned storage or metadata (`local-imports`,
  `downloads`, `datasets`, `runs`, `staging`, `cache`, `project.json`,
  `workflow.json`) are refused.
- **No-follow descriptor boundary**: Every path component is verified
  against symlinks. Descriptors are opened with `O_RDONLY | O_NOFOLLOW | O_NONBLOCK`.
- **Object verification**: Non-regular files, FIFOs, Unix domain sockets,
  device nodes, unreadable files, and hard links (`st_nlink > 1`) are refused.
- **Descriptor stat before and after hashing**: Before hashing, `fstat`
  records `st_dev`, `st_ino`, `st_size`, `st_mtime_ns`, and `st_ctime_ns`.
  Hashing proceeds in bounded `VERIFY_CHUNK_BYTES` (64 KiB) chunks, checking
  cancellation after each chunk. After reading to EOF, `fstat` and `lstat`
  re-verify that descriptor attributes—including `st_ctime_ns`—and file path
  were not mutated in place, replaced, or modified with restored mtime.
- **Complete tree rescan**: The entire source directory is rescanned after
  hashing all files, comparing (size, dev, ino, mtime_ns, ctime_ns) to detect
  concurrent file additions, removals, replacements, or mtime-restored mutations.
- **Resource limits**: Strictly bounded by `MAX_SCAN_DEPTH` (16),
  `MAX_SCAN_FILES` (10,000), and `MAX_IMPORT_BYTES` (10 GiB).

### 4. Restart recovery and offline reopening

- Import records are persisted atomically at
  `local-imports/<import_id>/import.json`.
- When the service restarts, `POST /api/datasets/local/recover` reconciles
  records: any record left in `scanning` or `pending` state is marked `failed`
  with failure code `interrupted`.
- Records in `ready` state reload completely offline. On reload, the embedded
  lock identity is recomputed and checked against the stored `dataset_identity`
  to detect disk corruption or tampering.

### 5. Leak-free static errors

- API and service errors never return raw exception strings (`str(exc)`),
  server stack traces, session tokens, or absolute filesystem paths. All error
  messages are curated static strings with typed response models.
