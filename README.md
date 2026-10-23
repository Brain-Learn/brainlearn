# BrainLearn

BrainLearn is a local-first visual workflow builder for reproducible neuroscience research. The current experimental EEG path imports a project-local BIDS recording, inspects its metadata, applies a version-pinned MNE-Python filter, previews the change, and exports a runnable Python script.

**Project stage: early development. No stable release. Independent EEG methods review pending.** The synthetic filter comparison is engineering evidence, not scientific validation. ICA review is not scientifically approved; epoching, ERP, PSD, and a complete report are not yet an executable end-to-end workflow.

- **[Documentation website](https://brain-learn.github.io/brainlearn/)** — installation, 10-minute EEG walkthrough, examples, user guide, API reference, and project status.
- [Full proposal](PROPOSAL.md)
- [Agent and contributor handoff](AGENTS.md)
- [Step-by-step implementation checklist](docs/implementation-plan.md)
- [Local development guide](docs/development.md)
- [Initial interface concept](design/brainlearn-layout-concept-v1.png)

BrainLearn is research software. It is not a medical device or diagnostic system.

BrainLearn's original code is licensed under the [BSD 3-Clause License](LICENSE). Third-party libraries, scientific tools, datasets, atlases, models, and plugins remain subject to their own licenses and citations.
