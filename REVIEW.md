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
