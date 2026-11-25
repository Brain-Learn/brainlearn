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
uv sync --locked --all-packages --group eeg
npm --prefix apps/web ci
```

The optional EEG group installs MNE-Python and MNE-BIDS at the versions used by the engineering reference. To run only core development checks, follow the [contributor guide](https://github.com/Brain-Learn/brainlearn/blob/main/docs/development.md).

## Start the local app

```sh
uv run --locked --all-packages --group eeg brainlearn-launch
```

The source-checkout launcher starts the API and React interface, waits for
both, then opens the browser. The API prints the per-process session token in
the terminal; paste it into the Local project panel when needed. Press Ctrl-C
in that terminal to stop both services. The API and UI stay on loopback. Do not
expose them to a public network.

This is a launcher proof of concept, not a packaged desktop installer. It still
requires Python, `uv`, Node.js, npm, and the installed source-checkout
dependencies. Pass `--no-browser` to keep the browser closed, or `--check` to
start both services, verify readiness, and stop them automatically. The
previous two-terminal development commands remain available for frontend
development.

For a guided first run, continue to {doc}`quickstart`.
