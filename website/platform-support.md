# Platform support

The interface and local API can run in a source checkout on Python 3.12 and Node.js 22 or newer. This does not certify that every scientific dependency or processing path behaves identically on every operating system.

| Platform | Project target | Current evidence |
| --- | --- | --- |
| Linux x86-64 | Reference heavy-compute platform | CI covers core and the pinned EEG test environment on Ubuntu; end-user installation and full workflow matrix are not yet certified |
| macOS Apple Silicon | CPU target | Source-level use is expected; a supported-version installation and EEG run matrix is pending |
| Windows | UI and CPU EEG target | Source-level use is in scope; a supported-version installation and EEG run matrix is pending |
| CUDA, MPS, containers, WSL | Optional integrations | Detection or candidate reporting is not runtime certification |

When an adapter's dependency is missing or unsupported, the workflow should report that state clearly. BrainLearn must not silently substitute another scientific method.

See the [implementation plan](https://github.com/Brain-Learn/brainlearn/blob/main/docs/implementation-plan.md) for the platform and packaging gates.
