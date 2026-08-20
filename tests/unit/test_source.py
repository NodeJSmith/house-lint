import ast
from pathlib import Path

import pytest

import house_lint.source
from house_lint.results import ScanResult
from house_lint.source import MAX_SOURCE_BYTES, SourceFile


def assert_not_clean(root: Path, source: SourceFile) -> None:
    assert source.error is not None
    result = ScanResult(root, None, (), 0, 0, errors=(source.error,))

    assert not result.is_clean
    assert result.to_dict()["summary"]["error_count"] == 1


def test_source_file_caches_representations_and_exposes_comments_docstrings_and_statements(
    write_sample,
):
    path = write_sample('''"""module docs"""\n# comment\nvalue = 1\n''')
    source = SourceFile(path, path.parent)

    assert not source._analyzed
    assert source._tokens == ()
    assert source._tree is None
    assert source.error is None
    assert source.text is source.text
    assert source.lines == ['"""module docs"""', "# comment", "value = 1"]
    assert source.tree is source.tree
    assert isinstance(source.tree, ast.Module)
    assert source.comments == {2: "# comment"}
    assert source.comments is source.comments
    assert source.docstring_spans == ((1, 1),)
    assert len(source.statements) == 2


def test_pep263_encoding_is_decoded_with_tokenize_open(tmp_path):
    path = tmp_path / "encoded.py"
    path.write_bytes(b"# -*- coding: latin-1 -*-\nname = '\xe9'\n")

    assert "é" in SourceFile(path, tmp_path).text


def test_decode_failure_is_structured_and_not_clean(tmp_path):
    path = tmp_path / "undecodable.py"
    path.write_bytes(b"name = '\xff'\n")
    source = SourceFile(path, tmp_path)

    assert source.error is not None
    assert source.error.kind == "decode"
    assert source.error.code == "decode-error"
    assert_not_clean(tmp_path, source)


def test_syntax_failure_is_structured_atomic_and_not_clean(tmp_path):
    path = tmp_path / "broken.py"
    path.write_text("value = 1\nif:\n")
    source = SourceFile(path, tmp_path)

    assert source.error is not None
    assert source.error.kind == "syntax"
    assert source.error.code == "syntax-error"
    assert source.tree is None
    assert source.tokens == ()
    assert source.comments == {}
    assert source.docstring_spans == ()
    assert source.statements == ()
    with pytest.raises(RuntimeError, match="source is unavailable"):
        _ = source.text
    with pytest.raises(RuntimeError, match="source is unavailable"):
        _ = source.lines
    assert_not_clean(tmp_path, source)


def test_non_regular_and_oversized_files_are_rejected_and_not_clean(tmp_path):
    directory = tmp_path / "directory.py"
    directory.mkdir()
    non_regular = SourceFile(directory, tmp_path)
    assert non_regular.error is not None
    assert non_regular.error.kind == "path"
    assert non_regular.content_bytes is None
    assert_not_clean(tmp_path, non_regular)

    oversized = tmp_path / "large.py"
    oversized.write_bytes(b"x" * (MAX_SOURCE_BYTES + 1))
    too_large = SourceFile(oversized, tmp_path)
    assert too_large.error is not None
    assert too_large.error.kind == "budget"
    assert too_large.error.code == "source-too-large"
    # The bytes are still reported even though the file is too large to analyze; it is
    # `hash_source_content` that declines to key a cache entry on them.
    assert too_large.content_bytes is not None
    assert_not_clean(tmp_path, too_large)


def test_tokenize_failure_is_structured_and_not_clean(tmp_path):
    path = tmp_path / "tokens.py"
    path.write_text("value = '''unterminated\n")
    source = SourceFile(path, tmp_path)

    assert source.error is not None
    assert source.error.kind == "tokenize"
    assert isinstance(source.error.operation, str)
    assert_not_clean(tmp_path, source)


def test_read_failure_is_structured_and_not_clean(monkeypatch, tmp_path):
    path = tmp_path / "unreadable.py"
    path.write_text("value = 1\n")

    def fail_open(_path: str | Path, _flags: int) -> int:
        raise OSError("permission denied")

    monkeypatch.setattr(house_lint.source.os, "open", fail_open)
    source = SourceFile(path, tmp_path)

    assert source.error is not None
    assert source.error.kind == "read"
    assert source.error.code == "read-error"
    assert source.error.path == "unreadable.py"
    assert_not_clean(tmp_path, source)


def test_symlink_escaping_root_is_a_structured_path_error(tmp_path):
    root = tmp_path / "root"
    root.mkdir()
    target = tmp_path / "real.py"
    target.write_text("value = 1\n")
    link = root / "link.py"
    link.symlink_to(target)

    source = SourceFile(link, root)

    assert source.error is not None
    assert source.error.kind == "path"
    assert source.error.code == "path-error"
    assert source.error.path == "link.py"
    assert source.path == link.absolute()
    assert_not_clean(root, source)


def test_source_reads_the_resolved_target_it_was_given_not_a_fresh_one(tmp_path):
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

    source = SourceFile(link, root, resolved_path=resolved)

    assert source.error is None
    assert source.text == "approved = 1\n"
    # Without the threaded path the same construction follows the retargeted link instead.
    assert SourceFile(link, root).text == "swapped = 1\n"


def test_a_resolved_path_replaced_by_a_symlink_is_refused_rather_than_followed(tmp_path):
    """Discovery now resolves the whole selection before the scan begins, so the gap between a
    path being approved and being opened spans the run rather than a single file. A resolved
    path's final component is by construction not a symlink; if it is one by the time the scan
    opens it, it was swapped afterwards and must not be read. `O_NOFOLLOW` turns that into an
    ordinary read error instead of a read outside the root."""
    root = tmp_path / "root"
    root.mkdir()
    approved = root / "approved.py"
    approved.write_text("approved = 1\n")
    outside = tmp_path / "outside.py"
    outside.write_text("outside = 1\n")

    resolved = approved.resolve()  # what discovery validated
    approved.unlink()
    approved.symlink_to(outside)  # swapped after approval, before the read

    source = SourceFile(approved, root, resolved_path=resolved)

    assert source.error is not None
    assert source.error.code == "read-error"
    with pytest.raises(RuntimeError, match="source is unavailable"):
        _ = source.text


def test_escaped_symlink_source_cannot_be_read(tmp_path):
    root = tmp_path / "root"
    root.mkdir()
    target = tmp_path / "real.py"
    target.write_text("value = 1\n")
    link = root / "link.py"
    link.symlink_to(target)

    source = SourceFile(link, root)

    assert source.error is not None
    with pytest.raises(RuntimeError, match="source is unavailable"):
        _ = source.text


def test_nested_source_error_uses_root_relative_posix_path(tmp_path):
    path = tmp_path / "pkg" / "broken.py"
    path.parent.mkdir()
    path.write_text("if:\n")

    source = SourceFile(path, tmp_path)

    assert source.error is not None
    assert source.error.path == "pkg/broken.py"
