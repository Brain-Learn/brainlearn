"""Build and label immutable documentation versions for BrainLearn."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

_TAG_PATTERN = re.compile(
    r"^v(?P<major>\d+)\.(?P<minor>\d+)\.(?P<patch>\d+)(?:(?P<kind>a|b|rc)(?P<number>\d+))?$"
)


@dataclass(frozen=True, slots=True)
class ReleaseTag:
    name: str
    key: tuple[int, int, int, int, int]
    release_stage: str


@dataclass(frozen=True, slots=True)
class DocumentationVersion:
    version: str
    status: str
    release_stage: str
    url: str
    preferred: bool = False


def parse_release_tag(value: str) -> ReleaseTag:
    """Parse the immutable BrainLearn release tag forms documented for release."""
    match = _TAG_PATTERN.fullmatch(value)
    if match is None:
        raise ValueError(
            f"Unsupported documentation tag {value!r}; expected vMAJOR.MINOR.PATCH "
            "with an optional aN, bN, or rcN suffix."
        )
    major, minor, patch = (int(match[name]) for name in ("major", "minor", "patch"))
    kind = match["kind"]
    stage_order = {"a": 0, "b": 1, "rc": 2, None: 3}[kind]
    number = int(match["number"]) if match["number"] else 0
    return ReleaseTag(
        name=value,
        key=(major, minor, patch, stage_order, number),
        release_stage="Stable" if kind is None else "Pre-release",
    )


def documentation_versions(tags: list[str], site_url: str) -> list[DocumentationVersion]:
    """Return development plus tagged docs, marking only active lines unarchived."""
    root = site_url.rstrip("/") + "/"
    parsed = sorted(
        (parse_release_tag(tag) for tag in set(tags)), key=lambda item: item.key, reverse=True
    )
    stable_tags = [tag for tag in parsed if tag.release_stage == "Stable"]
    prerelease_tags = [tag for tag in parsed if tag.release_stage == "Pre-release"]
    latest_stable = stable_tags[0] if stable_tags else None
    latest_prerelease = prerelease_tags[0] if prerelease_tags else None
    current_prerelease = (
        latest_prerelease
        if latest_prerelease is not None
        and (latest_stable is None or latest_prerelease.key > latest_stable.key)
        else None
    )

    versions = [DocumentationVersion("development", "Development", "Development", root)]
    for tag in parsed:
        if latest_stable is not None and tag.name == latest_stable.name:
            status = "Stable"
            preferred = True
        elif current_prerelease is not None and tag.name == current_prerelease.name:
            status = "Pre-release"
            preferred = False
        else:
            status = "Archived"
            preferred = False
        versions.append(
            DocumentationVersion(
                version=tag.name,
                status=status,
                release_stage=tag.release_stage,
                url=f"{root}versions/{tag.name}/",
                preferred=preferred,
            )
        )
    return versions


def version_switcher_payload(versions: list[DocumentationVersion]) -> list[dict[str, Any]]:
    """Shape labels and links for the PyData Sphinx Theme version switcher."""
    payload: list[dict[str, Any]] = []
    for item in versions:
        if item.status == "Archived":
            name = f"{item.version} · Archived (was {item.release_stage.lower()})"
        elif item.status == "Development":
            name = "Development · development"
        else:
            name = f"{item.version} · {item.status}"
        entry: dict[str, Any] = {
            "name": name,
            "version": item.version,
            "url": item.url,
        }
        if item.preferred:
            entry["preferred"] = True
        payload.append(entry)
    return payload


def write_version_switcher(path: Path, versions: list[DocumentationVersion]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(version_switcher_payload(versions), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
