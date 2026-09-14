# BrainLearn monitored implementation review

Review date: 2026-09-14

## Current review

Scope: fifth and final monitoring review of Step 5A.1 after generalized
canonicalization repairs.

Status: **complete**. No blocking findings remain. The versioned provider-neutral
dataset contract is approved and may be committed. OpenNeuro integration may
begin only as the next bounded unit in `docs/implementation-plan.md`.

## Approved behavior

- Schema `1.0` defines deeply immutable, provider-neutral `CatalogEntry`,
  `DatasetLock`, `CatalogFile`, `VerifiedFile`, and `DatasetCitation` records.
- Catalogs require nonempty expected files and a positive covering total while
  permitting absent provider checksums; locks require exact sizes and hashes.
- Locks preserve the stable dataset, license, citation, compatibility,
  limitation, and landing-page context required for offline reopening.
- Identities bind the declared snapshot and retrieval contract while excluding
  explicitly documented review, retrieval-time, and local-location metadata.
- All persisted collections serialize in canonical order for JSON-shaped and
  programmatic list/tuple/model inputs. Reversed catalog, lock, and projection
  inputs retain equal identities and byte-identical JSON.
- Provider, dataset, snapshot, local, and expected-file paths use a conservative
  portable grammar with per-segment Windows restrictions and traversal/control
  rejection.
- Persisted URLs require plain HTTPS with validated host/port and no credentials,
  queries, or fragments. Citation DOIs reject whitespace, C0 controls, DEL, and
  malformed full strings.
- Access states distinguish public, restricted, and credentialed sources without
  allowing secrets in persisted records. Synthetic fixtures remain pending and
  make no approval or scientific claims.
- Migration entry points, genuine fixtures, round trips, stale identities,
  cross-field rules, deep immutability, and catalog-to-lock projection are tested.

## Verification reproduced

- `uv run ruff check .`: passed.
- `uv run ruff format --check .`: 46 files clean.
- `uv run mypy packages/core/src packages/server/src`: 19 source files clean.
- `uv run pytest -q`: 493 passed with two upstream Starlette/AnyIO warnings.
- `npm --prefix apps/web run lint`: passed.
- `npm --prefix apps/web run format:check`: passed.
- `npm --prefix apps/web test -- --run`: 121 passed in 13 files.
- `npm --prefix apps/web run build`: passed, 1,841 modules transformed.
- `npm --prefix apps/web audit --omit=dev`: 0 vulnerabilities.
- `git diff --check`: passed before this final review update.
- Independent direct probes confirmed canonical JSON for reversed validated-model
  tuples in both catalog and lock constructors and for reversed projection input.
  Earlier path, URL, DOI, identity, projection, and mutation probes remain green.

## Checklist decision

The first Step 5 subitem, the versioned curated-dataset manifest and lock
contract, is approved and checked. Step 5 remains open for provider access,
download lifecycle, BIDS/MNE inspection, UI, and scientific reference work.

## Next assignment

Implement only Step 5A.2 from `docs/implementation-plan.md`: the provider-neutral
read-only dataset-source interface, deterministic mock provider, and OpenNeuro
public metadata/catalog adapter. Do not download or extract datasets, add UI, add
MNE/BIDS dependencies, or mark any real dataset verified. Request monitoring
review before checking the OpenNeuro provider subitem.
