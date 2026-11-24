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
| ICA fitting, explicit component review, and exclusion persistence | Experimental, with an explicit per-recording decision |
| Event selection, epochs, ERP, PSD, and cohort HTML report | Implemented; completing engineering verification on the pinned sample |
| Pinned reference sample | OpenNeuro ds005284 v1.0.0, participants sub-001 through sub-005; engineering evidence only |

## Evidence and limits

The filter adapter, generated script, and direct MNE call agree within declared numerical tolerances on the synthetic engineering fixture. That is repeatability evidence for this implementation path only. The fixture contains no human data and cannot validate filter selection or scientific suitability.

The Step 7 sequencing waiver permits Step 8 engineering work to proceed; it is not scientific approval. The five-participant cohort path is experimental while engineering verification and independent methods review remain open. Direct-MNE parity demonstrates numerical agreement for the tested operations and parameters only. It does not establish suitable EEG methods, correct QC decisions, or scientific validity.

## Next gates

1. Complete the local and hosted engineering gates for the pinned ds005284 cohort path.
2. Obtain independent review of the EEG methods and reference comparison; address findings before making a certification claim.
3. Test task completion with independent research users.

BrainLearn is not a medical device and is not intended for clinical use.
