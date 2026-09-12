# BrainLearn implementation checklist

This is the authoritative build sequence for BrainLearn. Agents must work from the first unchecked step whose dependencies are complete. `PROPOSAL.md` explains the product strategy; `AGENTS.md` defines the permanent engineering and scientific rules.

## How completion is recorded

- `[ ]` means unverified or incomplete. Partial work stays unchecked.
- `[x]` means the stated behavior exists, its completion gate passed, and evidence appears in the completion log.
- An implementing agent may check an individual task only after running its stated verification.
- A top-level step may be checked only after the monitoring/reviewing agent inspects the change and reruns the completion gate.
- Do not check a step because files exist, code compiles, or a mock screen looks complete. Check the behavior described by the gate.
- If a later change breaks a completed gate, change the affected item back to `[ ]`, add a log entry explaining the regression, and repair it before expanding scope.
- Every work unit ends with the reporting format in `AGENTS.md` and an update to the completion log below.
- Each pull request or commit should address one top-level step or a clearly named subset of it.

## Required verification baseline

Unless a step explicitly adds more checks, every code change must pass:

```bash
uv run ruff check .
uv run ruff format --check .
uv run mypy
uv run pytest
npm --prefix apps/web run lint
npm --prefix apps/web run format:check
npm --prefix apps/web run test
npm --prefix apps/web run build
git diff --check
```

The agent must report exact results. “Tests passed” without the command and count is not completion evidence.

## [x] Step 0 — Repository and product baseline

- [x] Initialize Git on `main`.
- [x] Add the full proposal and scope boundaries.
- [x] Save the interface concept inside the repository.
- [x] Add the agent operating brief.
- [x] License BrainLearn's original code under BSD-3-Clause.
- [x] Put the SPDX identifier and full license notice into Python package distributions.

Completion gate: repository has a clean baseline, readable proposal and handoff, project-local design asset, and built Python wheels containing the BSD-3-Clause expression and license text.

## [x] Step 1 — Executable foundation

- [x] Scaffold the Python workspace and React application.
- [x] Define workflow schema version `1.0`.
- [x] Represent nodes, typed ports, parameters, positions, and edges.
- [x] Reject missing nodes, unknown ports, wrong directions, incompatible types, missing required parameters, and cycles.
- [x] Expose loopback health, system-capability, example-workflow, and validation endpoints.
- [x] Distinguish a detected GPU/container candidate from a runtime-validated capability.
- [x] Render the eight-node example EEG graph.
- [x] Update the inspector when a user selects a node.
- [x] Label the interface and graph as non-executing examples.

Completion gate: backend baseline passes with 10 tests; frontend baseline passes with one component test and a production build; a live API and interface render the example graph.

## [x] Step 2 — Node registry and editable graph

Goal: replace hard-coded UI examples with a backend-owned registry and support safe in-memory editing.

- [x] Define a versioned `NodeManifest` model containing identity, version, category, ports, parameter schema, review behavior, citations, license metadata, and capability requirements.
- [x] Add an initial registry for the eight illustrative EEG nodes. Mark every node `example`; do not imply execution support.
- [x] Expose read-only registry list/detail endpoints.
- [x] Add a workflow validation endpoint that accepts a submitted workflow body rather than validating only the bundled example.
- [x] Generate frontend node-library entries and inspector controls from manifests returned by the API.
- [x] Allow users to add and remove nodes in local UI state.
- [x] Allow compatible connections and disconnections using React Flow.
- [x] Display backend validation issues against the relevant node or edge.
- [x] Support editing parameter values with schema-derived controls.
- [x] Add undo and redo for graph edits.
- [x] Add contract tests ensuring registry manifests and frontend types agree.

Completion gate:

1. Start with an empty canvas, add BIDS EEG and Inspect Signal, and connect them.
2. Attempt an incompatible connection and show the backend-generated reason.
3. Change a required parameter, undo it, and redo it.
4. Serialize the edited graph, round-trip it through the API, and receive the same valid graph.
5. Baseline checks pass, including new backend and frontend tests for these behaviors.

## [x] Step 3 — Secure local projects and persistence

Goal: make edits durable without allowing arbitrary filesystem access.

- [x] Define a versioned project manifest and project-directory layout.
- [x] Add create, open, save, save-as, and recent-project operations.
- [x] Restrict all file operations to explicit user-selected project roots.
- [x] Add per-session authentication for the local service.
- [x] Validate Host and Origin and document the local threat model.
- [x] Persist graphs atomically and keep a recoverable previous version.
- [x] Add schema migrations and fixtures for every persisted schema version.
- [x] Recover unsaved UI edits after an application or browser interruption.
- [x] Add tests for path traversal, invalid tokens, disallowed origins, interrupted writes, and migration round-trips.

Completion gate: a saved project survives service restart; invalid filesystem paths and unauthenticated requests are rejected; a simulated interrupted write preserves the previous valid graph.

## [ ] Step 4 — Run records, artifact storage, and executor

Goal: execute deterministic demonstration nodes without claiming scientific processing.

- [x] Define run, node-run, artifact, environment, event, and failure schemas.
- [x] Create content identities from inputs, parameters, implementation version, environment, seeds, and relevant settings.
- [x] Implement topological scheduling and explicit dependency states.
- [ ] Run workers outside the API request lifecycle.
- [ ] Stream progress and events to the UI.
- [ ] Implement cancellation and service-restart recovery.
- [ ] Write outputs atomically and keep partial files out of successful artifact records.
- [ ] Implement cache reuse and downstream invalidation.
- [ ] Add non-scientific fixture nodes for copy, delay, failure, branching, and review pause.
- [ ] Display run status, logs, artifacts, cached status, and actionable failures in the run drawer.

Completion gate: a branched fixture workflow runs, reuses its cache on the second run, invalidates only affected descendants after a parameter change, survives service restart, and cancels without producing successful partial artifacts.

## [ ] Step 5 — BIDS EEG import and signal inspection

Goal: introduce the first real scientific dependency with no signal transformation.

- [ ] Select and document a small licensed BIDS EEG fixture or reproducible retrieval process with checksum.
- [ ] Add MNE-Python and MNE-BIDS as an optional, pinned EEG dependency group.
- [ ] Record upstream versions, licenses, citations, and installation status in node metadata.
- [ ] Implement BIDS EEG discovery and essential metadata validation.
- [ ] Create input content identities without copying the full dataset.
- [ ] Implement a read-only signal-inspection node.
- [ ] Produce downsampled trace, channel, event, and spectrum previews.
- [ ] Persist annotations and researcher inspection decisions separately from source data.
- [ ] Provide errors for unsupported formats and incomplete metadata.
- [ ] Add an independently reviewed direct-MNE reference script.

Completion gate: BrainLearn and the reference script identify the same recordings, channels, sampling frequency, events, and selected summary values on the pinned fixture. Source files remain unchanged.

Scientific review required before checking this step.

## [ ] Step 6 — First processing node and code-generation contract

Goal: execute a real filter and prove that the same graph can produce readable Python.

- [ ] Implement the MNE band-pass/notch filter adapter with explicit units and parameter constraints.
- [ ] Show before/after signal and spectrum previews.
- [ ] Record filter design, phase behavior, edge handling, software version, and warnings.
- [ ] Define a versioned code-rendering interface for node manifests.
- [ ] Generate a minimal standalone Python script for BIDS import, inspection, and filtering.
- [ ] Keep data paths configurable and escape generated literals safely.
- [ ] Include dependency versions and citations with the export.
- [ ] Add tests that execute the generated script on the fixture.
- [ ] Compare internal and generated-code outputs using predeclared numerical tolerances.

Completion gate: the internal adapter, direct-MNE reference, and generated Python script agree within documented tolerances on the same fixture and parameters. Changing filter parameters updates both execution and exported code.

Scientific review required before checking this step.

## [ ] Step 7 — Explicit quality control and ICA review

- [ ] Define immutable QC review records linked to input and node identities.
- [ ] Implement bad-channel review and annotation persistence.
- [ ] Implement ICA fitting as a separate operation from ICA component rejection.
- [ ] Provide component maps, time courses, spectra, and relevant diagnostics.
- [ ] Require an explicit researcher decision before applying exclusions.
- [ ] Invalidate an old decision when its input or fitted ICA artifact changes.
- [ ] Export the reviewed component choices to Python code and provenance.
- [ ] Add resume, rejection, and stale-decision tests.

Completion gate: the workflow pauses for review, resumes only after a persisted decision, invalidates that decision after an upstream change, and reproduces the reviewed output in exported Python.

Scientific review required before checking this step.

## [ ] Step 8 — Complete descriptive EEG workflow

- [ ] Implement explicit event selection and event diagnostics.
- [ ] Implement epoching with units, rejection rules, and baseline settings.
- [ ] Implement ERP averaging and uncertainty summaries.
- [ ] Implement PSD analysis with documented estimator settings.
- [ ] Join ERP and PSD artifacts into an HTML report.
- [ ] Report exclusions, failures, participant counts, and denominators.
- [ ] Generate methods text from actual run records with human-review placeholders.
- [ ] Generate a deduplicated citation bundle for executed methods.
- [ ] Compare every certified node against the direct-MNE reference workflow.
- [ ] Add a complete tutorial using the pinned public/synthetic dataset.

Completion gate: a clean installation completes the full EEG template through both GUI and CLI; results match the reviewed reference workflow; report, provenance, methods, and citations describe the actual run.

Scientific review required before checking this step.

## [ ] Step 9 — Complete Python project and notebook export

- [ ] Export `workflow.py`, configuration, README, environment lock, provenance, citations, and expected output descriptions.
- [ ] Export a readable notebook that separates setup, inspection, QC decisions, processing, and results.
- [ ] Provide readable direct-library mode and exact BrainLearn-runner replay mode.
- [ ] Ensure generated projects do not depend on the browser interface.
- [ ] Add deterministic formatting and snapshot review for generated code.
- [ ] Run exported projects in a clean environment during integration testing.
- [ ] Prevent embedded secrets, session tokens, and unintended absolute personal paths.
- [ ] Document which aspects are exact replay and which allow tolerance-based agreement.

Completion gate: a second clean environment runs the exported Python project without the BrainLearn GUI and reproduces the certified workflow within the declared agreement level.

## [ ] Step 10 — CI, packaging, and supported-platform matrix

- [ ] Add GitHub Actions for Python lint/type/tests and frontend lint/tests/build.
- [ ] Add Linux, macOS, and Windows clean-install smoke tests.
- [ ] Pin third-party actions to commit SHAs and minimize permissions.
- [ ] Add schema compatibility and generated-code integration jobs.
- [ ] Add scheduled scientific reference tests.
- [ ] Add dependency-license inventory and production vulnerability scanning.
- [ ] Build a launcher/installer spike without changing the core architecture.
- [ ] Publish an explicit OS/architecture/backend support matrix.
- [ ] Define release candidates, signing/checksum procedure, rollback, and changelog generation.
- [ ] Verify that old projects keep their recorded environment rather than silently upgrading.

Completion gate: a release candidate installs and completes the certified reference workflow on every advertised platform/backend combination. Unverified combinations remain clearly labeled unsupported or experimental.

## [ ] Step 11 — Public beta and contributor readiness

- [ ] Add CONTRIBUTING, CODE_OF_CONDUCT, SECURITY, GOVERNANCE, CHANGELOG, and CITATION.cff.
- [ ] Publish a worked “add a node” contributor tutorial.
- [ ] Document scientific-review and plugin-ownership expectations.
- [ ] Run installation and task-completion pilots with 5–8 users.
- [ ] Classify failures and revise the interface before larger evaluation.
- [ ] Recruit at least three independent pilot laboratories.
- [ ] Record repeat use, support burden, and independently completed analyses.
- [ ] Obtain any required ethics determination before formal usability research.

Completion gate: external users can install BrainLearn and finish the bounded EEG task without developer intervention; at least three independent labs complete an analysis and at least two return for another use.

## [ ] Step 12 — Stable EEG release and software paper

- [ ] Freeze a release candidate and complete license/citation inventory.
- [ ] Archive the release and obtain a version DOI.
- [ ] Publish versioned documentation and tutorial data instructions.
- [ ] Complete usability, replay, and semantic-guardrail evaluation.
- [ ] Prepare `paper.md`, bibliography, figures, limitations, and AI-use disclosure.
- [ ] Recheck current JOSS eligibility and policy at submission time.
- [ ] Submit only after sufficient public history and demonstrated research use.
- [ ] Track reviewer requests as repository issues and preserve the reviewed release.

Completion gate: stable release evidence is archived, the paper accurately describes tested behavior, and the chosen journal's current eligibility requirements are satisfied.

## [ ] Step 13 — MRI and advanced ML expansion

This step starts only after the EEG release is supportable by the available maintainers.

- [ ] Revalidate demand and choose one bounded MRI scientific question.
- [ ] Add NIfTI inspection, spatial metadata validation, and alignment overlays.
- [ ] Add selected Nilearn analysis through reviewed adapters.
- [ ] Treat FSL as an optional separately installed integration unless written licensing review approves another model.
- [ ] Add participant-aware scikit-learn templates and nested evaluation.
- [ ] Add PyTorch only after resource, determinism, and checkpoint contracts exist.
- [ ] Add SHAP/Captum explanations only for compatible models with explicit reference choices and limitations.

Completion gate: each new certified modality or ML template has its own reference workflow, scientific review, supported-platform matrix, export path, and independent user evidence.

## Completion log

Add one row whenever a task or top-level step changes state. Do not rewrite prior evidence.

| Date | Step | State | Evidence | Commit/review |
|---|---|---|---|---|
| 2026-09-12 | Step 0 repository/proposal baseline | Complete | Repository, proposal, handoff, and design asset inspected | `e184cfe` |
| 2026-09-12 | Step 1 executable foundation | Complete | Ruff, formatting, strict mypy, 10 pytest tests, ESLint, Prettier, 1 Vitest test, production build, live API/UI inspection | `ed2fc78`, monitored review |
| 2026-09-12 | Step 0 licensing | Complete | BSD-3-Clause root/package notices; wheel metadata and embedded license files inspected; 10 pytest tests and frontend build passed | `0a76335` |
| 2026-09-12 | Step 2 individual tasks | Verified; monitoring pending | `uv run pytest -q`: 18 passed; `npm --prefix apps/web run test`: 5 passed in 3 files; live POST gate: BIDS EEG → Inspect Signal round-tripped unchanged and valid, BIDS EEG → Filter returned edge-linked `incompatible_port_type`; Ruff, Ruff format, strict mypy, ESLint, Prettier, production build, and `git diff --check` passed | Uncommitted; awaiting monitored review |
| 2026-09-12 | Step 2 manifest authority repair | Verified; monitoring pending | Registry-aware validation rejects altered node label/category/description/review behavior and parameter label/required/description; focused tests prove ICA review cannot be disabled. Full baseline: 21 pytest tests and 5 Vitest tests passed; Ruff, Ruff format, strict mypy, ESLint, Prettier, production build, and `git diff --check` passed | Uncommitted; awaiting monitored review |
| 2026-09-12 | Step 2 monitored gate | Complete | Reviewer exercised empty-canvas node creation, compatible and incompatible connections, parameter editing, undo/redo, node removal/restoration, and backend issue display in the live UI; reviewer inspected manifest authority and reran 21 pytest tests, 5 Vitest tests, Ruff, formatting, strict mypy, ESLint, production build, production audit, and `git diff --check` | Monitored review complete; commit pending |
| 2026-09-12 | Step 3 individual tasks | Verified; monitoring pending | `uv run pytest -q`: 32 passed (11 new project/security tests); `npm --prefix apps/web run test`: 10 passed in 5 files (5 new persistence/project tests); live gate: create 200, save 200 with previous backup, reopen-after-restart 200 with edited name, traversal 403, unauth 401, evil Origin 403; Ruff, Ruff format (22 files), strict mypy (11 files), ESLint, Prettier, production build, `git diff --check` passed | Uncommitted; awaiting monitored review |
| 2026-09-12 | Step 3 review findings (REVIEW.md) | Verified; monitoring pending | Resolved P1 save-race (revision guard), P1 unique IDs (counter sync + allocator), P1 last-valid recovery (invalid WIP never replaces previous), P2 stale validation (sequence guard + connect guard), P2 token scope (sessionStorage). `uv run pytest -q`: 33 passed; `npm --prefix apps/web run test`: 16 passed in 5 files; live gate: create 200, save 200, invalid save 200 with valid False and previous kept as last valid, reopen 200, traversal 403, unauth 401, evil Origin 403; Ruff, Ruff format, strict mypy (11 files), ESLint, Prettier, production build, `git diff --check` passed | Uncommitted; awaiting monitored review |
| 2026-09-12 | Step 3 follow-up review (stale association + recovery validation) | Verified; monitoring pending | Stale project responses fully ignored via operation-identity + revision guard (no path/name reuse); recovery validation routed through sequence guard. New tests: edit-during-open, overlapping opens reverse order + subsequent save target, delayed-recovery validation race. `uv run pytest -q`: 33 passed; `npm --prefix apps/web run test`: 19 passed in 5 files; live gate: create 200, save 200, invalid save 200 (False, previous kept), reopen 200, traversal 403, unauth 401, evil Origin 403; Ruff, Ruff format (23 files), strict mypy (11 files), ESLint, Prettier, production build, `git diff --check` passed | Uncommitted; awaiting monitored review |
| 2026-09-12 | Step 3 P1 active-vs-folder identity split | Verified; monitoring pending | `activeProjectPath` stored separately from editable `folderInput`; drafts pair workflow with active path only; Save uses active path and is disabled with none; failed/stale ops change nothing. New tests: type-B-then-failed-open keeps A draft + Save targets A; open-B atomically switches graph + identity. `uv run pytest -q`: 33 passed; `npm --prefix apps/web run test`: 21 passed in 5 files; live gate: create 200, save 200, invalid save 200 (False, previous kept), reopen 200, traversal 403, unauth 401, evil Origin 403; Ruff, Ruff format (23 files), strict mypy (11 files), ESLint, Prettier, production build, `git diff --check` passed | Uncommitted; awaiting monitored review |
| 2026-09-12 | Step 3 monitored gate | Complete | Active project path and name are isolated from target-form values; recovery preserves both and legacy drafts omit unknown names; selecting Recent cannot rename the active project. Full gate: `uv run pytest -q` 33 passed; frontend 24 passed in 5 files; Ruff, Ruff format (23 files), strict mypy (11 files), ESLint, Prettier, production build, production audit (0 vulnerabilities), and `git diff --check` passed. Prior live gate verified restart recovery, path traversal 403, unauthenticated 401, hostile Origin 403, and last-valid workflow preservation. | `77e4686`, `fd1479b`; monitored review complete |
| 2026-09-12 | Step 4A execution contracts | Verified; monitoring pending | New `execution.py` (7 records, run/node state enums, lifecycle validators, 7 migration entry points) and `identity.py` (canonical JSON, SHA-256 `brainlearn-v1` domains, non-finite/non-JSON rejection); 7 fixtures under `tests/fixtures/`; `tests/test_execution_contracts.py` with 44 tests (round-trips, migrations, stability, per-input sensitivity, exclusion, lifecycle/event/path/timestamp invariants). `docs/execution-contracts.md` added. Full baseline: `uv run ruff check .` passed; `ruff format --check .` 27 files clean; `mypy` 13 files clean; `uv run pytest -q` 77 passed; ESLint passed; Prettier passed; Vitest 24 passed in 5 files; production build passed; `npm audit --omit=dev` 0 vulnerabilities; `git diff --check` passed. Step 4 heading left unchecked; stopped after 4A per plan. | Uncommitted; awaiting monitored review |
| 2026-09-12 | Step 4A monitoring review | Changes requested | Baseline reproduced: Ruff and formatting passed (27 files), strict mypy passed (13 files), pytest 77 passed, ESLint/Prettier passed, Vitest 24 passed in 5 files, production build passed, audit found 0 vulnerabilities, and `git diff --check` passed. Direct probes found Windows absolute artifact paths accepted; dangling dependencies and queued runs containing succeeded nodes accepted; `1` and `1.0` hashed differently; environment payloads could override identity schema version; and naive/reversed timestamps accepted. First two Step 4 boxes reopened pending contract repairs and regression tests in `REVIEW.md`. | Uncommitted; monitoring review failed |
| 2026-09-12 | Step 4A review repairs (REVIEW.md 5 findings) | Verified; monitoring pending | Repaired Windows/drive/UNC path rejection with canonical `/` storage; run referential integrity (unique IDs, resolving/acyclic deps, event/failure/review/artifact refs) plus aggregate rules (succeeded/queued/terminal); numeric canonicalization (`1`/`1.0`, `-0.0`/`0`, nested, JSON round-trip); tz-aware timestamps with chronology (`created<=started<=finished`, `requested<=decided`); identity format validation, schema-marker-wins, attempt 0 for skipped/reused. Fixtures regenerated with genuine identities; run fixture is a self-contained inspect→filter graph. `docs/execution-contracts.md` updated. Full baseline: `uv run ruff check .` passed; `ruff format --check .` 27 files clean; `mypy` 13 files clean; `uv run pytest -q` 120 passed (87 contract tests); ESLint passed; Prettier passed; Vitest 24 passed in 5 files; production build passed; `npm audit --omit=dev` 0 vulnerabilities; `git diff --check` passed. First two Step 4 boxes left unchecked for the monitor. | Uncommitted; awaiting monitored review |
| 2026-09-12 | Step 4A second monitoring review | Changes requested | Full baseline reproduced: Ruff and formatting passed (27 files), strict mypy passed (13 files), pytest 120 passed, ESLint/Prettier passed, Vitest 24 passed in 5 files, production build passed, audit found 0 vulnerabilities, and `git diff --check` passed. Direct probes still accepted duplicate dependency references; stale node identities after hashed fields or the embedded environment changed; queued `attempt: 1` despite `counts_as_execution` false; and cancelled runs finishing before creation. Four focused repairs and regression tests are specified in `REVIEW.md`; first two Step 4 boxes remain unchecked. | Uncommitted; monitoring review failed |
| 2026-09-12 | Step 4A second-review repairs (4 findings) | Verified; monitoring pending | Node `content_identity` recomputed from declared fields with mismatch rejected; run environment identity computed from `EnvironmentRecord` and required on every node run; duplicate dependency references rejected; `attempt: 0` required for queued/skipped/reused with positive attempts elsewhere; `created<=finished` enforced even when `started_at` is absent. New tests: per-field stale-identity mutations, substituted/edited environment rejection, duplicate deps, queued/running attempt boundaries, cancelled-before-start chronology. `docs/execution-contracts.md` updated. Full baseline: `uv run ruff check .` passed; `ruff format --check .` 27 files clean; `mypy` 13 files clean; `uv run pytest -q` 133 passed (100 contract tests); ESLint passed; Prettier passed; Vitest 24 passed in 5 files; production build passed; `npm audit --omit=dev` 0 vulnerabilities; `git diff --check` passed. First two Step 4 boxes left unchecked for the monitor. | Uncommitted; awaiting monitored review |
| 2026-09-12 | Step 4A final monitored gate | Complete | Reviewer independently reproduced all four repaired rejection cases, including a coherently rehashed per-node environment substitution. Reviewer aligned the queued model default to `attempt: 0` and added an omitted-default regression test. Full gate: `uv run pytest -q` 134 passed; frontend 24 passed in 5 files; Ruff, Ruff format (27 files), strict mypy (13 files), ESLint, Prettier, production build (1,837 modules), production audit (0 vulnerabilities), and `git diff --check` passed. The first two Step 4 items are approved; Step 4 remains open for 4B–4E. | Monitored review complete |
| 2026-09-12 | Step 4B run store and scheduler | Verified; monitoring pending | New `scheduler.py` (deterministic topological order, ready-node and downstream closures) and `run_store.py` (`runs/<id>/run.json` CRUD under explicit-root auth, atomic writes, nonterminal discovery, restart recovery voiding running nodes to queued/attempt-0 with waiting reviews preserved). Run endpoints create/open/save/list/recover behind the session token. Recovery follows the queued-attempt-0 rule: voided numbers live in events, next execution assigns a fresh attempt. New `run-running-1.0.json` fixture, `test_scheduler.py` (10 tests) and `test_run_store.py` (12 tests: round-trips, traversal/auth/overwrite guards, recovery semantics, restart simulation, endpoints). `docs/execution-contracts.md` extended. Full baseline: `uv run ruff check .` passed; `ruff format --check .` 31 files clean; `mypy` 15 files clean; `uv run pytest -q` 156 passed; ESLint passed; Prettier passed; Vitest 24 passed in 5 files; production build passed; `npm audit --omit=dev` 0 vulnerabilities; `git diff --check` passed. Step 4 heading left unchecked; workers/streaming/cancellation/cache/UI excluded per slice boundary. | Uncommitted; awaiting monitored review |
| 2026-09-12 | Step 4B review repairs (6 findings) | Verified; monitoring pending | Terminal runs immutable on save (read-before-write, byte-preserved, store + endpoint tests for succeeded/failed/cancelled); recovery reconciles every nonterminal run (running/all-queued→queued, mixed→running, all-completed→succeeded with finish time, waiting preserved only with valid prerequisites, stale reviews requeued transitively); corrupt `run.json` files raise with path+cause on list/recover (store + endpoint tests); scheduler entry points share unknown/duplicate/cycle validation with a documented state-key rule; run APIs require the exact authorized project root with manifest checks (descendant/deleted rejections); `node_queued` events carry the voided attempt structurally with attempt>1 and round-trip tests. `docs/execution-contracts.md` updated. Full baseline: `uv run ruff check .` passed; `ruff format --check .` 31 files clean; `mypy` 15 files clean; `uv run pytest -q` 175 passed (41 scheduler/store tests); ESLint passed; Prettier passed; Vitest 24 passed in 5 files; production build passed; `npm audit --omit=dev` 0 vulnerabilities; `git diff --check` passed. Scheduling bullet and Step 4 heading left unchecked for the monitor. | Uncommitted; awaiting monitored review |
| 2026-09-12 | Step 4B second-review repairs (3 findings) | Verified; monitoring pending | Discovery never follows symlinks, enforces the run-ID grammar, requires record/directory ID equality, and authorizes candidates before reading; per-run locks serialize create/save/recovery read-check-write sequences with deterministic terminal-wins and single-create race tests plus a documented in-process boundary; downstream queries reject unknown sources. `docs/execution-contracts.md` updated. Full baseline: `uv run ruff check .` passed; `ruff format --check .` 31 files clean; `mypy` 15 files clean; `uv run pytest -q` 184 passed (50 scheduler/store tests); ESLint passed; Prettier passed; Vitest 24 passed in 5 files; production build passed; `npm audit --omit=dev` 0 vulnerabilities; `git diff --check` passed. Scheduling bullet and Step 4 heading left unchecked for the monitor. | Uncommitted; awaiting monitored review |
| 2026-09-12 | Step 4B monitoring review | Changes requested | Full baseline reproduced: Ruff and formatting passed (31 files), strict mypy passed (15 files), pytest 156 passed, ESLint/Prettier passed, Vitest 24 passed in 5 files, production build passed, audit found 0 vulnerabilities, and `git diff --check` passed. Direct probes found terminal history writable; running/all-queued records unreconciled; dependent reviews preserved after their running prerequisite is reset; malformed run files silently omitted; `ready_node_ids` accepting an unknown graph dependency when a state exists; authorized descendants accepted as project roots; and interrupted attempt numbers retained only in message text. Repair criteria are in `REVIEW.md`; Step 4B remains open. | Uncommitted; monitoring review failed |
| 2026-09-12 | Step 4B second monitoring review | Changes requested | All six prior findings verified repaired and full baseline reproduced: Ruff and formatting passed (31 files), strict mypy passed (15 files), pytest 175 passed, ESLint/Prettier passed, Vitest 24 passed in 5 files, production build passed, audit found 0 vulnerabilities, and `git diff --check` passed. Controlled probes found discovery following an external run-directory symlink and accepting a directory/record-ID mismatch; overlapping terminal and stale saves restoring the nonterminal record; and `downstream_ids` silently ignoring an unknown source. Three focused repairs are specified in `REVIEW.md`; Step 4B remains open. | Uncommitted; monitoring review failed |
| 2026-09-12 | Step 4B final monitored gate | Complete | Reviewer independently reproduced unknown-source rejection, external symlink exclusion, directory/record-ID mismatch rejection, and terminal-wins concurrent saving; focused tests also cover symlinked files, invalid directory names, mixed unknown sources, and duplicate-create races. Full gate: `uv run pytest -q` 184 passed; frontend 24 passed in 5 files; Ruff, Ruff format (31 files), strict mypy (15 files), ESLint, Prettier, production build (1,837 modules), production audit (0 vulnerabilities), and `git diff --check` passed. In-process locking is approved provided Step 4C workers write only through `RunStore`; a separate writer process requires cross-process coordination. Scheduling is approved; Step 4 remains open for 4C–4E. | Monitored review complete |

## Next assignment

Read `AGENTS.md`, `REVIEW.md`, and this plan before acting. Step 4A is approved
in `25bf419`. The worktree currently contains uncommitted Step 4B plus a repair
row claiming the three findings from the second monitoring review are fixed.

### Immediate action — submit Step 4B repairs for review

1. Inspect the shared worktree and preserve every existing change. Do not
   rewrite the monitoring rows or remove prior evidence.
2. Confirm the repair implementation covers all three current findings:
   discovered run paths cannot follow symlinked directories or files and must
   match their directory ID; create/save/recovery share per-run serialization;
   and downstream queries reject unknown source IDs.
3. Confirm deterministic tests cover directory and file symlinks, invalid and
   mismatched run-directory IDs, terminal-versus-stale concurrent saves,
   duplicate concurrent creates, unknown-only sources, and mixed
   known/unknown sources.
4. Run the full repository verification baseline and report exact counts. The
   latest unreviewed row reports 184 Python tests and 24 frontend tests; do not
   copy those numbers without reproducing them.
5. Leave the scheduling bullet and Step 4 heading unchecked. Stop and request
   monitoring review. Do not commit and do not begin Step 4C.

Step 4B remains limited to `packages/core` scheduler/contracts,
`packages/server` run persistence and endpoints, their focused tests and
fixtures, `docs/execution-contracts.md`, `REVIEW.md`, and this plan. Do not add
worker execution, cache storage, scientific dependencies, UI run controls, or
arbitrary command execution.

### Step 4C — Worker lifecycle after monitored approval

Begin this slice only after the monitor approves and commits Step 4B.

1. Define a bounded worker protocol and lifecycle service. Work must execute
   outside FastAPI request handlers. Use an allowlisted registry of internal
   demonstration-node functions; never accept shell commands, module paths, or
   arbitrary Python from API payloads.
2. Route every run mutation through the Step 4B serialized writer path. Assign
   a fresh positive attempt when queued work starts, using structured event
   history to remain monotonic after restart.
3. Drive nodes in deterministic topological order. Start only ready queued
   nodes; propagate failed/cancelled prerequisites to the transitive downstream
   closure as `dependency_skipped`; author the run-level failed or cancelled
   transition that Step 4B deliberately deferred.
4. Append gapless structured events for run/node start, success, failure,
   dependency skip, review pause/resume, recovery, and cancellation. Provide a
   one-way local progress stream to the UI, preferring SSE unless bidirectional
   transport is demonstrably required.
5. Implement cooperative cancellation for queued and running work. Cancellation
   must reach a terminal state after a bounded wait and remain idempotent when
   requested repeatedly or after completion.
6. Stage every output beneath its run directory and atomically promote it only
   after node success. Failed, cancelled, or interrupted attempts must leave no
   successful `ArtifactRecord`; clean or quarantine temporary files without
   deleting prior successful history.
7. Recover service interruptions through the approved Step 4B recovery path,
   then resume schedulable work with fresh attempts while preserving valid
   pending reviews.
8. Add deterministic demonstration adapters needed to exercise lifecycle
   behavior only: short delay, controlled failure, and small file copy are
   sufficient here. Full branching/review/cache demonstration remains Step 4E.
9. Test state transitions, ordering, concurrent API requests, restart during
   work, cancellation before and during work, failure propagation, event-stream
   reconnect, attempt monotonicity, and atomic artifact promotion. Avoid tests
   that merely mirror implementation details.
10. Update `docs/execution-contracts.md`, add exact verification evidence to the
    completion log, check only the Step 4 worker/stream/cancellation/artifact
    bullets whose complete behavior passes, and request monitoring review.
    Leave the Step 4 heading unchecked and stop before Step 4D.

### Remaining Step 4 order

1. **Step 4D — Cache and invalidation:** reuse content-addressed outputs and
   invalidate only descendants affected by input, parameter, implementation,
   environment, seed, or setting changes.
2. **Step 4E — Demonstration gate:** complete the non-scientific branching,
   failure, review-pause, and cache fixtures; display run state, logs,
   artifacts, cache state, and actionable failures; execute the full Step 4
   completion gate.

Every slice ends with exact command results and a monitoring review. The Step
4 heading remains unchecked until the complete branched-workflow gate passes.
