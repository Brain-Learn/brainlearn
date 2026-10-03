"""Fetch and verify the documented OpenNeuro BIDS EEG subset."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import tempfile
from pathlib import Path, PurePosixPath
from urllib.parse import urlparse
from urllib.request import HTTPRedirectHandler, Request, build_opener

MANIFEST = (
    Path(__file__).resolve().parents[1] / "docs" / "fixtures" / "ds002181-sub-1473-1.0.0.json"
)
ALLOWED_HOSTS = {"openneuro.org", "s3.amazonaws.com"}


def prepare_output_path(destination: Path, relative: PurePosixPath) -> Path:
    """Create safe parent directories without following existing symlinks."""
    parent = destination
    for part in relative.parts[:-1]:
        parent = parent / part
        if parent.is_symlink():
            raise ValueError(f"refusing symlinked fixture directory: {parent}")
        parent.mkdir(exist_ok=True)
        if parent.is_symlink() or not parent.is_dir():
            raise ValueError(f"fixture parent is not a real directory: {parent}")

    output = parent / relative.parts[-1]
    if output.is_symlink():
        raise ValueError(f"refusing symlinked fixture file: {output}")
    if output.exists():
        if not output.is_file():
            raise ValueError(f"fixture path is not a regular file: {output}")
    return output


def is_verified_existing_file(output: Path, entry: dict[str, object]) -> bool:
    """Return whether an existing regular file already matches its manifest entry."""
    if output.is_symlink():
        raise ValueError(f"refusing symlinked fixture file: {output}")
    if not output.exists():
        return False
    if not output.is_file():
        raise ValueError(f"fixture path is not a regular file: {output}")

    existing = output.read_bytes()
    return (
        len(existing) == entry["byte_size"]
        and hashlib.sha256(existing).hexdigest() == entry["sha256"]
    )


def install_verified_payload(output: Path, payload: bytes) -> None:
    """Install a verified payload atomically, refusing to replace an existing file."""
    temporary_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            dir=output.parent, prefix=f".{output.name}.", delete=False
        ) as temporary:
            temporary_path = Path(temporary.name)
            temporary.write(payload)
            temporary.flush()
            os.fsync(temporary.fileno())
        os.link(temporary_path, output)
    finally:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)


def _validate_url_host(url: str, *, context: str) -> None:
    parsed = urlparse(url)
    if (
        parsed.scheme != "https"
        or parsed.hostname not in ALLOWED_HOSTS
        or parsed.username is not None
        or parsed.password is not None
    ):
        raise ValueError(f"unapproved HTTPS {context}")


class AllowlistedRedirectHandler(HTTPRedirectHandler):
    """Reject a redirect before urllib opens its target host."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        _validate_url_host(newurl, context="redirect target")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("destination", type=Path, help="directory for the fixture files")
    args = parser.parse_args()

    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    destination = args.destination.expanduser().resolve()
    destination.mkdir(parents=True, exist_ok=True)

    for entry in manifest["fixture"]["files"]:
        relative = PurePosixPath(entry["path"])
        if relative.is_absolute() or ".." in relative.parts:
            raise ValueError(f"unsafe fixture path: {entry['path']!r}")

        _validate_url_host(entry["source_url"], context="fixture source")

        output = prepare_output_path(destination, relative)
        if output.exists():
            if is_verified_existing_file(output, entry):
                print(f"already verified {entry['path']} ({entry['byte_size']} bytes)")
                continue
            raise FileExistsError(f"refusing to overwrite existing fixture file: {output}")

        request = Request(entry["source_url"], headers={"User-Agent": "BrainLearn-fixture/1.0"})
        opener = build_opener(AllowlistedRedirectHandler())
        with opener.open(request, timeout=30) as response:
            final = urlparse(response.geturl())
            if final.scheme != "https" or final.hostname not in ALLOWED_HOSTS:
                raise ValueError(f"unapproved redirect target for {entry['path']!r}")
            payload = response.read(entry["byte_size"] + 1)

        digest = hashlib.sha256(payload).hexdigest()
        if len(payload) != entry["byte_size"] or digest != entry["sha256"]:
            raise ValueError(f"size or SHA-256 mismatch for {entry['path']!r}")

        install_verified_payload(output, payload)
        print(f"verified {entry['path']} ({len(payload)} bytes, sha256 {digest})")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
