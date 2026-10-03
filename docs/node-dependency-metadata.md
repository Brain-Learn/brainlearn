# Node software dependency metadata

The versioned node registry schema 1.1 records the upstream software
associated with each planned EEG node. This metadata is descriptive: EEG nodes
are still examples and do not execute scientific processing.

Each software dependency record includes its Python distribution name, pinned
version, upstream SPDX license, software citations, and installation status.
The BIDS EEG input and signal-inspection nodes list MNE-BIDS and MNE-Python;
the planned processing nodes list MNE-Python.

Installation status is a snapshot taken when the local Python service imports
the registry. It reads installed distribution metadata without importing MNE
or MNE-BIDS. `installed` means the installed distribution version matches the
pin, `version_mismatch` means a distribution is present at another version,
and `missing` means no distribution metadata was found. This check does not
validate importability, transitive dependencies, platform support, numerical
behavior, or scientific certification. Restart the local service after
changing the Python environment to refresh the snapshot.

MNE-Python is recorded at version `1.13.2` under BSD-3-Clause. Its citations
include the canonical software paper and the project software archive. MNE-BIDS
is recorded at version `0.20.0` under BSD-3-Clause with its JOSS paper citation.
The project lock pins these versions in the optional `eeg` group; install them
with `uv sync --all-packages --group eeg`. The default environment omits them.

These software citations are not method-specific citations. When executable
nodes are added, each method adapter must record the citations appropriate to
the operations it actually performs.
