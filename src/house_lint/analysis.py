"""Private detector provenance models.

These types intentionally do not form part of the reporter/configuration API.
"""

import ast
from dataclasses import dataclass
from enum import Enum

from .source import SourceFile

MAX_CANDIDATES_PER_FILE = 10_000


class CandidateBudgetExceeded(RuntimeError):
    """Raised when one file produces more candidates than the fixed safety limit."""

    def __init__(self, path: str, limit: int = MAX_CANDIDATES_PER_FILE) -> None:
        self.path = path
        self.limit = limit
        super().__init__(f"candidate limit exceeded for {path}: {limit}")


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


def statement_key(statement: ast.stmt) -> StatementKey:
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
    owner: ast.stmt | None = None,
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


def statement_owner_for_line(source: SourceFile, line: int) -> ast.stmt | None:
    """Return the narrowest statement that begins or ends on a comment line."""
    candidates = [
        statement
        for statement in source.statements
        if statement.lineno == line or (statement.end_lineno or statement.lineno) == line
    ]
    if not candidates:
        return None
    return min(candidates, key=_statement_span)


def _statement_span(statement: ast.stmt) -> tuple[int, int, int, int]:
    return (
        (statement.end_lineno or statement.lineno) - statement.lineno,
        (statement.end_col_offset or statement.col_offset) - statement.col_offset,
        statement.lineno,
        statement.col_offset,
    )
