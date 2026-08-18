"""Single-file source loading and cached Python representations."""

import ast
import io
import os
import stat
import tokenize
from pathlib import Path
from typing import TypeAlias

from house_lint.results import LintError

MAX_SOURCE_BYTES = 10 * 1024 * 1024

Token: TypeAlias = tokenize.TokenInfo


class SourceFile:
    """Load one Python file, failing closed before any rule can inspect it."""

    def __init__(self, path: Path, root: Path) -> None:
        self.path = path.absolute()
        self.resolved_path = path.resolve()
        self.root = root.resolve()
        self._error: LintError | None = None
        self._debug_exception: BaseException | None = None
        self._loaded = False
        self._analyzed = False
        self._text: str | None = None
        self._lines: list[str] | None = None
        self._tokens: tuple[Token, ...] = ()
        self._tree: ast.Module | None = None
        self._docstrings: tuple[tuple[int, int], ...] | None = None
        self._statements: tuple[ast.stmt, ...] = ()
        self._comments: dict[int, str] | None = None
        try:
            self.relative_path = path.absolute().relative_to(root.absolute()).as_posix()
        except ValueError:
            self.relative_path = path.name
            self._error = self._make_error(
                "path-error", "path", "source-check", "root-check", "selected path escapes root"
            )
            return
        try:
            self.resolved_path.relative_to(self.root)
        except ValueError:
            self._error = self._make_error(
                "path-error", "path", "source-check", "root-check", "selected path escapes root"
            )
            return

    def _load(self) -> None:
        if self._loaded or self._error is not None:
            self._loaded = True
            return
        self._loaded = True
        try:
            # A nonblocking descriptor prevents a raced FIFO from stalling the scan.
            descriptor = os.open(self.resolved_path, os.O_RDONLY | getattr(os, "O_NONBLOCK", 0))
            with os.fdopen(descriptor, "rb") as handle:
                if not stat.S_ISREG(os.fstat(handle.fileno()).st_mode):
                    self._error = self._make_error(
                        "path-error", "path", "source-check", "regular-file", "not a regular file"
                    )
                    return
                source_bytes = handle.read(MAX_SOURCE_BYTES + 1)
            if len(source_bytes) > MAX_SOURCE_BYTES:
                self._error = self._make_error(
                    "source-too-large",
                    "budget",
                    "source-check",
                    "bounded-read",
                    "source file exceeds 10 MiB",
                )
                return
            buffer = io.BytesIO(source_bytes)
            encoding, _ = tokenize.detect_encoding(buffer.readline)
            buffer.seek(0)
            self._text = io.TextIOWrapper(buffer, encoding=encoding).read()
        except (UnicodeDecodeError, LookupError) as exc:
            self._debug_exception = exc
            self._error = self._make_error(
                "decode-error", "decode", "read", "bounded-read", "source could not be decoded"
            )
            return
        except SyntaxError as exc:
            self._debug_exception = exc
            self._error = self._make_error(
                "decode-error",
                "decode",
                "read",
                "bounded-read",
                "source encoding declaration is invalid",
            )
            return
        except IsADirectoryError:
            self._error = self._make_error(
                "path-error", "path", "source-check", "regular-file", "not a regular file"
            )
            return
        except OSError as exc:
            self._debug_exception = exc
            self._error = self._make_error(
                "read-error", "read", "read", "bounded-read", "source could not be read"
            )
            return

    def _analyze(self) -> None:
        if self._analyzed:
            return
        self._load()
        if self._error is not None:
            self._analyzed = True
            return
        self._analyzed = True
        assert self._text is not None
        try:
            self._tokens = tuple(tokenize.generate_tokens(io.StringIO(self._text).readline))
        except (tokenize.TokenError, IndentationError) as exc:
            self._debug_exception = exc
            self._error = self._make_error(
                "tokenize-error", "tokenize", "analysis", "tokenize", str(exc)
            )
            return
        try:
            self._tree = ast.parse(self._text, filename=str(self.path))
            assert self._tree is not None
            self._statements = tuple(
                sorted(
                    (node for node in ast.walk(self._tree) if isinstance(node, ast.stmt)),
                    key=self._node_key,
                )
            )
        except SyntaxError as exc:
            self._debug_exception = exc
            line = exc.lineno
            column = exc.offset
            self._error = self._make_error(
                "syntax-error",
                "syntax",
                "analysis",
                "ast-parse",
                exc.msg,
                line,
                column,
                line,
                column + 1 if column else None,
            )

    def _make_error(
        self,
        code: str,
        kind: str,
        phase: str,
        operation: str,
        message: str,
        line: int | None = None,
        column: int | None = None,
        end_line: int | None = None,
        end_column: int | None = None,
    ) -> LintError:
        return LintError(
            code,
            kind,
            self.relative_path,
            line,
            column,
            end_line,
            end_column,
            phase,
            operation,
            None,
            message,
        )

    @property
    def error(self) -> LintError | None:
        self._analyze()
        return self._error

    @property
    def debug_exception(self) -> BaseException | None:
        """Return the preserved operational exception for CLI debug output only."""
        self._analyze()
        return self._debug_exception

    @property
    def text(self) -> str:
        self._analyze()
        if self._error is not None or self._text is None:
            raise RuntimeError("source is unavailable")
        return self._text

    @property
    def lines(self) -> list[str]:
        if self._lines is None:
            self._lines = self.text.splitlines()
        return self._lines

    @property
    def tokens(self) -> tuple[Token, ...]:
        self._analyze()
        return () if self._error is not None else self._tokens

    @property
    def comments(self) -> dict[int, str]:
        if self._comments is None:
            self._comments = {
                token.start[0]: token.string
                for token in self.tokens
                if token.type == tokenize.COMMENT
            }
        return self._comments

    @property
    def tree(self) -> ast.Module | None:
        self._analyze()
        return self._tree

    @property
    def docstring_spans(self) -> tuple[tuple[int, int], ...]:
        if self._docstrings is None:
            if self.tree is None:
                return ()
            spans: list[tuple[int, int]] = []
            for node in ast.walk(self.tree):
                body = getattr(node, "body", ())
                if (
                    isinstance(
                        node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)
                    )
                    and body
                ):
                    first = body[0]
                    if (
                        isinstance(first, ast.Expr)
                        and isinstance(first.value, ast.Constant)
                        and isinstance(first.value.value, str)
                    ):
                        spans.append(
                            (first.value.lineno, first.value.end_lineno or first.value.lineno)
                        )
            self._docstrings = tuple(sorted(spans))
        return self._docstrings

    @property
    def statements(self) -> tuple[ast.stmt, ...]:
        self._analyze()
        return self._statements

    @staticmethod
    def _node_key(node: ast.stmt) -> tuple[int, int, int, int]:
        return (
            node.lineno,
            node.col_offset,
            node.end_lineno or node.lineno,
            node.end_col_offset or node.col_offset,
        )


__all__ = ["MAX_SOURCE_BYTES", "SourceFile"]
