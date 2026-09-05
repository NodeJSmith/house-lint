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
from house_lint.rule_catalog import ALWAYS_ON_RULE_ID, is_known_rule
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
                if candidate.rule_id == rule_id and _owns(pragma, target[0], candidate)
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
    if ALWAYS_ON_RULE_ID in ids:
        return None, f"{ALWAYS_ON_RULE_ID} cannot be suppressed"
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
            start = _statement_start_line(statement)
            if start <= line or statement.col_offset != column:
                continue
            if _comments_or_blanks_only(source, line + 1, start - 1):
                return statement_key(statement)
    return None


def _statement_start_line(statement: ast.stmt) -> int:
    """The statement's first physical line, counting its decorators.

    `FunctionDef`/`ClassDef.lineno` is the `def`/`class` line, but the statement's source begins
    at its first decorator. Placement checks comparing against "where the statement starts" must
    use the decorated start: otherwise an `ignore-next` above the decorator is rejected as
    misplaced (the decorator line defeats the blanks check), while an `ignore-file` sandwiched
    between decorator and `def` slips past the before-the-first-statement rule.

    Placement-only, deliberately: the `StatementKey` an accepted `ignore-next` resolves to still
    comes from `statement_key` (analysis.py), whose span starts at the undecorated `lineno` —
    the same span candidates are keyed by, so ownership matching is untouched.
    """
    if (
        isinstance(statement, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
        and statement.decorator_list
    ):
        return min(decorator.lineno for decorator in statement.decorator_list)
    return statement.lineno


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
        return token.start[0] < _statement_start_line(statement)
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


def _owns(pragma: _Pragma, owner: _Owner, candidate: CandidateFinding) -> bool:
    """Whether `pragma` claims `candidate`, given the owner its action resolved to.

    `owner` narrows structurally (`isinstance`, not `owner == "file"`) so the `StatementKey`
    branch below can read `owner.start_line` — pyright can't derive "file" as the type's only
    string value from an equality check against `_Owner = StatementKey | str`.

    A standalone comment (a divider, a filler-phrase comment) starts or ends no statement, so
    `statement_owner_for_line` leaves it `NO_OWNER` — it has no `StatementKey` an `ignore` pragma
    could ever match. `ignore-next` already treats such a comment as a placement-legal gap between
    itself and the statement it owns (`_next_owner`'s `_comments_or_blanks_only` check); this
    extends that same gap to also cover `NO_OWNER` findings raised on those in-between lines, so a
    pragma placed above the comment can suppress the comment's own finding. The window includes
    the pragma's own line: a physical line holds at most one comment token, so a `NO_OWNER`
    candidate there can only be the pragma's own reason text tripping the rule it names (e.g. a
    filler phrase in the reason) — the trailing `ignore` case already self-suppresses this for
    free, since its pragma and the finding it causes share one statement owner; `ignore-next`'s
    pragma line has no such owner to share, so it has to be named explicitly here instead. The
    bare line-range comparison below is only safe because everything from the pragma's own line
    through the gap was already verified comment-or-blank-only by `_next_owner` — it would
    otherwise risk reaching into unrelated code. For a decorated `def`/`class`, `owner.start_line`
    is the undecorated line (see `_statement_start_line`), so the window can extend into the
    decorator lines themselves; that stays safe because Python's grammar allows nothing there but
    decorator expressions, comments, and blank lines — never another statement.

    `candidate.line is not None` only narrows the type for the comparison below: every `NO_OWNER`
    candidate is built by `candidate_for_line` with a concrete `line`, so the check never actually
    excludes anything at runtime.
    """
    if isinstance(owner, str):
        return candidate.rule_id != ALWAYS_ON_RULE_ID
    if candidate.owner == owner:
        return True
    return (
        pragma.action == "ignore-next"
        and candidate.source_kind is SourceKind.NO_OWNER
        and candidate.line is not None
        and pragma.token.start[0] <= candidate.line < owner.start_line
    )


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
        ALWAYS_ON_RULE_ID,
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
