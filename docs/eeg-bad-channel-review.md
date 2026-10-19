# EEG bad-channel review

The experimental **Bad Channel Review** node records a researcher decision for
one inspected BIDS EEG recording. Connect the raw output of **Inspect Signal**
to the review node and run the workflow. BrainLearn pauses before completing
the node.

Review the recording in the EEG signal preview, then enter the researcher's
name and any EEG channel names to exclude. The local service rechecks the
recording identity and verifies each selected name against the current EEG
channel list before accepting the decision. Approving or rejecting the review
is explicit; the review record stores the decision, researcher, note,
excluded channels, run and node identities, input identities, and BIDS source
content identity.

Records are written atomically under
`runs/<run-id>/qc-reviews/` inside the open BrainLearn project and are included
when the run is opened again. They are kept separate from the BIDS source
files. This node records the bad-channel decision; it does not edit the raw
recording or apply exclusions to a later processing node. Rejected decisions
remain in run history and fail that run.

This is an experimental research workflow feature. The selection remains a
researcher's judgment and has not received independent EEG-methods review.
