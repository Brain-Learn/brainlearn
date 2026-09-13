# BrainLearn monitored implementation review

Review date: 2026-09-13

## Current review

Scope: fourth monitoring review of Step 4E.3 demonstration manifests,
branched fixture, example loading, and message ownership.

Status: **approved**. The final asynchronous-message race is repaired, the
complete gate passes, and the frontend suite remained green across six
additional consecutive runs. Step 4E.3 may be committed. Continue separately
with Step 4E.4; Step 4 remains open.

## Findings

No blocking findings.

## Repair verified

- A superseded success returns before changing the workflow or message, so the
  latest loaded example and its success message remain authoritative.
- A superseded failure follows the same silent path and cannot replace a newer
  success with an obsolete error.
- A current operation invalidated by a graph edit preserves the edited graph
  and reports the canvas-unchanged notice.
- A current, revision-valid failure remains visible to the researcher.
- Deterministic deferred-response tests cover older success, older rejection,
  and current-request invalidation by a graph edit.
- The earlier sidebar, example API/UI, manifest-authority, insertion-matrix,
  canonical-fixture, and cache-documentation repairs remain present.

## Verification reproduced

- `uv run ruff check .`: passed.
- `uv run ruff format --check .`: 42 files clean.
- `uv run mypy packages/core/src packages/server/src`: 18 source files clean.
- `uv run pytest -q`: 315 passed with two upstream Starlette/AnyIO warnings.
- `npm --prefix apps/web run lint`: passed.
- `npm --prefix apps/web run format:check`: passed.
- `npm --prefix apps/web test -- --run`: 86 passed in 9 files.
- The complete frontend suite passed six further consecutive runs, each with
  86 tests in 9 files; the previously reported transient failure did not recur.
- `npm --prefix apps/web run build`: passed, 1,839 modules transformed.
- `npm --prefix apps/web audit --omit=dev`: 0 vulnerabilities.
- `git diff --check`: passed before this review update.

## Next assignment

Implement only Step 4E.4: connect the editor to the existing run API, SSE event
stream, review decisions, cancellation, and artifact records through a run
drawer. Show queued/running/waiting-review/succeeded/failed/cancelled state,
node attempts and cache reuse, ordered events and actionable failures, and safe
artifact metadata/open actions. Preserve the loaded workflow while a run is in
progress, handle reconnect/replay without duplicate events, and make run-state
motion honor reduced-motion preferences. Add focused API/client/component tests
and live browser probes for success, failure, review/resume, cancellation,
cache reuse, artifact inspection, and SSE reconnect. Do not begin Step 4E.5 or
Step 5. Leave the run-drawer and motion checkboxes unchecked for monitoring.
