"""The Python layer over a loaded file: decoding, tokens, tree, and what derives from them."""

import ast
import io
import tokenize
from dataclasses import dataclass
from functools import cached_property
from pathlib import Path
from typing import TypeAlias

from house_lint.results import LintError, error
from house_lint.source import LoadedFile

Token: TypeAlias = tokenize.TokenInfo


@dataclass(frozen=True)
class ParseFailure:
    """A file whose bytes were read but aren't usable Python.

    Its outcome follows from the bytes, so unlike a `LoadFailure` it has a cache key and is cached.
    `debug_exception` is preserved for `--debug` output only.
    """

    error: LintError
    debug_exception: BaseException


@dataclass(frozen=True)
class PythonSource:
    """One decoded, tokenized, and parsed Python file, which is what a rule is handed.

    `statements` holds every statement at any depth, in source order, not just the module body.
    """

    path: Path
    relative_path: str
    text: str
    tokens: tuple[Token, ...]
    tree: ast.Module
    statements: tuple[ast.stmt, ...]

    @cached_property
    def lines(self) -> list[str]:
        """`text` split on the tokenizer's line model: `\\n` only.

        Not `str.splitlines()`, which also splits on `\\f`, `\\v`, `\\x1c`-`\\x1e`, `\\x85`,
        `\\u2028`, and `\\u2029` — characters the tokenizer and AST do not count as line breaks.
        Every consumer indexes this list with tokenizer/AST line numbers, so one such character
        anywhere in the source (legal in a string literal, or as a bare `\\f` page break) would
        shift every later index. The universal-newline decode in `parse_python` has already
        normalized `\\r\\n` and `\\r`, so splitting on `\\n` matches the tokenizer exactly.
        """
        lines = self.text.split("\n")
        if lines and lines[-1] == "":
            lines.pop()
        return lines

    @cached_property
    def comments(self) -> dict[int, str]:
        return {
            token.start[0]: token.string for token in self.tokens if token.type == tokenize.COMMENT
        }

    @cached_property
    def docstring_spans(self) -> tuple[tuple[int, int], ...]:
        spans: list[tuple[int, int]] = []
        for node in ast.walk(self.tree):
            body = getattr(node, "body", ())
            if (
                isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef))
                and body
            ):
                first = body[0]
                if (
                    isinstance(first, ast.Expr)
                    and isinstance(first.value, ast.Constant)
                    and isinstance(first.value.value, str)
                ):
                    spans.append((first.value.lineno, first.value.end_lineno or first.value.lineno))
        return tuple(sorted(spans))


def parse_python(loaded: LoadedFile) -> PythonSource | ParseFailure:
    """Decode, tokenize, and parse a loaded file, in that order. The first failure is the result."""
    relative = loaded.relative_path
    try:
        buffer = io.BytesIO(loaded.content)
        encoding, _ = tokenize.detect_encoding(buffer.readline)
        buffer.seek(0)
        text = io.TextIOWrapper(buffer, encoding=encoding).read()
    except (UnicodeDecodeError, LookupError) as exc:
        return ParseFailure(
            error("decode", "read", "bounded-read", "source could not be decoded", path=relative),
            exc,
        )
    except SyntaxError as exc:
        message = "source encoding declaration is invalid"
        return ParseFailure(error("decode", "read", "bounded-read", message, path=relative), exc)
    try:
        tokens = tuple(tokenize.generate_tokens(io.StringIO(text).readline))
    except (tokenize.TokenError, IndentationError) as exc:
        return ParseFailure(error("tokenize", "analysis", "tokenize", str(exc), path=relative), exc)
    try:
        tree = ast.parse(text, filename=str(loaded.path))
    except SyntaxError as exc:
        return ParseFailure(_syntax_error(exc, relative), exc)
    statements = tuple(
        sorted((node for node in ast.walk(tree) if isinstance(node, ast.stmt)), key=_node_key)
    )
    return PythonSource(loaded.path, relative, text, tokens, tree, statements)


def _syntax_error(exc: SyntaxError, path: str) -> LintError:
    line = exc.lineno
    column = exc.offset
    return LintError(
        "syntax-error",
        "syntax",
        path,
        line,
        column,
        line,
        column + 1 if column else None,
        "analysis",
        "ast-parse",
        None,
        exc.msg,
    )


def _node_key(node: ast.stmt) -> tuple[int, int, int, int]:
    return (
        node.lineno,
        node.col_offset,
        node.end_lineno or node.lineno,
        node.end_col_offset or node.col_offset,
    )


__all__ = ["ParseFailure", "PythonSource", "Token", "parse_python"]
