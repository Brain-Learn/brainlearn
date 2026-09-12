# BrainLearn implementation review

Review date: 2026-09-12

## Current review

Scope: final monitoring review of the uncommitted Step 4A execution contracts
and deterministic content identities.

Status: **approved**. All findings from both monitoring reviews are resolved,
the independent mutation probes reject the previously accepted contradictory
records, and the complete repository verification baseline passes.

Step 3 remains approved through commits `77e4686`, `fd1479b`, and
`68efda6`.

## Approved behavior

- Seven versioned execution records define runs, node runs, artifacts,
  environments, events, failures, and review pauses, with explicit migration
  entry points for schema `1.0`.
- Content identities deterministically cover every declared computation input
  and canonicalize mapping order and numerically equivalent JSON values.
- Node content identities are recomputed during validation, and every node run
  is tied to the environment record contained by its run.
- Run and node lifecycle rules enforce timezone-aware chronology, failure,
  review, artifact, attempt, reference, dependency, and aggregate-state
  consistency.
- Artifact paths are portable project-relative paths and reject rooted,
  drive-qualified, UNC, traversal, and ambiguous dot-segment forms.
- The fixtures contain a self-contained inspect-to-filter dependency graph and
  identities computed from their declared fields.

## Monitoring adjustment

The final review found that `NodeRunRecord` still defaulted to the invalid pair
`state="queued"` and `attempt=1`. The reviewer changed the attempt default to
zero and added a regression test proving that omitted state and attempt fields
produce a valid queued record. This aligns the model defaults with the already
documented execution-attempt contract.

## Independent probes

The final review directly confirmed rejection of:

- duplicate references in a node's dependency list;
- changed parameters paired with a stale node identity;
- an edited run environment paired with stale node environment identities;
- a coherent, recomputed per-node environment substitution when the run does
  not persist that environment;
- a cancelled run whose finish time precedes creation; and
- queued work carrying a positive attempt or running work carrying zero.

Valid queued work with attempt zero and valid running work with a positive
attempt both pass.

## Verification

The complete baseline passed after the monitoring adjustment:

- `uv run ruff check .`: passed.
- `uv run ruff format --check .`: 27 files already formatted.
- `uv run mypy`: passed for 13 source files.
- `uv run pytest -q`: 134 passed, with 2 upstream deprecation warnings.
- `npm --prefix apps/web run lint`: passed.
- `npm --prefix apps/web run format:check`: passed.
- `npm --prefix apps/web run test`: 24 passed across 5 files.
- `npm --prefix apps/web run build`: passed; 1,837 modules transformed.
- `npm --prefix apps/web audit --omit=dev`: 0 vulnerabilities.
- `git diff --check`: passed.

## Next assignment

Step 4B is ready. Implement the run store and topological scheduler described
in `docs/implementation-plan.md`. Keep worker execution, event streaming,
cancellation, cache storage, UI controls, scientific integrations, and
arbitrary command execution outside that slice. Request monitoring review
before committing Step 4B.
