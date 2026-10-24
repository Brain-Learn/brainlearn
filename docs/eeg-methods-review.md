# Step 7 independent EEG methods review packet

## Review status

**Pending.** Partial email feedback from Arnaud Delorme is recorded below.
It is not a full review or approval of the bounded workflow. Requests to
additional reviewers are pending. Step 7's top-level and
scientific-review checkboxes stay unchecked until an independent EEG methods
reviewer assesses the workflow against the pinned recording, records a
decision, and any required changes have been completed and verified.

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

### Interactive synthetic-demo verification

On 2026-10-24, the local GUI was exercised end to end in a fresh temporary
project with the manifest-pinned synthetic BIDS fixture in the
[synthetic fixture manifest](../examples/synthetic-eeg-bids-manifest.json). Run
`run-01715e7585f4` completed the BIDS input, signal inspection, and MNE filter
nodes. The UI opened signal and Welch-spectrum comparisons for Cz and Pz. The
Python export downloaded from that run was executed with MNE 1.13.2 and
MNE-BIDS 0.20.0, and wrote a FIF output plus provenance. The GUI and standalone
script FIF readbacks both contained two channels and 2,500 samples; their
maximum absolute difference was `0 µV` (`rtol=1e-6`, `atol=2e-5 µV`). All nine
fixture-file checksums still matched the committed manifest after both paths.

This verifies the interactive engineering path on the deterministic synthetic
recording. It does not establish suitable filter settings for human EEG, assess
signal quality, or complete the independent methods review. The temporary
project path and session token are intentionally omitted.

The comparison uses a 1–40 Hz zero-phase FIR filter and MNE FastICA with seed
0. The component-review record explicitly keeps every component. That choice
exists to make the three software paths deterministic; it is not an artifact
decision. BrainLearn does not assign artifact labels automatically.

## Current annotation behavior to review

The adapter currently calls MNE filtering with
`skip_by_annotation=("edge", "bad_acq_skip")`. MNE splits filtering around
segments whose annotation descriptions begin with either prefix, matching
without regard to case. Thus `BAD_ACQ_SKIP` segments are skipped by filtering,
but a segment such as `BAD_artifact` is not. The subsequent ICA fit explicitly
uses `reject_by_annotation=False`, so MNE does not omit `bad`-annotated
segments from the fit. In MNE 1.13.2, `ICA.fit` defaults to
`reject_by_annotation=True`, which omits annotations whose descriptions begin
with `bad`, without regard to case. See the pinned
[MNE Raw filtering documentation](https://mne.tools/1.13/generated/mne.io.Raw.html)
and [MNE ICA documentation](https://mne.tools/1.13/generated/mne.preprocessing.ICA.html).

These settings are preserved in the direct-MNE comparison and generated
export so the three paths agree; that agreement does not establish that
filtering `BAD_artifact` spans or including them in ICA is appropriate. Please
assess whether bad spans must be rejected or explicitly reviewed before
filtering and ICA, what decision should be persisted, and what behavior is safe
when the input has no suitable annotations. Do not infer that annotations were
absent or harmless merely from the numerical comparison.

### Observations in the pinned recording

The EDF was fetched from OpenNeuro's public S3 mirror and matched the fixture
manifest's size and SHA-256. Reading it with the pinned MNE 1.13.2 found 272
embedded annotations, including exactly one `BAD`-prefix annotation:
`BAD_ACQ_SKIP` at 323.466 seconds, lasting 0.534 seconds and ending at 324.000
seconds. The BIDS `events.tsv` contains 271 rows. Under the current adapter
settings, filtering skips this `BAD_ACQ_SKIP` span, while ICA fitting includes
it because `reject_by_annotation=False`.

The EEG sidecar reports 500 Hz and 323.464 seconds, but lists `EEGReference`,
`PowerLineFrequency`, and `SoftwareFilters` as `n/a`. The EDF contains 129 EEG
channels and 162,000 samples (324 seconds). The BIDS channels table labels all
129 channel rows `good`; that source label is not a BrainLearn QC decision and
does not establish signal quality, reference, or hardware filtering. The
annotation begins 2 ms after the sidecar's stated duration and ends with the
EDF; both source duration values remain unreconciled. These are observations for
review, not a scientific interpretation or approval.

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

## EEGPrep candidate raised in partial feedback

Delorme's replies recommended evaluating EEGPrep, and clarified that MNE could
be replaced by EEGPrep, particularly for ICA. The cited 2026 preprint
describes an EEGLAB-style workflow including average rereferencing,
clean_rawdata, Picard ICA, ICLabel, channel interpolation, and epoching. It
reports numerical comparisons with MATLAB on a 64-channel P300 dataset from
13 participants (12 analyzed subjects on ARM), including near-zero differences
for deterministic stages and maximum end-to-end RMS difference at or below
2.1e-5 microvolts for the tested pipeline. This supports implementation parity
for those tested steps and data; it does not establish general scientific
suitability or validate BrainLearn's specific choices.

The current PyPI release is EEGPrep 0.4.0, published 2026-09-23 and classified
as Alpha; it requires Python 3.12 or later and declares BSD-2-Clause. This
matches BrainLearn's Python floor, but its release status and separately
licensed dependencies still require review before adding it to a supported
environment. EEGPrep's own MNE integration guide says EEGPrep is not a
replacement for MNE-Python: it describes converting MNE Raw objects into
EEGPrep, running EEGPrep preprocessing/review, then converting back to MNE.
The guide also calls out unit and event-latency differences and temporary
EEGLAB files in conversion. These are adapter and provenance boundaries to
test, not evidence of incompatibility.

Assessment: EEGPrep is a credible candidate for a side-by-side engineering and
methods comparison on the pinned BIDS recording, especially for its complete
EEGLAB-derived preprocessing path and ICA. The recommendation does not establish
that an EEGPrep conversion is suitable for this recording or that it can replace
the current node without further review. The paper does not settle handling of
this recording's terminal `BAD_ACQ_SKIP`, unknown EEG reference, or unknown
power-line frequency. No dependency or algorithm has been changed. Before any
adoption, compare BIDS metadata and annotations across the conversion boundary,
units, events, filtering, reference, ICA fit/review, and output provenance on
the same checksum-pinned input.

Sources: [EEGPrep preprint](https://arxiv.org/abs/2607.16647),
[EEGPrep 0.4.0 release metadata](https://pypi.org/project/eegprep/0.4.0/),
[EEGPrep MNE integration guide](https://eegprep.org/user_guide/mne_integration.html),
and [EEGPrep preprocessing guide](https://eegprep.org/user_guide/preprocessing_pipeline.html).

## Questions for the reviewer

1. Is a 1–40 Hz zero-phase FIR filter an acceptable engineering-demo default
   for this recording, and what assumptions or limitations must be shown to
   researchers? Should the reference use the dataset paper's 0.5–40 Hz setting
   to support a comparison claim?
2. Given the exact annotation behavior above, should `BAD` spans be protected
   from filtering and omitted from ICA fitting? Should the workflow require a
   researcher decision when bad annotations exist or are absent? How should
   channel reference, montage, bad-channel review, interpolation, and line-noise
   handling be exposed, persisted, or fail closed before ICA?
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
7. Does EEGPrep's reported EEGLAB parity make it a better candidate for the
   pinned recording, and what validation would still be needed for its
   annotation, metadata, unit, event, and manual-review behavior?

## Reproduction material

The exact code snapshot for review is the tree at
[`103f5588c76d9d11a8810058ab400e85551d4303`](https://github.com/Brain-Learn/brainlearn/tree/103f5588c76d9d11a8810058ab400e85551d4303).
It contains the current filter, QC, ICA, and Python-export implementation as
well as the public-data comparison. The relevant implementation history is
[filtering and Python export (`2949d7f`)](https://github.com/Brain-Learn/brainlearn/commit/2949d7ffed093be49f69539706541eac4caf009e),
[immutable QC review records (`f6b5c6b`)](https://github.com/Brain-Learn/brainlearn/commit/f6b5c6be6ac2bbf9ff2058a68bb1d90366459969),
[bad-channel review persistence (`8df8c8d`)](https://github.com/Brain-Learn/brainlearn/commit/8df8c8d8dd16208c857c3d77a3baa3d70d6670ef),
[ICA review (`b09419d`)](https://github.com/Brain-Learn/brainlearn/commit/b09419db746c71de99ecbe7857a14bc2c7785d8d),
and [public-data validation (`103f558`)](https://github.com/Brain-Learn/brainlearn/commit/103f5588c76d9d11a8810058ab400e85551d4303).
Main has advanced since that code snapshot through documentation-only
commits; the source, tests, and fixture at the pinned revision are the scope
for this review.

The exact public-data three-path test and fixture retrieval commands are in
[`docs/public-eeg-reference.md`](public-eeg-reference.md). Engineering
verification recorded in `REVIEW.md` and `docs/implementation-plan.md` passed
830 tests with 4 skipped, and the monitor approved the public-data engineering
comparison leaf. No reviewer has approved the scientific assumptions yet.

## Reviewer record

- Reviewer and relevant EEG methods experience: Arnaud Delorme; UCSD lists EEG
  analysis, independent component analysis, and neuroinformatics among his
  research areas ([UCSD profile](https://profiles.ucsd.edu/arnaud.delorme)).
- Date and reviewed code commit: email response received 2026-10-23; the
  reviewer did not identify a reviewed code commit.
- Decision for the bounded Step 7 workflow: no overall decision. The response
  gives partial feedback on the EEGPrep/ICA direction and channel/segment
  handling before ICA.
- Feedback and scope: the initial reply expressed concern about validation of
  the ICLabel pipeline in MNE and suggested EEGPrep. BrainLearn's pinned
  workflow currently uses MNE FastICA followed by explicit manual component
  review; it does not run ICLabel. The ICLabel concern therefore does not
  directly describe the current implementation. In a follow-up reply (message
  `1a169a8428d52434`), Delorme said MNE could be replaced by EEGPrep,
  particularly for ICA. In response to the annotation and metadata questions,
  he characterized these as data-specific and said their usual practice is to
  remove channels and segments with `clean_rawdata` before ICA. He did not
  specify parameters for this recording or give an overall decision. This is
  partial feedback, not approval of the current handling or a validated
  instruction to change the workflow.
- References provided: [Automatic EEG Independent Component Classification
  Using ICLabel in Python (2024)](https://arxiv.org/abs/2411.17721) compares
  Python and MATLAB ICLabel results across 14 participants. [EEGPrep: a
  validated Python implementation of the EEGLAB preprocessing pipeline
  (2026)](https://arxiv.org/abs/2607.16647) reports stage-wise and end-to-end
  comparisons against MATLAB on 13 participants. These papers support
  evaluating the suggested route; they do not validate BrainLearn.
- Required changes and their verification: no implementation change was
  requested or made. A follow-up was sent 2026-10-23 in the same Gmail thread
  (message `1a169a569d750452`) asking whether the ICLabel comment concerns a
  different pipeline and whether the pinned recording's annotation/metadata
  issues change the recommendation; the response arrived later that day
  (message `1a169a8428d52434`). Evaluate EEGPrep's fit for BrainLearn before
  making a processing or architecture decision.
- Additional independent-review request: Kay Robbins, Professor Emerita at
  UT San Antonio, whose work includes EEG preprocessing, PREP, and EEG
  preprocessing sensitivity ([UTSA profile](https://caicc.utsa.edu/faculty/profiles/robbins-kay.html),
  [PREP paper](https://doi.org/10.3389/fninf.2015.00016), and
  [preprocessing sensitivity study](https://doi.org/10.1109/TNSRE.2020.2980223)).
  A brief request for an independent critique or referral was sent to her
  publicly listed institutional address on 2026-10-24 (Gmail message/thread
  `1a16db33c9ccb242`). Response pending.
- Remaining limitations: this partial feedback is not a review of the full
  Step 7 assumptions and does not close the independent scientific-review
  gate.

Do not check Step 7's parent or scientific-review gate based on completion of
this packet. Record the reviewer's actual decision and evidence in the
implementation plan and `REVIEW.md`.
