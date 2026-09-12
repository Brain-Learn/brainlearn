# BrainLearn monitored implementation review

Review date: 2026-09-12

## Current review

Scope: fifth monitoring review of the uncommitted Step 4C worker lifecycle and
the fourth-review staging-boundary repair.

Status: **approved**. The staging setup failure now terminates the affected node
and run safely, all earlier Step 4C findings remain repaired, and the complete
repository gate passes. Step 4C may be committed. Step 4 remains open for cache,
canvas interaction, fixture demonstration, and run UI work in Steps 4D and 4E.

Step 4A is approved in commit `25bf419`. Step 4B is approved in commit
`91b9d2e`.

## Final repair independently verified

A direct probe created a valid queued `demo.copy` run, planted `staging/` as a
symlink to an external sentinel directory, and started the existing run. The API
returned 200, the worker recorded structured failure, the node and run both
reached `failed`, and the external sentinel remained byte-identical. No output
was written through the symlink. The regression test also proves downstream
skip propagation and absence of successful artifacts.

Staging reservation and input resolution now execute inside the node-attempt
failure boundary. Quarantine is called only when a staging path was initialized.

## Step 4C behavior approved

- Allowlisted demonstration workers execute outside FastAPI request handlers and
  persist mutations through `RunStore`.
- Deterministic scheduling, positive attempt assignment, failure propagation,
  review pause/resume, structured SSE events, and terminal run authorship work.
- Cancellation distinguishes never-started attempt 0 from executed attempts,
  remains idempotent, and does not publish partial artifacts.
- Restart recovery quarantines interrupted staging and unreferenced final
  attempts, preserves valid reviews, and resumes schedulable work monotonically.
- Output promotion validates paths, symlinks, manifest ports, required outputs,
  unique destinations, and containment before one atomic attempt rename.
- Post-promotion cancellation and persistence failures remove or quarantine
  unreferenced attempts without modifying external sentinel targets.
- One adapter manifest controls versions, required/optional ports, and handlers;
  real relay coverage proves exact upstream artifact bytes reach downstream.
- Unexpected request and adapter failures are captured by the local fault log
  without request bodies or authentication tokens.

## Verification reproduced

- `uv run ruff check .`: passed.
- `uv run ruff format --check .`: 37 files clean.
- `uv run mypy packages/core/src packages/server/src`: passed for 18 source files.
- `uv run pytest -q`: 232 passed; two warnings are upstream Starlette/AnyIO
  deprecations and none originate in BrainLearn code.
- Focused repair group: 6 passed, covering staging symlink failure, blocked
  post-promotion quarantine, blocked orphan quarantine, missing required output,
  disconnected required input, and omitted required adapter output.
- `npm --prefix apps/web run lint`: passed.
- `npm --prefix apps/web run format:check`: passed.
- `npm --prefix apps/web test -- --run`: 24 passed in 5 files.
- `npm --prefix apps/web run build`: passed; 1,837 modules transformed.
- `npm --prefix apps/web audit --omit=dev`: 0 vulnerabilities.
- `git diff --check`: passed before this approval update.

## Next assignment

Proceed only with Step 4D cache and invalidation as specified in
`docs/implementation-plan.md`. Do not begin Step 4E UI interaction work in the
same work unit. Keep the Step 4 heading unchecked until the complete 4E gate is
reviewed.
