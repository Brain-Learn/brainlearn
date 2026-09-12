# BrainLearn implementation review

Review date: 2026-09-12

Scope: Step 3 secure local projects and persistence, including commits
`77e4686` and `fd1479b`.

Status: **approved**. All review findings are resolved, the full verification
baseline passes, and the Step 3 completion gate is satisfied.

## Final repair

The active project now owns separate path and name state. The editable folder
and name fields are used only as targets for Create, Open, and Save as.

- Recovery drafts persist the active project name with the workflow and path.
- Saving a recovered project retains its manifest name.
- Legacy drafts with no recorded project name omit the optional name during
  Save, allowing the backend to preserve the existing manifest name.
- Selecting another recent project changes only the target form; it cannot
  rename the active project before Open succeeds.
- Save remains disabled until a project is active and always uses that active
  project's path and name.

Regression tests cover recovered-name saving, recent-project selection, legacy
draft compatibility, active/requested path separation, stale project
responses, validation ordering, unique IDs, and last-valid recovery.

## Resolved findings

| Finding | Resolution |
|---|---|
| Active project name could be lost during recovery | Drafts persist active name; legacy drafts cause Save to omit an unknown name. |
| Recent selection could rename the active project | Target-form name and active project name are separate. |
| Folder entry could replace the active save target | Active path and folder input are separate; Save uses only the active path. |
| Pending responses could overwrite newer edits or project identity | Workflow revision and project-operation sequence guards discard stale responses. |
| Loaded graphs could generate duplicate IDs | ID allocation synchronizes with loaded node and edge IDs and checks collisions. |
| Previous recovery graph could be invalid | Invalid work-in-progress saves never replace the last semantically valid recovery graph. |
| Validation responses could arrive out of order | Sequenced validation and connection revision checks keep diagnostics current. |
| Filesystem bearer token persisted beyond the browser session | The token uses `sessionStorage` and is absent from project drafts. |

## Verification

The complete baseline passed after the final repair:

- `uv run ruff check .`: passed.
- `uv run ruff format --check .`: 23 files already formatted.
- `uv run mypy`: passed for 11 source files.
- `uv run pytest -q`: 33 passed, with 2 upstream deprecation warnings.
- `npm --prefix apps/web run lint`: passed.
- `npm --prefix apps/web run format:check`: passed.
- `npm --prefix apps/web run test`: 24 passed across 5 files.
- `npm --prefix apps/web run build`: passed; 1,837 modules transformed.
- `npm --prefix apps/web audit --omit=dev`: 0 vulnerabilities.
- `git diff --check`: passed.

The monitored Step 3 gate also includes the previously exercised live checks:
project creation and saving, recovery after service authorization reset,
rejection of path traversal and unauthenticated requests, hostile-Origin
rejection, atomic writes, and preservation of the last valid workflow.

## Next assignment

Step 4 is ready. The implementing agent must begin with **Step 4A — Versioned
execution contracts** in `docs/implementation-plan.md`. It should not start
workers, execution, caching, or scientific integrations until the Step 4A
contracts and identity tests pass monitoring review.
