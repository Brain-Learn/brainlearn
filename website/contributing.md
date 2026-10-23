# Contributing

BrainLearn is early-stage research software. Contributions should be small, reproducible, and explicit about scientific assumptions. Start with the repository's [agent and contributor handoff](https://github.com/Brain-Learn/brainlearn/blob/main/AGENTS.md), the [development setup](https://github.com/Brain-Learn/brainlearn/blob/main/docs/development.md), and the [GitHub pull request workflow](https://github.com/Brain-Learn/brainlearn/blob/main/docs/github-workflow.md).

## Before opening a change

1. Find the first unchecked assignment in `docs/implementation-plan.md` and keep the change within it.
2. Read the relevant schema, adapter, and scientific reference documents.
3. Preserve the original BSD-3-Clause license and upstream dependency, tool, and dataset notices.
4. Never add private, restricted, or identifiable research data to the repository.
5. For scientific behavior, add a direct-library reference and method-specific tolerances. Screenshots do not establish numerical correctness.

## Pull requests

All implementation work goes through a scoped branch and pull request. The repository gate includes formatting, typing, Python and frontend tests, and a production build. Scientific changes also require the relevant independent review and reference test. Do not check a monitor-owned completion box on the basis of an author self-review.

## Add or change a node

Define scientific port types and parameter constraints first. Keep algorithms in a domain adapter, report required capabilities and citations, handle large artifacts as file references, and persist QC decisions explicitly. Follow the [node dependency metadata contract](https://github.com/Brain-Learn/brainlearn/blob/main/docs/node-dependency-metadata.md) and the relevant existing MNE adapter. A dedicated worked tutorial will be added as contributor coverage matures.

## Report an issue

Include steps to reproduce, software and operating-system versions, and a synthetic or public fixture when possible. Remove local paths, session tokens, participant identifiers, and private research data from logs and screenshots.
