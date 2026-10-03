"""Fetch and verify the documented OpenNeuro BIDS EEG subset."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import tempfile
from pathlib import Path, PurePosixPath
from urllib.parse import urlparse
from urllib.request import Request, urlopen

MANIFEST = (
    Path(__file__).resolve().parents[1] / "docs" / "fixtures" / "ds002181-sub-1473-1.0.0.json"
)
ALLOWED_HOSTS = {"openneuro.org", "s3.amazonaws.com"}


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

        source = urlparse(entry["source_url"])
        if source.scheme != "https" or source.hostname not in ALLOWED_HOSTS:
            raise ValueError(f"unapproved fixture source for {entry['path']!r}")

        output = destination.joinpath(*relative.parts)
        output.parent.mkdir(parents=True, exist_ok=True)
        request = Request(entry["source_url"], headers={"User-Agent": "BrainLearn-fixture/1.0"})
        with urlopen(request, timeout=30) as response:
            final = urlparse(response.geturl())
            if final.scheme != "https" or final.hostname not in ALLOWED_HOSTS:
                raise ValueError(f"unapproved redirect target for {entry['path']!r}")
            payload = response.read(entry["byte_size"] + 1)

        digest = hashlib.sha256(payload).hexdigest()
        if len(payload) != entry["byte_size"] or digest != entry["sha256"]:
            raise ValueError(f"size or SHA-256 mismatch for {entry['path']!r}")

        temporary_path: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(
                dir=output.parent, prefix=f".{output.name}.", delete=False
            ) as temporary:
                temporary_path = Path(temporary.name)
                temporary.write(payload)
                temporary.flush()
                os.fsync(temporary.fileno())
            os.replace(temporary_path, output)
        finally:
            if temporary_path is not None:
                temporary_path.unlink(missing_ok=True)
        print(f"verified {entry['path']} ({len(payload)} bytes, sha256 {digest})")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
