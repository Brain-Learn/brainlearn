# Pinned sample-bearing EEG engineering reference

## Dataset and license

The reference input is `sub-EP10/ses-01/task-dots/run-01` from the
[EEGEyeNet Dataset, OpenNeuro ds007338 snapshot 1.0.0](https://openneuro.org/datasets/ds007338/versions/1.0.0).
The snapshot's `dataset_description.json` declares CC0 1.0 Universal. Its
dataset DOI is [10.18112/openneuro.ds007338.v1.0.0](https://doi.org/10.18112/openneuro.ds007338.v1.0.0).
The dataset describes the paired EEG and eye-tracking resource in Kastrati et
al. ([2021 paper](https://doi.org/10.48550/ARXIV.2111.05100)). Exact file URLs,
sizes, and SHA-256 digests for the selected subset are in
[`docs/fixtures/ds007338-sub-EP10-1.0.0.json`](fixtures/ds007338-sub-EP10-1.0.0.json).

The subset contains one EDF recording and its BIDS metadata and sidecars,
including events, channel names, and CapTrak electrode positions. It downloads
about 42 MB; no recording samples are checked into this repository. Retrieve
the pinned files into a new directory with:

```bash
uv run python scripts/fetch_bids_eeg_fixture.py /tmp/brainlearn-eegeyenet \
  --manifest docs/fixtures/ds007338-sub-EP10-1.0.0.json
```

To run the three-path integration check against that copy:

```bash
BRAINLEARN_PUBLIC_BIDS_EEG_FIXTURE=/tmp/brainlearn-eegeyenet \
  uv run --frozen pytest -q \
  tests/test_eeg_ica.py::test_public_bids_eeg_ica_matches_direct_mne_and_generated_export
```

The test runs BrainLearn, a separate direct-MNE script, and the generated
Python export using the same recording, 1–40 Hz zero-phase FIR filter, seed 0,
FastICA settings, and explicit empty exclusion list. It compares the resulting
FIF samples in microvolts with `rtol=1e-6` and `atol=3e-5 µV`, checks ICA
convergence and the recorded 129-channel fit, and confirms the source files do
not change. The empty list is an explicit engineering decision to preserve
every fitted component; it is not an artifact assessment or recommendation.

This is not a reproduction of the EEGEyeNet publication's preprocessing. Its
Appendix A describes bad-channel detection, a 0.5–40 Hz band-pass, Zapline line
noise removal, and interpolation; its maximal pipeline adds ICA with ICLabel
artifact classification. BrainLearn's 1–40 Hz engineering path does not run
those procedures. See the [Step 7 methods-review packet](eeg-methods-review.md)
for the questions that need independent evaluation.

## Limits

This snapshot supplies real samples for reproducible software comparison. It
contains one participant and one selected recording, so it cannot validate
cohort behavior or population claims. The EDF has 129 EEG channels, 500 Hz
sampling frequency, and 162,000 samples (324 seconds). Its EEG sidecar reports
323.464 seconds. The fixture keeps both source values and does not reconcile
that discrepancy. The filter, ICA model, exclusion choice, and numerical
tolerance have not been reviewed for scientific suitability. This is not a
certified preprocessing recipe and must not support clinical claims.
