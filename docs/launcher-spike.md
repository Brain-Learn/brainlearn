# Local launcher spike

BrainLearn has an experimental source-checkout launcher. It starts the existing
Python API and React/Vite interface as separate local processes, waits for both
to respond, and then opens the interface in the default browser. The launcher
does not replace the Python service, move EEG work into the browser, or claim
to be a desktop installer.

## Requirements

- Python 3.12 or newer and `uv`.
- Node.js 22 or newer and npm.
- The locked Python environment and frontend dependencies installed from the
  repository root:

  ```sh
  uv sync --locked --all-packages --group eeg
  npm --prefix apps/web ci
  ```

## Launch

From the repository root:

```sh
uv run --locked --all-packages --group eeg brainlearn-launch
```

The API listens on `127.0.0.1:8000`; Vite listens on `127.0.0.1:5173` and
proxies API requests to that local service. The service prints its per-session
token in the terminal. Paste that token into the Local project panel when
filesystem access is needed. Press Ctrl-C in the launcher terminal to stop
both processes.

Useful options:

```sh
uv run --locked --all-packages --group eeg brainlearn-launch --no-browser
uv run --locked --all-packages --group eeg brainlearn-launch --check
uv run --locked --all-packages --group eeg brainlearn-launch --startup-timeout 60
```

`--check` starts both services, verifies the API health endpoint and the Vite
page, then stops them. It does not run an EEG workflow or certify scientific
behavior; it also suppresses the temporary API token from its startup output.
The launcher first checks that the fixed ports are free and uses a transient
instance marker from each child process for readiness, so an old service
cannot make the check pass. It uses strict ports and reports a conflict
instead of silently choosing a port that the API security and Vite proxy
settings do not expect.

## Scope of this spike

This proof of concept requires a source checkout, Python dependencies, Node.js,
and installed frontend dependencies. It does not bundle a Python runtime or
frontend assets, create a signed installer, perform automatic updates, or
certify an operating-system/architecture combination. The normal two-terminal
development commands remain available. Local data and the session token stay
on the machine; do not expose either loopback service to a public network.

The launcher preserves the current architecture: React remains a browser
client, Vite serves the UI, and the Python service owns validation and
execution. The Step 10 support-matrix and release/installer gates remain open.
