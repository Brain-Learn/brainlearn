<p align="center">
  <img src="website/_static/brainlearn-mark.png" alt="BrainLearn EEG signal branching into workflow nodes" width="360">
</p>

# BrainLearn

BrainLearn is a local-first visual workflow builder for reproducible neuroscience research. Its experimental EEG path includes BIDS cohort selection, explicit per-recording quality-control decisions, event-locked epochs, ERP and PSD summaries, reports, and a portable Python/notebook export. These are engineering capabilities, not scientific approval: independent EEG methods review is pending, and no EEG workflow is certified.

**Project stage: early development. No stable release. Independent EEG methods review pending.** The synthetic filter comparison and direct-library parity checks are engineering evidence, not evidence that the selected methods or QC decisions are scientifically suitable.

- **[Documentation website](https://brain-learn.github.io/brainlearn/)** — installation, 10-minute EEG walkthrough, examples, user guide, API reference, and project status.
- [Full proposal](PROPOSAL.md)
- [Agent and contributor handoff](AGENTS.md)
- [Step-by-step implementation checklist](docs/implementation-plan.md)
- [Local development guide](docs/development.md)
- [Initial interface concept](design/brainlearn-layout-concept-v1.png)

## Project policies

- [Contributing](CONTRIBUTING.md)
- [Code of Conduct](CODE_OF_CONDUCT.md)
- [Security policy](SECURITY.md)
- [Governance](GOVERNANCE.md)
- [Changelog](CHANGELOG.md)
- [Citation metadata](CITATION.cff)

BrainLearn is research software. It is not a medical device or diagnostic system.

BrainLearn's original code is licensed under the [BSD 3-Clause License](LICENSE). Third-party libraries, scientific tools, datasets, atlases, models, and plugins remain subject to their own licenses and citations.
