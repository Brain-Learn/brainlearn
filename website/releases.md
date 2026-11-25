# Releases and versioned documentation

BrainLearn is under active development and does not yet have a stable public release. The documentation site labels the current build **Development**. A green CI run or a working demo is not a software release or scientific certification.

When release candidates and stable tags exist, their documentation must be built from the matching source revision. Archived scientific runs must keep their recorded workflow, dependency, and documentation versions; they must not silently inherit current claims.

The development site does not yet offer a historical version switcher because there are no tagged releases to serve. CI labels `main` builds as Development and tag builds as Pre-release or Stable, but only `main` is published. A version archive and selector are required before old runs or releases are treated as maintained documentation.

This page defines the release procedure; it does not create a release workflow, tag, signing identity, package, or installer. No release candidate is available yet.

## Release stages and gates

Use immutable, monotonically numbered tags for one BrainLearn source revision:

| Stage | Example tag | Distribution label | Gate |
| --- | --- | --- | --- |
| Alpha | `v0.1.0a1` | Pre-release | Internal engineering evaluation; label every download experimental and list known failures. |
| Beta | `v0.1.0b1` | Pre-release | Bounded external pilot after a clean install and advertised workflow replay pass for the beta build; record feedback and limitations. |
| Release candidate | `v0.1.0rc1` | Pre-release | Code freeze, reviewed release notes, green required CI, clean artifact install/replay, dependency/license inventory, checksums, and verifiable signature. A material repair creates the next candidate tag. |
| Stable | `v0.1.0` | Stable | All scientific and product completion gates pass; the certified reference workflow completes on each advertised platform/backend; documentation and support claims match that evidence. |

Version the application tag independently from component versions. Record every included Python package, UI, workflow schema, node, dependency-lock, and documentation version in the release record; do not assume they all share one number. Build from a clean checkout of the reviewed tag and preserve the exact source commit. Before the first candidate, verify that repository tag protections prevent updates and deletions for `v*` tags and restrict tag creation to release maintainers. Until those protections are confirmed, do not issue release tags. Do not create or move tags as part of a pull request.

BrainLearn has no approved stable scientific workflow today. Step 7/8/9 scientific review and certification gates remain open, so this procedure authorizes no stable EEG claim. Alpha, beta, or candidate artifacts must state their research-only and experimental status and must not imply independent methods approval.

## Candidate build and release record

Before tagging a candidate, the release owner prepares a reviewed PR that records the intended tag, exact source commit, included component versions, dependency locks, support-matrix revision, candidate status, user-facing changes, known limitations, and links to required CI and reference-workflow evidence. Confirm the tag is not already present. Use a clean checkout with locked dependencies and build only the artifact types explicitly supported by the release. Install the built artifacts in a second clean environment and run the candidate's required smoke and reference checks. Record exact commands and results; a source-checkout smoke is not an artifact install or scientific certification.

The reviewed release notes are drafted from the release range with:

```sh
uv run --frozen python scripts/generate_changelog.py \
  --version v0.1.0rc1 --from-ref <previous-release-tag> --to-ref <candidate-commit>
```

The command writes a draft to standard output and never edits `CHANGELOG.md`. For the first candidate, select and record an explicit starting commit because no previous release tag exists. Review every line for user relevance, breaking changes, scientific caveats, security disclosures, and attribution. Copy the final, edited notes into both `CHANGELOG.md` and the release entry; generated text is not approval.

## Checksums and signatures

Place the final release files in a flat `dist/` directory, excluding temporary files, research data, credentials, and build logs. The checksum helper accepts only regular files with portable ASCII names, writes a sorted UTF-8 `SHA256SUMS` file atomically, and verifies that no file is missing, changed, or unlisted:

```sh
uv run --frozen python scripts/release_checksums.py create --directory dist
uv run --frozen python scripts/release_checksums.py verify --directory dist
```

Run verification again after download from the hosted release. Publish `SHA256SUMS` beside the artifacts. Sign the manifest only after artifact bytes are final; the signature then binds every listed artifact digest without a circular hash. The planned signer is a protected GitHub Actions release workflow using short-lived GitHub OIDC identity with [Cosign keyless blob signing](https://docs.sigstore.dev/cosign/signing/signing_with_blobs/). That workflow is not implemented yet. Its exact workflow identity must be published with the release so recipients can verify the bundle, for example:

```sh
cosign sign-blob --yes dist/SHA256SUMS \
  --bundle dist/SHA256SUMS.sigstore.json

cosign verify-blob dist/SHA256SUMS \
  --bundle dist/SHA256SUMS.sigstore.json \
  --certificate-identity '<exact BrainLearn release workflow identity>' \
  --certificate-oidc-issuer https://token.actions.githubusercontent.com
```

The maintainer must test the verifier against the public bundle before publishing. Never put a long-lived signing key or research data in CI secrets. [Sigstore documents](https://docs.sigstore.dev/cosign/verifying/verify/) explain identity and transparency-log verification. GitHub build provenance attestations may be added with a separately reviewed release workflow; they describe where/how an artifact was built, but are not a security or scientific-quality guarantee ([GitHub artifact attestations](https://docs.github.com/en/actions/how-tos/secure-your-work/use-artifact-attestations)).

Attach the artifacts, checksum file, signature bundle, reviewed release notes, source tag, and test-evidence links to one GitHub Release entry. Mark alpha, beta, and release-candidate entries as pre-releases; publish a stable entry only after the stable gate above passes. After publication, never replace an asset or move a tag in place; a changed build receives a new candidate or patch version and a new manifest/signature.

## Rollback and incident handling

1. **Stop exposure.** Pause further downloads and announcements. Mark the affected release entry with a visible warning, identify the exact tag and files, and explain whether the issue is an install defect, security problem, or scientific/workflow defect. Preserve the release record for traceability.
2. **Restore a known-good build.** Direct users to the latest known-good signed release and its checksum/signature verification. If a release channel or documentation pointer is wrong, point it back to that release without rewriting a published tag. Never replace a published binary under the same filename.
3. **Issue a repair.** Fix the problem on a reviewed branch, update the changelog with impact and repair, and publish a new monotonically numbered candidate or patch. A broken signature or compromised release identity invalidates trust in the affected release; do not ask users to rely on its checksum alone.
4. **Handle scientific impact explicitly.** State which workflow versions and run records may be affected, what is known and unknown, and what users should review. Preserve existing run records and provenance. Do not silently migrate, rerun, overwrite, or reinterpret a research analysis as part of application rollback.
5. **Close the incident.** Record the cause, affected versions, recovery path, evidence, and any follow-up gate in the release notes and project issue tracker.

Application rollback does not establish compatibility of old projects or environments. The separate Step 10 gate for verifying that recorded environments remain pinned is still open; do not advertise that behavior until its gate passes.

## Release checks

A stable release requires a supported-platform installation path, clean-install checks, replay of the certified reference workflow, release notes, checksums, and a documented rollback. Publication eligibility and independent use evidence are separate gates. See the [implementation plan](https://github.com/Brain-Learn/brainlearn/blob/main/docs/implementation-plan.md).
