# BrainLearn monitored implementation review

Review date: 2026-09-13

## Current review

Scope: pre-implementation audit and assignment split for Step 4E.

Status: **ready for Step 4E.1**. The worktree is clean at commit `2768456` and
contains no Step 4E implementation. The existing UI supplies controlled React
Flow nodes without `onNodesChange`, exposes palette insertion only through
buttons, has no persisted presentation model, and has no run API client or run
drawer. The backend run, cancel, review, and SSE endpoints are already present.
The next agent must implement only direct canvas manipulation and palette drop
as defined by the `Next assignment` in `docs/implementation-plan.md`.

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

The unchanged baseline was reproduced again on 2026-09-13: 273 Python tests and
24 frontend tests passed; Ruff, Ruff formatting, strict mypy, ESLint, Prettier,
the frontend production build (1,837 modules), production audit (0
vulnerabilities), and `git diff --check` all passed. The two Python warnings are
upstream Starlette/AnyIO deprecations.

## Next assignment

Proceed only with Step 4E.1 direct canvas manipulation in
`docs/implementation-plan.md`. Stop for monitoring review before beginning
motion, presentation customization, demonstration manifests, or run controls.
Keep scientific processing and dataset downloading out of this work unit. All
Step 4E boxes and the Step 4 heading remain unchecked.
