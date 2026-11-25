# Platform support

**Matrix version:** 0.1 · **Verified:** 2026-11-25
**Evidence revision:** `e8b7ad775f738931171f3a7972fdcfb4e5650529`

This matrix describes tested source-checkout environments. It is not a signed
installer compatibility list and does not certify the scientific EEG workflow.
The launcher keeps the Python API and browser interface on loopback. Runner
labels and architectures follow the
[GitHub-hosted runner reference](https://docs.github.com/en/actions/reference/runners/github-hosted-runners).

## Status definitions

- **Smoke-tested** means the exact CI environment installed the locked Python
  workspace and EEG dependency group, tested the API and cohort CLI, installed
  and built the web interface, and completed `brainlearn-launch --check`.
- **Experimental** means software is present and some tests exist, but the
  complete scientific workflow has not passed its independent review and
  platform certification gates.
- **Unsupported / unverified** means no runtime validation exists for that
  platform or backend. Do not advertise it as supported or silently replace a
  requested scientific method with another one.

## Operating system and architecture

| Environment | Architecture | Tested toolchain | Source-checkout application evidence | Scientific EEG status |
| --- | --- | --- | --- | --- |
| Ubuntu 24.04 (`ubuntu-24.04`) | Linux x86-64 | Python 3.12; uv 0.12.5; Node.js 22; locked MNE-Python 1.13.2 and MNE-BIDS 0.20.0 | **Smoke-tested.** API/cohort CLI checks, frontend build, and integrated launcher check pass. The Linux Python job also runs the complete test suite. | **Experimental.** A five-participant direct-MNE epoch/ERP/PSD comparison passed on Linux in the scheduled dataset job. This is engineering/reference evidence, not independent methods approval or platform certification. |
| macOS 15 (`macos-15`) | Apple Silicon (arm64) | Python 3.12; uv 0.12.5; Node.js 22; locked MNE-Python 1.13.2 and MNE-BIDS 0.20.0 | **Smoke-tested.** API/cohort CLI checks, frontend build, and integrated launcher check pass. | **Experimental.** Clean-install and startup smoke only; no real-data numerical reference run or independent scientific certification on macOS. |
| Windows 2025 (`windows-2025`) | x86-64 | Python 3.12; uv 0.12.5; Node.js 22; locked MNE-Python 1.13.2 and MNE-BIDS 0.20.0 | **Smoke-tested.** API/cohort CLI checks, frontend build, and integrated launcher check pass. | **Experimental.** Clean-install and startup smoke only; no real-data numerical reference run or independent scientific certification on Windows. |

The [main CI run on evidence revision `e8b7ad7`](https://github.com/Brain-Learn/brainlearn/actions/runs/45574914100)
passed the listed clean-install smoke jobs on all three environments. Those jobs
test source-checkout installation and startup; they do not run the complete
real-data EEG workflow on each operating system. The pinned five-participant
comparison passed on Linux in [Dataset Smoke run `45382882213`](https://github.com/Brain-Learn/brainlearn/actions/runs/45382882213)
at code head `a1c02487ca5871a1ee7e9367f9474ebcf005685e`.

## Compute backends

| Backend | Status | Evidence and boundary |
| --- | --- | --- |
| CPU | **Experimental** | The application smoke path runs on all three OS/architecture rows above. Linux has the reference dataset comparison. No row is certified for a complete, independently reviewed EEG workflow. |
| NVIDIA CUDA | **Unsupported / unverified** | Candidate detection is not runtime validation. Current workflows do not exercise CUDA execution. |
| Apple Metal (MPS) | **Unsupported / unverified** | Candidate detection is not runtime validation. Current workflows do not exercise MPS execution. |
| Containers and WSL | **Unsupported / unverified** | Runtime discovery does not certify a container image, WSL integration, or scientific execution inside one. |

## Other unverified platforms and integrations

Linux ARM64, Intel macOS, Windows ARM64, other operating systems, and FSL are
not advertised as supported. No clean-install or scientific workflow evidence
has been collected for them. Containers and WSL are listed above as
unsupported/unverified. Before release, workflows requiring a missing or
unvalidated runtime must show an unsupported state before execution; BrainLearn
must not silently switch methods. This matrix does not claim that every such
execution guard is already implemented.

This matrix is a dated engineering snapshot. Update its version and evidence
links whenever the tested runners, runtime versions, scientific workflow, or
backend coverage changes. A future release may claim a platform/backend as
supported only after a clean install and the certified reference workflow pass
for that exact combination.
