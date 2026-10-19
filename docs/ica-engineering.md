# Experimental EEG ICA fit and review

## Scope

This implementation adds an engineering path from the existing BIDS
inspection/filter workflow through a persisted ICA fit and an explicit human
component decision. ICA fitting and ICA application are separate nodes. The fit
node produces an unreviewed model, diagnostics, and provenance; it never
excludes components. The review node pauses, saves the researcher's decision
against the exact raw and fitted artifacts, and only applies an approved choice
after the run resumes.

This is not a scientifically certified artifact-removal procedure. The current
end-to-end acceptance fixture is a small synthetic BIDS recording. Component
selection is manual. BrainLearn does not identify artifacts automatically,
recommend exclusions, or claim that removing a selected component improves an
analysis. MNE recommends high-pass filtering before ICA and describes 1 Hz as a
typical cutoff; the demo's preceding filter defaults to 1 Hz, but researchers
must review their own data and parameter choices. MNE also cautions that repair
may be unnecessary when artifacts are small. See the [MNE ICA API
reference](https://mne.tools/stable/generated/mne.preprocessing.ICA.html) and
[MNE artifact-correction tutorial](https://mne.tools/stable/auto_tutorials/preprocessing/40_artifact_correction_ica.html).

## Fit settings and bounds

The adapter uses pinned MNE-Python 1.13.2 and scikit-learn 1.7.2. It fits
`mne.preprocessing.ICA` with `method="fastica"`, `n_components=None`,
`max_iter=1000`, `fit_params=None`, `allow_ref_meg=False`, and the run's unsigned
32-bit seed as `random_state`. It selects EEG channels, disables annotation
rejection, and uses every sample (`decim=1`). Fits are limited to 256 EEG
channels and 25,000,000 channel-samples. These are product safeguards, not
recommended scientific settings for every recording.

MNE performs its own pre-whitening and PCA before ICA. The fitted component
count and input channel names are stored in the fit provenance. Researchers
should inspect data rank, reference, bad-channel handling, annotations,
filtering, and convergence for their experiment. This initial implementation
does not estimate rank, interpolate channels, reject bad spans, fit reference
channels, or infer component labels. FastICA is based on the fixed-point ICA
algorithm described by Hyvärinen ([1999](https://doi.org/10.1109/72.761722)).

## Diagnostics shown to the reviewer

The fit publishes signed MNE component channel weights, a bounded source
time-course envelope, and a component power spectrum. The preview uses the
first four seconds of data (or the entire recording if shorter). The spectrum
uses MNE Welch estimation with a Hamming window, mean averaging, DC removal,
`n_fft=512`, `n_per_seg=min(512, sample_count)`, and 50% overlap. The units are
reported as arbitrary ICA-source units and arbitrary units squared per Hz.
When complete sensor positions are present, they are retained in diagnostics;
the current UI still displays channel weights rather than rendering a scalp
map. It states when sensor locations are absent so weights are not mistaken for
a scalp topography.

These summaries support a human review but do not replace inspecting the
recording, experimental context, or MNE's dedicated plotting tools. The
reviewer enters one-based component numbers in the UI; records and generated
Python store zero-based MNE indices. An empty exclusion list is a valid explicit
approval.

## Persistence, stale decisions, and export

The saved QC record contains the run, review node, graph-node and input
identities, current input artifact identities, reviewer, decision, exclusions,
note, and timestamp. Before accepting an ICA decision, the service verifies the
raw and fitted artifacts and confirms that the fit provenance names the same
raw artifact. For a BIDS reference it also rechecks the current source identity.
When either input artifact is no longer the current graph input, the historical
record remains on disk for audit but is hidden from the active run and cannot be
reused. Rejection is persisted and ends that workflow run without emitting
reviewed EEG.

The standalone Python renderer is
`brainlearn.eeg.mne-ica.python@0.1.0`. It is available for the supported five
node topology only: BIDS EEG, Inspect Signal, Band-pass Filter, Fit ICA, and ICA
Component Review. It includes the saved reviewer choice and decision metadata
in the script and output provenance. The generated script is executed in the
engineering fixture acceptance test; it is not a general arbitrary-workflow
exporter.

## Numerical engineering check

`tests/test_eeg_ica.py` runs the BrainLearn workflow, a separately written
direct-MNE reference, and the generated standalone script on the same local
synthetic BIDS recording, filter settings, seed, and excluded component. Their
reviewed FIF samples are compared in microvolts with `rtol=1e-6` and
`atol=3e-5 µV`. The test also confirms the BIDS source files are unchanged, the
fit pauses before exclusions, approval resumes processing, rejection does not
produce reviewed data, and changes to either input artifact hide the saved
choice. This tolerance describes only this pinned engineering fixture and
environment. It is not a scientific acceptance threshold across devices,
recordings, MNE versions, or platforms.

## Citations and software

- Gramfort, A., et al. (2013). *MEG and EEG data analysis with MNE-Python*.
  [doi:10.3389/fnins.2013.00267](https://doi.org/10.3389/fnins.2013.00267).
- Hyvärinen, A. (1999). *Fast and robust fixed-point algorithms for independent
  component analysis*. [doi:10.1109/72.761722](https://doi.org/10.1109/72.761722).
- MNE-Python 1.13.2, BSD-3-Clause; scikit-learn 1.7.2, BSD-3-Clause. Their
  dependency metadata is recorded on the ICA node manifests.

An independent EEG methods review and a sample-bearing public BIDS reference
remain required before any scientific-suitability claim or Step 7 approval.
