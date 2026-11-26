# Contributing to BrainLearn

BrainLearn is early-stage research software. Contributions should be focused,
reproducible, and clear about their assumptions. The EEG workflow is
experimental; engineering review or numerical agreement does not certify a
method or analysis.

## Before starting

1. Read the [project proposal](PROPOSAL.md), [agent handoff](AGENTS.md), and
   [implementation plan](docs/implementation-plan.md). Work on the first
   assignment whose dependencies are complete, and keep each pull request
   within that assignment.
2. For a substantial change, open an issue first to discuss scope and the
   scientific question it supports.
3. Use a scoped branch and submit a pull request. Follow
   [the repository review workflow](docs/github-workflow.md); do not commit
   implementation work directly to `main`.
4. Preserve the original BSD-3-Clause license and SPDX metadata. Review the
   license and citation terms for every dependency, dataset, atlas, model, and
   other third-party material.
5. Never commit private, identifiable, restricted, or large research data.
   Keep fixtures synthetic or use small, clearly licensed public data with its
   source, version, license, and checksum documented.

## Development setup and checks

Follow the tested setup in [the development guide](docs/development.md). Keep
Python dependencies in the locked `uv` workspace and frontend dependencies in
their checked-in npm lockfile. Before requesting review, run the full gate
listed in [the GitHub workflow](docs/github-workflow.md), including Python
quality and tests, frontend quality, tests and build, and the documentation
build. Report the exact commands and results in the pull request. Do not weaken
or skip a failing check to obtain a green result.

## Scientific and workflow changes

- Keep the workflow graph acyclic and use typed scientific ports. A conversion
  between data types must be an explicit node.
- Keep scientific algorithms in their domain adapter; preserve upstream
  licenses and citations.
- Expose assumptions, parameters, versions, input identities, QC decisions,
  outputs, and citations in run records. A QC node must wait for an explicit
  researcher decision; it cannot approve itself.
- For scientific behavior, add an independently constructed direct-library
  reference and method-specific numerical tolerances. State what that test
  establishes and what it does not establish.
- Do not silently interpolate, resample, change references, or substitute a
  different method when inputs or dependencies are incompatible. Give the
  researcher corrective guidance instead.
- Treat all current EEG processing as experimental until the documented
  independent methods and monitoring gates are complete. Do not add clinical
  claims, diagnosis, treatment advice, or participant scoring.

For node contracts, start with
[`docs/node-dependency-metadata.md`](docs/node-dependency-metadata.md) and the
existing MNE adapter. A separate worked add-a-node tutorial is planned; this
guide does not certify undocumented internal APIs as extension points.

## Pull requests, issues, and credit

Use a clear title and describe the user-visible change, scope, evidence,
limitations, and any data or scientific assumptions. Include tests for
scientific or persistence behavior and update user-facing documentation when
behavior changes. For issues, include reproducible steps and software versions;
remove local paths, session tokens, participant identifiers, and private data
from logs or screenshots.

Contributors should receive credit for substantive code, documentation,
testing, methods, data curation, and maintenance work. Discuss attribution with
the affected contributors rather than inferring authorship from commit count.
See the [Code of Conduct](CODE_OF_CONDUCT.md), [Governance](GOVERNANCE.md), and
[Security policy](SECURITY.md) for reporting and project decisions.
