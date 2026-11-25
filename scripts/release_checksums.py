from __future__ import annotations

import argparse
import hashlib
import os
import re
import sys
import tempfile
from pathlib import Path

MANIFEST_NAME = "SHA256SUMS"
SIGNATURE_BUNDLE_NAME = "SHA256SUMS.sigstore.json"
_ALLOWED_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9._+-]*\Z")
_MANIFEST_LINE = re.compile(r"([0-9a-f]{64})  ([A-Za-z0-9][A-Za-z0-9._+-]*)\Z")
_WINDOWS_RESERVED_STEMS = {"CON", "PRN", "AUX", "NUL"}
_WINDOWS_NUMBERED_DEVICE = re.compile(r"(?:COM|LPT)[1-9]\Z", re.IGNORECASE)
_METADATA_NAMES = {MANIFEST_NAME, SIGNATURE_BUNDLE_NAME}
_METADATA_NAMES_CASEFOLDED = {name.casefold() for name in _METADATA_NAMES}
_CHUNK_SIZE = 1024 * 1024


class ChecksumError(ValueError):
    """Raised when the release directory or manifest is unsafe or inconsistent."""


def _is_portable_filename(name: str) -> bool:
    if not _ALLOWED_NAME.fullmatch(name) or name.endswith("."):
        return False
    stem = name.split(".", maxsplit=1)[0].upper()
    return stem not in _WINDOWS_RESERVED_STEMS and not _WINDOWS_NUMBERED_DEVICE.fullmatch(stem)


def _check_case_insensitive_collisions(names: list[str]) -> None:
    seen: dict[str, str] = {}
    for name in names:
        key = name.casefold()
        previous = seen.get(key)
        if previous is not None:
            raise ChecksumError(
                f"artifact filenames collide on case-insensitive filesystems: "
                f"{previous!r} and {name!r}"
            )
        seen[key] = name


def _validate_directory(directory: Path) -> Path:
    try:
        root = directory.resolve(strict=True)
    except OSError as exc:
        raise ChecksumError(f"artifact directory does not exist: {directory}") from exc
    if not root.is_dir():
        raise ChecksumError(f"artifact path is not a directory: {directory}")
    return root


def _digest_file(path: Path) -> str:
    digest = hashlib.sha256()
    try:
        with path.open("rb") as artifact:
            for chunk in iter(lambda: artifact.read(_CHUNK_SIZE), b""):
                digest.update(chunk)
    except OSError as exc:
        raise ChecksumError(f"could not read artifact {path.name}: {exc}") from exc
    return digest.hexdigest()


def _artifact_files(root: Path, *, allow_empty: bool = False) -> list[Path]:
    artifacts: list[Path] = []
    for entry in sorted(root.iterdir(), key=lambda item: item.name):
        if entry.is_symlink():
            raise ChecksumError(f"symlinks are not allowed in the artifact directory: {entry.name}")
        if entry.name.casefold() in _METADATA_NAMES_CASEFOLDED:
            if entry.name not in _METADATA_NAMES:
                raise ChecksumError(
                    f"artifact filename collides with release metadata: {entry.name}"
                )
            if not entry.is_file():
                raise ChecksumError(f"release metadata must be regular files: {entry.name}")
            continue
        if not _is_portable_filename(entry.name):
            raise ChecksumError(
                f"artifact filename must use portable ASCII characters: {entry.name!r}"
            )
        if not entry.is_file():
            raise ChecksumError(f"artifact directory must contain only regular files: {entry.name}")
        artifacts.append(entry)
    _check_case_insensitive_collisions([path.name for path in artifacts])
    if not artifacts and not allow_empty:
        raise ChecksumError("artifact directory contains no release files")
    return artifacts


def _manifest_entries(manifest: Path) -> dict[str, str]:
    try:
        contents = manifest.read_text(encoding="utf-8")
    except OSError as exc:
        raise ChecksumError(f"could not read {MANIFEST_NAME}: {exc}") from exc
    if not contents.endswith("\n"):
        raise ChecksumError(f"{MANIFEST_NAME} must end with a newline")

    entries: dict[str, str] = {}
    for line_number, line in enumerate(contents.splitlines(), start=1):
        match = _MANIFEST_LINE.fullmatch(line)
        if match is None:
            raise ChecksumError(f"invalid {MANIFEST_NAME} line {line_number}")
        digest, name = match.groups()
        if not _is_portable_filename(name):
            raise ChecksumError(
                f"non-portable artifact filename in {MANIFEST_NAME} line {line_number}: {name}"
            )
        if name.casefold() in _METADATA_NAMES_CASEFOLDED:
            raise ChecksumError(f"release metadata cannot hash itself: {name}")
        if name in entries:
            raise ChecksumError(f"duplicate artifact in {MANIFEST_NAME}: {name}")
        entries[name] = digest
    _check_case_insensitive_collisions(list(entries))
    if not entries:
        raise ChecksumError(f"{MANIFEST_NAME} contains no entries")
    return entries


def create_manifest(directory: Path) -> Path:
    """Atomically create a sorted SHA-256 manifest for a flat artifact directory."""
    root = _validate_directory(directory)
    manifest = root / MANIFEST_NAME
    if manifest.is_symlink():
        raise ChecksumError(f"refusing to replace symlink: {MANIFEST_NAME}")

    lines = [f"{_digest_file(path)}  {path.name}" for path in _artifact_files(root)]
    contents = "\n".join(lines) + "\n"
    temporary_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            newline="\n",
            dir=root,
            prefix=f".{MANIFEST_NAME}.",
            delete=False,
        ) as temporary:
            temporary_path = Path(temporary.name)
            temporary.write(contents)
            temporary.flush()
            os.fsync(temporary.fileno())
        os.chmod(temporary_path, 0o644)
        os.replace(temporary_path, manifest)
    except OSError as exc:
        raise ChecksumError(f"could not write {MANIFEST_NAME}: {exc}") from exc
    finally:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)
    return manifest


def verify_manifest(directory: Path) -> list[str]:
    """Verify that the manifest names exactly the release files and their bytes."""
    root = _validate_directory(directory)
    manifest = root / MANIFEST_NAME
    if manifest.is_symlink() or not manifest.is_file():
        raise ChecksumError(f"{MANIFEST_NAME} must be a regular file")
    entries = _manifest_entries(manifest)
    artifacts = {path.name: path for path in _artifact_files(root, allow_empty=True)}
    expected_names = set(entries)
    actual_names = set(artifacts)
    if expected_names != actual_names:
        missing = sorted(expected_names - actual_names)
        unlisted = sorted(actual_names - expected_names)
        problems = []
        if missing:
            problems.append(f"missing: {', '.join(missing)}")
        if unlisted:
            problems.append(f"unlisted: {', '.join(unlisted)}")
        raise ChecksumError("artifact set does not match SHA256SUMS (" + "; ".join(problems) + ")")

    for name in sorted(entries):
        actual = _digest_file(artifacts[name])
        if actual != entries[name]:
            raise ChecksumError(f"SHA-256 mismatch: {name}")
    return sorted(entries)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Create or verify a portable SHA256SUMS manifest for release files."
    )
    subparsers = parser.add_subparsers(dest="command", required=True)
    for command in ("create", "verify"):
        child = subparsers.add_parser(command)
        child.add_argument(
            "--directory",
            type=Path,
            default=Path("dist"),
            help="flat directory containing the release artifacts (default: dist)",
        )
    return parser


def main(argv: list[str] | None = None) -> int:
    arguments = _parser().parse_args(argv)
    try:
        if arguments.command == "create":
            manifest = create_manifest(arguments.directory)
            print(f"Wrote {manifest}")
        else:
            names = verify_manifest(arguments.directory)
            print(f"Verified {len(names)} release artifact(s) in {MANIFEST_NAME}")
    except (ChecksumError, OSError) as exc:
        print(f"release checksum error: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
