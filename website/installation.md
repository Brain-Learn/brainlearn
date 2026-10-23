# Install BrainLearn

BrainLearn does not yet provide a stable desktop installer or supported release package. Use the source checkout for the current experimental EEG workflow.

## Requirements

- Python 3.12 or newer.
- [uv](https://docs.astral.sh/uv/) for the locked Python environment.
- Node.js 22 or newer and npm for the React interface.
- A local operating-system account with permission to create a project directory.

## Source installation

```sh
git clone https://github.com/Brain-Learn/brainlearn.git
cd brainlearn
uv sync --all-packages --group eeg
npm --prefix apps/web ci
```

The optional EEG group installs MNE-Python and MNE-BIDS at the versions used by the engineering reference. To run only core development checks, follow the [contributor guide](https://github.com/Brain-Learn/brainlearn/blob/main/docs/development.md).

## Start the local app

```sh
# Terminal 1 (prints the per-process session token)
uv run brainlearn-server

# Terminal 2
npm --prefix apps/web run dev
```

Open the frontend URL printed by Vite. The API stays on loopback. Do not expose it to a public network.

For a guided first run, continue to {doc}`quickstart`.
