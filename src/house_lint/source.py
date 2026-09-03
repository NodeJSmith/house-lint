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


def read_regular_file_bytes(path: Path, *, max_bytes: int) -> bytes | None:
    """Read up to `max_bytes` + 1 from a regular file via a nonblocking descriptor.

    The nonblocking descriptor prevents a raced FIFO from stalling the read. Returns None if
    the path isn't a regular file; raises OSError for other failures (missing file, permission
    denied, etc.) so callers can decide how to report them — `SourceFile` turns both cases into
    a `LintError`, while cache-key hashing just treats either as an uncacheable file.

    `O_NOFOLLOW` narrows the window between discovery resolving a path and the scan opening it.
    Callers pass an already-fully-resolved path, so its final component is by construction not a
    symlink — unless it was replaced with one after discovery approved it, which is exactly the
    case that must not be read. Refusing to follow it turns that race into an ordinary read
    error rather than an out-of-root read. This does not close the window on the *directory*
    components of the path, which would need an `openat`-based descent from the root.
    """
    descriptor = os.open(
        path, os.O_RDONLY | getattr(os, "O_NONBLOCK", 0) | getattr(os, "O_NOFOLLOW", 0)
    )
    with os.fdopen(descriptor, "rb") as handle:
        if not stat.S_ISREG(os.fstat(handle.fileno()).st_mode):
            return None
        return handle.read(max_bytes + 1)


class SourceFile:
    """Load one Python file, failing closed before any rule can inspect it.

    This is the only place a scanned file's bytes are read. `content_bytes` exposes that single
    buffer so the cache key can be derived from exactly the content the detectors analyze — see
    `cli._scan`.
    """

    def __init__(self, path: Path, root: Path, *, resolved_path: Path | None = None) -> None:
        # `resolved_path` is discovery's own `resolve()` result, threaded through rather than
        # recomputed. Resolving a second time here would reopen a window in which a symlink
        # retargeted after the containment check sends the read somewhere discovery never
        # approved. Containment is still re-checked below against whichever path is used, so
        # passing one in cannot widen what this class will read.
        self.path = path.absolute()
        self.resolved_path = path.resolve() if resolved_path is None else resolved_path
        self.root = root.resolve()
        self._error: LintError | None = None
        self._debug_exception: BaseException | None = None
        self._loaded = False
        self._analyzed = False
        self._source_bytes: bytes | None = None
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

    def load(self) -> None:
        """Read this file's bytes, once. Idempotent; failures become `error`, never exceptions."""
        if self._loaded or self._error is not None:
            self._loaded = True
            return
        self._loaded = True
        try:
            source_bytes = read_regular_file_bytes(self.resolved_path, max_bytes=MAX_SOURCE_BYTES)
            self._source_bytes = source_bytes
            if source_bytes is None:
                self._error = self._make_error(
                    "path-error", "path", "source-check", "regular-file", "not a regular file"
                )
                return
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
        self.load()
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
    def content_bytes(self) -> bytes | None:
        """The raw bytes this file was read as, or None if it could not be read at all.

        None covers both "not a regular file" and a failed read; an oversized or undecodable
        file still reports the bytes that were read. Callers that need a cache key apply their
        own cacheability policy to this (`cache.hash_source_content`) rather than re-reading.
        """
        self.load()
        return self._source_bytes

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
        """`text` split on the tokenizer's line model: `\\n` only.

        Not `str.splitlines()`, which also splits on `\\f`, `\\v`, `\\x1c`-`\\x1e`, `\\x85`,
        `\\u2028`, and `\\u2029` — characters the tokenizer and AST do not count as line breaks.
        Every consumer indexes this list with tokenizer/AST line numbers, so one such character
        anywhere in the source (legal in a string literal, or as a bare `\\f` page break) would
        shift every later index. `load()`'s universal-newline decode has already normalized
        `\\r\\n` and `\\r`, so splitting on `\\n` matches the tokenizer exactly.
        """
        if self._lines is None:
            lines = self.text.split("\n")
            if lines and lines[-1] == "":
                lines.pop()
            self._lines = lines
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


__all__ = ["MAX_SOURCE_BYTES", "SourceFile", "read_regular_file_bytes"]
