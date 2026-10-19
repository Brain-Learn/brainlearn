# BrainLearn monitored implementation review

Review date: 2026-09-16

## Current review

Scope: final monitoring review of Step 5A.5 after the redirect/archive
implementation and two rounds of archive-limit repairs on PR #9.

Status: **approved**. Redirect validation, archive containment, declared and
live extraction limits, cancellation/retry behavior, verified finalization,
and the repaired ZIP/tar edge cases all pass. No blocking finding remains.

## Findings resolved

1. Valid large ZIPs are no longer rejected from total byte size. A bounded
   EOCD/ZIP64 tail probe seeks to the real file tail and checks member count
   before `ZipFile` construction; `infolist()` retains the authoritative
   post-parse check. Regressions prove a 5 MB one-member ZIP is accepted and
   an over-member archive is rejected without constructing the ZIP parser.
2. Tar and tar.gz enforce compression ratio from declared member sizes before
   extraction or discard and from actual bytes while streaming. Skipped
   members share the same accounting, so a highly compressed member cannot
   bypass the ratio limit during resume. Both written and skipped ~924x bombs
   reject before output bytes land.
3. The original redirect and extraction guarantees remain intact: approved
   HTTPS hosts and bounded hops only; no credential, signed-target, or scheme
   downgrade redirects; portable allowlisted members only; links, traversal,
   duplicates, conflicts, special files, oversized expansion, and writes
   outside staging are refused; successful finalization still requires the
   exact verified catalog tree.

## Verification reproduced

- Direct probes: the 5,000,118-byte ZIP reports one member from the bounded
  pre-parser, and a ~924x skipped tar.gz member is rejected as
  `ratio-exceeded`.
- Focused archive/redirect/download suites: 95 passed with two upstream
  Starlette/AnyIO warnings.
- `uv run ruff check .`: passed.
- `uv run ruff format --check .`: 62 files clean.
- `uv run mypy packages/core/src packages/server/src`: 25 source files clean.
- `uv run pytest -q`: 653 passed, 1 opt-in live smoke skipped, with the same
  two upstream warnings.
- `npm --prefix apps/web run lint`: passed.
- `npm --prefix apps/web run format:check`: passed.
- Complete Vitest suite: 152 passed in 16 files.
- `npm --prefix apps/web run build`: passed, 1,843 modules transformed.
- `npm --prefix apps/web audit --omit=dev`: 0 vulnerabilities.
- `git diff --check`: passed before this review update.
- Required GitHub CI jobs passed on implementation head `bf82a4b`.

## Scope decision

Step 5A.5 is approved. The redirect/archive-hardening checklist item is checked;
Step 5 remains open. The next work unit is only Step 5A.6: preserve local/private
dataset import as an equal offline path and record an immutable local identity.
Do not begin the pinned integration fixture, MNE/MNE-BIDS, BIDS discovery, signal
inspection, or later scientific work in that unit.

## Post-merge CI follow-up

The first `main` run after PR #9 exposed a pre-existing run-review scheduler
race: a driver holding a stale pre-decision record could enter settlement after
an approval was saved and overwrite the durable decision by parking the run as
`waiting_for_review`. GitHub Actions run 35098055658 reproduced the failure in
`test_two_runs_reuse_trunk_and_reexecute_zero_output_nodes` while every other
Python test and the complete frontend job passed.

The repair serializes review decisions with every parking transition, reloads
the run under that guard before settlement, and redrives newly ready work rather
than treating it as a scheduler stall. A deterministic regression blocks the
driver immediately before settlement, records an approval, and proves the stale
snapshot cannot overwrite it; the run succeeds with exactly one
`review_decided` event.

Local verification for the repair branch: Ruff and format passed (62 files),
strict mypy passed (25 source files), pytest passed 654 tests with 1 opt-in live
smoke skipped and 2 upstream warnings, ESLint and Prettier passed, Vitest passed
152 tests in 16 files, the production build passed (1,843 modules), the
production npm audit found 0 vulnerabilities, and `git diff --check` passed.
PR #10 passed both required hosted checks, received a monitored approval
comment, and was squash-merged as `7a72f54`. The resulting `main` run
35102093017 passed both Python and frontend jobs, including the test that had
failed before the repair. No Step 5A.6 work has started.

## Step 5A.6 first monitoring review

Review date: 2026-09-17

Scope: PR #12, local/private offline dataset import and immutable local
identity.

Status: **changes requested**. The complete baseline gate passes, but five
contract and integrity findings remain:

1. A same-size in-place mutation can evade verification when the writer
   restores `st_mtime_ns`. A direct 128 KiB probe modified the second half
   during hashing, restored mtime, and was accepted because descriptor,
   pathname, and tree snapshots omit `st_ctime_ns`.
2. `LocalImportRecord` does not bind a ready record to its embedded lock. A
   record whose `local_path` is `source-b` accepts a valid lock naming
   `source-a`, and the record does not require the local provider/snapshot,
   restricted access, or null catalog identity.
3. `DatasetLock` exempts every `provider="local"` record from public metadata
   requirements without enforcing the documented local invariants. A directly
   constructed, correctly re-identified record accepted public access, a
   non-local snapshot, EEG/task/participant/template claims, and MIT metadata.
4. The browser guard accepts state-inconsistent records and incomplete locks,
   including a ready record with no lock and expected-file entries without
   path, size, or hash validation.
5. The claimed location-independent identity still includes the final source
   directory name via `dataset_id`; identical bytes and explicit metadata in
   `folder-a` and `folder-b` produced different identities.

The monitor reproduced Ruff and format success (64 files), strict mypy success
(26 source files), pytest 699 passed with 1 opt-in smoke skipped and 2 upstream
warnings, ESLint and Prettier success, Vitest 159 passed in 17 files, production
build success (1,844 modules), production audit with 0 vulnerabilities, and a
clean diff check. Both required hosted CI jobs are also green on `f69c7e7`.
These passing gates do not cover the direct probes above. PR #12 remains open;
Step 5A.6 stays unchecked and no later Step 5 work may begin.

## Step 5A.6 final monitoring review

Review date: 2026-09-17

Scope: PR #12 after repairs at `0daa1f7`, local/private offline dataset import
and immutable local identity.

Status: **approved**. All five blocking findings are resolved. The monitor
reproduced the restored-mtime mutation refusal, location-independent identity,
record/lock binding refusal, and model-boundary local-invariant refusal. Focused
browser guard and local-import tests passed 17 tests.

The complete gate also passed: Ruff; formatting for 64 files; strict mypy for 26
source files; pytest with 703 passed, 1 opt-in smoke skipped, and 2 upstream
warnings; ESLint; Prettier; Vitest with 161 passed in 17 files; production build
with 1,844 modules; production audit with 0 vulnerabilities; and
`git diff --check`. Both required hosted CI jobs were green on `0daa1f7`.

No scientific assumptions changed and no blocking findings remain. Step 5A.6 is
approved and checked. Step 5 remains open; the next bounded work unit is Step
5A.7, deterministic mock-provider coverage plus one tiny pinned scheduled
integration download.

## Step 5A.7 first monitoring review

Review date: 2026-09-17

Scope: PR #13, deterministic provider tests and pinned integration smoke at
`a298a81`.

Status: **changes requested**. The complete baseline gate and the opt-in live
download pass, but four contract gaps remain:

1. Drift diagnostics are not secret-free. `_sanitize_text` removes query and
   fragment suffixes but retains URL userinfo and arbitrary exception text. A
   direct `ProviderError` probe emitted `alice:TOPSECRET` verbatim into the JSON
   diagnostic that the workflow would upload.
2. The documented CC0 claim is not established by the pin. Live resolution of
   `ds001037:00001` still yields `license_name="Unverified OpenNeuro license
   (pending curator review)"`, null SPDX, and reuse terms pending verification,
   while the documentation and completion log call it CC0. The assignment
   requires an exact, reviewed license.
3. The offline guard patches `urllib.request.urlopen`, but both production
   OpenNeuro transports use `OpenerDirector.open`; an accidental provider call
   during reopening would bypass the claimed network block.
4. `tests/fixtures/pinned-integration-snapshot-1.0.json` is described as the
   reviewed pin but is never loaded. `FIXTURES` is unused, so the fixture and
   in-code manifest can diverge while all tests and the scheduled smoke pass.

The monitor reproduced Ruff and format success (67 files), strict mypy success
(28 source files), pytest 710 passed with 2 opt-in tests skipped and 2 upstream
warnings, ESLint and Prettier success, Vitest 161 passed in 17 files, production
build success (1,844 modules), production audit with 0 vulnerabilities, and a
clean diff check. Both required hosted CI jobs are green on `a298a81`. The
opt-in live smoke also passed independently in 2.60 seconds. Passing gates do
not cover the four probes above. PR #13 remains open; Step 5A.7 stays unchecked
and Step 5A.8 must not begin.

## Step 5A.7 final monitoring review

Review date: 2026-10-03

Scope: PR #13 final head `52e3014`, compared with `9d3e274`.

Status: **approved**. The four original blockers are resolved: diagnostics
never copy untrusted exception text; the pinned snapshot carries reviewed CC0
terms with evidence; offline reopening blocks both the production OpenNeuro
opener and sockets and proves zero provider/source calls; and the reviewed JSON
fixture is validated against the runtime manifest. A second review found and
the branch repaired two more issues before approval: the missing-lock
diagnostic had included an absolute project root, and the initial CC0 mapping
inference was broader than the exact reviewed snapshot. The diagnostic now
contains only a relative dataset path, the CC0 exception is limited to
`ds001037:00001`, other unreviewed snapshots remain pending, and the provider
documentation describes the 36-month grace period consistently. The pin
comments now cite evidence for this exact tag.

Independent clean-checkout review found no remaining blockers. The complete
gate was reproduced at `52e3014`: Ruff passed; formatting passed for 67 files;
strict mypy passed for 28 source files; pytest passed 723 tests, 2 opt-in tests
skipped, and 2 upstream warnings in 35.14 seconds; ESLint and Prettier passed;
Vitest passed 161 tests in 17 files; production build passed with 1,844 modules;
production audit found 0 vulnerabilities; and `git diff --check` passed. The
opt-in live OpenNeuro smoke passed in 2.74 seconds. Both required hosted CI
checks passed in run `37118433673`.

Step 5A.7 is approved and checked. Step 5 remains open. The next bounded work
unit is Step 5A.8: select and document a small licensed BIDS EEG fixture or a
reproducible retrieval process with exact snapshot and checksums. No scientific
processing assumptions were introduced.

## Step 5A.8 initial monitoring review

Review date: 2026-10-03

Scope: PR #15 initial head `24fa992`, compact BIDS EEG fixture retrieval.

Status: **changes requested**. The fixture and provenance claims checked out,
but the retrieval helper had two safety defects: an existing symlink in the
destination path could redirect writes outside that directory and replace
existing files, and it followed redirects before validating the target host.
The monitor reproduced the live five-file hashes, exact snapshot metadata,
CC0 evidence, and associated paper citation. The implementer repaired both
issues on the same PR branch and added regression tests for symlink rejection,
no-overwrite behavior, and redirect rejection before follow-up contact.

## Step 5A.8 final monitoring review

Review date: 2026-10-03

Scope: PR #15 final head `5310fc1`, compared with `4c7efa0`.

Status: **approved**. Re-review confirmed that all initial findings are fixed:
the helper rejects every existing symlink path component; verified files are
accepted only when size and digest match; differing existing paths are never
replaced; atomic installation uses a same-directory hard link; and redirect
hosts are validated before urllib follows them. Three focused regressions
cover the symlink, overwrite, and redirect cases. The manifest and docs agree
with the immutable OpenNeuro snapshot, CC0 policy evidence, file paths, byte
counts, SHA-256 digests, and valid associated publication DOI. The source's
`mockDOI` placeholder is explicitly not treated as a citation identifier.

The monitor reproduced the full gate in a clean Python 3.12 checkout at
`5310fc1`: Ruff passed; formatting passed for 70 files; mypy passed for 28
source files; pytest passed 726 tests, 2 opt-in tests skipped, and 2 upstream
deprecation warnings in 34.95 seconds; ESLint and Prettier passed; Vitest
passed 161 tests in 17 files; production build passed with 1,844 modules;
production audit found 0 vulnerabilities; and `git diff --check` passed. A
fresh live retrieval verified all five files' sizes and SHA-256 digests. Both
required hosted checks passed in GitHub Actions run `37122628393`.

Step 5A.8 is approved and checked. Step 5 remains open. The next bounded work
unit is Step 5A.9: add MNE-Python and MNE-BIDS as an optional, pinned EEG
dependency group. No signal processing or BIDS discovery behavior was added.

## Step 5A.9 final monitoring review

Review date: 2026-10-03

Scope: PR #16 implementation head `072d0a2`, optional pinned EEG dependencies.

Status: **approved**. The diff is limited to the optional `eeg` dependency
group, its resolved lockfile, setup documentation, and implementation-plan
evidence. `mne==1.13.2` and `mne-bids==0.20.0` are exact pins; the lock resolves
their runtime requirements, including the matching MNE dependency. The default
`uv tree --locked` omits both packages, while `uv tree --locked --group eeg`
includes them. A clean Python 3.12 group install imported the pinned versions;
a separate default sync had neither import available. No application startup
imports, node metadata, BIDS discovery, or processing behavior were added.

The clean-checkout Python 3.12 gate passed: Ruff; formatting (70 files); strict
mypy (28 source files); and pytest (726 passed, 2 opt-in tests skipped, 2
upstream deprecation warnings in 33.71 seconds). Frontend ESLint and Prettier
passed; Vitest passed 161 tests in 17 files; the production build passed with
1,844 modules; `npm audit --omit=dev` found 0 vulnerabilities; and
`git diff --check` passed. Both required hosted checks passed on the PR, and
were rerun after the monitor-owned evidence update before merge.

Step 5A.9 is approved and checked. Step 5 remains open; the next bounded item
is recording upstream versions, licenses, citations, and installation status
in node metadata. No scientific processing assumptions or claims were added.

## Step 5A.10 final monitoring review

Review date: 2026-10-03

Scope: PR #17 final head `93e9ab6`, compared with `abb32c4`.

Status: **approved**. The full diff stays within Step 5A.10: NodeManifest
contract 1.1 adds strict software dependency metadata; the registry records
MNE-Python 1.13.2 and MNE-BIDS 0.20.0 with their BSD-3-Clause licenses and
software citations; and the inspector reports package metadata status and its
limits. The exact versions and licenses match the published packages, and the
MNE-Python and MNE-BIDS citations match their canonical upstream records.
Package mapping is coherent: the BIDS input and signal-inspection nodes list
both packages, planned EEG processing nodes list MNE-Python, and non-EEG/demo
nodes do not list these dependencies. No processing or BIDS-discovery behavior
was added.

Installation status is a startup snapshot read through
`importlib.metadata.version`; loading the registry did not import `mne` or
`mne_bids`. In a clean Python 3.12 environment, default sync reported both
packages `missing` with no installed-version value, while the optional `eeg`
group reported MNE 1.13.2 and MNE-BIDS 0.20.0 as `installed`; guarded registry
imports passed in both environments. Focused Python tests passed (27); focused
run-lifecycle tests passed (9). The complete clean-checkout gate passed:
Ruff; formatting (71 files); strict mypy (28 source files); pytest (731
passed, 2 opt-in skips, 2 upstream deprecation warnings in 34.09 seconds);
ESLint and Prettier; Vitest (162 tests in 17 files); production build (1,844
modules); production audit (0 vulnerabilities); and `git diff --check`.

Two initial hosted frontend runs exposed test synchronization races. The final
repair reads current render state for artifact opening, waits for launch and
button readiness, and targets the new run's event cursor directly. Both
required hosted checks passed on final head `93e9ab6` in run `37127604967`.

Step 5A.10 is approved and checked. Step 5 remains open; the next bounded item
is BIDS EEG discovery and essential metadata validation. The registry status
does not certify imports, runtime compatibility, or scientific behavior.

## Step 5A.11 final monitoring review

Review date: 2026-10-04

Scope: PR #18 head `a2b04675c77f6e5ffcf05f42332b11ca3d15f7fe`, compared with
`1e2db6f`.

Status: **approved**. The complete diff is confined to metadata-only raw BIDS
EEG discovery, its API/UI entry points, tests, and documentation. The scanner
checks BIDS 1.x dataset description fields; discovers the specified raw EEG
formats in subject/session EEG directories; applies inherited JSON/TSV
sidecars; validates the required EEG metadata; and returns explicit
unsupported, incomplete, non-BIDS, and no-recording states. It uses bounded
metadata reads, a shared directory-entry budget, and cached sidecar indexes.
Project-root authorization, relative-path validation, symlink rejection, and
the per-session API token are enforced. Recording files are not opened for
signal data, copied, hashed, or changed. The user-facing documentation
explicitly limits this to metadata readiness, not complete BIDS validation or
signal validation.

The pinned `ds002181:1.0.0` fixture was checked against all five manifest file
sizes and SHA-256 values (697,051 bytes total). Discovery returned `ready` for
BIDS 1.2 and one EEGLAB recording, with 124 EEG channels and 31 events. All five
source SHA-256 values were identical after scanning. Focused discovery tests
passed (12 Python, 3 Vitest).

The independent Python 3.12 gate passed: `uv run --locked ruff check .`;
`uv run --locked ruff format --check .` (74 files); `uv run --locked mypy
packages/core/src packages/server/src` (29 source files); and `uv run --locked
pytest -q` (743 passed, 2 opt-in skipped, 2 upstream deprecation warnings in
33.98 seconds). Frontend ESLint and Prettier passed; Vitest passed 165 tests in
18 files; the production build passed with 1,845 modules; `npm audit
--omit=dev` found 0 vulnerabilities; and `git diff --check` passed. Both
required hosted checks passed on the reviewed head in Actions run
`37186231295`.

Step 5A.11 is approved and checked. Step 5 remains open. The next bounded item
is creating input content identities without copying the full dataset. The
scan does not certify signal contents or complete BIDS-validator conformance.

## Step 5A.12 final monitoring review

Review date: 2026-10-04

Scope: PR #19 repair head `35b5ace1719d6686a1aad0929031d95150ebe9e0`, compared
with `5d179def83f8b3ef5ea777fafd6a794fb6f1b0ad`.

Status: **approved**. The repair addresses both review findings. After all
source files are hashed, the service now re-stats every included path and
refuses an input if any stat identity differs from its pre-hash snapshot. The
directory snapshot walk now validates and records only the recording directory
through the selected dataset root, so unrelated ancestors are not inspected.
The new cross-file mutation regression changes `channels.tsv` after its hash
while later file hashes run and confirms that identity creation is refused.
The previously reproduced stale identity no longer occurs.

The clean managed worktree reproduced the focused race regression (1 passed,
17 deselected) and the full Python 3.12 gate: locked all-package sync passed;
Ruff passed; formatting passed (74 files); strict mypy passed (29 source
files); pytest passed 749 tests, 2 opt-in skips, and 2 upstream deprecation
warnings in 34.58 seconds. Frontend ESLint and Prettier passed; Vitest passed
166 tests in 18 files; the production build passed with 1,845 modules; the
production audit found 0 vulnerabilities; and `git diff --check` passed. Both
required hosted checks passed on repair head `35b5ace` in Actions run
`37191289608`.

Step 5A.12 is approved and checked. Step 5 remains open. The next bounded item
is a read-only signal-inspection node; input hashing remains an identity step
and makes no claim about scientific validity or signal correctness.

## Step 5A.13 final monitoring review

Review date: 2026-10-04

Scope: PR #20 repair head `52e1df4fb1a5f7eea30f2fae6637c5b067e8fd69`, compared
with `a3f7464fb08b759c2326b6d301c3771b1f551efd`.

Status: **approved**. The first review found that signal inspection was only
available as a dataset-panel action and that `eeg.inspect` could not execute
in a workflow. The repair adds allowlisted worker adapters for `input.bids_eeg`
and `eeg.inspect`, plus a real worker regression for the connected path. The
workflow stores a source identity-bound recording reference and a JSON metadata
report; neither output stores signal samples. Both nodes bypass the cache. The
inspector calculates source identity before and after MNE closes and refuses to
publish when it changes. The deterministic mutation regression confirms the
run fails with no inspection artifacts or cache. The prior review finding is
resolved.

The clean managed checkout passed the focused worker regression (2 passed, 42
deselected) and the full Python 3.12 gate: locked all-package sync, Ruff,
formatting (74 files), strict mypy (29 source files), and `git diff --check`
passed; pytest passed 754 tests, 2 opt-in skips, and 2 upstream deprecation
warnings in 31.83 seconds. Frontend ESLint and Prettier passed; Vitest passed
167 tests in 18 files; the production build passed with 1,845 modules; and the
production dependency audit found 0 vulnerabilities.

The direct pinned MNE 1.13.2/MNE-BIDS 0.20.0 check on the published
`ds002181:1.0.0` EEGLAB header plus a temporary sparse `.fdt` companion
reported 500 Hz, 62,000 samples, 124 seconds, 125 channels (124 EEG, 1 misc),
and 31 annotations. Input content identity matched before and after inspection;
all six temporary source-file hashes and the source inventory were unchanged.
The API requires its session token and the worker path resolves only an
authorized project-relative dataset. Hosted Python and frontend jobs both
passed on `52e1df4` in Actions run `37196203680`.

Step 5A.13 is approved and checked. Step 5 remains open. No processing,
previews, or researcher annotations were added; reported properties describe
MNE reader metadata and do not establish signal quality or scientific validity.

## Step 5A.14 next-assignment pointer review

Review date: 2026-10-04

Scope: docs-only PR #21 head `7c7fe3a359aa595d08c3a1bce8c355e34eda07f1`,
compared with `9eb6d962e047dead6b1793489fd3bdb17c13407b`.

Status: **approved**. The change only updates the plan's `Next assignment`
section. Its Step 5A.14 pointer matches the first unchecked Step 5 checklist
item: “Produce downsampled trace, channel, event, and spectrum previews.” The
handoff constrains that future unit to bounded previews derived from an
inspected source identity, with read-only source handling, project/resource
limits, focused tests, and a direct-MNE comparison. It explicitly defers
annotation persistence and processing. No checklist item was checked, no
preview implementation was started, and Step 5 remains unchecked.

`git diff --check origin/main...HEAD` passed. Both hosted jobs passed on PR #21
in Actions run `37196688271`. The following implementation assignment remains
Step 5A.14; this review does not start it.


## Step 5A.14 final monitoring review

Review date: 2026-10-04

Scope: PR #22 head `93050a6871c4823c892a39d91bc136d3bb4611af`, compared with
`6c41315d2146038bf65dd6dbe185abc07c8efcc8`.

Status: **approved**. The implementation provides an authenticated BIDS EEG
preview action with bounded channel count, duration, sample work, envelope bins,
annotation output, and Welch frequencies. Preview output carries the verified
source content identity, uses explicitly reported Hamming Welch settings, and
remains transient; it does not persist signal samples, create artifacts, or
modify the source. MNE reads only the requested EEG channels and time range.
The direct-MNE synthetic reference independently checks envelope minima, PSD
frequencies and power, in-window annotations, and unchanged source bytes. The
review first requested a deterministic race regression: code had a pre/post
identity guard, but coverage did not mutate the input during a read. The final
head adds a same-size source mutation in the raw-reader close callback and
confirms that preview generation raises `BidsSignalInspectionError` instead of
returning stale data.

The clean managed checkout passed Ruff, format (75 files), strict mypy (29
source files), and `git diff --check`. `uv run --locked pytest -q` passed 756
tests with 2 optional skips and 2 upstream deprecation warnings (34.38s). The
pinned optional EEG direct-MNE test passed (1 test). ESLint and Prettier passed;
Vitest passed 169 tests in 18 files; production build passed with 1,846 modules;
production audit found 0 vulnerabilities. Both required hosted jobs passed on
implementation head `93050a6` in Actions run `37205077644`.

Step 5A.14 is approved and checked. Step 5 remains unchecked because its
scientific completion gate includes additional work. Preview settings and
synthetic numerical agreement do not certify signal quality or experimental
suitability.


## Step 5A.15 next-assignment pointer review

Review date: 2026-10-04

Scope: docs-only PR #23 head `53c6833f8bd89fde851d462078e79df120881003`,
compared with merged main `482b2163c5f5900a29fd0f3c0d617e27a3c77f57`.

Status: **approved**. The first unchecked Step 5 checklist item is “Persist
annotations and researcher inspection decisions separately from source data,”
and the new `Next assignment` section describes that same work unit. The Step 5
heading remains unchecked, and the completed Step 5A.14 preview item remains
checked. The diff changes only `docs/implementation-plan.md`, appends one new
completion-log row, and preserves all prior rows; it contains no implementation
for the next assignment. `git diff --check` passed. Both required hosted jobs
passed on PR #23 head `53c6833` in Actions run `37205649638`.

This review changes no behavior checklist state and does not begin Step 5A.15.


## Step 5A.15 final monitoring review

Review date: 2026-10-04

Scope: PR #24 repaired head `6ecc13489cfcb72da194bf60d2f12b25ea141a24`,
compared with merged main `fe4ac4af99aecadf49a87873fa9f9c7406d95b98`.

Status: **approved**. The PR adds an authenticated, project-authorized,
versioned append-only store for researcher annotations and explicit EEG
inspection decisions. Records bind researcher, dataset and recording paths,
note, optional time/channel locations, creation/update times, and the current
source content identity. Atomic persistence stays under project annotations;
the raw BIDS files are not modified. Reads and writes recompute the identity
and refuse stale requests. The UI requires an explicit decision and action.

The first review found that a delayed save response could be shown after the
project or source context changed. The repair invalidates pending UI work on
project changes, rediscovery, and re-identification, and guards success, error,
and cleanup state updates with the captured context and generation. The
delayed-response regression switches projects before resolving the old save
and confirms the stale decision is not displayed. The inline finding was
answered on the PR and the repaired behavior was independently verified.

Clean-checkout focused backend tests passed (4); focused UI tests passed (8).
The full Python gate with the optional EEG group passed 760 tests with 2
optional skips and 2 upstream deprecation warnings. Vitest passed 171 tests in
18 files. Ruff, formatting (77 files), strict mypy (30 source files), ESLint,
Prettier, production build (1,846 modules), production audit (0
vulnerabilities), and `git diff --check` passed. Both hosted jobs passed on
head `6ecc134` in Actions run `37207868929`.

Step 5A.15 is approved and checked. Step 5 remains unchecked because its
scientific completion gate still includes unsupported-format errors and an
independently reviewed direct-MNE reference script. No filtering or signal
processing was introduced by this work unit.


## Step 5A.16 final monitoring review

Review date: 2026-10-05

Scope: PR #26 final head `d0c15bf4958e08d1e41131f8512a893e6ca95cad`, compared
with main `58a94c48f9242cd4d5c35cadd3d56e4cb6bbe335`.

Status: **approved**. Discovery now reports unsupported recording formats and
incomplete required BIDS metadata with corrective guidance. Dataset and
recording readiness gate UI identity and inspection actions, and the service
rechecks readiness before identity, inspection, preview, and workflow-input
execution. Invalid input is rejected before MNE or downstream inspection; the
worker regression confirms the inspection node is dependency-skipped without
artifacts. API tests cover unsupported extensions, missing EEG sidecars, and
missing dataset descriptions, with HTTP 422 responses and unchanged raw source
bytes. The UI keeps actions unavailable for globally invalid datasets.

The first review found that malformed `channels.tsv` diagnostics named the
recording rather than the table, duplicate-column detection was quadratic, and
malformed EEG JSON diagnostics named the recording and cascaded into misleading
missing-field errors. The final head names the channels/JSON sidecars, reports
missing or duplicate TSV headers, uses a linear duplicate scan, and suppresses
required-field checks after a JSON parse failure. Regressions cover these cases.
The malformed-sidecar inline finding was replied to and verified on the final
head.

In the clean managed checkout at `d0c15bf`, focused metadata/API checks passed
(7 passed, 21 deselected); the optional EEG full suite passed 768 tests with 2
skips and 2 upstream deprecation warnings. Ruff passed; format check passed (77
files); strict mypy passed (30 source files); ESLint and Prettier passed;
Vitest passed 171 tests in 18 files; production build passed (1,846 modules,
with the existing chunk-size advisory); production audit found 0
vulnerabilities; `git diff --check` passed. Both hosted jobs passed in Actions
run `37303304533`.

Step 5A.16 is approved and checked. Step 5 remains unchecked pending its full
scientific completion gate and the independent direct-MNE reference script.
No signal processing was added.


## Step 5A.17 next-assignment pointer review

Review date: 2026-10-05

Scope: docs-only PR #27 head `de38486ab37e713300a9b99f4d48ccf7d1be994f`,
compared with main `9f67986a21ea06340f3e8646e37d048f019b4743`.

Status: **approved**. The first unchecked Step 5 checklist item is “Add an
independently reviewed direct-MNE reference script,” and the new `Next
assignment` describes that work unit. Step 5A.16 remains checked, while the
Step 5 heading and direct-MNE item remain unchecked. The complete diff changes
only `docs/implementation-plan.md`: it preserves prior completion-log entries,
adds the Step 5A.17 pending row, and updates the handoff pointer. No reference
script implementation is included. `git diff --check origin/main...origin/pr-27`
passed. Both required hosted jobs passed on PR #27 head `de38486` in Actions
run `37305761700`.

This review approves the handoff only; it does not begin the direct-MNE
reference-script assignment.


## Step 5A.17 final direct-MNE reference review

Review date: 2026-10-05

Scope: PR #28 final head `f4cb1ccf033f37b548b46f2ab12446bda1074ab3`, compared with
main `9f67986a21ea06340f3e8646e37d048f019b4743`.

Status: **approved for the metadata/event reference scope**. The script is
independent of BrainLearn discovery and inspection adapters: it verifies the
manifest inputs, selects the `.set` recording with
`mne_bids.find_matching_paths`, then reads metadata and BIDS events through
`mne_bids.read_raw_bids`. On the pinned fixture it reports the expected single
recording, 125 channels (124 EEG and 1 misc), 500 Hz, 62,000 header-declared
samples, 124 seconds, zero bad channels, and 31 annotations. The BIDS
`events.tsv` has 31 rows; MNE-BIDS annotation descriptions reflect its `value`
column (`onset`, `target`), matching BrainLearn's discovery choice of `value`
when `trial_type` is absent. Tests compare recording path, labels/types,
sampling frequency, event count/type labels, and selected inspection metadata
exactly. They do not compare event timing/duration against BrainLearn, which
does not expose those fields in discovery/inspection parity, and do not claim
signal-value agreement.

The `.set` header names an external `.fdt` absent from the pinned five-file
subset. The script checks that reference is a single safe filename, copies
only manifest-verified source files into a temporary tree, and creates a zero
byte `.fdt` there. `preload=False` is used, and neither reference nor
BrainLearn inspection requests data samples. I independently patched
MNE's `RawEEGLAB._read_segment_file` to raise and verified that both the
direct reference and BrainLearn inspection completed without invoking the
sample-read path. This supports metadata/event parsing only: sample count and
duration come from the `.set` header and are not validated against a missing
sample payload. It does not certify amplitudes, signal quality, processing, or
experimental suitability. Raw fixture hashes were unchanged.

The five retrieved files matched the pinned manifest exactly:

- `dataset_description.json`: `02120902abf903e6eb3ffd001b2bba8720023a1e21946d38b3ab4ddbd491daad`
- `sub-1473/eeg/sub-1473_task-Baseline_channels.tsv`: `6cc38eb82a7d632a94b077d26a7e31904f44e6152d386ce2d40b6f1422d26cab`
- `sub-1473/eeg/sub-1473_task-Baseline_eeg.json`: `2abb9356b1f752b4f947a4bf90f3d18643527d7e0d81c4b2ccb633ef57bc5df2`
- `sub-1473/eeg/sub-1473_task-Baseline_eeg.set`: `123900cfe3b81528f63f24292dc1d1207d05ad17d9b44324e16fed7d0fe31fe2`
- `sub-1473/eeg/sub-1473_task-Baseline_events.tsv`: `86cbb325e4b070ece0a9ceed09dcc049eb59167d2327f2a07eeb38a4ee561634`

The fixture-gated test passed (1). The full fixture-configured Python suite
passed 769 tests, 2 skipped, with 2 upstream deprecation warnings. Ruff,
format (79 files), strict mypy (30 source files), ESLint, Prettier, Vitest
(171 tests/18 files), production build (1,846 modules; existing chunk-size
advisory), production audit (0 vulnerabilities), and `git diff --check` passed.
Both PR checks passed in Actions run `37313314345`. The scheduled workflow was
also manually dispatched on this PR head; locked EEG installation, existing
pinned integration smoke, fixture retrieval, and the direct-MNE parity step
all passed in run `37313828413`.

The independent review supports checking the Step 5A.17 reference-script
item only for the stated metadata and event-structure scope. Step 5 remains
unchecked because the broader completion gate is not met; this reference is
not evidence of signal-value agreement.


## Step 5 full completion-gate handoff review

Review date: 2026-10-05

Scope: docs-only PR #29 head `75495ee1a1a3c642fb338cd37080eb11fdef3f86`,
compared with main `3cbc145189db24d41ad9f578bf5f01d43da3289e`.

Status: **approved**. Step 5 remains unchecked after its Step 5A.17 leaf was
checked; the full dataset-access and EEG-inspection completion gate still
requires independent review. The new handoff correctly assigns review of that
full gate, including GUI discovery/download/offline reopen, BIDS EEG
discovery/identity/inspection/previews/QC, and the bounded direct-MNE reference.
It explicitly keeps Step 6 out of scope and does not claim signal-value
agreement. The complete diff changes only `docs/implementation-plan.md`,
preserves all prior completion-log rows, appends the pointer evidence row, and
contains no implementation changes. `git diff --check` passed. Both required
hosted jobs passed on PR #29 head `75495ee` in Actions run `37318352835`.

This approval covers the handoff only; it does not approve the Step 5
completion gate or check the Step 5 parent item.

## Step 5 full completion-gate final monitoring review

Review date: 2026-10-05

Scope: PR #30 code head `664d5baa37ce314ced482ee8aa05a4651cdd4aed`, with the
hosted-evidence update on head `0ab6bb142b83667002026a96405e5bc8ae8a5433`,
against merged main `4da3abb44a6e5684c7f1a2f9acf293dd33e58137`.

Status: **approved**. The previously identified event-timing gap is resolved:
the pinned fixture comparison now checks a digest over each event's label,
onset, and duration at 1 ns precision. I found and requested a repair for a
signed-zero canonicalization edge; both independent digest implementations
now normalize rounded zero, and a regression confirms row-order invariance and
equivalence of opposite-sign sub-nanosecond onsets in the same rounded bucket.
The focused discovery and direct-MNE fixture tests passed 31 tests with 2
upstream deprecation warnings.

The pinned five-file fixture matched all manifest hashes. Direct MNE-BIDS
selected the same recording as BrainLearn and matched its channels, types,
sampling frequency, event labels/count/timing, and selected metadata summaries.
The `.set` references an `.fdt` absent from the pinned subset; the reference
creates an empty companion only in a temporary copy to read metadata and BIDS
events. The test does not read signal samples, validate payload-based sample
count/duration, or claim signal-value agreement. Independent instrumentation
previously confirmed both direct MNE and BrainLearn metadata inspection avoid
MNE's sample-read path. Preview trace envelopes and Welch PSD calculations are
independently compared with direct MNE on controlled synthetic data, and the
source-preservation and mutation-during-preview regressions pass.

Dataset-library UI tests cover metadata display and search; download UI tests
cover lifecycle controls using API doubles. Backend service/API tests exercise
download safety, cancellation/resume, BIDS discovery and identity, inspection,
preview, and persisted researcher decisions. The pinned integration smoke
verified checksum-based download and offline reopen. These complementary tests
support the GUI/API gate; no browser-to-live-service end-to-end run is claimed.

In this clean checkout, fixture-enabled `uv run --locked --group eeg pytest
-q` passed 771, 2 skipped, and 2 upstream deprecation warnings. Ruff, format
(79 files), strict mypy (30 source files), ESLint, Prettier, Vitest (172 tests
in 18 files), production build (1,846 modules; existing chunk-size advisory),
production audit (0 vulnerabilities), and `git diff --check` passed. Required
hosted checks passed on evidence head `0ab6bb1` in Actions run `37351050885`.
Scheduled run `37350611830` passed the pinned download/offline smoke, fixture
retrieval, and direct-MNE event-timing parity on code head `664d5ba`.

The Step 5 completion gate is approved and checked. Step 6 remains unchecked;
this review makes no signal-processing or clinical claims.

## Step 5 live-application follow-through

Review date: 2026-10-12

On PR #30 head `1978dcfa2aa4844fe0ed02dff6ae95e35021ea46`, I used the live
browser app and local service with an isolated synthetic BrainVision BIDS
project. The UI imported the local dataset, discovered one recording, inspected
it through MNE (500 Hz, 1,500 samples, 3 seconds, two EEG channels), displayed
trace and Welch spectrum previews, created a content identity, and saved an
explicit `needs_review` decision with a test-only note. The decision appeared
in the project-local annotation collection and was bound to that recording's
content identity. The seven source files had identical sizes and SHA-256 hashes
before and after discovery, inspection, preview, identity creation, and decision
save. The catalogue also returned `ds002181:1.0.0` under the EEG filter and
showed its detail view.

This supplements the component, API, and hosted integration evidence; it is a
live browser-to-service test of local import through the QC decision path, not a
browser-driven download of the full public snapshot. The download lifecycle
remains supported by UI component tests, backend service tests, and the hosted
checksum/offline-reopen smoke. The missing `.fdt` limitation on the direct-MNE
fixture is unchanged: no signal-sample agreement or payload-based sample-count
or duration validation is claimed. Step 5 remains approved; Step 6 was not
started in this review unit.

## Step 6 final implementation and scientific review

Review date: 2026-10-19

Scope: PR #31 code repair `6fbd513e99cb925573497b81361c72e1d5438c04` and
hosted-evidence head `5466ab954006ee83cb7a04d15577f0fc2606b18a`, compared with
main `084695a6c7230244846d3696883b76fe8cd2679c`.

Status: **approved for the experimental first filter workflow and Python
export contract.** The repaired identity logic binds `dataset_description.json`,
optional `participants.tsv` and subject/session `scans.tsv`, and all matching
inherited EEG JSON, channels/events TSV, events JSON, electrodes TSV, and
coordinate-system JSON sidecars. I checked this inventory against the pinned
MNE-BIDS 0.20.0 `read_raw_bids` event and sidecar loading paths; no other
MNE-BIDS metadata input used by this supported EEG import path was omitted.
Regressions confirm that changing each added metadata class changes the source
identity, the worker refuses a persistent participant-table edit after
inspection, the generated script refuses a participant-table edit after
`read_raw_bids`, and both worker and script provenance contain the new fixture
metadata. The deterministic sample-bearing two-channel fixture matches its
CC0-1.0 manifest, including the generator checksum.

The generated script's second no-overwrite issue is fixed. It stages the FIF
and provenance, then uses same-filesystem hard links as atomic create-if-absent
publication. I independently reran the subprocess regression for both race
targets: a test hook creates a sentinel destination during `filtered.save`;
the script exits unsuccessfully, preserves the sentinel bytes, does not leave
the other destination, and cleans temporary or partial outputs. The explicit
`--overwrite` path remains covered by the end-to-end export test, which also
checks that a pre-existing output pair is left untouched when overwrite is not
requested and replaced when it is. Renderer version is 1.2.1.

The focused subprocess, parity, and stale-metadata checks passed 4 tests with
2 upstream deprecation warnings. The full clean-checkout gate passed:
`uv sync --locked --all-packages --group eeg`; Ruff; formatting (86 files);
strict mypy (32 source files); pytest (802 passed, 3 skipped, 2 upstream
Starlette/AnyIO deprecation warnings in 42.06 seconds); frontend ESLint and
Prettier; Vitest (176 tests in 19 files); production build (1,848 modules,
with the existing chunk-size advisory); production dependency audit (0
vulnerabilities); `uv lock --check`; and `git diff --check`. The focused
comparison and complete suite exercise the BrainLearn worker, a separately
written direct-MNE call, and the executed generated script on identical
parameters and fixture, within `rtol=1e-6` and `atol=2e-5 µV`. The hosted
Python and frontend jobs both passed on head `5466ab9` in Actions run
`39230094615`.

The review of filter assumptions confirms that the experimental defaults and
MNE FIR settings are explicit, optional notch centers are researcher supplied,
and the preview is described as a comparison rather than a signal-quality or
filter-suitability assessment. This is a bounded engineering approval: the
synthetic fixture has actual sample bytes and supports numerical agreement,
but the pinned public `ds002181:1.0.0` EEGLAB subset still lacks its referenced
`.fdt`. No public-sample signal validation, general filter appropriateness,
experimental suitability, clinical use, or scientific certification is
claimed. Preserve the experimental label; a sample-bearing public reference
and external EEG methods review remain necessary before claiming a
scientifically validated EEG workflow. No browser-to-live-service run was
performed for Step 6.

Step 6 is checked for its experimental filter and code-generation completion
gate. Step 7 remains the next assignment and was not started in this review.

## Step 7 immutable QC review record contract

Review date: 2026-10-19

Scope: PR #33 head `55fdb63d3903ab0e0735dcae9fb83478ef215bf1`, compared with
main `2949d7ffed093be49f69539706541eac4caf009e`.

Status: **approved for the immutable QC review record contract only.** The
record stores an explicit decision, reviewer, timestamp, exclusions, run and
node-run IDs, node ID, node content identity, and the complete input-port
identity set. Both the parent record and nested input identity models are
frozen; the decision collections are tuples of immutable values. Its scope
validator accepts the matching run fixture and rejects a different run,
node, node-run, node content identity, or input identity mapping. Duplicate
ports and exclusions, blank reviewer names, naive timestamps, boolean or
negative ICA indices, and unsupported schema versions are rejected.

In a clean managed checkout, the focused QC tests passed 7 (140 deselected).
Additional probes confirmed assignment to outer fields, nested identity
fields, and the input/channel/ICA tuples fails; a matching run scope succeeds,
and each run/node/node-run/node-content/input mismatch is rejected. The full
local gate passed: `uv sync --locked --all-packages --group eeg`; Ruff;
formatting (86 files); scoped mypy (32 source files); strict mypy (34 source
files); pytest (810 passed, 3 skipped, 2 upstream deprecation warnings in
43.01 seconds); ESLint; Prettier; Vitest (176 tests in 19 files); production
build (1,848 modules, with the existing chunk-size advisory); production
dependency audit (0 vulnerabilities); `uv lock --check`; and
`git diff --check origin/main...HEAD`. Both hosted jobs passed on the reviewed
code/evidence head `55fdb63` in Actions run `39232312768`. CI must pass again
on this final review-documentation update.

This is a data-contract approval, not a scientific-method approval. The
contract is not yet persisted or consumed by a workflow. No bad-channel
annotation behavior, ICA fitting or application, GUI, or export path is
included. The first Step 7 checklist leaf is complete; Step 7's parent and
scientific-review gate remain unchecked, and bad-channel review with
annotation persistence remains the next assignment.

## Step 7 bad-channel review and QC persistence

Review date: 2026-10-19

Scope: PR #34 final code head `14ed393f33e84eadf47eb2de4e5a01a5a0f46510`,
compared with main `f6b5c6be6ac2bbf9ff2058a68bb1d90366459969`.

Status: **approved for explicit bad-channel annotation persistence only.** The
review is bound to its run, graph node, node run, input identities, and current
BIDS source identity. Channel names are checked against the live EEG channel
inventory. The researcher can approve or reject explicitly; rejected decisions
are persisted and fail the run. Opening a run restores its saved decision. The
node records annotations only: it does not alter the source recording or apply
the exclusions to downstream processing.

The first review reproduced two blockers. A source mutation immediately after
channel inventory returned could produce a stale approved decision, and a
symlinked QC review directory made opening a run return HTTP 500. On the same
PR, the persistence boundary now rechecks the current BIDS identity before the
atomic save, and run responses translate unsafe or invalid QC storage into a
controlled client error. Deterministic regressions prove the race returns 409
with no decision saved, and the symlink returns 409 without reading or writing
outside the project.

Focused bad-channel, QC scope, and symlink tests passed 7 (192 deselected).
The full clean-checkout gate passed: `uv sync --locked --all-packages --group
eeg`; Ruff; formatting (87 files); scoped mypy (32 source files); strict mypy
(34 source files); pytest (816 passed, 3 skipped, 2 upstream deprecation
warnings in 37.86 seconds); ESLint; Prettier; Vitest (177 tests in 19 files);
production build (1,848 modules, with the existing chunk-size advisory);
production dependency audit (0 vulnerabilities); `uv lock --check`; and
`git diff --check origin/main...HEAD`. Both hosted jobs passed on code head
`14ed393` in Actions run `39237012948`. Required CI must pass again on this
review-documentation update.

This is an engineering approval for saving a researcher's explicit channel
annotations. It does not validate bad-channel criteria or downstream signal
effects. Step 7's bad-channel review leaf is complete; the Step 7 parent and
scientific-review gate remain unchecked. No ICA work was started. The wider
EEG workflow remains experimental pending a sample-bearing public reference and
independent EEG methods review.

## Step 7 final hosted re-review

Review date: 2026-10-19

Scope: PR #34 final head `7220365b98e4abdd052248fe4bfe46f60bbbe0a6`,
compared with main `f6b5c6be6ac2bbf9ff2058a68bb1d90366459969`.

Status: **approved for explicit bad-channel annotation persistence only.** The
source identity is checked both when the pause is created and at the QC record
save boundary; invalid channel names are rejected; decisions are immutable and
run/node/input/source bound; approval and rejection persist across reopening;
and rejected decisions fail the run. The source is unchanged, and the review
does not apply exclusions downstream.

Two unrelated asynchronous test races surfaced on the monitor-documentation
updates and were repaired on this same PR. The branched-workflow test now waits
for the exact cached trunk-node states before comparing identities. A stale
run-history response could replace a newer run entry; `mergeHistoryEntry` now
invalidates older list requests, and a regression holds an empty list response
until after a run appears and confirms it stays visible. I reviewed both
repairs in the final diff.

On the final clean checkout, focused bad-channel/QC-scope/symlink checks passed
12 (187 deselected), and the cache-reuse synchronization regression passed 1.
The complete gate passed: locked EEG sync; Ruff; formatting (87 files); scoped
mypy (32 source files); strict mypy (34 source files); pytest (816 passed, 3
skipped, 2 upstream deprecation warnings in 37.96 seconds); ESLint; Prettier;
Vitest (177 tests in 19 files); production build (1,848 modules, with the
existing chunk-size advisory); production dependency audit (0 vulnerabilities);
`uv lock --check`; and `git diff --check origin/main...HEAD`. Both hosted jobs
passed on final head `7220365` in Actions run `39239172052` (Python 1m22s;
frontend 30s).

This approval covers persistence of a researcher's explicit channel
annotations. It does not validate bad-channel criteria, downstream signal
effects, or scientific suitability. Step 7's bad-channel review leaf is
complete; the Step 7 parent and scientific-review gate remain unchecked, and
ICA was not started. The wider EEG workflow remains experimental pending a
sample-bearing public reference and independent EEG methods review.

## Step 7 ICA final monitored review

Review date: 2026-10-19

Scope: PR #35 final head `3af4115b98878b9f1a8b04c3118912be0ea5b72b`,
compared with main `8df8c8d8dd16208c857c3d77a3baa3d70d6670ef`.

Status: **approved for the ICA engineering implementation.** The separate fit,
review, and apply stages preserve an explicit researcher decision bound to the
run, node, input identities, and current raw/model artifacts. Stale or
historical decisions cannot be applied. Non-converged fits fail closed; the
fit provenance records exactly which good EEG channels were fitted and which
bad channels were excluded. Both decision and apply paths require current
schema-1.1 converged-fit provenance, and the generated script checks the BIDS
source inventory, hashes, content identity, and fit signature before applying
the saved exclusions. Staging and JSON writes reject symlinked or pre-existing
targets. The pre-load sample bound counts every EEG channel that will be
loaded, including bad channels; the fit separately caps usable EEG channels at
256. Legacy convergence records remain view-only. The legacy no-edges project
migration retains filter migration behavior and the user's settings.

Independent clean-checkout verification at this head: focused ICA tests passed
(12); locked EEG dependency sync passed; Ruff passed; formatting passed (91
files); strict mypy passed (36 source files); pytest passed (829 passed, 3
skipped, 2 upstream Starlette/AnyIO warnings); ESLint and Prettier passed;
Vitest passed (183 tests in 21 files); production build passed (1,850
modules, with the existing chunk-size advisory); the production npm audit
reported 0 vulnerabilities; `uv lock --check` and `git diff --check` passed.
On the pinned synthetic BIDS fixture, BrainLearn, direct MNE, and generated
Python outputs agreed at `rtol=1e-6`, `atol=3e-5 µV`, and source files remained
unchanged. Hosted Python and frontend checks both passed on final head
`3af4115` in Actions run `39247940198` (Python 1m54s; frontend 46s).

This is engineering approval on a synthetic fixture, not scientific
certification. The fixture has no sensor locations, and a sample-bearing public
BIDS recording plus independent EEG methods review remain outstanding. Step
7's parent and scientific-review gate remain unchecked; Step 8 must not start
until those review gates pass.
