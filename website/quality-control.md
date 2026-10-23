# Quality control and researcher decisions

BrainLearn treats quality control as a documented part of the analysis. A workflow may pause for an explicit decision; the software does not infer that a signal is acceptable just because a node completed.

## What the researcher must decide

- Whether the input recording and acquisition metadata match the study.
- Which channels require attention and what action is justified.
- Whether a filter's transition and pass bands fit the research question.
- Whether ICA components should be excluded and why.
- Whether epochs meet the study's inclusion and rejection rules.
- Whether a report accurately reflects the analyzed and excluded data.

The application can display bounded previews and persist decisions. A preview is not a validated automatic QC score. Any final decision remains the researcher's responsibility and should be recorded with a reason and the evidence reviewed.

## ICA review boundary

Fitting an ICA model and applying a component exclusion are separate actions. Exclusions should be explicitly reviewed, persisted, and invalidated when the signal, decomposition, or relevant settings change. BrainLearn's ICA work has an engineering implementation, but the methods review gate is still open; do not treat it as a scientifically approved workflow.

## No clinical use

BrainLearn is not a medical device, diagnostic system, or treatment tool. It does not diagnose or score participants. Research analyses require appropriate ethics approvals, data governance, and investigator judgment.

See the current {doc}`status` and the [ICA engineering notes](https://github.com/Brain-Learn/brainlearn/blob/main/docs/ica-engineering.md).
