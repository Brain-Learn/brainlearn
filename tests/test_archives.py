"""Bounded, hardened archive extraction: adversarial offline coverage."""

import io
import os
import stat
import tarfile
import threading
import warnings
import zipfile
from pathlib import Path

import pytest
from brainlearn_core.archives import (
    ArchiveCancelled,
    ArchiveLimits,
    ArchiveRejectedError,
    detect_archive_format,
    extract_archive,
)


def _make_zip(path: Path, members: list[tuple[str, bytes]]) -> Path:
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_STORED) as archive:
        for name, blob in members:
            archive.writestr(name, blob)
    return path


def _make_tar_gz(path: Path, members: list[tuple[str, bytes]]) -> Path:
    with tarfile.open(path, "w:gz") as archive:
        for name, blob in members:
            info = tarfile.TarInfo(name)
            info.size = len(blob)
            info.mode = 0o644
            archive.addfile(info, io.BytesIO(blob))
    return path


def _read_tree(root: Path) -> dict[str, bytes]:
    found: dict[str, bytes] = {}
    for current, _, filenames in os.walk(root):
        for name in filenames:
            child = Path(current) / name
            found[str(child.relative_to(root))] = child.read_bytes()
    return found


def test_detect_format_by_magic_not_suffix(tmp_path: Path) -> None:
    zipped = _make_zip(tmp_path / "data.bin", [("a.txt", b"a")])
    assert detect_archive_format(zipped.read_bytes()[:8]) == "zip"
    tgz = _make_tar_gz(tmp_path / "data.bin", [("a.txt", b"a")])
    assert detect_archive_format(tgz.read_bytes()[:8]) == "tar.gz"
    with pytest.raises(ArchiveRejectedError, match="unsupported-format"):
        detect_archive_format(b"not an archive at all.....")


def test_zip_round_trip_with_allowlist(tmp_path: Path) -> None:
    archive = _make_zip(tmp_path / "snap.zip", [("a.txt", b"a" * 10), ("sub/b.txt", b"b" * 20)])
    dest = tmp_path / "out"
    dest.mkdir()
    members = extract_archive(archive, dest, expected={"a.txt": 10, "sub/b.txt": 20})
    assert set(members) == {"a.txt", "sub/b.txt"}
    assert _read_tree(dest) == {"a.txt": b"a" * 10, "sub/b.txt": b"b" * 20}


def test_tar_gz_round_trip_with_allowlist(tmp_path: Path) -> None:
    archive = _make_tar_gz(tmp_path / "snap.tgz", [("a.txt", b"a" * 10), ("sub/b.txt", b"b" * 20)])
    dest = tmp_path / "out"
    dest.mkdir()
    members = extract_archive(archive, dest, expected={"a.txt": 10, "sub/b.txt": 20})
    assert set(members) == {"a.txt", "sub/b.txt"}
    assert _read_tree(dest) == {"a.txt": b"a" * 10, "sub/b.txt": b"b" * 20}


@pytest.mark.parametrize("container", ["zip", "tar.gz"])
@pytest.mark.parametrize(
    "members",
    [
        [("/abs.txt", b"x")],
        [("C:/win.txt", b"x")],
        [("../escape.txt", b"x")],
        [("sub/../../escape.txt", b"x")],
        [("", b"x")],
        [("a/./b.txt", b"x")],
    ],
)
def test_absolute_and_traversal_members_rejected(
    tmp_path: Path, container: str, members: list[tuple[str, bytes]]
) -> None:
    archive = tmp_path / f"evil.{container}"
    if container == "zip":
        _make_zip(archive, members)
    else:
        _make_tar_gz(archive, members)
    dest = tmp_path / "out"
    dest.mkdir()
    with pytest.raises(ArchiveRejectedError) as excinfo:
        extract_archive(archive, dest)
    assert excinfo.value.code in ("absolute-path", "traversal", "unsafe-name")
    assert _read_tree(dest) == {}


def _make_zip_symlink(path: Path, linkname: str, target: str) -> Path:
    info = zipfile.ZipInfo(linkname)
    info.create_system = 3
    info.external_attr = 0o120777 << 16
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr(info, target)
    return path


def _make_tar_symlink(path: Path, linkname: str, target: str) -> Path:
    with tarfile.open(path, "w") as archive:
        info = tarfile.TarInfo(linkname)
        info.type = tarfile.SYMTYPE
        info.linkname = target
        archive.addfile(info)
    return path


@pytest.mark.parametrize("container", ["zip", "tar"])
def test_symlink_members_rejected(tmp_path: Path, container: str) -> None:
    archive = tmp_path / f"link.{container}"
    if container == "zip":
        _make_zip_symlink(archive, "evil", "/etc/passwd")
    else:
        _make_tar_symlink(archive, "evil", "/etc/passwd")
    dest = tmp_path / "out"
    dest.mkdir()
    with pytest.raises(ArchiveRejectedError) as excinfo:
        extract_archive(archive, dest)
    assert excinfo.value.code == "link"
    assert _read_tree(dest) == {}


def test_tar_special_files_rejected(tmp_path: Path) -> None:
    archive = tmp_path / "special.tar"
    with tarfile.open(archive, "w") as tar:
        fifo = tarfile.TarInfo("pipe")
        fifo.type = tarfile.FIFOTYPE
        tar.addfile(fifo)
        dev = tarfile.TarInfo("null")
        dev.type = tarfile.CHRTYPE
        dev.devmajor, dev.devminor = 1, 3
        tar.addfile(dev)
    dest = tmp_path / "out"
    dest.mkdir()
    with pytest.raises(ArchiveRejectedError) as excinfo:
        extract_archive(archive, dest)
    assert excinfo.value.code == "special-file"
    assert _read_tree(dest) == {}


def test_duplicate_and_conflicting_paths_rejected(tmp_path: Path) -> None:
    archive = tmp_path / "dup.zip"
    # The duplicate name warning is the point: stdlib tolerates what we refuse.
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", UserWarning)
        with zipfile.ZipFile(archive, "w") as container:
            container.writestr("a.txt", b"first")
            container.writestr("a.txt", b"second")
    dest = tmp_path / "out"
    dest.mkdir()
    with pytest.raises(ArchiveRejectedError) as excinfo:
        extract_archive(archive, dest)
    assert excinfo.value.code == "duplicate-path"

    archive2 = tmp_path / "conflict.tar"
    with tarfile.open(archive2, "w") as tar:
        blob = b"x"
        file_info = tarfile.TarInfo("a")
        file_info.size = len(blob)
        tar.addfile(file_info, io.BytesIO(blob))
        dir_info = tarfile.TarInfo("a/b.txt")
        dir_info.size = len(blob)
        tar.addfile(dir_info, io.BytesIO(blob))
    dest2 = tmp_path / "out2"
    dest2.mkdir()
    with pytest.raises(ArchiveRejectedError) as excinfo:
        extract_archive(archive2, dest2)
    assert excinfo.value.code in ("conflicting-path", "duplicate-path")


def test_unexpected_and_missing_members_rejected(tmp_path: Path) -> None:
    archive = _make_zip(tmp_path / "a.zip", [("a.txt", b"a"), ("stowaway.txt", b"s")])
    dest = tmp_path / "out"
    dest.mkdir()
    with pytest.raises(ArchiveRejectedError) as excinfo:
        extract_archive(archive, dest, expected={"a.txt": 1})
    assert excinfo.value.code == "unexpected-member"

    archive2 = _make_zip(tmp_path / "b.zip", [("a.txt", b"a")])
    dest2 = tmp_path / "out2"
    dest2.mkdir()
    with pytest.raises(ArchiveRejectedError) as excinfo:
        extract_archive(archive2, dest2, expected={"a.txt": 1, "missing.txt": 2})
    assert excinfo.value.code == "incomplete-archive"


def test_limits_enforced_before_and_during_extraction(tmp_path: Path) -> None:
    big = _make_zip(tmp_path / "big.zip", [(f"f{i}.txt", b"x") for i in range(10)])
    dest = tmp_path / "out"
    dest.mkdir()
    with pytest.raises(ArchiveRejectedError) as excinfo:
        extract_archive(big, dest, limits=ArchiveLimits(max_members=3))
    assert excinfo.value.code == "too-many-members"

    bomb = _make_zip(tmp_path / "bomb.zip", [("huge.bin", b"z" * 1000)])
    dest2 = tmp_path / "out2"
    dest2.mkdir()
    with pytest.raises(ArchiveRejectedError) as excinfo:
        extract_archive(bomb, dest2, budget_bytes=100)
    assert excinfo.value.code == "total-too-large"

    lying = tmp_path / "lying.zip"
    with zipfile.ZipFile(lying, "w") as container:
        container.writestr("grow.bin", b"g" * 500)
    dest3 = tmp_path / "out3"
    dest3.mkdir()
    with pytest.raises(ArchiveRejectedError) as excinfo:
        extract_archive(lying, dest3, expected={"grow.bin": 10})
    # The declared size already exceeds the allowlist, so the prescan
    # refuses before any byte lands.
    assert excinfo.value.code == "total-too-large"
    assert not (dest3 / "grow.bin").exists()


def test_streaming_overrun_aborts_live(tmp_path: Path) -> None:
    """A stream longer than its cap aborts mid-write, bounded in memory."""

    from brainlearn_core.archives import _write_member

    dest = tmp_path / "out"
    dest.mkdir()

    def endless() -> object:
        while True:
            yield b"z" * 65536

    with pytest.raises(ArchiveRejectedError) as excinfo:
        _write_member(
            dest,
            "grow.bin",
            endless(),
            expected_size=10,
            budget=[10**9],
            limits=ArchiveLimits(),
            cancel=None,
        )
    assert excinfo.value.code == "file-too-large"
    assert (dest / "grow.bin").stat().st_size <= 10 + 65536


def test_oversized_central_directory_refused_before_parsing(
    tmp_path: Path,
) -> None:
    archive = tmp_path / "commented.zip"
    # The stdlib truncates overlong comments with a warning of its own;
    # the remaining tens of kilobytes still dwarf what one member allows.
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", UserWarning)
        with zipfile.ZipFile(archive, "w") as container:
            container.writestr("a.txt", b"a")
            container.comment = b"x" * 1_000_000
    dest = tmp_path / "out"
    dest.mkdir()
    # One member, but directory-sized bytes far beyond the allowance: the
    # size gate refuses without parsing it all.
    with pytest.raises(ArchiveRejectedError) as excinfo:
        extract_archive(archive, dest, limits=ArchiveLimits(max_members=10))
    assert excinfo.value.code == "too-many-members"
    assert _read_tree(dest) == {}


def test_ratio_limit_rejects_bombs(tmp_path: Path) -> None:
    import zlib

    payload = b"0" * 1_000_000
    archive = tmp_path / "ratio.zip"
    with zipfile.ZipFile(
        archive, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9
    ) as container:
        container.writestr("zeros.bin", payload)
    assert len(zlib.compress(payload, 9)) < 10_000
    dest = tmp_path / "out"
    dest.mkdir()
    with pytest.raises(ArchiveRejectedError) as excinfo:
        extract_archive(archive, dest, limits=ArchiveLimits(max_ratio=10.0))
    assert excinfo.value.code == "ratio-exceeded"


def test_cancellation_abandons_extraction(tmp_path: Path) -> None:
    archive = _make_zip(tmp_path / "many.zip", [(f"f{i}.txt", b"x" * 100) for i in range(20)])
    dest = tmp_path / "out"
    dest.mkdir()
    calls = {"count": 0}

    def cancel() -> bool:
        calls["count"] += 1
        return calls["count"] > 3

    with pytest.raises(ArchiveCancelled):
        extract_archive(archive, dest, cancel=cancel)
    assert calls["count"] > 3

    event = threading.Event()
    event.set()
    with pytest.raises(ArchiveCancelled):
        extract_archive(archive, dest, cancel=event)
    # Partial members remain for the caller to clean up; the call just stops.
    assert event.is_set()


def test_unsafe_names_and_depth_rejected(tmp_path: Path) -> None:
    deep = "/".join(["d"] * 40) + "/x.txt"
    archive = _make_zip(tmp_path / "deep.zip", [(deep, b"x")])
    dest = tmp_path / "out"
    dest.mkdir()
    with pytest.raises(ArchiveRejectedError) as excinfo:
        extract_archive(archive, dest, limits=ArchiveLimits(max_depth=8))
    assert excinfo.value.code == "depth-exceeded"

    nulled = _make_zip(tmp_path / "nul.zip", [("ok.txt", b"x")])
    assert nulled.is_file()
    dest2 = tmp_path / "out2"
    dest2.mkdir()
    members = extract_archive(nulled, dest2)
    assert members == ("ok.txt",)


def test_corrupt_and_empty_archives_rejected(tmp_path: Path) -> None:
    broken = tmp_path / "broken.zip"
    broken.write_bytes(b"PK\x03\x04 truncated garbage here")
    dest = tmp_path / "out"
    dest.mkdir()
    with pytest.raises(ArchiveRejectedError) as excinfo:
        extract_archive(broken, dest)
    assert excinfo.value.code == "corrupt-archive"

    empty = tmp_path / "empty.bin"
    empty.write_bytes(b"")
    with pytest.raises(ArchiveRejectedError) as excinfo:
        extract_archive(empty, dest)
    assert excinfo.value.code == "corrupt-archive"


def test_symlinked_archive_and_destination_refused(tmp_path: Path) -> None:
    real = _make_zip(tmp_path / "real.zip", [("a.txt", b"a")])
    link = tmp_path / "link.zip"
    link.symlink_to(real)
    dest = tmp_path / "out"
    dest.mkdir()
    with pytest.raises(ArchiveRejectedError) as excinfo:
        extract_archive(link, dest)
    assert excinfo.value.code == "link"

    dest_link = tmp_path / "destlink"
    dest_link.symlink_to(dest, target_is_directory=True)
    with pytest.raises(ArchiveRejectedError) as excinfo:
        extract_archive(real, dest_link)
    assert excinfo.value.code == "link"
    assert _read_tree(dest) == {}


def test_skip_keeps_verified_members_while_proving_presence(tmp_path: Path) -> None:
    archive = _make_zip(tmp_path / "a.zip", [("keep.txt", b"keep"), ("fresh.txt", b"new-bytes")])
    dest = tmp_path / "out"
    dest.mkdir()
    (dest / "keep.txt").write_bytes(b"keep")
    members = extract_archive(
        archive,
        dest,
        expected={"keep.txt": 4, "fresh.txt": 9},
        skip={"keep.txt"},
    )
    assert members == ("fresh.txt",)
    assert (dest / "keep.txt").read_bytes() == b"keep"
    assert (dest / "fresh.txt").read_bytes() == b"new-bytes"

    with pytest.raises(ArchiveRejectedError) as excinfo:
        extract_archive(archive, dest, expected={"keep.txt": 4, "fresh.txt": 9}, skip={"ghost.txt"})
    assert excinfo.value.code == "unexpected-member"


def test_extracted_files_are_regular_and_unlinked(tmp_path: Path) -> None:
    archive = _make_zip(tmp_path / "a.zip", [("a.txt", b"data")])
    dest = tmp_path / "out"
    dest.mkdir()
    extract_archive(archive, dest, expected={"a.txt": 4})
    target = dest / "a.txt"
    assert not target.is_symlink()
    assert stat.S_ISREG(target.lstat().st_mode)
    assert oct(target.stat().st_mode & 0o777) == "0o644"
