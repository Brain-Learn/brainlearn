BrainLearn documentation
========================

```{raw} html
<section class="bl-hero" aria-labelledby="bl-hero-title">
  <p class="bl-eyebrow">Local-first · open source · research software</p>
  <h2 id="bl-hero-title">Build neuroscience workflows you can inspect and replay.</h2>
  <p class="bl-lede">BrainLearn is a visual workflow builder for EEG research. Connect typed analysis steps, inspect what each step produces, record quality-control decisions, and keep the workflow beside your data.</p>
  <div class="bl-hero-actions">
    <a class="sd-btn sd-btn-primary" href="eeg-cohort.html">Run the experimental cohort walkthrough</a>
    <a class="sd-btn sd-btn-outline-primary" href="auto_examples/index.html">Explore examples</a>
  </div>
  <p class="bl-status-line"><span class="bl-status">Experimental</span> A descriptive EEG cohort path is under engineering verification. Its processing settings are provisional and have not received independent methods approval.</p>
</section>
```

```{image} _static/images/workflow-map.svg
:alt: BrainLearn's experimental EEG cohort path from BIDS recordings through explicit quality review, filtering, ICA, event-locked epochs, ERP, PSD, and a descriptive report.
:class: bl-workflow-map
```

## Start with a real, small workflow

BrainLearn is being built for researchers who want a visual workflow without giving up inspectability. The first runnable path reads one project-local BIDS EEG recording, records its metadata, applies a version-pinned MNE-Python filter, and saves a Python export with provenance.

The project is early-stage software. There is no stable release yet. The cohort workflow and its comparison with direct MNE calls are under engineering verification; these checks do not establish that the settings are suitable for a study.

::::{grid} 1 2 2 2
:gutter: 3

:::{grid-item-card} Quickstart
:link: quickstart
:link-type: doc

Install from source and learn the local synthetic EEG filter path.
:::

:::{grid-item-card} EEG cohort walkthrough
:link: eeg-cohort
:link-type: doc

Run the experimental five-participant BIDS cohort path with explicit per-recording QC.
:::

:::{grid-item-card} Examples
:link: auto_examples/index
:link-type: doc

Reproducible, sample-bearing examples with expected outputs and limits.
:::

:::{grid-item-card} User guide
:link: user-guide
:link-type: doc

Learn how projects, typed workflow edges, previews, and QC decisions fit together.
:::

:::{grid-item-card} API reference
:link: api
:link-type: doc

Browse the maintained Python workflow-definition API.
:::
::::

## What works today

| Area | Current status |
| --- | --- |
| Visual graph editing and workflow validation | Available in the source checkout |
| Local project save/open, BIDS EEG discovery, and dataset inspection | Implemented; research-data handling remains local |
| MNE-Python band-pass and optional notch filtering | Experimental; explicit parameters and per-recording QC |
| Cohort execution, event-locked epochs, ERP, PSD, and HTML report | Under engineering verification on the pinned five-participant sample |
| Independent EEG methods approval and scientific certification | Pending; no scientific approval is claimed |
| Stable release and external pilot evidence | Not available yet |

See {doc}`status` for the exact maturity boundary and next project gates.

## Built for local research workflows

- **Local by default.** The app's Python service binds to loopback. The static documentation site does not accept data or run analyses.
- **Explicit connections.** Workflow ports carry scientific types, so incompatible steps can be rejected before a run.
- **Reviewable work.** Intermediate artifacts and researcher decisions are part of the workflow record; the app does not silently approve QC.
- **Open tools, preserved attribution.** EEG processing uses MNE-Python and MNE-BIDS. Their own licenses and citations remain attached to the analysis.

BrainLearn is research software. It is not a medical device and does not provide diagnosis or treatment advice.

```{toctree}
:hidden:
:maxdepth: 2

quickstart
eeg-cohort
user-guide
workflow-model
quality-control
reproducibility
installation
platform-support
auto_examples/index
api
contracts
citation
releases
status
contributing
governance
decisions/0001-documentation-stack
deployment
```
