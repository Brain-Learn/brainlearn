from __future__ import annotations

import importlib.util
import json
from email.message import Message
from pathlib import Path
from types import SimpleNamespace

import pytest

REPOSITORY_ROOT = Path(__file__).parents[1]
_SPEC = importlib.util.spec_from_file_location(
    "dependency_license_inventory",
    REPOSITORY_ROOT / "scripts" / "generate_dependency_license_inventory.py",
)
assert _SPEC is not None and _SPEC.loader is not None
_INVENTORY = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(_INVENTORY)

_license_record = _INVENTORY._license_record
_npm_inventory = _INVENTORY._npm_inventory
_npm_package_name = _INVENTORY._npm_package_name
_python_inventory = _INVENTORY._python_inventory
_python_group_closures = _INVENTORY._python_group_closures
build_inventory = _INVENTORY.build_inventory
write_inventory = _INVENTORY.write_inventory


def test_spdx_expression_is_normalized_and_not_marked_for_manual_review() -> None:
    record = _license_record(expression="mit OR BSD-3-Clause")

    assert record["status"] == "spdx_expression"
    assert record["normalized_expression"] == "MIT OR BSD-3-Clause"
    assert record["metadata_review_required"] is False


def test_legacy_and_unknown_license_metadata_are_explicitly_flagged() -> None:
    legacy = _license_record(
        expression=None,
        legacy_license="BSD License",
        classifiers=["License :: OSI Approved :: BSD License"],
    )
    unknown = _license_record(expression=None)
    invalid = _license_record(expression="License :: Something Custom")
    unlicensed = _license_record(expression="UNLICENSED")

    assert legacy["status"] == "legacy_metadata_review_required"
    assert legacy["metadata_review_required"] is True
    assert unknown["status"] == "missing_license_metadata_review_required"
    assert invalid["status"] == "invalid_spdx_expression_review_required"
    assert unlicensed["status"] == "declared_unlicensed_review_required"


def test_uninstalled_locked_python_package_does_not_guess_license() -> None:
    record = _license_record(expression=None, observed=False)

    assert record["status"] == "not_observed_for_target_review_required"
    assert record["normalized_expression"] is None
    assert record["metadata_review_required"] is True


def test_python_group_closure_includes_transitive_and_direct_dependencies() -> None:
    lock = {
        "package": [
            {
                "name": "brainlearn-workspace",
                "source": {"virtual": "."},
                "dependencies": [{"name": "brainlearn-core"}, {"name": "brainlearn-server"}],
                "dev-dependencies": {
                    "audit": [{"name": "pip-audit"}],
                    "eeg": [{"name": "mne"}],
                },
            },
            {
                "name": "brainlearn-core",
                "dependencies": [{"name": "pydantic"}],
            },
            {
                "name": "brainlearn-server",
                "dependencies": [{"name": "uvicorn", "extra": ["standard"]}],
            },
            {"name": "pydantic", "dependencies": [{"name": "typing-extensions"}]},
            {"name": "typing-extensions"},
            {
                "name": "uvicorn",
                "dependencies": [{"name": "h11"}],
                "optional-dependencies": {
                    "standard": [
                        {"name": "httptools"},
                        {"name": "python-dotenv"},
                        {"name": "pyyaml"},
                        {"name": "uvloop"},
                        {"name": "watchfiles"},
                        {"name": "websockets"},
                    ]
                },
            },
            {"name": "h11"},
            {"name": "httptools"},
            {"name": "python-dotenv"},
            {"name": "pyyaml"},
            {"name": "uvloop"},
            {"name": "watchfiles", "dependencies": [{"name": "anyio"}]},
            {"name": "anyio"},
            {"name": "websockets"},
            {
                "name": "pip-audit",
                "dependencies": [{"name": "cachecontrol", "extra": ["filecache"]}],
            },
            {
                "name": "cachecontrol",
                "dependencies": [{"name": "msgpack"}],
                "optional-dependencies": {"filecache": [{"name": "filelock"}]},
            },
            {"name": "msgpack"},
            {"name": "filelock"},
            {"name": "mne", "dependencies": [{"name": "numpy"}]},
            {"name": "numpy"},
        ]
    }

    groups = _python_group_closures(lock)

    assert groups["runtime"] == {
        "brainlearn-core",
        "brainlearn-server",
        "pydantic",
        "typing-extensions",
        "uvicorn",
        "h11",
        "httptools",
        "python-dotenv",
        "pyyaml",
        "uvloop",
        "watchfiles",
        "anyio",
        "websockets",
    }
    assert groups["audit"] == {"pip-audit", "cachecontrol", "msgpack", "filelock"}
    assert groups["eeg"] == {"mne", "numpy"}


@pytest.mark.parametrize(
    ("installed_version", "installation_status", "license_status"),
    [
        ("1.0", "installed_locked_version", "spdx_expression"),
        ("2.0", "version_mismatch", "installed_version_mismatch_review_required"),
    ],
)
def test_python_license_metadata_must_match_the_locked_version(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    installed_version: str,
    installation_status: str,
    license_status: str,
) -> None:
    (tmp_path / "pyproject.toml").write_text(
        '[project]\nname = "brainlearn-workspace"\nlicense = "BSD-3-Clause"\n',
        encoding="utf-8",
    )
    lock_path = tmp_path / "uv.lock"
    lock_path.write_text(
        """version = 1

[[package]]
name = "brainlearn-workspace"
version = "0.1.0"
source = { virtual = "." }
dependencies = [{ name = "sample-dep" }]

[[package]]
name = "sample-dep"
version = "1.0"
source = { registry = "https://pypi.org/simple" }
""",
        encoding="utf-8",
    )
    metadata = Message()
    metadata["Name"] = "sample-dep"
    metadata["License-Expression"] = "MIT"
    distribution = SimpleNamespace(version=installed_version, metadata=metadata)
    monkeypatch.setattr(
        _INVENTORY,
        "_installed_distributions",
        lambda: {"sample-dep": [distribution]},
    )

    records = _python_inventory(lock_path)
    dependency = next(item for item in records if item["name"] == "sample-dep")

    assert dependency["installation_status"] == installation_status
    assert dependency["license"]["status"] == license_status
    if installation_status == "version_mismatch":
        assert dependency["license"]["declared_expression"] is None


def test_npm_inventory_keeps_scoped_names_and_unknown_metadata(tmp_path: Path) -> None:
    lock_path = tmp_path / "package-lock.json"
    lock_path.write_text(
        json.dumps(
            {
                "packages": {
                    "": {},
                    "node_modules/@scope/library": {
                        "version": "2.1.0",
                        "license": "MIT OR Apache-2.0",
                        "integrity": "sha512-test",
                    },
                    "node_modules/missing-license": {
                        "version": "1.0.0",
                        "dev": True,
                    },
                }
            }
        ),
        encoding="utf-8",
    )

    packages = _npm_inventory(lock_path, tmp_path)
    by_name = {item["name"]: item for item in packages}

    assert _npm_package_name("node_modules/@scope/library", {}) == "@scope/library"
    assert by_name["@scope/library"]["license"]["status"] == "spdx_expression"
    assert by_name["missing-license"]["license"]["status"] == (
        "missing_license_metadata_review_required"
    )
    assert by_name["missing-license"]["development_only"] is True


def test_repository_inventory_covers_all_lockfiles_and_exposes_review_items() -> None:
    inventory = build_inventory(REPOSITORY_ROOT)

    assert set(inventory["lockfile_sha256"]) == {
        "uv.lock",
        "apps/web/package-lock.json",
        "website/package-lock.json",
    }
    assert inventory["summary"]["python_locked_packages"] > 0
    assert inventory["summary"]["npm_locked_packages"] > 0
    assert inventory["summary"]["license_review_required"] == len(
        inventory["license_review_required"]
    )
    assert any(
        item["name"] == "mne" and item["dependency_groups"] == ["eeg"]
        for item in inventory["packages"]
    )
    python_packages = {
        item["name"]: item for item in inventory["packages"] if item["ecosystem"] == "python"
    }
    for name in {"httptools", "python-dotenv", "uvloop", "watchfiles", "websockets"}:
        assert "runtime" in python_packages[name]["dependency_groups"]
    assert {"runtime", "docs"} <= set(python_packages["pyyaml"]["dependency_groups"])
    assert "audit" in python_packages["filelock"]["dependency_groups"]
    assert all(item["license"]["status"] for item in inventory["packages"])


def test_inventory_json_output_is_stable_and_round_trips(tmp_path: Path) -> None:
    inventory = build_inventory(REPOSITORY_ROOT)
    output = tmp_path / "nested" / "inventory.json"

    write_inventory(inventory, output)
    first = output.read_bytes()
    write_inventory(inventory, output)

    assert output.read_bytes() == first
    assert json.loads(first) == inventory
