# BrainLearn monitored implementation review

Review date: 2026-09-14

## Current review

Scope: fifth monitoring review of Step 4E.4 artifact metadata, regular-file
enforcement, and the complete run-drawer change set.

Status: **approved**. The two fourth-review blockers are repaired and no new
blocking findings remain. Step 4E.4 may be committed. Step 4 stays open for the
Step 4E.5 completion gate.

## Findings

No actionable findings.

## Repairs verified

- `ArtifactRecord` and `CacheOutput` accept only bounded, parameter-free
  `type/subtype` tokens. A shared validator is also applied before artifact
  opening, so persisted metadata cannot inject `Content-Type`.
- The original hostile `text/plain\r\nX-Probe: injected` probe now returns a
  structured 409 with `application/json`; no injected response header exists.
- Artifact descriptors are opened with no-follow and nonblocking flags where
  available, inspected with `fstat`, and required to be regular files before
  hashing. Directory and FIFO replacements return structured 409 responses.
- Size and SHA verification still use bounded 1 MiB reads from the same
  descriptor later streamed to the response. Tests track descriptor closure.
- The prior token restoration, SSE cancellation/ownership, project/history
  scoping, cache behavior, review/cancel lifecycle, deep-symlink refusal,
  disposition encoding, and reduced-motion behavior remain green.

## Verification reproduced

- Focused artifact/contract/cache suite: 194 passed with two upstream warnings.
- Focused run client/drawer/lifecycle suite: 30 passed.
- `uv run ruff check .`: passed.
- `uv run ruff format --check .`: 43 files clean.
- `uv run mypy packages/core/src packages/server/src`: 18 source files clean.
- `uv run pytest -q`: 365 passed with two upstream Starlette/AnyIO warnings.
- `npm --prefix apps/web run lint`: passed.
- `npm --prefix apps/web run format:check`: passed.
- `npm --prefix apps/web test -- --run`: 121 passed in 13 files.
- `npm --prefix apps/web run build`: passed, 1,841 modules transformed.
- `npm --prefix apps/web audit --omit=dev`: 0 vulnerabilities.
- `git diff --check`: passed.
- Live UI: restored run history, opened a succeeded run, rendered its artifact
  path/size/SHA/media type, and the artifact Open action reached
  `POST /api/artifacts/open` with HTTP 200 and no blocked-download message.

## Checklist decision

The run-drawer and reduced-motion transition items are approved. Step 4 remains
unchecked until Step 4E.5 independently executes the full completion gate.

## Next assignment

Execute only Step 4E.5 as defined in `docs/implementation-plan.md`. Do not begin
Step 5 dataset work. Request the final Step 4 monitoring review with the
complete acceptance evidence.
