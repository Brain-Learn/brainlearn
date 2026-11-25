from __future__ import annotations

import hashlib
import importlib.util
import subprocess
import sys
from pathlib import Path

import pytest

REPOSITORY_ROOT = Path(__file__).parents[1]


def _load_script(module_name: str, file_name: str):
    spec = importlib.util.spec_from_file_location(
        module_name, REPOSITORY_ROOT / "scripts" / file_name
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


_CHECKSUMS = _load_script("release_checksums", "release_checksums.py")
_CHANGELOG = _load_script("generate_changelog", "generate_changelog.py")


def test_release_checksum_manifest_is_sorted_atomic_and_round_trips(tmp_path: Path) -> None:
    (tmp_path / "brainlearn-0.1.0rc1.whl").write_bytes(b"wheel bytes")
    (tmp_path / "brainlearn-0.1.0rc1.tar.gz").write_bytes(b"source bytes")

    manifest = _CHECKSUMS.create_manifest(tmp_path)
    original_contents = manifest.read_text(encoding="utf-8")
    expected_names = ["brainlearn-0.1.0rc1.tar.gz", "brainlearn-0.1.0rc1.whl"]
    assert [line.split("  ", maxsplit=1)[1] for line in original_contents.splitlines()] == (
        expected_names
    )
    assert original_contents.startswith(
        f"{hashlib.sha256(b'source bytes').hexdigest()}  brainlearn-0.1.0rc1.tar.gz\n"
    )
    assert _CHECKSUMS.verify_manifest(tmp_path) == expected_names

    assert _CHECKSUMS.create_manifest(tmp_path).read_text(encoding="utf-8") == original_contents


def test_release_checksum_verification_rejects_tampering_and_artifact_set_changes(
    tmp_path: Path,
) -> None:
    artifact = tmp_path / "brainlearn-0.1.0rc1.whl"
    artifact.write_bytes(b"original")
    _CHECKSUMS.create_manifest(tmp_path)

    artifact.write_bytes(b"modified")
    with pytest.raises(_CHECKSUMS.ChecksumError, match="SHA-256 mismatch"):
        _CHECKSUMS.verify_manifest(tmp_path)

    _CHECKSUMS.create_manifest(tmp_path)
    artifact.unlink()
    with pytest.raises(_CHECKSUMS.ChecksumError, match="missing:"):
        _CHECKSUMS.verify_manifest(tmp_path)

    (tmp_path / "unlisted.whl").write_bytes(b"new artifact")
    with pytest.raises(_CHECKSUMS.ChecksumError, match="unlisted:"):
        _CHECKSUMS.verify_manifest(tmp_path)


def test_release_checksum_manifest_rejects_traversal_and_symlinks(tmp_path: Path) -> None:
    artifact = tmp_path / "artifact.whl"
    artifact.write_bytes(b"safe")
    manifest = _CHECKSUMS.create_manifest(tmp_path)
    digest = hashlib.sha256(b"safe").hexdigest()
    manifest.write_text(f"{digest}  ../outside.whl\n", encoding="utf-8")
    with pytest.raises(_CHECKSUMS.ChecksumError, match="invalid SHA256SUMS line"):
        _CHECKSUMS.verify_manifest(tmp_path)

    manifest.unlink()
    link = tmp_path / "linked.whl"
    try:
        link.symlink_to(artifact)
    except OSError as exc:
        pytest.skip(f"symlink creation is not available on this runner: {exc}")
    with pytest.raises(_CHECKSUMS.ChecksumError, match="symlinks are not allowed"):
        _CHECKSUMS.create_manifest(tmp_path)


def test_release_checksum_creation_requires_portable_flat_artifacts(tmp_path: Path) -> None:
    with pytest.raises(_CHECKSUMS.ChecksumError, match="contains no release files"):
        _CHECKSUMS.create_manifest(tmp_path)

    (tmp_path / "artifact with spaces.whl").write_bytes(b"artifact")
    with pytest.raises(_CHECKSUMS.ChecksumError, match="portable ASCII characters"):
        _CHECKSUMS.create_manifest(tmp_path)

    (tmp_path / "artifact with spaces.whl").unlink()
    (tmp_path / "nested").mkdir()
    with pytest.raises(_CHECKSUMS.ChecksumError, match="only regular files"):
        _CHECKSUMS.create_manifest(tmp_path)


@pytest.mark.parametrize(
    "filename",
    [
        "CON",
        "con.txt",
        "PRN.txt",
        "NUL.tar.gz",
        "AUX.whl",
        "COM1.bin",
        "com9.tar.gz",
        "LPT1.txt",
        "lpt9.whl",
        "artifact.",
    ],
)
def test_release_checksum_creation_rejects_windows_reserved_or_unportable_names(
    tmp_path: Path, filename: str
) -> None:
    (tmp_path / filename).write_bytes(b"artifact")

    with pytest.raises(_CHECKSUMS.ChecksumError, match="portable ASCII characters"):
        _CHECKSUMS.create_manifest(tmp_path)


def test_release_checksum_verification_rejects_reserved_names_in_manifest(tmp_path: Path) -> None:
    manifest = tmp_path / "SHA256SUMS"
    manifest.write_text(f"{'0' * 64}  PRN.txt\n", encoding="utf-8")

    with pytest.raises(_CHECKSUMS.ChecksumError, match="non-portable artifact filename"):
        _CHECKSUMS.verify_manifest(tmp_path)


def test_release_checksum_rejects_case_insensitive_collisions_in_manifests(
    tmp_path: Path,
) -> None:
    manifest = tmp_path / "SHA256SUMS"
    digest = "0" * 64
    manifest.write_text(f"{digest}  BrainLearn.whl\n{digest}  brainlearn.WHL\n", encoding="utf-8")

    with pytest.raises(_CHECKSUMS.ChecksumError, match="case-insensitive filesystems"):
        _CHECKSUMS.verify_manifest(tmp_path)


@pytest.mark.parametrize("filename", ["sha256sums", "sha256sums.sigstore.json"])
def test_release_checksum_creation_rejects_case_variants_of_metadata(
    tmp_path: Path, filename: str
) -> None:
    (tmp_path / filename).write_bytes(b"artifact")

    with pytest.raises(_CHECKSUMS.ChecksumError, match="collides with release metadata"):
        _CHECKSUMS.create_manifest(tmp_path)


def test_release_checksum_creation_rejects_case_only_files_when_filesystem_allows(
    tmp_path: Path,
) -> None:
    (tmp_path / "BrainLearn.whl").write_bytes(b"first")
    try:
        (tmp_path / "brainlearn.WHL").write_bytes(b"second")
    except OSError as exc:
        pytest.skip(f"filesystem prevents case-only names: {exc}")
    if len(list(tmp_path.iterdir())) != 2:
        pytest.skip("filesystem aliases case-only names")

    with pytest.raises(_CHECKSUMS.ChecksumError, match="case-insensitive filesystems"):
        _CHECKSUMS.create_manifest(tmp_path)


def test_release_checksum_cli_creates_and_verifies_a_manifest(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    (tmp_path / "brainlearn-0.1.0rc1.whl").write_bytes(b"release bytes")

    assert _CHECKSUMS.main(["create", "--directory", str(tmp_path)]) == 0
    assert "Wrote" in capsys.readouterr().out
    assert _CHECKSUMS.main(["verify", "--directory", str(tmp_path)]) == 0
    assert "Verified 1 release artifact(s)" in capsys.readouterr().out


def test_changelog_draft_groups_conventional_commits_and_keeps_unknown_subjects() -> None:
    commits = [
        _CHANGELOG.Commit("a" * 40, "feat(ui): add explicit QC decisions"),
        _CHANGELOG.Commit("b" * 40, "fix: preserve prior project history"),
        _CHANGELOG.Commit("c" * 40, "perf: reduce preview memory"),
        _CHANGELOG.Commit(
            "d" * 40,
            "refactor(api): change run response",
            "BREAKING CHANGE: clients must read the new result field.",
        ),
        _CHANGELOG.Commit("e" * 40, "Clarify local-only boundary"),
    ]

    draft = _CHANGELOG.render_changelog("v0.2.0rc1", commits)

    assert draft.index("### Breaking changes") < draft.index("### Features")
    assert "- refactor(api): change run response (`ddddddd`)" in draft
    assert "  - Breaking change: clients must read the new result field." in draft
    assert "### Fixes" in draft
    assert "### Performance" in draft
    assert "### Other changes" in draft
    assert "- Clarify local-only boundary (`eeeeeee`)" in draft


def test_changelog_refuses_invalid_version_and_empty_range() -> None:
    commit = _CHANGELOG.Commit("a" * 40, "feat: a feature")
    with pytest.raises(ValueError, match="version must look like"):
        _CHANGELOG.render_changelog("v0.1.0\n## forged", [commit])
    with pytest.raises(ValueError, match="no non-merge commits"):
        _CHANGELOG.render_changelog("v0.1.0", [])


def test_changelog_cli_uses_a_git_range_and_excludes_merge_commits(tmp_path: Path) -> None:
    def git(*arguments: str) -> None:
        subprocess.run(["git", *arguments], cwd=tmp_path, check=True, capture_output=True)

    git("init", "--quiet")
    git("config", "user.name", "BrainLearn release test")
    git("config", "user.email", "release-test@example.invalid")
    (tmp_path / "history.txt").write_text("first\n", encoding="utf-8")
    git("add", "history.txt")
    git("commit", "--quiet", "-m", "docs: establish starting point")
    git("tag", "v0.1.0")

    (tmp_path / "history.txt").write_text("second\n", encoding="utf-8")
    git("commit", "--quiet", "-am", "feat(report): add reproducible release notes")
    script = REPOSITORY_ROOT / "scripts" / "generate_changelog.py"
    result = subprocess.run(
        [
            sys.executable,
            str(script),
            "--version",
            "v0.2.0rc1",
            "--from-ref",
            "v0.1.0",
        ],
        cwd=tmp_path,
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )

    assert result.stdout.startswith("## v0.2.0rc1\n\n### Features\n")
    assert "- feat(report): add reproducible release notes" in result.stdout
