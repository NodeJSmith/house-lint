"""Token- and AST-aware unified suppression handling."""

import ast
import re
import tokenize
from collections import defaultdict
from collections.abc import Callable, Collection
from dataclasses import dataclass
from typing import TypeAlias

from house_lint.analysis import (
    CandidateBudgetExceeded,
    CandidateFinding,
    SourceKind,
    StatementKey,
    statement_key,
    statement_owner_for_line,
)
from house_lint.results import Finding
from house_lint.rule_catalog import is_known_rule
from house_lint.source import SourceFile, Token

_ID = re.compile(r"HSL[0-9]{3}\Z")
_PRAGMA = re.compile(r"#\s*house-lint:\s*(ignore-next|ignore-file|ignore)\[([^]]*)\] - (.+)\Z")
_MIN_REASON_ALNUM_CHARS = 3

_Owner: TypeAlias = StatementKey | str


@dataclass(frozen=True)
class SuppressionResult:
    """Public findings and suppression count for one source file."""

    findings: tuple[Finding, ...]
    suppressed_count: int


class SuppressionBudgetExceeded(CandidateBudgetExceeded):
    """Carry the bounded visible prefix when suppression diagnostics exceed the budget."""

    def __init__(self, source: SourceFile, result: SuppressionResult) -> None:
        self.result = result
        super().__init__(source.relative_path)


@dataclass(frozen=True)
class _Pragma:
    action: str
    ids: tuple[str, ...]
    token: Token
    owner: StatementKey | None


@dataclass(frozen=True)
class _Claim:
    pragma: _Pragma
    rule_id: str
    target: tuple[_Owner, str]
    candidates: tuple[int, ...]


def apply_suppressions(
    source: SourceFile,
    candidates: tuple[CandidateFinding, ...],
    enabled_rules: Collection[str],
    *,
    candidates_complete: bool = True,
    limit: int | None = None,
) -> SuppressionResult:
    """Apply valid pragmas, retaining diagnostics and provenance until public conversion."""
    diagnostics: list[CandidateFinding] = []
    diagnostic_limit = None if limit is None else max(0, limit - len(candidates))
    diagnostics_exceeded = False

    def add_diagnostic(candidate: CandidateFinding) -> None:
        nonlocal diagnostics_exceeded
        if diagnostic_limit is not None and len(diagnostics) >= diagnostic_limit:
            diagnostics_exceeded = True
            return
        diagnostics.append(candidate)

    claims = _collect_claims(source, candidates, enabled_rules, add_diagnostic)
    conflicts = _conflicting_claims(claims)
    for index, claim in enumerate(claims):
        if index in conflicts:
            add_diagnostic(
                _diagnostic(
                    source, claim.pragma.token, f"conflicting suppression for {claim.rule_id}"
                )
            )
        elif not claim.candidates and candidates_complete:
            add_diagnostic(
                _diagnostic(source, claim.pragma.token, f"unused suppression for {claim.rule_id}")
            )

    suppressed = {
        candidate_index
        for index, claim in enumerate(claims)
        if index not in conflicts
        for candidate_index in claim.candidates
    }
    visible = tuple(
        candidate for index, candidate in enumerate(candidates) if index not in suppressed
    ) + tuple(diagnostics)
    result = SuppressionResult(tuple(_public(candidate) for candidate in visible), len(suppressed))
    if diagnostics_exceeded:
        raise SuppressionBudgetExceeded(source, result)
    return result


def _collect_claims(
    source: SourceFile,
    candidates: tuple[CandidateFinding, ...],
    enabled_rules: Collection[str],
    add_diagnostic: Callable[[CandidateFinding], None],
) -> list[_Claim]:
    """Scan pragma comments and build one claim per rule ID each pragma names.

    Malformed, misplaced, unknown-rule, and disabled-rule pragmas report through
    `add_diagnostic` as a side effect rather than appearing in the returned claims.
    """
    claims: list[_Claim] = []
    for token in source.tokens:
        if token.type != tokenize.COMMENT or not _is_pragma(token):
            continue
        pragma, message = _parse_pragma(source, token)
        if pragma is None:
            add_diagnostic(_diagnostic(source, token, message))
            continue
        owner, placement_error = _owner_for_pragma(source, pragma)
        if placement_error is not None:
            add_diagnostic(_diagnostic(source, token, placement_error))
            continue
        pragma = _Pragma(pragma.action, pragma.ids, pragma.token, owner)
        for rule_id in pragma.ids:
            if not is_known_rule(rule_id):
                add_diagnostic(_diagnostic(source, token, f"unknown suppression rule {rule_id}"))
                continue
            if rule_id not in enabled_rules:
                add_diagnostic(
                    _diagnostic(source, token, f"unused suppression for disabled rule {rule_id}")
                )
                continue
            target = _target(pragma)
            owned = tuple(
                index
                for index, candidate in enumerate(candidates)
                if candidate.rule_id == rule_id and _owns(target[0], candidate)
            )
            claims.append(_Claim(pragma, rule_id, target, owned))
    return claims


def _is_pragma(token: Token) -> bool:
    return bool(re.match(r"#\s*house-lint:", token.string))


def _parse_pragma(source: SourceFile, token: Token) -> tuple[_Pragma | None, str]:
    match = _PRAGMA.fullmatch(token.string)
    if match is None:
        return None, "malformed suppression pragma"
    action, raw_ids, reason = match.groups()
    ids = tuple(item.strip() for item in raw_ids.split(","))
    if not ids or any(not _ID.fullmatch(rule_id) for rule_id in ids):
        return None, "malformed suppression rule IDs"
    if len(set(ids)) != len(ids):
        return None, "duplicate suppression rule IDs"
    if "HSL900" in ids:
        return None, "HSL900 cannot be suppressed"
    if sum(character.isalnum() for character in reason) < _MIN_REASON_ALNUM_CHARS:
        return None, (
            f"suppression reason must contain at least {_MIN_REASON_ALNUM_CHARS} "
            "alphanumeric characters"
        )
    return _Pragma(action, ids, token, None), ""


def _owner_for_pragma(
    source: SourceFile, pragma: _Pragma
) -> tuple[StatementKey | None, str | None]:
    if pragma.action == "ignore":
        owner = _trailing_owner(source, pragma.token)
        return (owner, None) if owner is not None else (None, "misplaced ignore suppression")
    if pragma.action == "ignore-next":
        owner = _next_owner(source, pragma.token)
        return (owner, None) if owner is not None else (None, "misplaced ignore-next suppression")
    if _is_file_prologue(source, pragma.token):
        return None, None
    return None, "misplaced ignore-file suppression"


def _trailing_owner(source: SourceFile, token: Token) -> StatementKey | None:
    line, column = token.start
    if not source.lines[line - 1][:column].strip():
        return None
    owner = statement_owner_for_line(source, line, column)
    return statement_key(owner) if owner is not None else None


def _next_owner(source: SourceFile, token: Token) -> StatementKey | None:
    line, column = token.start
    if source.lines[line - 1][:column].strip():
        return None
    for suite in _suites(source.tree):
        for statement in suite:
            if statement.lineno <= line or statement.col_offset != column:
                continue
            if _comments_or_blanks_only(source, line + 1, statement.lineno - 1):
                return statement_key(statement)
    return None


def _suites(tree: ast.Module | None) -> tuple[tuple[ast.stmt, ...], ...]:
    """Return Python grammar suites eligible for `ignore-next` ownership."""
    if tree is None:
        return ()
    suites: list[tuple[ast.stmt, ...]] = []

    def add_suite(statements: list[ast.stmt]) -> None:
        if not statements:
            return
        suite = tuple(statements)
        suites.append(suite)
        for statement in suite:
            visit(statement)

    def visit(statement: ast.stmt) -> None:
        if isinstance(statement, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            add_suite(statement.body)
        elif isinstance(statement, (ast.If, ast.For, ast.AsyncFor, ast.While)):
            add_suite(statement.body)
            add_suite(statement.orelse)
        elif isinstance(statement, (ast.With, ast.AsyncWith)):
            add_suite(statement.body)
        elif isinstance(statement, (ast.Try, ast.TryStar)):
            add_suite(statement.body)
            for handler in statement.handlers:
                add_suite(handler.body)
            add_suite(statement.orelse)
            add_suite(statement.finalbody)
        elif isinstance(statement, ast.Match):
            for case in statement.cases:
                add_suite(case.body)

    add_suite(tree.body)
    return tuple(suites)


def _comments_or_blanks_only(source: SourceFile, start: int, end: int) -> bool:
    return all(
        not source.lines[line - 1].strip() or source.lines[line - 1].lstrip().startswith("#")
        for line in range(start, end + 1)
    )


def _is_file_prologue(source: SourceFile, token: Token) -> bool:
    if source.lines[token.start[0] - 1][: token.start[1]].strip():
        return False
    if source.tree is None:
        return False
    for index, statement in enumerate(source.tree.body):
        if _prologue_statement(statement, is_module_docstring=index == 0):
            continue
        return token.start[0] < statement.lineno
    return True


def _prologue_statement(statement: ast.stmt, *, is_module_docstring: bool) -> bool:
    if isinstance(statement, ast.Expr) and isinstance(statement.value, ast.Constant):
        return is_module_docstring and isinstance(statement.value.value, str)
    return isinstance(statement, ast.ImportFrom) and statement.module == "__future__"


def _target(pragma: _Pragma) -> tuple[_Owner, str]:
    if pragma.action == "ignore-file":
        return "file", pragma.action
    assert pragma.owner is not None
    return pragma.owner, pragma.action


def _owns(owner: _Owner, candidate: CandidateFinding) -> bool:
    if owner == "file":
        return candidate.rule_id != "HSL900"
    return candidate.owner == owner


def _conflicting_claims(claims: list[_Claim]) -> set[int]:
    conflicts: set[int] = set()
    by_target: dict[tuple[_Owner, str, str], list[int]] = defaultdict(list)
    by_candidate: dict[int, list[int]] = defaultdict(list)
    for index, claim in enumerate(claims):
        by_target[(*claim.target, claim.rule_id)].append(index)
        for candidate in claim.candidates:
            by_candidate[candidate].append(index)
    for indexes in (*by_target.values(), *by_candidate.values()):
        if len(indexes) > 1:
            conflicts.update(indexes)
    return conflicts


def _diagnostic(source: SourceFile, token: Token, message: str) -> CandidateFinding:
    line, column = token.start
    return CandidateFinding(
        "HSL900",
        source.relative_path,
        message,
        line,
        column + 1,
        token.end[0],
        token.end[1] + 1,
        SourceKind.NO_OWNER,
    )


def _public(candidate: CandidateFinding) -> Finding:
    return Finding(
        candidate.rule_id,
        candidate.path,
        candidate.line,
        candidate.column,
        candidate.end_line,
        candidate.end_column,
        candidate.message,
    )


__all__ = ["SuppressionResult", "apply_suppressions"]
