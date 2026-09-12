# BrainLearn monitored implementation review

Review date: 2026-09-12

## Current review

Scope: final monitoring review of Step 4D content-addressed caching, including
the two second-review repairs completed in the shared worktree.

Status: **approved**. Cache identity, lookup, materialization, publication,
invalidation, recovery, cancellation, and corruption handling satisfy the Step
4D gate. The complete repository gate passes. Step 4D may be committed; Step 4
remains open for the Step 4E interaction and demonstration gate.

Steps 4A–4C are approved in commits `25bf419`, `91b9d2e`, and `55ae9e1`.

## Final repairs independently verified

- Cancellation raised during cache copying now persists a terminal cancelled run
  and node at attempt 0, with no timestamps, artifacts, staging, or final reuse
  tree.
- Cancellation after the atomic reuse rename and before run-record persistence
  has the same terminal attempt-0 semantics and removes the unreferenced final
  tree.
- Other ordinary reuse exceptions fall through to normal execution rather than
  escaping to the driver and stranding a record.
- `CacheEntry` recomputes `node_content_identity` from node type, version, inputs,
  parameters, environment, seed, and settings. Each stale-field mutation is
  rejected during model validation; worker comparisons remain in place.

## Step 4D behavior approved

- Exact second runs reuse verified outputs as `cache_reused`, attempt 0, with
  typed per-run artifacts and structured events.
- Cache keys bind inputs, parameters, implementation, environment, seed, and
  settings. Changes invalidate exactly the affected node and descendants while
  independent branches remain reusable.
- Entry metadata, manifest output ports, required outputs, file size, SHA-256,
  containment, and symlink boundaries are verified before reuse.
- Reused outputs stage and verify before one atomic rename; failures and
  cancellations leave no partial successful tree.
- Cache publication is atomic and serialized in-process. Corrupt directories and
  regular-file occupants are quarantined and healed; symlink targets remain
  untouched.
- Interrupted publication staging is quarantined during recovery. Zero-output,
  failed, cancelled, and review-paused work does not create misleading entries.
- Schema `1.0` has a genuine fixture, round-trip and stable-identity coverage,
  per-field stale-identity rejection, and unsupported-version rejection.

## Verification reproduced

- `uv run ruff check .`: passed.
- `uv run ruff format --check .`: 38 files clean.
- `uv run mypy packages/core/src packages/server/src`: passed for 18 source files.
- `uv run pytest -q`: 273 passed; two warnings are upstream Starlette/AnyIO
  deprecations and none originate in BrainLearn code.
- Focused final repair group: 9 passed.
- Direct cancellation-during-copy probe: run and node `cancelled`, attempt 0,
  reuse tree absent.
- Direct stale-fixture probe: `CacheEntry.model_validate` rejected the changed
  version under the old identity.
- `npm --prefix apps/web run lint`: passed.
- `npm --prefix apps/web run format:check`: passed.
- `npm --prefix apps/web test -- --run`: 24 passed in 5 files.
- `npm --prefix apps/web run build`: passed; 1,837 modules transformed.
- `npm --prefix apps/web audit --omit=dev`: 0 vulnerabilities.
- `git diff --check`: passed before this approval update.

## Next assignment

Proceed only with Step 4E canvas interaction and the complete demonstration
workflow gate in `docs/implementation-plan.md`. Keep scientific processing and
dataset downloading out of that work unit. The Step 4 heading remains unchecked
until the entire 4E completion gate passes monitored review.
