from __future__ import annotations

import json
import runpy
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parents[1] / "scripts"))

from build_versioned_docs import _git_tags  # noqa: E402
from docs_versioning import (  # noqa: E402
    documentation_versions,
    parse_release_tag,
    write_version_switcher,
)


def test_release_tag_parser_orders_alpha_beta_rc_and_stable() -> None:
    tags = [
        parse_release_tag("v1.2.0"),
        parse_release_tag("v1.2.0rc2"),
        parse_release_tag("v1.2.0b1"),
        parse_release_tag("v1.2.0a3"),
    ]

    assert [tag.name for tag in sorted(tags, key=lambda item: item.key)] == [
        "v1.2.0a3",
        "v1.2.0b1",
        "v1.2.0rc2",
        "v1.2.0",
    ]
    assert [tag.release_stage for tag in tags] == [
        "Stable",
        "Pre-release",
        "Pre-release",
        "Pre-release",
    ]


@pytest.mark.parametrize("tag", ["v1.2", "1.2.0", "v1.2.0-dev1", "v1.2.0+local", "v1.2.0a"])
def test_unrecognized_release_tags_fail_closed(tag: str) -> None:
    with pytest.raises(ValueError, match="expected vMAJOR.MINOR.PATCH"):
        parse_release_tag(tag)


def test_version_manifest_labels_development_active_tags_and_archives() -> None:
    versions = documentation_versions(
        [
            "v0.1.0a1",
            "v0.1.0b1",
            "v0.1.0rc1",
            "v0.1.0",
            "v0.2.0a1",
            "v0.2.0rc1",
            "v0.2.0rc2",
        ],
        "https://docs.example.test/brainlearn/",
    )
    by_version = {version.version: version for version in versions}

    assert by_version["development"].status == "Development"
    assert by_version["v0.1.0"].status == "Stable"
    assert by_version["v0.1.0"].preferred is True
    assert by_version["v0.2.0rc2"].status == "Pre-release"
    assert by_version["v0.2.0rc1"].status == "Archived"
    assert by_version["v0.2.0rc1"].release_stage == "Pre-release"
    assert by_version["v0.1.0rc1"].status == "Archived"
    assert by_version["v0.2.0rc2"].url == (
        "https://docs.example.test/brainlearn/versions/v0.2.0rc2/"
    )
    assert sum(version.preferred for version in versions) == 1


def test_prerelease_without_stable_has_no_false_preferred_stable() -> None:
    versions = documentation_versions(["v0.1.0a1", "v0.1.0rc1"], "https://docs.example.test/")
    by_version = {version.version: version for version in versions}

    assert by_version["v0.1.0rc1"].status == "Pre-release"
    assert by_version["v0.1.0a1"].status == "Archived"
    assert not any(version.preferred for version in versions)


def test_version_switcher_json_uses_visible_status_labels(tmp_path: Path) -> None:
    versions = documentation_versions(["v1.0.0rc1", "v1.0.0"], "https://docs.example.test/")
    destination = tmp_path / "_static" / "docs-versions.json"

    write_version_switcher(destination, versions)
    payload = json.loads(destination.read_text(encoding="utf-8"))

    assert payload[0]["name"] == "Development · development"
    assert next(entry for entry in payload if entry["version"] == "v1.0.0")["preferred"] is True
    assert next(entry for entry in payload if entry["version"] == "v1.0.0rc1")["name"].endswith(
        "Archived (was pre-release)"
    )


@pytest.mark.parametrize(
    ("release", "status", "release_stage", "label"),
    [
        ("development", "Development", "Development", "Development docs · development"),
        ("v0.2.0rc1", "Pre-release", "Pre-release", "Pre-release docs · v0.2.0rc1"),
        ("v0.1.0", "Stable", "Stable", "Stable docs · v0.1.0"),
        ("v0.1.0", "Archived", "Stable", "Archived docs · v0.1.0 (was stable)"),
    ],
)
def test_sphinx_configuration_marks_each_documentation_state(
    monkeypatch: pytest.MonkeyPatch,
    release: str,
    status: str,
    release_stage: str,
    label: str,
) -> None:
    monkeypatch.setenv("BRAINLEARN_DOCS_VERSION", release)
    monkeypatch.setenv("BRAINLEARN_DOCS_STATUS", status)
    monkeypatch.setenv("BRAINLEARN_DOCS_RELEASE_STAGE", release_stage)
    monkeypatch.setenv(
        "BRAINLEARN_DOCS_BASEURL", "https://docs.example.test/brainlearn/versions/v0.1.0/"
    )
    monkeypatch.setenv(
        "BRAINLEARN_DOCS_SWITCHER_JSON_URL",
        "https://docs.example.test/brainlearn/_static/docs-versions.json",
    )
    config = runpy.run_path(str(Path(__file__).parents[1] / "website" / "conf.py"))

    assert config["version_label"] == label
    assert config["html_theme_options"]["switcher"]["version_match"] == release
    assert config["html_theme_options"]["switcher"]["json_url"].endswith(
        "/_static/docs-versions.json"
    )
    status_link_label = "project status" if status == "Development" else "version-specific status"
    assert status_link_label in config["html_theme_options"]["announcement"]
    if status == "Archived":
        assert "may differ from current BrainLearn" in config["html_theme_options"]["announcement"]


def test_archived_documentation_requires_original_release_stage(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("BRAINLEARN_DOCS_VERSION", "v0.1.0")
    monkeypatch.setenv("BRAINLEARN_DOCS_STATUS", "Archived")
    monkeypatch.delenv("BRAINLEARN_DOCS_RELEASE_STAGE", raising=False)

    with pytest.raises(ValueError, match="original Stable or Pre-release stage"):
        runpy.run_path(str(Path(__file__).parents[1] / "website" / "conf.py"))


def test_versioned_builder_rejects_tags_outside_main_history(tmp_path: Path) -> None:
    repository = tmp_path / "repository"
    repository.mkdir()

    def git(*arguments: str) -> None:
        subprocess.run(
            ["git", "-C", str(repository), *arguments],
            check=True,
            capture_output=True,
            text=True,
        )

    git("init", "--initial-branch=main")
    git("config", "user.name", "Docs test")
    git("config", "user.email", "docs-test@example.invalid")
    (repository / "tracked.txt").write_text("main", encoding="utf-8")
    git("add", "tracked.txt")
    git("commit", "-m", "main revision")
    git("tag", "v1.0.0")
    git("switch", "-c", "unmerged-release")
    (repository / "tracked.txt").write_text("unreviewed", encoding="utf-8")
    git("commit", "-am", "unmerged revision")
    git("tag", "v1.1.0")
    git("switch", "main")

    with pytest.raises(RuntimeError, match="not in the checked-out main history"):
        _git_tags(repository)
