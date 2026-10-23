# Decision 0001: documentation stack

**Status:** Accepted for the first public documentation site

**Date:** 2026-10-23

## Decision

Use Sphinx, the PyData Sphinx Theme, Sphinx-Gallery, and MyST Markdown. Generate the public Python API reference from `brainlearn_core`; keep interface and service contracts as versioned documentation pages. Publish only static output through GitHub Pages.

## Why this fits BrainLearn

The project is built in Python for a scientific audience. Sphinx supports Python API generation, cross-references, versioned documentation workflows, and executable example galleries. Sphinx-Gallery runs checked-in scripts to generate example pages and downloadable notebooks. The PyData theme provides responsive navigation, search, light/dark modes, and a sidebar suited to a multi-section reference. MyST lets the team reuse Markdown knowledge while retaining Sphinx directives for API pages and examples.

Sphinx 8.2.3 is pinned because the selected `sphinx-design` release requires Sphinx below 9; the dependency resolver rejected Sphinx 9.2.0. Upgrade the stack only after the full build and theme compatibility gate passes.

## Alternatives considered

| Option | Benefits | Trade-offs for BrainLearn |
| --- | --- | --- |
| **MkDocs Material** | Strong search and authoring experience; easy Markdown-first site | Python API generation and scientific executable galleries need additional extensions and maintenance; less direct continuity with scientific Python projects |
| **Docusaurus** | Strong versioning and polished navigation; familiar React ecosystem | Requires a parallel JavaScript documentation layer and custom Python API generation; adds a second docs toolchain for a Python-first project |
| **Sphinx + PyData + Sphinx-Gallery** | Native Python autodoc, scientific ecosystem conventions, example execution and generated notebooks | More structured configuration; strict builds require careful docstring and cross-reference maintenance |

## Consequences and evidence

- Public examples live under `website/gallery/` and must be deterministic, bounded, and free of human data.
- Only the curated `brainlearn_core` package is generated as the supported API; other component contracts remain explicitly provisional.
- Builds use locked dependencies, fail on warnings, check links, and run accessibility/responsive checks in a browser.
- GitHub Pages receives generated static files only. It does not run BrainLearn or accept data uploads.
- Sphinx and MyST references: [Sphinx Markdown](https://www.sphinx-doc.org/en/master/usage/markdown.html), [PyData Sphinx Theme](https://pydata-sphinx-theme.readthedocs.io/en/stable/), and [Sphinx-Gallery](https://sphinx-gallery.github.io/stable/).
