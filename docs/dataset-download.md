# Verified dataset download lifecycle (Step 5A.4)

This document covers downloading an explicitly selected immutable public
snapshot through the local Python service into the authorized project. It
does not cover archive extraction, local/private import, MNE/BIDS
inspection, or credentials: those arrive in later Step 5 units.

## What a download fulfills

A download fulfills the exact file set of one `CatalogEntry`: every
expected file streams into a unique partial tree, each file is verified
(catalog checksum where one exists, exact declared size always), and only
then does the tree finalize. Hashes for catalog files without a checksum
are computed over the landed bytes and recorded in the lock, so the lock
identity always derives from what actually arrived. Size-only verification
is honest about its limits; the lock records which hashes are locally
computed versus provider-declared.

## Layout (implements the reserved layout in `dataset-contract.md`)

```text
<project>/downloads/<download-id>/transfer.json   transfer record (atomic writes)
<project>/datasets/.partial/<download-id>/...     in-progress bytes, unique per transfer
<project>/datasets/<provider>/<dataset-id>/<snapshot>/
  ...                                             verified bytes (appear atomically)
  dataset-lock-1.0.json                           lock, written after verification
```

The finalized tree appears atomically via rename: it is either complete
and verified or absent. A destination that already holds a valid lock, or
any obstructing file, refuses the download with 409 instead of merging or
overwriting. Partial bytes of a failed transfer are removed through the guarded
boundary only; cancelled and paused transfers keep theirs for resume.
Nothing is ever written, moved, or deleted through a path the storage
boundary has not proven owned by the project.

## Storage boundary

Every record, staging, and destination path crosses one component-checked
boundary before each filesystem operation: every path component is checked
lexically, each existing ancestor must be a real directory inside the
authorized project, and no component may be a symlink. File opens use
`O_NOFOLLOW`, hashing and verification read through no-follow descriptors
with `fstat` regular-file checks in bounded chunks, and writes that fail
the boundary stop the transfer instead of touching foreign bytes. A blocked
layout answers 409 with a static message.

## Transfer-record binding

A persisted transfer is bound to its embedded catalog entry: top-level
provider, dataset id, snapshot, and expected total must equal the catalog,
the catalog identity is recomputed and compared, and the transfer file set
(path, size, declared checksum) must be the canonical projection of the
catalog expectations in canonical order. Only a succeeded transfer may
carry a lock identity. Tampered records fail validation on read.

## Transfer states

`queued → downloading → succeeded`, with `paused`, `cancelled`, and
`failed` on the side. Resume requeues `queued`, `paused`, and `cancelled`
transfers; `failed` and `succeeded` are terminal. Attempts are bounded
(3 passes by default): a file error spends one attempt and retries the
file, except deterministic faults (size overrun, symlinks, occupied
destination), which fail fast. Running out of disk pauses with the bytes
kept instead of failing.

Success requires every file verified, the atomic rename, the atomic
lock write, and a durable succeeded record, in that order — no partial
output is ever a successful output. If anything fails after the rename,
the owned tree returns to its unique staging location and the transfer
parks as resumable; a destination that cannot be proven owned is never
deleted or replaced, so a terminal failure never coexists with an unlocked
finalized dataset.

## Restart recovery

Recovery requeues `downloading` records with byte accounting re-derived
from disk. It never invents success from sizes alone: a finalized tree is
adopted as succeeded only after every expected file re-hashes through the
hardened boundary and matches the lock, the lock file set matches the
catalog exactly, and no foreign object (extra file, symlink, FIFO,
directory swap) is present. Any non-terminal record whose destination
already verifies this way is adopted; a lockless destination that provably
holds only this transfer's files moves back to staging for resume, and an
unprovable destination parks the transfer as resumable without touching
the bytes. Drivers never auto-start on recovery; resuming bytes is always
explicit.

## Disk space

Free space must cover the catalog expectation plus a 64 MiB safety margin
(`DOWNLOAD_DISK_MARGIN_BYTES`), checked before start and before every
resume. Mid-download exhaustion pauses the transfer with bytes kept. The
service answers 507 when space is short.

## Sources and resume behavior

Providers expose streamed bytes through `FileDownloadSource`; sources
never write, verify, or persist. The OpenNeuro source serves
`https://openneuro.org/crn/datasets/<id>/snapshots/<tag>/files/<path>`
(same host only; cross-host redirects are refused) in bounded chunks.
Probed 2026-09-14, the endpoint ignores `Range` requests (HTTP 200 with
the full body), so `supports_resume` is false there: resume keeps
completed files and restarts the in-progress file from zero. A source that
answers a full stream to a resume offset raises `RangeUnsupportedError`
and the engine truncates and restarts that file within the same attempt.
Resume re-hashes the surviving prefix incrementally, one bounded chunk at
a time, retaining only a byte count: resume memory stays flat no matter
how large the completed prefix grows. A prefix that shrinks mid-hash
restarts the file once, then fails instead of looping.
Deterministic `ScriptedDownloadSource` doubles model short reads,
timeouts, retry exhaustion, cancellation gates, and both range behaviors
in offline tests.

## API behavior

All endpoints require the session token and an explicitly opened project
path (401/403). `POST /api/datasets/downloads` starts (409 on duplicate
active transfers and completed datasets, 404 on unknown snapshots,
422 on malformed identifiers, 507 on short disk); `GET` lists and reads
records; `POST .../cancel` halts keeping bytes; `POST .../resume`
rechecks space and requeues; `POST .../recover` reconciles after
restart. Error and failure messages are static strings plus validated
identifiers and counts only; provider exception text never reaches
responses, records, or logs.

## UI behavior

The dataset details panel offers one Download action per selected
snapshot, disabled while an action is in flight; the service answers 409
for genuine duplicates. Progress shows verified bytes with an accessible
progress element, polling only while a transfer is active and stopping on
view change, unmount, or project switch (sequence-guarded, no crossover).
Cancel and Resume act on the visible transfer; failure names its stable
code with a retry path; success appears only for verified completion with
byte counts. Transfer discovery (existing-transfer adoption) and user
mutations (Start/Cancel/Resume) hold separate ownership: a late adoption
can neither overwrite a user-installed transfer nor strand an action in
its pending state. No transfer URL, filesystem path, or credential field
is ever displayed.
