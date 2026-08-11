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
    assert_not_clean(tmp_path, non_regular)

    oversized = tmp_path / "large.py"
    oversized.write_bytes(b"x" * (MAX_SOURCE_BYTES + 1))
    too_large = SourceFile(oversized, tmp_path)
    assert too_large.error is not None
    assert too_large.error.kind == "budget"
    assert too_large.error.code == "source-too-large"
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

    def fail_open(*args, **kwargs):
        raise OSError("permission denied")

    monkeypatch.setattr(house_lint.source.tokenize, "open", fail_open)
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
