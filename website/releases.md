# Releases and versioned documentation

BrainLearn is under active development and does not yet have a stable public release. The documentation site labels the current build **Development**. A green CI run or a working demo is not a software release or scientific certification.

When release candidates and stable tags exist, their documentation must be built from the matching source revision. Archived scientific runs must keep their recorded workflow, dependency, and documentation versions; they must not silently inherit current claims.

The development site does not yet offer a historical version switcher because there are no tagged releases to serve. CI labels `main` builds as Development and tag builds as Pre-release or Stable, but only `main` is published. A version archive and selector are required before old runs or releases are treated as maintained documentation.

## Release checks

A stable release requires a supported-platform installation path, clean-install checks, replay of the certified reference workflow, release notes, checksums, and a documented rollback. Publication eligibility and independent use evidence are separate gates. See the [implementation plan](https://github.com/Brain-Learn/brainlearn/blob/main/docs/implementation-plan.md).
