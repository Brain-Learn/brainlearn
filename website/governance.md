# Governance and project boundaries

BrainLearn is maintained in public through repository history, issues, and pull requests. The project is not yet at public-beta maturity and has no stable release governance process. Changes to scientific behavior require evidence and review proportional to the method.

## Research-only purpose

BrainLearn supports reproducible neuroscience research workflows. It is not a medical device, diagnostic system, participant-scoring service, or treatment tool. The project does not make clinical claims.

## Data stewardship

The application is local-first. Research data is not uploaded to the documentation site, and the site has no analysis service. Never commit personal, identifiable, restricted, or large research data. Use synthetic fixtures or public data with a recorded source, version, license, and checksum.

## Third-party software and data

BrainLearn's original code uses BSD-3-Clause. Each dependency, scientific tool, dataset, atlas, and plugin keeps its own license and attribution. Review licensing and citations before bundling or redistributing any third-party material.

## Reviews and decisions

Engineering review checks implementation behavior. Independent methods review checks scientific assumptions and interpretation. These are separate decisions. The current EEG methods gate is pending, as shown on the {doc}`status` page.

The repository's [security and data threat model](https://github.com/Brain-Learn/brainlearn/blob/main/docs/threat-model.md) describes the local-service boundary and is the source of truth for changes to file access, origin checks, or session tokens.
