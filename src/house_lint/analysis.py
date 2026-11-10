"""Private detector provenance models.

These types intentionally do not form part of the reporter/configuration API.
"""

import ast
from collections.abc import Callable
from dataclasses import dataclass
from enum import Enum

from house_lint.python_source import PythonSource

MAX_CANDIDATES_PER_FILE = 10_000


class SourceKind(Enum):
    STATEMENT = "statement"
    FILE = "file"
    FILENAME = "filename"
    NO_OWNER = "no-owner"


@dataclass(frozen=True)
class StatementKey:
    start_line: int
    start_column: int
    end_line: int
    end_column: int


@dataclass(frozen=True)
class CandidateFinding:
    rule_id: str
    path: str
    message: str
    line: int | None
    column: int | None
    end_line: int | None
    end_column: int | None
    source_kind: SourceKind
    owner: StatementKey | None = None


class CandidateBudgetExceeded(RuntimeError):
    """Raised when one file produces more candidates than the fixed safety limit."""

    def __init__(self, path: str, *, candidates: tuple[CandidateFinding, ...] = ()) -> None:
        self.path = path
        self.limit = MAX_CANDIDATES_PER_FILE
        self.candidates = candidates
        super().__init__(f"candidate limit exceeded for {path}: {self.limit}")


def append_candidate(
    candidates: list[CandidateFinding],
    candidate: CandidateFinding,
    source: PythonSource,
    limit: int | None,
) -> None:
    """Append a candidate without exceeding the per-detector capacity."""
    if limit is not None and len(candidates) >= limit:
        raise CandidateBudgetExceeded(source.relative_path, candidates=tuple(candidates))
    candidates.append(candidate)


def statement_key(statement: ast.stmt | ast.excepthandler) -> StatementKey:
    """Span key for an owner node.

    Accepts `ast.excepthandler` alongside statements: an except clause is not an `ast.stmt`,
    but HSL103 keys its candidates by handler so one pragma cannot silence sibling handlers of
    the same `try` — see `exception_names.detect` and `statement_owner_for_line`'s refinement.
    """
    return StatementKey(
        statement.lineno,
        statement.col_offset + 1,
        statement.end_lineno or statement.lineno,
        (statement.end_col_offset or statement.col_offset) + 1,
    )


def candidate_for_statement(
    source: PythonSource, rule_id: str, message: str, statement: ast.stmt
) -> CandidateFinding:
    """Build a candidate owned by an AST statement."""
    owner = statement_key(statement)
    return CandidateFinding(
        rule_id,
        source.relative_path,
        message,
        owner.start_line,
        owner.start_column,
        owner.end_line,
        owner.end_column,
        SourceKind.STATEMENT,
        owner,
    )


def candidate_for_line(
    source: PythonSource,
    rule_id: str,
    message: str,
    line: int,
    owner: ast.stmt | ast.excepthandler | None = None,
) -> CandidateFinding:
    """Build a line candidate, retaining no-owner provenance when appropriate."""
    statement_owner = statement_key(owner) if owner is not None else None
    return CandidateFinding(
        rule_id,
        source.relative_path,
        message,
        line,
        1,
        line,
        source.byte_column(line, len(source.lines[line - 1])),
        SourceKind.STATEMENT if statement_owner is not None else SourceKind.NO_OWNER,
        statement_owner,
    )


def statement_owner_for_line(
    source: PythonSource, line: int, column: int
) -> ast.stmt | ast.excepthandler | None:
    """Return the statement a comment or docstring line is attached to.

    This owns file-rule findings and every pragma. A project-rule occurrence uses
    `occurrence_owner`, where a decorator line belongs to the definition it decorates.

    Trailing comments (code precedes them on the line) resolve to their innermost enclosing
    statement. A standalone comment (nothing but whitespace before it) never resolves here: a
    statement's `lineno`/`end_lineno` names a line that carries one of its own tokens, which a
    comment-only line by definition does not, so the standalone-comment candidate search always
    comes up empty and this returns `None`. Callers see that as `SourceKind.NO_OWNER`
    (`comment_owner_for_line`, `candidate_for_line`) — `suppressions.py`'s `_owns` reaches those
    findings separately, by checking whether the finding's line falls between an `ignore-next`
    pragma and the statement it owns, rather than through an owner returned from here.

    A line inside a `try`/`try*` statement's *except clause* — the `except ... as name:` line(s),
    which no body statement covers — refines to the `ast.excepthandler` itself. HSL103 keys its
    candidates by handler so one suppression cannot silence sibling handlers; a trailing pragma
    on the except line must resolve to that same key to match them.
    """
    if source.lines[line - 1][:column].strip():
        candidates = [
            statement
            for statement in source.statements
            if statement.lineno <= line <= (statement.end_lineno or statement.lineno)
        ]
    else:
        candidates = [
            statement
            for statement in source.statements
            if statement.lineno == line or (statement.end_lineno or statement.lineno) == line
        ]
    return innermost_owner(candidates, line, start_of=lambda statement: statement.lineno)


def occurrence_owner(source: PythonSource, line: int) -> ast.stmt | ast.excepthandler | None:
    """Return the statement a project-rule occurrence starting on `line` belongs to.

    Pragmas still resolve through `statement_owner_for_line`; the two agree on every statement's
    key (`statement_key`), so a pragma claims an occurrence when their owners are equal.

    The innermost statement whose decorated span, from its first decorator to its last line,
    contains the line. Unlike `statement_owner_for_line`, a decorator line belongs to the
    definition it decorates, so a pragma placed where a decorated duplicate visibly starts claims
    it, and the answer depends only on the line. A line outside the parsed file has no owner: a
    location that disagrees with the file stays a visible finding.
    """
    if not 1 <= line <= len(source.lines):
        return None
    candidates = [
        statement
        for statement in source.statements
        if decorated_start_line(statement) <= line <= (statement.end_lineno or statement.lineno)
    ]
    return innermost_owner(candidates, line, start_of=decorated_start_line)


def decorated_start_line(statement: ast.stmt) -> int:
    """The statement's first physical line, counting its decorators.

    `FunctionDef`/`ClassDef.lineno` is the `def`/`class` line, but the statement's source begins
    at its first decorator.
    """
    if (
        isinstance(statement, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
        and statement.decorator_list
    ):
        return min(decorator.lineno for decorator in statement.decorator_list)
    return statement.lineno


def innermost_owner(
    candidates: list[ast.stmt], line: int, *, start_of: Callable[[ast.stmt], int]
) -> ast.stmt | ast.excepthandler | None:
    """The innermost of the statements covering `line`, refined to an except clause if it's one.

    Innermost is the latest start, then the furthest-right column, for a statement sharing a line.
    When that lands on a `try` itself, no body statement covers the line: an except-clause line
    falls inside its handler's span and refines to it, while the try's other header lines
    (`else:`, `finally:`) match no handler and keep the try.
    """
    if not candidates:
        return None
    owner = max(candidates, key=lambda statement: (start_of(statement), statement.col_offset))
    if isinstance(owner, (ast.Try, ast.TryStar)):
        for handler in owner.handlers:
            if handler.lineno <= line <= (handler.end_lineno or handler.lineno):
                return handler
    return owner


def comment_owner_for_line(
    source: PythonSource, line: int, comment: str
) -> ast.stmt | ast.excepthandler | None:
    """Return the statement syntactically attached to this comment token."""
    return statement_owner_for_line(source, line, source.lines[line - 1].index(comment))


def docstring_owner_for_line(source: PythonSource, line: int) -> ast.stmt:
    """Return the string-expression statement containing a docstring line."""
    for statement in source.statements:
        if (
            isinstance(statement, ast.Expr)
            and isinstance(statement.value, ast.Constant)
            and isinstance(statement.value.value, str)
            and statement.lineno <= line <= (statement.end_lineno or statement.lineno)
        ):
            return statement
    raise RuntimeError("docstring line has no string-expression statement")
