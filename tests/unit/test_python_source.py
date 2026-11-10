import ast
from pathlib import Path

from _source_helpers import assert_reported_as_error, load, parse_file

from house_lint.python_source import ParseFailure, parse_python


def _failure(path: Path, root: Path) -> ParseFailure:
    failure = parse_python(load(path, root))
    assert isinstance(failure, ParseFailure)
    assert_reported_as_error(failure.error, root)
    return failure


def test_python_source_exposes_each_representation_and_caches_the_derived_ones(
    write_sample,
) -> None:
    path = write_sample('''"""module docs"""\n# comment\nvalue = 1\n''')

    source = parse_file(path)

    assert source.path == path
    assert source.relative_path == "sample.py"
    assert source.text == '"""module docs"""\n# comment\nvalue = 1\n'
    assert source.lines == ['"""module docs"""', "# comment", "value = 1"]
    assert source.lines is source.lines
    assert isinstance(source.tree, ast.Module)
    assert source.comments == {2: "# comment"}
    assert source.comments is source.comments
    assert source.docstring_spans == ((1, 1),)
    assert source.docstring_spans is source.docstring_spans
    assert len(source.statements) == 2


def test_pep263_encoding_is_honored(tmp_path: Path) -> None:
    path = tmp_path / "encoded.py"
    path.write_bytes(b"# -*- coding: latin-1 -*-\nname = '\xe9'\n")

    assert "é" in parse_file(path).text


def test_universal_newlines_are_normalized_before_the_line_split(tmp_path: Path) -> None:
    path = tmp_path / "newlines.py"
    path.write_bytes(b"a = 1\r\nb = 2\rc = 3\n")

    assert parse_file(path).lines == ["a = 1", "b = 2", "c = 3"]


def test_undecodable_bytes_are_a_decode_failure_in_the_read_phase(tmp_path: Path) -> None:
    path = tmp_path / "undecodable.py"
    path.write_bytes(b"x = 1\n\n\nname = '\xff'\n")

    failure = _failure(path, tmp_path)

    assert failure.error.code == "decode-error"
    assert failure.error.kind == "decode"
    assert failure.error.phase == "read"
    assert failure.error.operation == "bounded-read"
    assert failure.error.message == "source could not be decoded"
    assert isinstance(failure.debug_exception, UnicodeDecodeError)


def test_invalid_encoding_declaration_is_a_decode_failure(tmp_path: Path) -> None:
    path = tmp_path / "declared.py"
    path.write_bytes(b"# -*- coding: nope -*-\nx = 1\n")

    failure = _failure(path, tmp_path)

    assert failure.error.code == "decode-error"
    assert failure.error.phase == "read"
    assert failure.error.message == "source encoding declaration is invalid"
    assert isinstance(failure.debug_exception, SyntaxError)


def test_syntax_failure_reports_where_it_happened(tmp_path: Path) -> None:
    path = tmp_path / "pkg" / "broken.py"
    path.parent.mkdir()
    path.write_text("value = 1\nif:\n")

    failure = _failure(path, tmp_path)

    assert failure.error.code == "syntax-error"
    assert failure.error.kind == "syntax"
    assert failure.error.phase == "analysis"
    assert failure.error.operation == "ast-parse"
    assert failure.error.path == "pkg/broken.py"
    assert failure.error.line == 2
    assert failure.error.column == 3
    assert failure.error.end_column == 4
    assert isinstance(failure.debug_exception, SyntaxError)


def test_tokenize_failure_comes_before_any_parse(tmp_path: Path) -> None:
    path = tmp_path / "tokens.py"
    path.write_text("value = '''unterminated\n")

    failure = _failure(path, tmp_path)

    assert failure.error.code == "tokenize-error"
    assert failure.error.kind == "tokenize"
    assert failure.error.operation == "tokenize"


def test_lines_follow_the_tokenizer_line_model_not_splitlines(write_sample) -> None:
    # str.splitlines() splits on \f, \v, U+2028 and friends, which the tokenizer and AST
    # do not count as line breaks -- every consumer indexes `lines` with tokenizer/AST
    # line numbers, so the two models must agree on any legal Python source.
    for content, expected in (
        ('x = "a\fb"\n# after\n', ['x = "a\fb"', "# after"]),
        ("x = 1\n\fy = 2\n# after\n", ["x = 1", "\fy = 2", "# after"]),
        ('x = "a b"\n# after\n', ['x = "a b"', "# after"]),
    ):
        assert parse_file(write_sample(content)).lines == expected


def test_byte_column_counts_utf8_bytes_before_a_character_offset(parse_sample) -> None:
    source = parse_sample("x = 1  # \u00e9t\u00e9 \u2014 note\n")

    assert source.byte_column(1, 0) == 1
    # "\u00e9" is two bytes, so the first 12 characters ("x = 1  # " and the word) are 14 bytes.
    assert source.byte_column(1, 12) == 15
    # "\u2014" is three bytes, so the line's 19 characters end at byte 23.
    assert source.byte_column(1, 19) == 24
