# Dependency licenses and security checks

BrainLearn preserves the licenses and attribution of external tools and
libraries. The CI inventory records locked Python packages from `uv.lock` and
JavaScript packages from `apps/web/package-lock.json` and
`website/package-lock.json`. It includes exact versions, lockfile SHA-256
digests, package-manager groups or install paths, and the package-declared
license metadata available on the runner.

For Python, CI reads license metadata only when the installed distribution
version matches the locked version. A package not installed on the Linux
inventory runner is listed with `not_observed_for_target_review_required`;
the inventory does not guess metadata for another operating system. NPM
metadata is read from the committed package lockfiles. Valid SPDX expressions
are normalized. Missing, invalid, legacy, and unobserved license metadata is
listed for human review. These declarations are inventory evidence, not legal
advice or approval. Review upstream terms and obligations before redistribution
or bundling any dependency.

The `dependency-license-inventory-linux-x86_64` CI artifact is retained for 30
days and is tied to the run's commit and the lockfile digests in its JSON. To
regenerate it locally after installing the locked workspace:

```bash
uv sync --locked --all-packages --all-groups
uv run --locked python scripts/generate_dependency_license_inventory.py \
  --output dependency-license-inventory.json
```

Production vulnerability checks run on Linux x86-64, macOS Apple Silicon, and
Windows x86-64. Python checks use the pinned `pip-audit` version against
runtime dependencies from every workspace package and the optional EEG group;
development and documentation groups are not included. NPM checks use
`npm audit --omit=dev` for the application and documentation site. A known
production vulnerability fails CI. The checks do not automatically upgrade
packages: dependency changes require a reviewed lockfile update and the normal
test gate. Advisory databases can change over time, so a future run may report
new findings for unchanged lockfiles.

To run the Python production audit locally:

```bash
uv sync --locked --all-packages --no-default-groups --group eeg --group audit
uv export --locked --all-packages --no-default-groups --group eeg \
  --no-emit-workspace --no-hashes --format requirements.txt \
  --output-file /tmp/brainlearn-production-requirements.txt
uv run --locked --no-sync pip-audit \
  --requirement /tmp/brainlearn-production-requirements.txt
```

The package inventory and vulnerability scan do not replace a licensing review,
security assessment, scientific methods review, or platform certification.
