"""Write a switcher manifest for the Sphinx build currently being checked."""

from __future__ import annotations

import os
from pathlib import Path

from docs_versioning import documentation_versions, write_version_switcher

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]


def main() -> int:
    version = os.environ.get("BRAINLEARN_DOCS_VERSION", "development")
    status = os.environ.get("BRAINLEARN_DOCS_STATUS", "Development")
    site_url = os.environ.get(
        "BRAINLEARN_DOCS_SITE_ROOT_URL", "https://brain-learn.github.io/brainlearn/"
    )
    tags = [] if version == "development" else [version]
    versions = documentation_versions(tags, site_url)
    selected = versions[0] if not tags else versions[1]
    if selected.version != version or selected.status != status:
        raise ValueError(
            f"Documentation manifest classified {version!r} as {selected.status}, "
            f"but this build requested {status}."
        )
    manifest = REPOSITORY_ROOT / "website" / "_static" / "docs-versions.json"
    write_version_switcher(manifest, versions)
    print(f"Prepared the {status} version-switcher entry for {version}.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
