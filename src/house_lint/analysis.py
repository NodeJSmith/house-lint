"""Private detector provenance models.

These types intentionally do not form part of the reporter/configuration API.
"""

import ast
from dataclasses import dataclass
from enum import Enum

from house_lint.source import SourceFile

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
    source: SourceFile,
    limit: int | None,
) -> None:
    """Append a candidate without exceeding the per-detector capacity."""
    if limit is not None and len(candidates) >= limit:
        raise CandidateBudgetExceeded(source.relative_path, candidates=tuple(candidates))
    candidates.append(candidate)


def parsed_tree(source: SourceFile) -> ast.Module | None:
    """Return the parsed module, or None when the file failed to load or parse.

    `source.error is None` guarantees `source.tree is not None` — the loader parses eagerly and
    only leaves an error set when parsing failed.
    """
    if source.error is not None:
        return None
    assert source.tree is not None
    return source.tree


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
    source: SourceFile, rule_id: str, message: str, statement: ast.stmt
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
    source: SourceFile,
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
        len(source.lines[line - 1]) + 1,
        SourceKind.STATEMENT if statement_owner is not None else SourceKind.NO_OWNER,
        statement_owner,
    )


def statement_owner_for_line(
    source: SourceFile, line: int, column: int
) -> ast.stmt | ast.excepthandler | None:
    """Return the statement a comment or docstring line is attached to.

    Trailing comments (code precedes them on the line) resolve to their innermost enclosing
    statement; standalone comments resolve to whichever statement starts or ends on that line.

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
    if not candidates:
        return None
    owner = max(candidates, key=lambda statement: (statement.lineno, statement.col_offset))
    if isinstance(owner, (ast.Try, ast.TryStar)):
        # Innermost-statement resolution landed on the try itself, so no body statement covers
        # this line. An except-clause line falls inside its handler's span and refines to it;
        # the try's other header lines (`else:`, `finally:`) match no handler and keep the try.
        for handler in owner.handlers:
            if handler.lineno <= line <= (handler.end_lineno or handler.lineno):
                return handler
    return owner


def comment_owner_for_line(
    source: SourceFile, line: int, comment: str
) -> ast.stmt | ast.excepthandler | None:
    """Return the statement syntactically attached to this comment token."""
    return statement_owner_for_line(source, line, source.lines[line - 1].index(comment))


def docstring_owner_for_line(source: SourceFile, line: int) -> ast.stmt:
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
