from __future__ import annotations

import urllib.request
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path, PurePosixPath

import pytest

HELPER_PATH = Path(__file__).resolve().parents[1] / "scripts" / "fetch_bids_eeg_fixture.py"
HELPER_SPEC = spec_from_file_location("fetch_bids_eeg_fixture", HELPER_PATH)
assert HELPER_SPEC is not None and HELPER_SPEC.loader is not None
HELPER = module_from_spec(HELPER_SPEC)
HELPER_SPEC.loader.exec_module(HELPER)
AllowlistedRedirectHandler = HELPER.AllowlistedRedirectHandler
install_verified_payload = HELPER.install_verified_payload
prepare_output_path = HELPER.prepare_output_path


def test_fixture_retrieval_rejects_symlinked_parent(tmp_path: Path) -> None:
    outside = tmp_path / "outside"
    outside.mkdir()
    destination = tmp_path / "destination"
    destination.mkdir()
    try:
        (destination / "sub-1473").symlink_to(outside, target_is_directory=True)
    except (NotImplementedError, OSError):
        pytest.skip("this platform cannot create directory symlinks")

    with pytest.raises(ValueError, match="symlinked fixture directory"):
        prepare_output_path(
            destination,
            PurePosixPath("sub-1473/eeg/sub-1473_task-Baseline_eeg.set"),
        )
    assert list(outside.iterdir()) == []


def test_fixture_retrieval_does_not_replace_existing_file(tmp_path: Path) -> None:
    output = tmp_path / "existing.set"
    output.write_bytes(b"researcher file")

    with pytest.raises(FileExistsError):
        install_verified_payload(output, b"fixture bytes")

    assert output.read_bytes() == b"researcher file"
    assert sorted(path.name for path in tmp_path.iterdir()) == ["existing.set"]


def test_fixture_redirect_rejects_unapproved_host_before_following() -> None:
    request = urllib.request.Request("https://openneuro.org/start")
    handler = AllowlistedRedirectHandler()

    with pytest.raises(ValueError, match="unapproved HTTPS redirect target"):
        handler.redirect_request(
            request,
            None,
            302,
            "Found",
            {},
            "https://example.invalid/redirected-file",
        )
