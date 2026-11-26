# Build, deploy, and roll back the documentation site

The site is generated HTML, CSS, JavaScript, and static images. GitHub Pages receives only the output of the Sphinx build. The published site has no BrainLearn service, file browser, or research-data upload.

## Build locally

From the repository root:

```sh
uv sync --locked --all-packages --group docs --group eeg
BRAINLEARN_DOCS_VERSION=development \
  uv run --locked --group docs --group eeg \
  sphinx-build -E -a -n -W --keep-going -b html website /tmp/brainlearn-docs
npm --prefix website ci
npx --prefix website playwright install chromium
npm --prefix website run check -- /tmp/brainlearn-docs
```

The browser check opens representative pages at desktop and mobile widths, verifies the version label and selector, and runs axe-core WCAG checks. The GitHub Actions workflow also runs Sphinx's external link checker before deployment.

To assemble the same multi-version tree used for Pages, run this in a clean checkout that has all release tags fetched. Use a new, empty output path:

```sh
uv run --locked --group docs --group eeg python \
  scripts/build_versioned_docs.py --output /tmp/brainlearn-pages
```

The builder keeps the checked-out `main` docs at the site root and renders each supported `v*` tag from its own source archive under `versions/<tag>/` with that tag's locked docs environment. It refuses tags whose commits are not in reviewed `main` history. It writes `_static/docs-versions.json` for the PyData version selector. Latest stable and latest prerelease tags keep their respective labels; older tags are marked Archived and retain their original release stage. Invalid `v*` tag names fail the deployment instead of disappearing from the selector.

## Configure GitHub Pages

Set the repository Pages source to **GitHub Actions** and restrict the `github-pages` environment to `main`. A `main` push deploys a site assembled from current `main` plus all release-tag snapshots. A tag push runs checks but does not deploy; after a protected release tag is created, a maintainer dispatches the workflow from `main` to publish the new archive. Keep the Actions permissions at repository read access for the build and grant only `pages: write` and `id-token: write` to the deployment job.

The build job is unprivileged and produces a static Pages artifact only for `main` pushes and manual dispatches from `main`. The deployment job receives that exact checked artifact and publishes it. No secrets are required to render the docs or gallery.

## Roll back

1. Identify the affected `main` commit or release tag and the last known-good content.
2. For development documentation, revert or repair the source through a PR and let the normal `main` deployment rebuild the site. Avoid using a previous run's Pages artifact as a rollback; it may have expired and would restore an older complete site rather than repair the reviewed source.
3. For a published release snapshot, preserve the immutable tag. Mark the release entry with a warning and publish any repair under a new reviewed version; do not replace the same version path with changed documentation.
4. Confirm the home page, quickstart, example gallery, search, current version label, and unaffected archive paths on the published URL.
5. Record the rollback and affected source revision in the project history.

Do not edit generated files in the Pages artifact. Fix their source in `website/` and rebuild from a reviewable commit.
