# BrainLearn: project proposal and delivery roadmap

Prepared 11 September 2026. Working name; naming and package availability remain to be checked. Planning basis: one mostly full-time developer. All schedules, targets, and success probabilities below are estimates, not observed project results.

## 1. Recommendation

Build a local-first, visual research workflow platform for neuroscience. Researchers connect scientifically typed nodes, inspect intermediate data, review quality-control decisions, and export an executable record of their analysis. Start with one complete EEG workflow; expand to MRI, machine learning, and explainability after external users can reliably finish that workflow.

The project can produce publishable research software. However, integrating familiar libraries behind a canvas does not by itself establish a strong scientific contribution. The defensible contribution should be **an evaluated visual workflow system that reduces analysis errors and preserves reproducibility while remaining usable without routine programming**.

Recommended first delivery: a Python core and local browser interface. Later add a desktop launcher using the same interface and backend. Treat operating-system support and computational-backend support as separate promises.

Target a focused, externally tested release and software-paper submission in 12–18 months. Allow 18–30 months for substantial MRI and deep-learning expansion. A comprehensive multimodal ecosystem is a multi-year effort and probably requires additional maintainers.

## 2. Intended users and scientific aims

Primary users are graduate researchers and laboratory scientists who understand their experiment but cannot comfortably assemble and maintain Python or shell pipelines. Secondary users are experienced analysts who want reusable templates and methods developers who want to distribute tested nodes.

The product should help users:

1. Import data and understand whether essential metadata are present.
2. Choose a reviewed template and see its assumptions.
3. Inspect signals/images before and after processing.
4. Connect compatible operations and receive useful explanations for incompatible connections.
5. Execute, interrupt, resume, and compare analyses.
6. Export results, parameters, decisions, environments, and citations.

Near-zero code means no routine programming for supported workflows. It does not mean no scientific judgment. Filtering, artifact rejection, confound selection, validation design, and interpretation must remain visible decisions.

The first release supports research analysis. Clinical decision support, acquisition-device control, real-time BCI, and automated diagnosis are separate projects outside this proposal.

## 3. Existing work and the novelty test

| Existing ecosystem | Established capability | Implication for BrainLearn |
|---|---|---|
| brainlife | MRI/EEG/MEG processing apps, workflows, provenance, compute resources, and publication of workflow records | Multimodality and reproducibility alone are insufficient differentiation |
| Nipype / Pydra | Composition and execution of scientific tasks; Pydra provides caching and execution backends | Reuse an engine behind an adapter where practical |
| Orange | Visual, connected data-analysis widgets and extensibility | The node-canvas interaction itself is not novel |
| MNE-BIDS-Pipeline | Configurable, cached EEG/MEG processing with reports | Wrap or interoperate with established processing rather than inventing an EEG pipeline |
| BIDS ecosystem | Standardized neuroscience datasets and metadata | Use established formats and identifiers |

Sources: [brainlife](https://brainlife.io/about/), [Pydra](https://nipype.github.io/pydra/), [Orange](https://neworange.biolab.si/home/visual-programming/), [MNE-BIDS-Pipeline](https://github.com/mne-tools/mne-bids-pipeline), [BIDS](https://bids.neuroimaging.io/).

During discovery, also evaluate EEGLAB, Brainstorm, 3D Slicer, KNIME, and relevant visual neuroimaging workflow projects. This initial review is not an exhaustive novelty search. Do not claim that another platform lacks a feature until its current implementation has been inspected.

Test this proposed differentiation: local operation with no mandatory data upload, explicit scientific compatibility checks, recorded human QC, validation-aware ML subgraphs, and portable execution records work together to reduce mistakes and setup effort. Build a feature matrix with evidence links and run equivalent tasks in the closest two alternatives.

If an extension to an existing platform satisfies most requirements with lower maintenance, prefer that route. The first month must justify a standalone product.

## 4. Native application versus local website

| Option | Advantages | Costs and limitations | Recommendation |
|---|---|---|---|
| Native GUI, such as Qt | Direct desktop integration and mature desktop interaction | Separate GUI expertise, packaging burden, harder reuse for a lab server | Choose only if discovery establishes a substantial need |
| Local browser UI plus Python service | One interface across OSs; fits Python science tools; reusable for remote execution | Must launch/manage a service and secure local file access | First implementation |
| Desktop shell around the same UI | Installer, launcher, file picker, and familiar app lifecycle | Adds signing, update, and backend-bundling work | Add after the processing workflow is proven |
| Public hosted computation service | Central deployment and collaboration | Data transfers, privacy, storage, authentication, and operating costs | Defer; not needed for publication |

A local website is served by software on the user's computer. The browser renders the interface; Python workers process local data. It does not require uploading research data to our server. Bundle UI resources so supported workflows work offline after dependencies and necessary atlases are installed.

Proposed implementation candidates: React and TypeScript for the interface, React Flow for the graph editor, FastAPI/Pydantic for the local service and schemas, and SQLite plus filesystem artifacts for local projects. These are proposed choices, subject to a short prototype and dependency review. Keep graph semantics independent of the canvas library.

The local service should bind to loopback, use a per-session token, validate Host/Origin, restrict file access to selected project roots, and avoid running shell text supplied by the canvas. An optional lab-server mode needs its own authentication and isolation design; changing the bind address is not a server product.

### Platform policy

| Platform | First supported scope | Heavy-tool policy |
|---|---|---|
| Windows | UI and certified CPU EEG workflows | FSL through validated WSL/VM execution; GPU combinations certified separately |
| macOS Apple Silicon | UI and certified CPU EEG workflows | Native compatible tools; evaluate each container architecture; optional GPU acceleration later |
| Linux x86-64 | UI and certified CPU EEG workflows; reference heavy-compute platform | Preferred initial container and NVIDIA validation platform |
| Intel macOS / other Linux architectures | Add when runners, demand, and dependencies justify certification | Do not advertise universal support before testing |

FSL officially provides Linux and Intel/Apple Silicon macOS installation, with Windows use through WSL or a VM. A desktop wrapper does not remove this constraint. [FSL installation](https://fsl.fmrib.ox.ac.uk/fsl/docs/install/index.html), [Windows guidance](https://fsl.fmrib.ox.ac.uk/fsl/docs/install/windows.html).

Publish a versioned capability matrix of OS, architecture, Python/runtime, tool versions, and CPU/GPU mode. “Unsupported” must appear before execution. Do not silently substitute a different scientific algorithm when a dependency is missing.

## 5. Scope and library integration

### First complete workflow: EEG

Select one public, redistributable or independently downloadable BIDS EEG dataset and one scientific task during discovery. Record its exact version, license, checksum, event definitions, and reference analysis.

Template: import → metadata/event checks → signal inspection → filtering/reference configuration → bad-channel and artifact review → epoching/baseline → ERP or spectral analysis → figures and report.

Use MNE-Python and MNE-BIDS adapters. ICA can follow basic artifact handling and must include a recorded component-review step. The first supported workflow should have approximately 12–18 well-tested operations, including import, inspection, processing, QC, and export. A node count is a scope ceiling, not a success metric.

Add a small scikit-learn classification/regression template only after the descriptive EEG path passes external testing. Data splitting must be explicit and appropriate to the intended generalization claim.

### Expansion catalogue

| Area | Planned methods/libraries | Phase |
|---|---|---|
| Data foundation | BIDS, NIfTI, supported EEG formats, NiBabel, tabular metadata | EEG first; NIfTI with MRI |
| EEG | MNE filtering, referencing, epochs, ICA/QC, ERP, PSD, time-frequency | Initial release, then expansion |
| Structural MRI | Orientation inspection, brain extraction, registration, masks, regional measurements | After EEG release |
| FSL | BET skull stripping and selected validated interfaces | Optional MRI adapter |
| fMRI | Import derivatives, confound review, masking, GLM, connectivity, decoding using Nilearn | Derivatives first; raw pipeline later |
| Raw fMRI preprocessing | Integrate an established BIDS App rather than recreate its internal steps | Later |
| Classical ML | scikit-learn pipelines, grouped/nested validation, appropriate metrics, permutation tests | Late first release / subsequent release |
| Deep learning | PyTorch model templates, training, checkpoints, inference, resource estimates | After reliable ML semantics |
| Explainability | SHAP for compatible models; Captum/Grad-CAM for compatible neural-network layers | Model-specific expansion |
| Statistics and reporting | Effect sizes, uncertainty, multiple-testing options, exclusions, QC summaries, exportable figures | Relevant subset from first release |
| Advanced modalities | Diffusion MRI, MEG, PET, source reconstruction, multimodal fusion | Demand-led roadmap beyond initial scope |

Nilearn supports GLM, connectivity, decoding, and predictive modeling. Captum provides layer Grad-CAM, while SHAP explainers have model/data requirements such as a background dataset. These belong in task-specific adapters rather than universal “explain” buttons. [Nilearn](https://nilearn.github.io/stable/index.html), [Captum](https://captum.ai/api/layer.html), [SHAP DeepExplainer](https://shap.readthedocs.io/en/stable/generated/shap.DeepExplainer.html).

Grad-CAM and SHAP are model-attribution methods, not generic image-cleaning steps. Record target output, model/checkpoint, layer or background sample, preprocessing, and feature/spatial mapping. Add sanity checks and stability inspection. Present attribution as model behavior; do not label it causal brain evidence.

Ship optional dependency groups and isolated tool environments. Do not force every EEG user to install FSL, PyTorch, and a container runtime.

## 6. Architecture and scientific execution model

The system has five layers: interface → versioned workflow specification → scientific validator/compiler → execution adapter → isolated workers and artifact storage. Reports and viewers read artifacts; the graph stores references rather than large arrays.

The reusable Python core exposes the same graph validation and execution through both GUI and CLI. Browser closure must not terminate a worker; restarting the application should recover run status. Use a local single-user architecture before introducing distributed services.

Evaluate Pydra against concrete tests: cancellation, cache correctness, subprocess isolation, human-review pauses, installation burden, and maintained interfaces for the selected tools. Its current documentation includes prerelease versioning, so pin a tested version and isolate the engine behind an adapter. If the spike fails, use a narrowly scoped local executor temporarily; do not embark on a general workflow-engine rewrite. [Pydra documentation](https://nipype.github.io/pydra/).

Each node manifest should define:

- Stable identifier, semantic version, owner, license, and citations.
- Input/output scientific types and metadata requirements.
- Parameter schema, units, defaults, valid ranges, and explanatory text.
- Environment and platform constraints; CPU/RAM/GPU requirements.
- Execution entry point, timeout/cancellation behavior, and deterministic settings.
- Whether the operation learns from data and its allowed evaluation scope.
- Expected QC artifacts, tests, and known limitations.

Scientific types must carry more than “file” or “array”: modality, sampling rate, channel types, coordinate system, image affine, voxel dimensions, participant/session identifiers, event units, and fitted-model state. Missing required metadata should prevent execution. Conversions must be explicit nodes.

Graphs are acyclic at the top level. Cohort mapping and cross-validation are structured subgraphs; neural-network training loops live inside training nodes. This keeps an n8n-like interface while making scientific execution tractable.

Represent QC as a persisted review artifact linked to the data version, reviewer decision, exclusions, and timestamp. A resumed workflow must use that decision only when its input artifact still matches.

Cache keys incorporate input content identities, parameters, node implementation version, environment digest, seeds, and relevant execution settings. Changing an upstream parameter invalidates dependent results. Write artifacts atomically and never mark partial output as successful. Cancellation of training resumes only from an explicit valid checkpoint.

Every run exports a graph, input manifest, environment lock/container digest, run log, QC decisions, outputs, and methods/citation bundle. The same graph runs through the CLI without the GUI. Human-readable Python export can follow later; canonical JSON plus a documented runner is the first reproducibility contract.

## 7. Scientific correctness is a product feature

The system should enforce these first-class rules:

- Group repeated observations by participant when evaluating generalization to new participants. Use run/session/site-aware schemes when scientifically appropriate.
- Fit scaling, imputation, feature selection, learned harmonization, and relevant confound transformations inside training folds. Never use held-out labels in preprocessing or model selection.
- Nest hyperparameter selection inside evaluation, and separate validation from final test-set reporting.
- Keep learned EEG transforms within the intended evaluation boundary when making predictive claims; descriptive analysis and prediction have different requirements.
- Record temporal/spatial transforms and verify alignment before overlays or regional extraction.
- Report exclusions, failed participants, missing data, and the analysis denominator.
- Distinguish exploratory choices from a frozen confirmatory workflow. Track changes made after inspecting results.

scikit-learn explicitly documents leakage risks and pipeline-based prevention. BrainLearn's additional proposal is to encode the relevant scientific constraints in the visual graph and evaluate their effectiveness. [scikit-learn pitfalls](https://scikit-learn.org/stable/common_pitfalls.html).

Reproducibility has levels: exact replay in a fixed environment, tolerance-based numerical agreement across supported environments, and scientific agreement in the reported conclusion. Do not promise bitwise-identical CPU/GPU results. PyTorch cautions that reproducibility is not guaranteed across releases and platforms. [PyTorch reproducibility](https://pytorch.org/docs/stable/notes/randomness.html).

## 8. User experience

Open into a template gallery, not an empty canvas. Each template states its intended data, scientific question, assumptions, resource requirements, and validation status.

Use progressive disclosure: essential parameters first, advanced settings available, and defaults explained. A dataset panel shows participants and warnings; a central canvas shows processing; a side panel shows parameters and previews; a run panel shows progress and actionable failures.

Canvas interaction and dataset acquisition are specified in `docs/ui-dataset-roadmap.md`. Registry nodes must support pointer drag-and-drop to an exact canvas position while retaining click and keyboard alternatives. Existing nodes must track the pointer continuously and save one undoable final move. Short, reduced-motion-aware transitions should smooth discrete layout and status changes without adding latency to direct manipulation. Instance labels, notes, colors, and collapsed state remain presentation metadata; manifest-owned scientific identity, ports, licensing, citations, and execution behavior remain immutable.

The dataset panel will start with a curated, OpenNeuro-first catalogue of pinned public BIDS EEG snapshots. It shows version, size, license, citation, and compatibility before a local Python worker downloads and verifies data inside the authorized project. DANDI follows when NWB support is scheduled. PhysioNet automation initially covers open-access records only; restricted or credentialed resources retain their provider-controlled access flow.

Examples of useful messages: “Epoch time is in milliseconds; this node expects seconds,” or “Your evaluation targets new participants, but participant 12 appears in both training and test data.”

Provide signal traces, spectra, and trial summaries for EEG; later provide orthogonal image slices, masks, registration overlays, and statistical maps. Use downsampled previews and lazy loading for large datasets. Support keyboard navigation, readable contrast, and undo/redo. Do not automatically execute expensive nodes whenever a parameter changes.

A methods report must be generated from the actual run record, with explicit placeholders for scientific context the software cannot infer. Any free-text interpretation remains author-reviewed.

## 9. Validation and paper evidence

Plan the experiments before building the interface so that the product can collect the evidence needed for publication.

| Question | Evaluation | Proposed acceptance target |
|---|---|---|
| Are computations correct? | Compare against independently reviewed direct-library reference scripts | All certified workflows meet predeclared, method-specific tolerances |
| Does the system prevent known mistakes? | Seed unit, alignment, leakage, missing-metadata, and cache errors | All designated blocking cases caught; false positives documented |
| Can researchers use it? | Pilot with 5–8 users; then approximately 15–25 participants if feasible | At least 80% complete the bounded primary task without developer intervention |
| Is it more efficient? | Counterbalanced comparison with a relevant existing workflow | Estimate time/error differences and confidence intervals; improvement is a hypothesis |
| Does replay work? | Fresh installation, exported graph, documented data, second supported environment | Successful replay with complete provenance and justified numerical agreement |
| Is there real demand? | External lab pilots over several weeks | At least 3 independent labs perform an analysis; at least 2 return for another |

These are internal targets, not journal acceptance criteria. Final usability-study size needs pilot variance and a power/precision rationale. Obtain institutional ethics determination before recruiting a formal human-participant study. Compare equal tasks and training time; do not compare a polished template with an unnecessarily difficult coding baseline.

Use tiny synthetic fixtures in CI, a small openly licensed integration dataset, and a larger pinned benchmark outside ordinary PR tests. Keep research data out of the repository. Where redistribution is restricted, publish retrieval instructions and checksums instead of files.

For a methods paper, isolate the contribution through an ablation: compare the same workflow interface with and without semantic guardrails, using controlled nonclinical test data. Report limitations and failures, not only successful screenshots.

## 10. Publication and citation strategy

| Venue | Fit | Recommended timing |
|---|---|---|
| Journal of Open Source Software (JOSS) | Focused, reusable research software with strong engineering and demonstrated use | First software paper after mature release and eligibility |
| Neuroinformatics | Neuroscience software, methodology, integration, and evaluations | Detailed platform/evaluation paper when the evidence supports it |
| Frontiers in Neuroinformatics, Technology and Code | Neuroscience software description with validation | Alternative full software-paper route |

JOSS currently requires more than six months of active public history, research use, open-source practices, and sustained iteration. Web tools need a testable core or rigorous domain architecture. It charges no submission/publication fee and requires disclosure of AI assistance; its policy also restricts AI use in author–reviewer conversations. Recheck policy before submission. [JOSS requirements](https://joss.readthedocs.io/en/latest/submitting.html).

Neuroinformatics explicitly welcomes software tools and their evaluation. Frontiers offers a Technology and Code article type; publication charges apply under its stated article policy. Obtain current fees and any waiver eligibility before choosing that route. [Neuroinformatics scope](https://link.springer.com/journal/12021/aims-and-scope), [Frontiers article types](https://www.frontiersin.org/journals/neuroinformatics/for-authors/article-types).

Recommended first manuscript title: “BrainLearn: a local-first visual workflow system for reproducible EEG analysis.” Broaden the title to multimodal neuroscience only after MRI is validated. Its outline should cover the research need, existing alternatives, design choices, workflow semantics, installation, reference analyses, evaluation, availability, and limitations.

A later methods paper should answer a distinct research question, such as whether semantic validation reduces analysis mistakes. Disclose related manuscripts and follow both venues' overlap policies; do not duplicate the same paper.

Make the software citable before journal acceptance: archive meaningful releases with Zenodo, maintain CITATION.cff and author metadata, and provide the release DOI in reports. Use an exact-version DOI for reproducibility and a concept DOI for the evolving project where appropriate. Update preferred citation when a paper exists. [Zenodo integration](https://help.zenodo.org/docs/github/), [DOI versioning](https://zenodo.org/help/versioning).

Include citations for the upstream libraries, algorithms, datasets, models, and atlases actually used. Credit only executed methods, deduplicate references, and allow BibTeX export. Platform credit should complement upstream credit.

## 11. GitHub, CI/CD, and public release

Begin active public development early, after checking the repository contains no sensitive material. This proposal does not create or publish a GitHub repository.

Suggested layout:

```text
packages/core/          workflow schema, validation, run records
packages/server/        local API and lifecycle
apps/web/               GUI
plugins/mne/            certified EEG adapters
plugins/sklearn/        evaluation and models
plugins/mri/            later MRI adapters
examples/              portable workflows and dataset manifests
tests/                 unit, contract, integration, end-to-end
benchmarks/            reference scripts and evaluation protocols
docs/                  user, contributor, architecture, release guides
paper/                 manuscript and bibliography when ready
.github/workflows/     CI, compatibility, documentation, release
```

| Trigger | Checks/delivery | Proposed budget |
|---|---|---|
| Every pull request | Formatting, lint/type checks, unit/contract tests, schema migrations, small GUI E2E, docs build | Aim under 15 minutes |
| Main branch | Supported OS CPU smoke tests and installation from built packages | Under 30 minutes where feasible |
| Nightly/weekly | Pinned scientific integration tests, dependency compatibility, cancellation/recovery, supported browser tests | Separate scheduled budget |
| Trusted scheduled/manual run | GPU and WSL/backend certification on suitable isolated machines | Hardware-specific |
| Release candidate | Build/install artifacts in clean environments, reference workflow, migrations, attribution/license inventory | Release gate |
| Approved version tag | Publish packages/installers, release notes, checksums, versioned docs, archival record | Automated after checks |

Add a small OS matrix immediately; expand its scientific coverage as adapters ship. Hosted Windows tests alone do not certify a WSL/FSL backend. Build GPU coverage only when a corresponding feature is advertised.

Protect the main branch with required checks. Keep action permissions minimal, pin third-party actions to commit SHAs, separate untrusted PR code from secrets and privileged runners, and use trusted publishing/OIDC where supported. Never run arbitrary fork PRs on a personal GPU workstation containing research data. [GitHub secure-use guidance](https://docs.github.com/en/actions/reference/security/secure-use).

Release alpha builds first, then betas, then a stable release with a documented support matrix. Preserve previous installers and environments. Scientific runs keep their original pinned tools unless the user explicitly migrates; automatic app updates must not silently change old analyses. Provide graph migration tests, release rollback procedures, and deprecation notices.

## 12. Contributor guide and governance

Before inviting external contributions, provide README, CONTRIBUTING, CODE_OF_CONDUCT, SECURITY, GOVERNANCE, CHANGELOG, and citation metadata. BrainLearn's original code is licensed under BSD-3-Clause; preserve that SPDX identifier in package metadata and keep third-party licenses separate.

FSL is predominantly licensed for non-commercial use, with component-specific exceptions. Do not treat it as an unrestricted dependency or assume containers solve redistribution conditions. Start with an optional user-installed adapter and review the actual components, atlases, and distribution model before bundling. [FSL license](https://fsl.fmrib.ox.ac.uk/fsl/docs/license.html).

The contributor guide should contain:

1. A tested development setup for supported OSs and a tiny demo workflow.
2. How to choose an issue, discuss substantial changes, and submit a focused PR.
3. Architecture boundaries and the node manifest contract.
4. A worked example adding a node, including scientific assumptions and error states.
5. Required tests: reference correctness, input validation, serialization, cancellation where relevant, and citation/license metadata.
6. Rules for data fixtures: provenance, small size, no identifiable or restricted data.
7. Compatibility, versioning, deprecation, and documentation requirements.
8. Scientific review expectations and experimental versus certified plugin labels.
9. Credit policy covering code, documentation, testing, methods, and maintenance; authorship is contribution-based and discussed explicitly.
10. Security reporting, support expectations, and release responsibilities.

Initially the founder maintains the core. Recruit at least one EEG methods reviewer and one statistics/ML reviewer for periodic review; they need not be full-time developers. External methods review is a dependency for claiming scientific validation. If unavailable, retain experimental labels.

Keep arbitrary code plugins outside the default trusted catalogue. Installing a plugin executes code; signed metadata and review improve provenance but do not make it a sandbox. Delay an open marketplace until isolation and maintenance policies exist.

## 13. Solo roadmap and decision gates

Assume approximately 30–35 focused project hours weekly, with a quarter reserved for tests, documentation, support, and release work. Dates are relative to a committed start and include iteration; an EEG-first scope is essential.

| Phase | Window | Deliverables | Gate |
|---|---|---|---|
| Discovery | Weeks 1–4 | 8–12 interviews, competitor task comparison, dataset selection, prototype, architecture decision | At least 3 potential pilot labs and a specific unmet task |
| Executable foundation | Months 2–3 | Public development, core schema/CLI, engine spike, local UI, first import→analysis→report path, basic CI | Identical graph works through GUI and CLI |
| EEG alpha | Months 4–6 | Selected complete workflow, viewers, QC, caching/recovery, methods export, OS CPU smoke tests | Reproduces reviewed reference analysis |
| External beta | Months 7–9 | Installation improvements, pilot use, contributor guide, validation rules, small ML template if capacity permits | External users finish tasks and rerun analyses |
| Stable focused release | Months 10–12 | Regression suite, certified platform matrix, archival release, benchmark and usability results | Three external labs; complete release evidence |
| Publication and stabilization | Months 12–18 | Software-paper submission, revisions, support, evaluation replication | Evidence and journal eligibility, not calendar alone |
| MRI expansion | Months 18–24 | NIfTI viewing, BET adapter/QC, derivatives-based Nilearn workflow | Independently reviewed MRI reference workflow |
| Advanced ML and scope expansion | Months 24–30+ | PyTorch templates, selected explanations, optional desktop launcher/HPC | Demonstrated user demand and maintainable workload |

If a gate fails, reduce scope or fix the workflow rather than expand the catalogue. A paper submission date is controllable; review duration and acceptance are not.

### First 30 days

- Week 1: write the target user/task definition; recruit interviews; shortlist two public datasets; identify a scientific reviewer.
- Week 2: complete comparative task walkthroughs; test whether an existing platform extension is sufficient; document observed pain points.
- Week 3: prototype the smallest typed graph and direct-library reference analysis; test local service lifecycle and engine integration.
- Week 4: test the prototype with three researchers; decide standalone versus extension; freeze the EEG MVP and publish a concrete issue roadmap.

No elaborate node marketplace, arbitrary model builder, cloud service, or all-modality installer belongs in this month.

## 14. Resources and sustainability

Budget primarily in person-months: approximately 12–18 solo developer-months for the focused release and publication work; additional expansion is separate. Estimate cost as months × the developer's actual monthly cost, plus local hardware, CI, storage, signing, study participation, and publication expenses. A location-independent dollar total would be misleading without those inputs.

Use existing CPU hardware for the first EEG workflow; benchmark before purchasing a GPU. Seek access to Windows, Linux, and Apple Silicon validation machines. Schedule external methods review at design and release gates. Obtain a statistics consultation for the evaluation protocol before recruiting the formal study.

Keep operating costs low through local computation, static documentation, public code, and small CI fixtures. Pursue institutional backing or research-software grants once pilot retention is visible. Reserve ongoing maintenance time after publication. By the first stable release, aim to have a second person capable of making a release from the documented procedure.

## 15. Risk register

| Risk | Early signal | Response |
|---|---|---|
| Scope overwhelms one developer | Many half-working adapters; no complete external run | Freeze expansion until the reference workflow ships |
| Novelty is too weak | Users prefer a small extension to existing tools | Build the extension or focus on tested semantic validation |
| Scientifically invalid results | Leakage, incorrect units, silent exclusions | Typed metadata, reference tests, external review, explicit validation boundaries |
| Cross-platform installation fails | Repeated manual support | Smaller dependency profiles and an honest certified matrix |
| Dependency churn | Upstream updates repeatedly break adapters | Pin known environments, scheduled compatibility tests, isolate adapters |
| No adoption | Demo interest but no second use | Prioritize observed workflow friction; reevaluate after beta |
| Maintenance bottleneck | Support crowds out correctness work | Limit catalogue, document ownership, recruit maintainers |
| Licensing blocks distribution | Restricted binaries or atlas terms | Optional integrations and component-level review |
| Paper lacks evidence | Screenshots without comparative results | Design evaluation early and collect external-use records |

## 16. Success chances and citation expectations

No available evidence supports a statistically calibrated probability for this particular project. These are broad subjective planning ranges, conditional on sustained mostly full-time effort, an EEG-first scope, external scientific review, and regular user testing. Outcomes overlap; percentages should not be added or multiplied as if independent.

| Outcome | Horizon | Subjective chance |
|---|---|---|
| Usable focused EEG release | 12–18 months | 60–80% |
| Peer-reviewed software publication | Within 24 months | 40–65% |
| Repeated use by at least 10 independent labs | Within 3 years | 15–35% |
| At least 100 scholarly citations to project papers | Within 5 years of first paper | 5–15% |
| Broad field-standard status / thousands of citations | 5–10 years | Under 5% |

These are judgment estimates, not journal acceptance rates or promises. A simultaneous “all modalities, all OSs, all models” scope would put the delivery estimate well below the focused scenario; assigning it a precise percentage would add little information. Reassess after discovery and again after external beta using task completion, repeat use, and support burden.

Publication and widespread citation are different goals. A rigorous niche tool may deserve publication and remain modestly cited. Nobody can credibly promise that everyone will cite it. The strongest path to citations is sustained independent research use, clear release citations, good methods exports, reliable installation, and teaching materials that lead to real analyses.

Prioritize completed external analyses and repeat users over GitHub stars. After beta, publish one excellent tutorial per validated scientific question, a short walkthrough, and reproducible workflow examples. Seek workshops and lab demonstrations through genuine collaborations. Gather consent before naming labs or sharing their feedback.

**Decision:** proceed with a four-week discovery and executable EEG prototype. Continue to the full build only if independent researchers validate the unmet need and the first scientific workflow can be reproduced through the proposed architecture.
