# Project status

**Stage: early development. No stable release. EEG scientific review pending.**

This site documents software behavior that exists in the repository; it does not imply independent approval. The first runnable path is deliberately narrow.

## Current EEG scope

| Capability | Status |
| --- | --- |
| Local BIDS dataset discovery and import | Implemented; local project boundary applies |
| Acquisition metadata and signal inspection | Implemented for the bounded path |
| MNE FIR band-pass and explicit optional notch filtering | Implemented; experimental |
| Before/after trace and Welch spectrum preview | Implemented as bounded inspection summaries |
| Standalone Python export for the filter path | Implemented with pinned MNE dependencies |
| ICA fitting, explicit component review, and exclusion persistence | Engineering work exists; independent methods review is still pending |
| Epochs, ERP, PSD, HTML report | Not yet a complete executable workflow |
| Sample-bearing public EEG scientific reference dataset | Still required for the scientific comparison gate |

## Evidence and limits

The filter adapter, generated script, and direct MNE call agree within declared numerical tolerances on the synthetic engineering fixture. That is repeatability evidence for this implementation path only. The fixture contains no human data and cannot validate filter selection or scientific suitability.

The independent EEG methods review is pending. Keep scientific approval unchecked until a qualified reviewer examines the workflow and the recorded evidence. The fuller workflow and external pilots remain future work.

## Next gates

1. Obtain and record an independent review of the EEG methods and reference comparison.
2. Resolve method findings and close the scientific gate with reproducible evidence.
3. Add the sample-bearing public BIDS reference dataset and complete the descriptive EEG path.
4. Test installation and task completion with independent research users.

BrainLearn is not a medical device and is not intended for clinical use.
