"""Assemble the development site and immutable tagged documentation snapshots."""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import tarfile
import tempfile
from io import BytesIO
from pathlib import Path

from docs_versioning import DocumentationVersion, documentation_versions, write_version_switcher

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
SITE_URL = "https://brain-learn.github.io/brainlearn/"
SWITCHER_URL = f"{SITE_URL}_static/docs-versions.json"


def _git_tags(repository: Path) -> list[str]:
    result = subprocess.run(
        ["git", "-C", str(repository), "tag", "--list", "v*", "--sort=-version:refname"],
        check=True,
        capture_output=True,
        text=True,
    )
    tags = [tag for tag in result.stdout.splitlines() if tag]
    for tag in tags:
        commit = subprocess.run(
            ["git", "-C", str(repository), "rev-parse", "--verify", f"{tag}^{{commit}}"],
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
        ancestry = subprocess.run(
            ["git", "-C", str(repository), "merge-base", "--is-ancestor", commit, "HEAD"],
            capture_output=True,
        )
        if ancestry.returncode != 0:
            raise RuntimeError(
                f"Refusing to build documentation tag {tag}: its commit is not in the "
                "checked-out main history. Merge the reviewed source before publishing its docs."
            )
    return tags


def _run_sphinx(
    project_root: Path,
    output: Path,
    *,
    version: DocumentationVersion,
    base_url: str,
    use_locked_environment: bool,
) -> None:
    env = os.environ.copy()
    env.update(
        {
            "BRAINLEARN_DOCS_VERSION": version.version,
            "BRAINLEARN_DOCS_STATUS": version.status,
            "BRAINLEARN_DOCS_RELEASE_STAGE": version.release_stage,
            "BRAINLEARN_DOCS_BASEURL": base_url,
            "BRAINLEARN_DOCS_SWITCHER_JSON_URL": SWITCHER_URL,
        }
    )
    command = [
        "-E",
        "-a",
        "-n",
        "-W",
        "--keep-going",
        "-b",
        "html",
        str(project_root / "website"),
        str(output),
    ]
    if use_locked_environment:
        uv = shutil.which("uv")
        if uv is None:
            raise RuntimeError("uv is required to build each tagged documentation environment.")
        env.pop("VIRTUAL_ENV", None)
        executable = [
            uv,
            "run",
            "--locked",
            "--project",
            str(project_root),
            "--all-packages",
            "--group",
            "docs",
        ]
        if "eeg" in _dependency_groups(project_root):
            executable.extend(["--group", "eeg"])
        command = [*executable, "sphinx-build", *command]
    else:
        command = ["sphinx-build", *command]
    subprocess.run(command, cwd=project_root, env=env, check=True)


def _dependency_groups(project_root: Path) -> dict[str, object]:
    import tomllib

    with (project_root / "pyproject.toml").open("rb") as file:
        project = tomllib.load(file)
    groups = project.get("dependency-groups", {})
    return groups if isinstance(groups, dict) else {}


def _extract_tag(repository: Path, tag: str, destination: Path) -> None:
    archived = subprocess.run(
        ["git", "-C", str(repository), "archive", "--format=tar", tag],
        check=True,
        capture_output=True,
    )
    destination.mkdir(parents=True)
    with tarfile.open(fileobj=BytesIO(archived.stdout), mode="r:") as archive:
        archive.extractall(destination, filter="data")


def _build_site(repository: Path, output: Path) -> None:
    versions = documentation_versions(_git_tags(repository), SITE_URL)
    if output.exists():
        raise FileExistsError(f"Refusing to overwrite existing documentation output: {output}")
    output.mkdir(parents=True)
    development = versions[0]
    _run_sphinx(
        repository,
        output,
        version=development,
        base_url=SITE_URL,
        use_locked_environment=False,
    )

    tags = versions[1:]
    if tags:
        with tempfile.TemporaryDirectory(prefix="brainlearn-docs-tags-") as temporary:
            temporary_root = Path(temporary)
            for item in tags:
                source = temporary_root / item.version
                destination = output / "versions" / item.version
                _extract_tag(repository, item.version, source)
                if not (source / "website" / "conf.py").is_file():
                    raise RuntimeError(
                        f"Release tag {item.version} has no versioned Sphinx site; "
                        "do not publish this tag until documentation is added."
                    )
                destination.parent.mkdir(parents=True, exist_ok=True)
                _run_sphinx(
                    source,
                    destination,
                    version=item,
                    base_url=f"{SITE_URL}versions/{item.version}/",
                    use_locked_environment=True,
                )

    write_version_switcher(output / "_static" / "docs-versions.json", versions)
    print(f"Built development docs and {len(tags)} tagged documentation snapshot(s) at {output}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repository", type=Path, default=REPOSITORY_ROOT)
    parser.add_argument("--output", type=Path, required=True)
    arguments = parser.parse_args()
    _build_site(arguments.repository.resolve(), arguments.output.resolve())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
