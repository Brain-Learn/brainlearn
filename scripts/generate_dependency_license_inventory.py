"""Build a deterministic, lockfile-linked third-party license inventory."""

from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import json
import os
import platform
import subprocess
import sys
import tempfile
import tomllib
from collections import defaultdict
from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import Any

from packaging.licenses import InvalidLicenseExpression, canonicalize_license_expression
from packaging.utils import canonicalize_name

_ROOT = Path(__file__).resolve().parents[1]
_LOCK_FILES = (
    "uv.lock",
    "apps/web/package-lock.json",
    "website/package-lock.json",
)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _normalise_license_field(value: object) -> str | None:
    if isinstance(value, str):
        return value.strip() or None
    if isinstance(value, Mapping):
        license_type = value.get("type")
        return str(license_type).strip() if license_type else json.dumps(value, sort_keys=True)
    if isinstance(value, list):
        parts = [part for item in value if (part := _normalise_license_field(item))]
        return " OR ".join(parts) if parts else None
    return None


def _license_record(
    *,
    expression: str | None,
    legacy_license: str | None = None,
    classifiers: Iterable[str] = (),
    observed: bool = True,
) -> dict[str, Any]:
    classifier_values = sorted(
        classifier for classifier in classifiers if classifier.startswith("License ::")
    )
    if not observed:
        status = "not_observed_for_target_review_required"
        normalized = None
        review_required = True
    elif expression and expression.casefold() == "unlicensed":
        status = "declared_unlicensed_review_required"
        normalized = None
        review_required = True
    elif expression:
        try:
            normalized = canonicalize_license_expression(expression)
        except InvalidLicenseExpression:
            status = "invalid_spdx_expression_review_required"
            normalized = None
            review_required = True
        else:
            status = "spdx_expression"
            review_required = False
    elif legacy_license or classifier_values:
        status = "legacy_metadata_review_required"
        normalized = None
        review_required = True
    else:
        status = "missing_license_metadata_review_required"
        normalized = None
        review_required = True

    return {
        "status": status,
        "declared_expression": expression,
        "normalized_expression": normalized,
        "legacy_license_text": legacy_license,
        "license_classifiers": classifier_values,
        "metadata_review_required": review_required,
    }


def _installed_distributions() -> dict[str, list[importlib.metadata.Distribution]]:
    distributions: dict[str, list[importlib.metadata.Distribution]] = defaultdict(list)
    for distribution in importlib.metadata.distributions():
        name = distribution.metadata.get("Name")
        if name:
            distributions[canonicalize_name(name)].append(distribution)
    return distributions


def _workspace_license_metadata(package: Mapping[str, Any], repo_root: Path) -> dict[str, Any]:
    source = package.get("source", {})
    if "virtual" in source:
        manifest = repo_root / "pyproject.toml"
    elif isinstance(source.get("editable"), str):
        manifest = repo_root / source["editable"] / "pyproject.toml"
    else:
        return {}
    if not manifest.is_file():
        return {}
    project = tomllib.loads(manifest.read_text(encoding="utf-8")).get("project", {})
    license_value = project.get("license")
    if isinstance(license_value, str):
        return {"expression": license_value, "source": manifest.relative_to(repo_root).as_posix()}
    if isinstance(license_value, Mapping):
        return {
            "legacy_license": license_value.get("text"),
            "source": manifest.relative_to(repo_root).as_posix(),
        }
    return {"source": manifest.relative_to(repo_root).as_posix()}


def _python_group_closures(lock: Mapping[str, Any]) -> dict[str, set[str]]:
    packages = {str(canonicalize_name(item["name"])): item for item in lock["package"]}
    root = next(
        (item for item in lock["package"] if item.get("source", {}).get("virtual") == "."),
        None,
    )
    if root is None:
        raise ValueError("uv.lock is missing the workspace root package")

    roots: dict[str, list[str]] = {
        "runtime": [str(canonicalize_name(item["name"])) for item in root.get("dependencies", [])]
    }
    roots.update(
        {
            group: [str(canonicalize_name(item["name"])) for item in members]
            for group, members in root.get("dev-dependencies", {}).items()
        }
    )

    closures: dict[str, set[str]] = {}
    for group, group_roots in roots.items():
        reached: set[str] = set()
        pending = list(group_roots)
        while pending:
            name = pending.pop()
            if name in reached or name not in packages:
                continue
            reached.add(name)
            pending.extend(
                str(canonicalize_name(dependency["name"]))
                for dependency in packages[name].get("dependencies", [])
            )
        closures[group] = reached
    return closures


def _python_inventory(lock_path: Path) -> list[dict[str, Any]]:
    repo_root = lock_path.parent
    lock = tomllib.loads(lock_path.read_text(encoding="utf-8"))
    groups = _python_group_closures(lock)
    installed = _installed_distributions()
    records: list[dict[str, Any]] = []

    for package in lock["package"]:
        name = package["name"]
        normalized_name = str(canonicalize_name(name))
        source = package.get("source", {})
        first_party = "virtual" in source or "editable" in source
        distributions = installed.get(canonicalize_name(name), [])
        matching = [
            distribution
            for distribution in distributions
            if distribution.version == package["version"]
        ]
        observed = bool(matching)
        metadata: importlib.metadata.PackageMetadata | dict[str, Any] = (
            matching[0].metadata if observed else {}
        )
        workspace_metadata = _workspace_license_metadata(package, repo_root) if first_party else {}
        if workspace_metadata:
            observed = True
        license_info = _license_record(
            expression=workspace_metadata.get("expression", metadata.get("License-Expression")),
            legacy_license=workspace_metadata.get("legacy_license", metadata.get("License")),
            classifiers=matching[0].metadata.get_all("Classifier", []) if matching else [],
            observed=observed,
        )
        if distributions and not matching:
            license_info = {
                **_license_record(expression=None, observed=False),
                "status": "installed_version_mismatch_review_required",
                "installed_versions": sorted({item.version for item in distributions}),
            }

        records.append(
            {
                "ecosystem": "python",
                "name": name,
                "version": package["version"],
                "source": source,
                "first_party": first_party,
                "dependency_groups": sorted(
                    group for group, members in groups.items() if normalized_name in members
                ),
                "installation_status": (
                    "installed_locked_version"
                    if matching
                    else "version_mismatch"
                    if distributions
                    else "not_installed_on_target"
                ),
                "license_metadata_source": (
                    workspace_metadata.get("source", "workspace package metadata")
                    if workspace_metadata
                    else "installed distribution metadata"
                    if matching
                    else "not available on this target"
                ),
                "license": license_info,
            }
        )

    return sorted(records, key=lambda item: (item["name"].casefold(), item["version"]))


def _npm_package_name(path: str, entry: Mapping[str, Any]) -> str:
    declared_name = entry.get("name")
    if isinstance(declared_name, str):
        return declared_name
    suffix = path.rsplit("node_modules/", 1)[-1]
    parts = suffix.split("/")
    return "/".join(parts[:2]) if parts[0].startswith("@") else parts[0]


def _npm_inventory(lock_path: Path, repo_root: Path) -> list[dict[str, Any]]:
    lock = json.loads(lock_path.read_text(encoding="utf-8"))
    records: list[dict[str, Any]] = []
    for path, package in lock.get("packages", {}).items():
        if not path or not path.startswith("node_modules/"):
            continue
        raw_license = _normalise_license_field(package.get("license"))
        records.append(
            {
                "ecosystem": "npm",
                "manifest": lock_path.relative_to(repo_root).as_posix(),
                "install_path": path,
                "name": _npm_package_name(path, package),
                "version": package.get("version"),
                "integrity": package.get("integrity"),
                "license_metadata_source": "package-lock.json",
                "development_only": bool(package.get("dev", False))
                and not bool(package.get("devOptional", False)),
                "optional": bool(package.get("optional", False)),
                "license": _license_record(expression=raw_license),
            }
        )
    return sorted(
        records,
        key=lambda item: (
            item["manifest"],
            item["name"].casefold(),
            item["install_path"],
        ),
    )


def build_inventory(repo_root: Path = _ROOT) -> dict[str, Any]:
    """Return the lock-linked package/license inventory for the current target."""

    python = _python_inventory(repo_root / "uv.lock")
    npm = [
        record
        for path in ("apps/web/package-lock.json", "website/package-lock.json")
        for record in _npm_inventory(repo_root / path, repo_root)
    ]
    packages = python + npm
    review_required = [
        {
            "ecosystem": package["ecosystem"],
            "name": package["name"],
            "version": package.get("version"),
            "manifest": package.get("manifest"),
            "status": package["license"]["status"],
        }
        for package in packages
        if package["license"]["metadata_review_required"]
    ]
    lock_hashes = {relative: _sha256(repo_root / relative) for relative in _LOCK_FILES}
    node_version: str | None = None
    try:
        completed = subprocess.run(
            ["node", "--version"],
            check=True,
            capture_output=True,
            text=True,
            timeout=5,
        )
    except (OSError, subprocess.CalledProcessError, subprocess.TimeoutExpired):
        pass
    else:
        node_version = completed.stdout.strip()
    return {
        "schema_version": "1.0",
        "generator": "BrainLearn dependency license inventory",
        "generator_version": "1",
        "lockfile_sha256": lock_hashes,
        "target": {
            "platform": platform.platform(),
            "system": platform.system(),
            "machine": platform.machine(),
            "python": f"{sys.version_info.major}.{sys.version_info.minor}",
            "node": node_version,
        },
        "summary": {
            "python_locked_packages": len(python),
            "npm_locked_packages": len(npm),
            "license_review_required": len(review_required),
        },
        "license_review_required": review_required,
        "packages": packages,
        "notice": (
            "Package-declared metadata is inventory evidence only, not legal advice or "
            "approval. Review license terms and obligations before redistribution. "
            "Packages not installed for this target are reported without guessed metadata."
        ),
    }


def write_inventory(inventory: Mapping[str, Any], output: Path) -> None:
    """Atomically write formatted JSON without timestamps for stable regeneration."""

    output.parent.mkdir(parents=True, exist_ok=True)
    data = json.dumps(inventory, indent=2, sort_keys=True, ensure_ascii=False) + "\n"
    temporary_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", dir=output.parent, delete=False
        ) as stream:
            temporary_path = Path(stream.name)
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary_path, output)
    finally:
        if temporary_path is not None and temporary_path.exists():
            temporary_path.unlink()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("dependency-license-inventory.json"),
        help="output JSON path (default: dependency-license-inventory.json)",
    )
    args = parser.parse_args()
    output = args.output if args.output.is_absolute() else Path.cwd() / args.output
    inventory = build_inventory()
    write_inventory(inventory, output)
    print(
        "Generated dependency license inventory: "
        f"Python {inventory['summary']['python_locked_packages']}, "
        f"npm {inventory['summary']['npm_locked_packages']}, "
        f"{inventory['summary']['license_review_required']} license metadata item(s) "
        f"need human review. Output: {output}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
