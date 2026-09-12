# BrainLearn agent handoff

This file is the operating brief for any agent working in this repository. Read it together with `PROPOSAL.md` and `docs/implementation-plan.md` before changing code. The implementation plan is the authoritative sequential checklist and must be updated as verified work is completed.

## Mission

BrainLearn is a local-first, near-zero-code visual workflow builder for reproducible neuroscience analysis. A researcher should be able to import a BIDS dataset, connect scientifically compatible processing nodes, inspect intermediate results, make explicit quality-control decisions, run the workflow, and export a reproducible record with citations.

The first product milestone is one complete EEG workflow. Do not broaden the initial implementation to every modality, cloud execution, arbitrary plugins, or clinical use.

## Current state

- `PROPOSAL.md` contains the product, publication, validation, CI/CD, licensing, and adoption plan.
- `design/brainlearn-layout-concept-v1.png` is the first interface reference.
- The repository was initialized on the `main` branch on 12 September 2026.
- The executable foundation includes the workflow schema, validator, local API, capability inspection, and visual graph shell. Scientific processing is not implemented yet.
- BrainLearn's original code uses BSD-3-Clause. Preserve the root and package license files and SPDX metadata. Every external tool, dependency, dataset, atlas, model, and plugin retains its own license and must be reviewed separately before bundling.

## Architecture decision

Use a Python core and local Python service with a web frontend:

```text
React/TypeScript UI
        |
local HTTP/WebSocket API
        |
FastAPI + Pydantic application layer
        |
workflow schema + scientific validator + executor adapters
        |
isolated Python/tool workers and artifact store
```

System inspection belongs in Python. The backend will report the operating system, CPU, memory, Python version, supported container/runtime tools, and optional accelerators. GPU detection must be capability based: check installed backends and report CUDA/MPS availability without importing heavy optional libraries at application startup.

The browser frontend does not prevent local hardware access because it requests that information from the local service. Keep the frontend independent of scientific execution. Do not move processing into browser JavaScript.

Before replacing this approach with a Python-rendered GUI, produce a short decision record comparing it against the required node editor, large-data previews, accessibility, testability, packaging, and remote/lab-server reuse. PySide6 is the credible native-Python alternative. Streamlit and notebook dashboards are not suitable foundations for the primary node-canvas application.

## Initial technology choices

- Python 3.12 for the backend and core.
- `uv` for Python dependency and lock management when available.
- FastAPI and Pydantic v2 for the local API and versioned schemas.
- React, TypeScript, Vite, and React Flow for the visual editor.
- SQLite for metadata; filesystem objects for large artifacts.
- Pytest for Python tests; Vitest for frontend unit tests; Playwright later for a few end-to-end flows.
- Ruff and mypy for Python quality checks; ESLint and Prettier for TypeScript.

Treat these as accepted defaults for the first scaffold. Record a decision before changing one.

## Required package boundaries

```text
apps/web/                    browser interface
packages/core/               graph schema, type system, validation, run records
packages/server/             local API, lifecycle, system capabilities
plugins/mne/                 EEG adapters, added after the core is stable
plugins/sklearn/             ML nodes, later
plugins/mri/                 MRI adapters, later
examples/                    versioned workflow examples
tests/                       cross-package and scientific reference tests
docs/                        user and contributor documentation
design/                      interface references
paper/                       manuscript files when evidence exists
.github/workflows/           CI and release workflows
```

Python import packages should use `brainlearn_*` names until the final distribution name is checked. Never assume the working name is available on PyPI, npm, GitHub, or as a trademark.

## Required implementation workflow

Work through `docs/implementation-plan.md` in order. Choose the first unchecked step whose dependencies are complete and keep the change inside that step.

An unchecked item remains unchecked until its stated behavior and verification exist. The implementing agent may check verified subitems and must add exact evidence to the completion log. The monitoring agent reviews the implementation, reruns the completion gate, and approves completion of the top-level step. If a later change breaks a completed gate, reopen the affected checkbox before doing expansion work.

The current assignment is the `Next assignment` named at the end of the implementation plan. Do not begin the following step in the same work unit unless the plan explicitly combines them.

## Workflow and scientific model

- The graph is a directed acyclic graph at the top level.
- Ports carry scientific types and metadata, not generic files or arrays.
- Initial port types may be `bids_dataset`, `raw_eeg`, `reviewed_eeg`, `epochs`, `evoked`, `spectrum`, and `report`.
- Conversion between scientific types must be explicit.
- A QC node may pause and emit a persisted decision. It cannot silently approve itself.
- Loops such as cross-validation and neural-network training belong inside structured nodes/subgraphs, not as graph cycles.
- Do not execute arbitrary shell text from workflow JSON.
- Store references to large artifacts in graph/run records rather than embedding arrays.
- Every run record must eventually contain workflow version, input identities, parameter values, implementation versions, environment identity, seeds, QC decisions, output identities, and citations.

## EEG MVP

The first scientifically certified template is:

```text
BIDS EEG -> Inspect Signal -> Band-pass Filter -> ICA Review -> Epochs -> ERP Average -> Report
                                                           \-> PSD ----/
```

The final edge topology may change after scientific review. Use MNE-Python and MNE-BIDS adapters rather than recreating their algorithms. Filtering, referencing, event handling, artifact rejection, epoching, ICA review, ERP, PSD, visualization, and report generation must all expose assumptions and provenance.

Real EEG nodes require independent direct-library reference scripts and method-specific numerical tolerances. UI snapshots are not evidence of scientific correctness.

## Safety and product boundaries

- This is research software, not a medical device or diagnostic system.
- Do not add diagnosis, treatment recommendations, patient scoring, or clinical claims.
- Keep research data local by default and bind the service to loopback.
- Use a per-session token, validate Host/Origin, and restrict file access to user-selected project roots before real file browsing is added.
- Never commit personal, identifiable, restricted, or large research data.
- Use synthetic fixtures or clearly licensed public data with source, version, license, and checksum recorded.
- Never upload user data or telemetry without an explicit product decision and opt-in design.

## ML and explainability constraints

- Participant-aware splits are required when the claim concerns unseen participants.
- Learned preprocessing, feature selection, imputation, scaling, harmonization, and model selection must remain within the training boundary.
- Nested validation is required when tuning and estimating generalization in the same study.
- Grad-CAM applies only to compatible neural layers. SHAP requires an appropriate explainer and background/reference data.
- Attribution describes model behavior and must not be presented as causal brain evidence.
- Do not promise bit-for-bit reproducibility across PyTorch versions, platforms, or CPU/GPU backends.

## Cross-platform policy

Support is a tested matrix of OS, architecture, dependency versions, and compute backend. The UI running on an OS does not certify every scientific tool there.

- Linux x86-64 is the reference heavy-compute platform.
- macOS Apple Silicon is a first-class CPU target; MPS support is optional and separately certified.
- Windows is a first-class UI and CPU EEG target.
- FSL on Windows is an optional WSL/VM integration.
- Missing dependencies must produce a clear unsupported state; never substitute a different scientific method silently.

## Engineering rules

- Prefer small, reviewable changes and keep `main` releasable.
- Do not overwrite user work or use destructive git commands.
- Add tests for behavior with scientific or persistence impact. Avoid tests that merely repeat implementation details.
- Pin reproducible environments for certified workflows and test dependency updates separately.
- Write artifacts atomically; partial outputs are not successful outputs.
- Cache identity must eventually include input content, parameters, node implementation, environment, seeds, and execution settings.
- Maintain schema migrations and round-trip tests from the first persisted version.
- Use clear error messages that tell a researcher what is wrong and how to correct it.
- Preserve upstream license and citation metadata in every adapter.

## Documentation required before public beta

- User installation and a ten-minute EEG walkthrough.
- Explanation of workflow types, QC pauses, caching, and reproducibility levels.
- Contributor setup and a worked “add a node” guide.
- Architecture and decision records.
- Supported-platform matrix and dependency troubleshooting.
- Governance, code of conduct, security policy, changelog, citation metadata, and license.
- Release process that another maintainer can execute.

## CI/CD expectations

Pull requests should run formatting, lint/type checks, backend tests, frontend tests/build, workflow-schema compatibility tests, and a documentation build. Main should add clean-install smoke tests on Linux, macOS, and Windows. Scientific integrations, WSL, GPU, and larger reference workflows belong in scheduled or trusted hardware-specific jobs.

Use minimal GitHub Actions permissions, pin third-party actions to commit SHAs, and do not expose secrets or personal runners to untrusted pull requests. Version tags publish only after release checks and human approval. Archive stable releases through Zenodo once GitHub publishing is configured.

## Publication and success evidence

The leading first venue is JOSS, after its current eligibility requirements are met. Neuroinformatics and Frontiers in Neuroinformatics are possible venues for a fuller evaluation paper. Recheck all journal rules at submission time.

Collect evidence during development:

- public, sustained commit/issue/release history;
- independently completed analyses and repeat use;
- reference workflow agreement;
- usability task completion and failure categories;
- replay on clean supported environments;
- examples of semantic validation catching designated mistakes;
- honest limitations and failed cases.

Do not submit a software paper based only on screenshots and feature claims.

## Decision gates

1. After the initial vertical slice: confirm one graph works through API and UI.
2. After the direct-library EEG reference: confirm the tool reproduces it within declared tolerances.
3. After pilot testing: continue only if independent researchers can complete the bounded task.
4. Before adding MRI or deep learning: confirm the EEG path is supportable by one maintainer.
5. Before journal submission: confirm eligibility, sustained public history, research use, documentation, tests, release DOI, and contributor pathway.

When a gate fails, reduce scope or correct the existing workflow before adding features.

## Agent reporting format

At the end of each work unit, report:

1. Outcome and user-visible behavior.
2. Files changed.
3. Verification performed and exact results.
4. Scientific assumptions introduced or changed.
5. Remaining risks or blockers.
6. The next smallest executable work unit.
7. Checklist items changed and the evidence added to `docs/implementation-plan.md`.

If blocked, show the evidence and continue any independent work that remains possible.
