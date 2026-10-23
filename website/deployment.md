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

The browser check opens representative pages at desktop and mobile widths and runs axe-core WCAG checks. The GitHub Actions workflow also runs Sphinx's external link checker before deployment.

## Configure GitHub Pages

Set the repository Pages source to **GitHub Actions**. The workflow deploys the reviewed `main` build through the `github-pages` environment. Version tags run the documentation checks but do not replace the published site; a version archive and switcher are still pending. Restrict the environment's allowed deployments to `main` until versioned publication is implemented. Keep the Actions permissions at repository read access for the build and grant only `pages: write` and `id-token: write` to the deployment job.

The build job is unprivileged and produces a static Pages artifact. The deployment job receives the artifact and publishes it. No secrets are required to render the docs or gallery.

## Roll back

1. Identify the last good workflow run in GitHub Actions.
2. Re-run deployment from that source revision, or revert the documentation change through a pull request and let the normal deployment run.
3. Confirm the home page, quickstart, example gallery, search, and version label on the published URL.
4. Record the rollback and affected source revision in the project history.

Do not edit generated files in the Pages artifact. Fix their source in `website/` and rebuild from a reviewable commit.
