from pathlib import Path

import pytest

import house_lint.source
from house_lint.results import ScanResult
from house_lint.source import MAX_SOURCE_BYTES, LoadedFile, LoadFailure, load_file


def _load(path: Path, root: Path) -> LoadedFile | LoadFailure:
    return load_file(path, root, resolved_path=path.resolve())


def _failure(path: Path, root: Path) -> LoadFailure:
    failure = _load(path, root)
    assert isinstance(failure, LoadFailure)
    result = ScanResult(root, None, (), 0, errors=(failure.error,))
    assert not result.is_clean
    assert result.to_dict()["summary"]["error_count"] == 1
    return failure


def test_loaded_file_holds_the_bytes_as_read_and_no_decoded_form(tmp_path: Path) -> None:
    path = tmp_path / "pkg" / "undecodable.py"
    path.parent.mkdir()
    path.write_bytes(b"name = '\xff'\n")

    loaded = _load(path, tmp_path)

    assert loaded == LoadedFile(path, "pkg/undecodable.py", b"name = '\xff'\n")


def test_directory_is_a_path_failure(tmp_path: Path) -> None:
    directory = tmp_path / "directory.py"
    directory.mkdir()

    failure = _failure(directory, tmp_path)

    assert failure.error.kind == "path"
    assert failure.error.operation == "regular-file"
    assert failure.error.message == "not a regular file"


def test_oversized_file_is_a_budget_failure_but_one_at_the_limit_loads(tmp_path: Path) -> None:
    oversized = tmp_path / "large.py"
    oversized.write_bytes(b"x" * (MAX_SOURCE_BYTES + 1))
    at_limit = tmp_path / "limit.py"
    at_limit.write_bytes(b"x" * MAX_SOURCE_BYTES)

    failure = _failure(oversized, tmp_path)

    assert failure.error.kind == "budget"
    assert failure.error.code == "source-too-large"
    assert failure.error.phase == "source-check"
    assert failure.debug_exception is None
    assert isinstance(_load(at_limit, tmp_path), LoadedFile)


def test_read_failure_preserves_the_exception_for_debug(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    path = tmp_path / "unreadable.py"
    path.write_text("value = 1\n")
    raised = OSError("permission denied")

    def fail_open(_path: str | Path, _flags: int) -> int:
        raise raised

    monkeypatch.setattr(house_lint.source.os, "open", fail_open)

    failure = _failure(path, tmp_path)

    assert failure.error.kind == "read"
    assert failure.error.code == "read-error"
    assert failure.error.path == "unreadable.py"
    assert failure.debug_exception is raised


def test_symlink_escaping_root_is_a_path_failure_without_a_read(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    root = tmp_path / "root"
    root.mkdir()
    target = tmp_path / "real.py"
    target.write_text("value = 1\n")
    link = root / "link.py"
    link.symlink_to(target)

    def fail_read(*_args: object, **_kwargs: object) -> bytes:
        raise AssertionError("an escaping path must not be read")

    monkeypatch.setattr(house_lint.source, "read_regular_file_bytes", fail_read)

    failure = _failure(link, root)

    assert failure.error.kind == "path"
    assert failure.error.code == "path-error"
    assert failure.error.path == "link.py"
    assert failure.error.message == "selected path escapes root"


def test_path_outside_root_reports_only_its_name(tmp_path: Path) -> None:
    root = tmp_path / "root"
    root.mkdir()
    outside = tmp_path / "pkg" / "real.py"
    outside.parent.mkdir()
    outside.write_text("value = 1\n")

    failure = _failure(outside, root)

    assert failure.error.path == "real.py"
    assert failure.error.message == "selected path escapes root"


def test_load_reads_the_resolved_target_it_was_given_not_a_fresh_one(tmp_path: Path) -> None:
    """Discovery resolves a symlink to check containment, then the scan reads it. Resolving a
    second time here would let a retarget landing in between send the read to a file discovery
    never approved, so the resolved target is threaded through instead of recomputed."""
    root = tmp_path / "root"
    root.mkdir()
    approved = root / "approved.py"
    approved.write_text("approved = 1\n")
    swapped = root / "swapped.py"
    swapped.write_text("swapped = 1\n")
    link = root / "link.py"
    link.symlink_to(approved)

    resolved = link.resolve()  # what discovery would have validated
    link.unlink()
    link.symlink_to(swapped)  # the retarget, landing before the read

    loaded = load_file(link, root, resolved_path=resolved)

    assert isinstance(loaded, LoadedFile)
    assert loaded.content == b"approved = 1\n"
    assert loaded.path == link


def test_a_resolved_path_replaced_by_a_symlink_is_refused_rather_than_followed(
    tmp_path: Path,
) -> None:
    """Discovery resolves the whole selection before the scan begins, so the gap between a path
    being approved and being opened spans the run rather than a single file. A resolved path's
    final component is by construction not a symlink; if it is one by the time the scan opens it,
    it was swapped afterwards and must not be read. `O_NOFOLLOW` turns that into an ordinary read
    error instead of a read outside the root."""
    root = tmp_path / "root"
    root.mkdir()
    approved = root / "approved.py"
    approved.write_text("approved = 1\n")
    outside = tmp_path / "outside.py"
    outside.write_text("outside = 1\n")

    resolved = approved.resolve()  # what discovery validated
    approved.unlink()
    approved.symlink_to(outside)  # swapped after approval, before the read

    failure = load_file(approved, root, resolved_path=resolved)

    assert isinstance(failure, LoadFailure)
    assert failure.error.code == "read-error"
