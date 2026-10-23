BrainLearn documentation
========================

```{raw} html
<section class="bl-hero" aria-labelledby="bl-hero-title">
  <p class="bl-eyebrow">Local-first · open source · research software</p>
  <h2 id="bl-hero-title">Build neuroscience workflows you can inspect and replay.</h2>
  <p class="bl-lede">BrainLearn is a visual workflow builder for EEG research. Connect typed analysis steps, inspect what each step produces, record quality-control decisions, and keep the workflow beside your data.</p>
  <div class="bl-hero-actions">
    <a class="sd-btn sd-btn-primary" href="quickstart.html">Start the 10-minute EEG walkthrough</a>
    <a class="sd-btn sd-btn-outline-primary" href="auto_examples/index.html">Explore examples</a>
  </div>
  <p class="bl-status-line"><span class="bl-status">Experimental</span> The BIDS import, signal inspection, and MNE filter path runs locally. The broader EEG workflow is still under development and independent methods review.</p>
</section>
```

```{image} _static/images/workflow-map.svg
:alt: BrainLearn's current experimental path from a BIDS EEG recording through signal inspection and MNE filtering; later analysis stages are marked planned.
:class: bl-workflow-map
```

## Start with a real, small workflow

BrainLearn is being built for researchers who want a visual workflow without giving up inspectability. The first runnable path reads one project-local BIDS EEG recording, records its metadata, applies a version-pinned MNE-Python filter, and saves a Python export with provenance.

The project is early-stage software. There is no stable release yet, and the demonstrated filter has been compared with direct MNE calls on a deterministic synthetic recording. That engineering comparison does not establish that any filter is suitable for a study.

::::{grid} 1 2 2 2
:gutter: 3

:::{grid-item-card} Quickstart
:link: quickstart
:link-type: doc

Install from source and run the synthetic EEG filter path locally.
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
| MNE-Python band-pass and optional notch filtering | Experimental; bounded to one recording and documented in the walkthrough |
| Standalone Python filter export | Available for the demonstrated filter path |
| ICA review, epoching, ERP, PSD, and complete reports | Not yet a certified end-to-end workflow |
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
