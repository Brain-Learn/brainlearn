# BrainLearn monitored implementation review

Review date: 2026-09-13

## Current review

Scope: second monitoring review of Step 4E.2 reduced motion and safe
per-instance presentation customization.

Status: **approved**. The first-review findings are repaired, the full gate
passes, and the live application preserves presentation through project
create/open. Commit this reviewed unit, then continue with Step 4E.3 only.

## Findings

No blocking findings.

The Step 4 reduced-motion checkbox remains open because its text also requires
run-state transitions. Those states are not visible until the Step 4E.4 run
drawer is implemented. The checked presentation checkbox and this approval do
not claim that later UI work is complete.

## Behavior verified

- Absent `presentation` is the canonical default for newly instantiated,
  reset, and undone nodes; explicit API `null` remains compatible.
- A violet accent remains violet for ordinary, selected, invalid, and
  selected-invalid cards. Compact rendering and custom titles are independent
  of validation and selection state.
- The inspector remounts on node selection, its CSS animation is active in the
  live app, node removal has a tested 160 ms leaving state, and reduced motion
  removes that leaving state and makes viewport fitting immediate.
- Python trims surrounding title whitespace and rejects whitespace-only
  titles. Notes remain plain text and field bounds are enforced.
- Workflow and node content identities exclude presentation, while scientific
  parameter changes still alter identity. A presentation-only second run
  reuses cache output.
- Live browser checks covered title trimming, accent plus compact presentation,
  selected-invalid styling (`rgb(176, 139, 240)` left border), plain-text notes,
  Reset, Undo, draft reload, and project create/open persistence. The browser
  console reported no warnings or errors.

## Verification reproduced

- `uv run ruff check .`: passed.
- `uv run ruff format --check .`: 40 files clean.
- `uv run mypy packages/core/src packages/server/src`: 18 source files clean.
- `uv run pytest -q`: 294 passed with two upstream Starlette/AnyIO deprecation
  warnings.
- `npm --prefix apps/web run lint`: passed.
- `npm --prefix apps/web run format:check`: passed.
- `npm --prefix apps/web test -- --run`: 58 passed in 7 files.
- `npm --prefix apps/web run build`: passed, 1,839 modules transformed.
- `npm --prefix apps/web audit --omit=dev`: 0 vulnerabilities.
- `git diff --check`: passed before this review update.

## Next assignment

Implement only Step 4E.3: make the non-scientific demonstration manifests
available through the registry and add a versioned branched fixture that can be
assembled and validated in the UI. Do not begin run-drawer controls, dataset
access, or scientific processing in that work unit.
