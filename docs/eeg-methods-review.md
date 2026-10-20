# Step 7 independent EEG methods review packet

## Review status

**Pending.** This packet prepares the scientific review; it is not a review or
approval. Step 7's top-level and scientific-review checkboxes stay unchecked
until an independent EEG methods reviewer records a decision and any required
changes have been completed and verified.

## Review scope

Review the Step 7 EEG workflow and its assumptions on the pinned sample-bearing
recording. The current engineering path is:

```text
BIDS EEG → Inspect Signal → 1–40 Hz band-pass → FastICA fit → explicit ICA review
```

The selected input is `sub-EP10/ses-01/task-dots/run-01` from OpenNeuro
`ds007338`, version `1.0.0`. It contains one participant and one EDF recording:
129 EEG channels, 500 Hz, and 162,000 samples (324 seconds). The EEG sidecar
reports 323.464 seconds; the reference preserves both source values. The
sample is downloaded at test time from a pinned 12-file manifest and is not
stored in Git. See the [fixture manifest](fixtures/ds007338-sub-EP10-1.0.0.json)
and [reference instructions](public-eeg-reference.md).

## What the current comparison proves

On the same sample and parameters, the BrainLearn adapter, a separately written
direct-MNE reference, and the generated Python export agree within
`rtol=1e-6`, `atol=3e-5 µV`; the BIDS source files remain unchanged. The
comparison demonstrates software agreement for this run. It does not show
that the selected processing is scientifically suitable.

The comparison uses a 1–40 Hz zero-phase FIR filter and MNE FastICA with seed
0. The component-review record explicitly keeps every component. That choice
exists to make the three software paths deterministic; it is not an artifact
decision. BrainLearn does not assign artifact labels automatically.

## Published pipeline context

The EEGEyeNet paper describes its own minimal preprocessing pipeline: bad
channel detection with clean_rawdata, a 0.5–40 Hz band-pass, Zapline line-noise
removal, and spherical-spline interpolation of marked channels. Its maximal
pipeline adds ICA with ICLabel artifact classification. BrainLearn's
1–40 Hz engineering comparison does not reproduce either pipeline. See
[Kastrati et al. (2021), especially Appendix A](https://arxiv.org/abs/2111.05100).

This difference is deliberate for the current deterministic software
comparison and has not been approved as a scientific recipe. The reviewer
should assess whether BrainLearn's current defaults and explicit review
boundaries are defensible for a bounded demo, and state what changes are
required before scientific claims or the Step 8 descriptive workflow.

## Questions for the reviewer

1. Is a 1–40 Hz zero-phase FIR filter an acceptable engineering-demo default
   for this recording, and what assumptions or limitations must be shown to
   researchers? Should the reference use the dataset paper's 0.5–40 Hz setting
   to support a comparison claim?
2. Are channel reference, montage, bad-channel, interpolation, line-noise, and
   annotation handling explicit and safe enough before ICA? Which choices must
   be researcher-controlled or fail closed?
3. Are FastICA's current fit settings, rank handling, convergence checks, and
   displayed component diagnostics sufficient for manual review on this
   recording? What diagnostic or reviewer action is missing?
4. Does an explicit empty exclusion list communicate the correct meaning and
   preserve a real human decision without implying that artifacts were
   removed?
5. Are the input metadata, event timing, sampling dimensions, units, and the
   sidecar/EDF duration discrepancy handled correctly for this reference?
6. What concrete scientific tests, parameter changes, or limitations are
   required before the Step 7 gate can pass? Please identify methods references
   that should be added to the workflow's citations.

## Reproduction material

The implementation is in merged commit `103f558`. The exact public-data
three-path test and fixture retrieval commands are in
[`docs/public-eeg-reference.md`](public-eeg-reference.md). Engineering
verification recorded in `REVIEW.md` and `docs/implementation-plan.md` passed
831 tests with 3 skipped, and the monitor approved the public-data engineering
comparison leaf. No reviewer has approved the scientific assumptions yet.

## Reviewer record

- Reviewer and relevant EEG methods experience: pending
- Date and reviewed code commit: pending
- Decision for the bounded Step 7 workflow: pending
- Required changes and their verification: pending
- Remaining limitations and references: pending

Do not check Step 7's parent or scientific-review gate based on completion of
this packet. Record the reviewer's actual decision and evidence in the
implementation plan and `REVIEW.md`.
