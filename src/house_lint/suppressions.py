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
    decorated_start_line,
    occurrence_owner,
    statement_key,
    statement_owner_for_line,
)
from house_lint.language import Language
from house_lint.python_source import PythonSource, Token
from house_lint.results import Finding
from house_lint.rule_catalog import (
    ALWAYS_ON_RULE_ID,
    STALE_SUPPRESSION_RULE_ID,
    is_known_rule,
    is_project_rule,
    rule_languages,
)

_ID = re.compile(r"HSL[0-9]{3}\Z")
_PRAGMA = re.compile(r"#\s*house-lint:\s*(ignore-next|ignore-file|ignore)\[([^]]*)\] - (.+)\Z")
_MIN_REASON_ALNUM_CHARS = 3
# Among overlapping pragmas, the broadest survives: rank 0 first. A file pragma's claims contain a
# statement pragma's, and an `ignore-next`'s contain a trailing `ignore`'s on the same statement.
_SCOPE_RANK = {"ignore-file": 0, "ignore-next": 1, "ignore": 2}

_Owner: TypeAlias = StatementKey | str


@dataclass(frozen=True)
class SuppressionResult:
    """Public findings and suppression count for one source file."""

    findings: tuple[Finding, ...]
    suppressed_count: int


class SuppressionBudgetExceeded(CandidateBudgetExceeded):
    """Carry the bounded visible prefix when suppression diagnostics exceed the budget."""

    def __init__(self, source: PythonSource, result: SuppressionResult) -> None:
        self.result = result
        super().__init__(source.relative_path)


@dataclass(frozen=True)
class _Pragma:
    action: str
    ids: tuple[str, ...]
    token: Token
    owner: StatementKey | None


@dataclass(frozen=True)
class AcceptedPragma:
    """One rule ID a well-formed, well-placed pragma names, after the rule checks pass."""

    pragma: _Pragma
    rule_id: str


@dataclass(frozen=True)
class PragmaScan:
    """Every pragma in one file: what each accepted pragma names, and what's wrong with the rest.

    `diagnostics` are HSL900's broken-pragma reports. `stale` are HSL901's reports of a pragma
    naming a rule this file doesn't run, present only when HSL901 itself runs.
    """

    accepted: tuple[AcceptedPragma, ...]
    diagnostics: tuple[CandidateFinding, ...]
    stale: tuple[CandidateFinding, ...]


@dataclass(frozen=True)
class _Claim:
    pragma: _Pragma
    rule_id: str
    target: tuple[_Owner, str]
    candidates: tuple[int, ...]


def apply_suppressions(
    source: PythonSource,
    candidates: tuple[CandidateFinding, ...],
    enabled_rules: Collection[str],
    *,
    candidates_complete: bool = True,
    limit: int | None = None,
) -> SuppressionResult:
    """Apply valid pragmas, retaining diagnostics and provenance until public conversion.

    Every claim suppresses what it owns, overlapping or not. HSL900 reports broken pragmas. When
    HSL901 runs, it reports stale ones: unused, naming a disabled rule, or redundant with an
    overlapping pragma that survives. A pragma naming HSL901 can claim those reports. The visible
    findings are the unsuppressed candidates, then HSL900's reports, then HSL901's.
    """
    diagnostics: list[CandidateFinding] = []
    diagnostic_limit = None if limit is None else max(0, limit - len(candidates))
    diagnostics_exceeded = False

    def add_diagnostic(candidate: CandidateFinding) -> None:
        nonlocal diagnostics_exceeded
        if diagnostic_limit is not None and len(diagnostics) >= diagnostic_limit:
            diagnostics_exceeded = True
            return
        diagnostics.append(candidate)

    scan = scan_pragmas(source, enabled_rules)
    for diagnostic in scan.diagnostics:
        add_diagnostic(diagnostic)
    claims = _collect_claims(scan, candidates)
    if STALE_SUPPRESSION_RULE_ID in enabled_rules:
        stale = (
            *scan.stale,
            *_unused_and_redundant_reports(source, claims, candidates_complete=candidates_complete),
        )
        for diagnostic in _unsuppressed_stale_reports(scan, stale):
            add_diagnostic(diagnostic)

    suppressed = {candidate_index for claim in claims for candidate_index in claim.candidates}
    visible = tuple(
        candidate for index, candidate in enumerate(candidates) if index not in suppressed
    ) + tuple(diagnostics)
    result = SuppressionResult(tuple(_public(candidate) for candidate in visible), len(suppressed))
    if diagnostics_exceeded:
        raise SuppressionBudgetExceeded(source, result)
    return result


def claims_for(source: PythonSource, rule_id: str) -> Callable[[int | None], bool]:
    """Return whether a pragma for project rule `rule_id` claims an occurrence starting on a line.

    The pragmas are scanned once, with `rule_id` as the only enabled rule. `pipeline.run_file_phase`
    puts a file in a rule's input only when the rule applies to it, so a pragma is accepted here
    exactly when the file phase accepted it. The diagnostics are the file phase's to report.

    An `ignore-file` claims every occurrence. A statement pragma claims one whose
    `occurrence_owner` is the pragma's owner; an occurrence with no line, or a line outside the
    file, has no owner. Two pragmas claiming one occurrence both claim it, and nothing is reported.
    """
    accepted = [entry.pragma for entry in scan_pragmas(source, (rule_id,)).accepted]
    if any(pragma.action == "ignore-file" for pragma in accepted):
        return lambda line: True
    owners = {pragma.owner for pragma in accepted}

    def claims(line: int | None) -> bool:
        if line is None:
            return False
        owner = occurrence_owner(source, line)
        return owner is not None and statement_key(owner) in owners

    return claims


def scan_pragmas(source: PythonSource, enabled_rules: Collection[str]) -> PragmaScan:
    """Check every pragma comment, returning one accepted entry per rule ID that passes.

    Malformed, misplaced, unknown-rule, and wrong-language pragmas become HSL900 diagnostics, in
    source order, rather than accepted entries. A disabled-rule entry is never accepted either; it
    becomes an HSL901 report only when `enabled_rules` holds HSL901.
    """
    accepted: list[AcceptedPragma] = []
    diagnostics: list[CandidateFinding] = []
    stale: list[CandidateFinding] = []
    for token in source.tokens:
        if token.type != tokenize.COMMENT or not _is_pragma(token):
            continue
        pragma, message = _parse_pragma(source, token)
        if pragma is None:
            diagnostics.append(_broken_pragma_report(source, token, message))
            continue
        owner, placement_error = _owner_for_pragma(source, pragma)
        if placement_error is not None:
            diagnostics.append(_broken_pragma_report(source, token, placement_error))
            continue
        pragma = _Pragma(pragma.action, pragma.ids, pragma.token, owner)
        for rule_id in pragma.ids:
            if not is_known_rule(rule_id):
                diagnostics.append(
                    _broken_pragma_report(source, token, f"unknown suppression rule {rule_id}")
                )
                continue
            # Checked first: `enabled_rules` never holds such a rule, so the disabled check below
            # would misreport it, and enabling the rule wouldn't help. HSL900 can't reach here,
            # since `_parse_pragma` rejects a pragma naming it.
            if Language.PYTHON not in rule_languages(rule_id):
                diagnostics.append(
                    _broken_pragma_report(source, token, f"{rule_id} does not handle Python files")
                )
                continue
            if rule_id not in enabled_rules:
                # Never accepted, so it suppresses nothing; only HSL901 says so.
                if STALE_SUPPRESSION_RULE_ID in enabled_rules:
                    stale.append(
                        _stale_report(
                            source, pragma, f"unused suppression for disabled rule {rule_id}"
                        )
                    )
                continue
            accepted.append(AcceptedPragma(pragma, rule_id))
    return PragmaScan(tuple(accepted), tuple(diagnostics), tuple(stale))


def _collect_claims(scan: PragmaScan, candidates: tuple[CandidateFinding, ...]) -> list[_Claim]:
    """Build one file-phase claim per accepted pragma entry.

    A pragma naming a project rule makes no claim here. That rule's findings don't exist yet when
    this file's scan runs; the project phase resolves it (`claims_for`). Nor does one naming
    HSL901, whose reports `_unsuppressed_stale_reports` resolves. Neither is ever reported unused or redundant.
    """
    claims: list[_Claim] = []
    for entry in scan.accepted:
        if is_project_rule(entry.rule_id) or entry.rule_id == STALE_SUPPRESSION_RULE_ID:
            continue
        target = _target(entry.pragma)
        owned = tuple(
            index
            for index, candidate in enumerate(candidates)
            if candidate.rule_id == entry.rule_id and _owns(entry.pragma, target[0], candidate)
        )
        claims.append(_Claim(entry.pragma, entry.rule_id, target, owned))
    return claims


def _is_pragma(token: Token) -> bool:
    return bool(re.match(r"#\s*house-lint:", token.string))


def _parse_pragma(source: PythonSource, token: Token) -> tuple[_Pragma | None, str]:
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
    source: PythonSource, pragma: _Pragma
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


def _trailing_owner(source: PythonSource, token: Token) -> StatementKey | None:
    line, column = token.start
    if not source.lines[line - 1][:column].strip():
        return None
    owner = statement_owner_for_line(source, line, column)
    return statement_key(owner) if owner is not None else None


def _next_owner(source: PythonSource, token: Token) -> StatementKey | None:
    line, column = token.start
    if source.lines[line - 1][:column].strip():
        return None
    # Placement compares against the decorated start, so an `ignore-next` above a decorator is
    # accepted and one between a decorator and its `def` is not. The key it resolves to still
    # comes from `statement_key`, which starts at the `def` line, the same key candidates use.
    for suite in _suites(source.tree):
        for statement in suite:
            start = decorated_start_line(statement)
            if start <= line or statement.col_offset != column:
                continue
            if _comments_or_blanks_only(source, line + 1, start - 1):
                return statement_key(statement)
    return None


def _suites(tree: ast.Module) -> tuple[tuple[ast.stmt, ...], ...]:
    """Return Python grammar suites eligible for `ignore-next` ownership."""
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


def _comments_or_blanks_only(source: PythonSource, start: int, end: int) -> bool:
    return all(
        not source.lines[line - 1].strip() or source.lines[line - 1].lstrip().startswith("#")
        for line in range(start, end + 1)
    )


def _is_file_prologue(source: PythonSource, token: Token) -> bool:
    if source.lines[token.start[0] - 1][: token.start[1]].strip():
        return False
    for index, statement in enumerate(source.tree.body):
        if _prologue_statement(statement, is_module_docstring=index == 0):
            continue
        return token.start[0] < decorated_start_line(statement)
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
    is the undecorated line (see `analysis.decorated_start_line`), so the window can extend into the
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


def _unused_and_redundant_reports(
    source: PythonSource, claims: list[_Claim], *, candidates_complete: bool
) -> list[CandidateFinding]:
    """Report each claim that's redundant with a surviving overlap, or else owns nothing.

    A claim that owns nothing is only known unused when every candidate was produced.
    """
    redundant = _redundant_claims(claims)
    reports: list[CandidateFinding] = []
    for index, claim in enumerate(claims):
        if index in redundant:
            message = f"redundant suppression for {claim.rule_id}"
        elif not claim.candidates and candidates_complete:
            message = f"unused suppression for {claim.rule_id}"
        else:
            continue
        reports.append(_stale_report(source, claim.pragma, message))
    return reports


def _redundant_claims(claims: list[_Claim]) -> set[int]:
    """Return every overlapping claim but the survivor of each overlap.

    Claims overlap when they share a target and rule, or claim one candidate. Overlapping claims
    for one rule nest (`_owns`): an `ignore-file` owns every candidate of its rule; an `ignore-next`
    owns its statement's candidates plus comment findings in its window, a superset of a trailing
    `ignore`'s on that statement; and of two pragmas sharing a target, the earlier's window holds
    the later's. Statement pragmas on different statements share no candidate, since each candidate
    has one owner. So each group's survivor (broadest scope, then earliest) owns everything the
    others do, a claim redundant in one group can't be the only owner of a candidate in another,
    and deleting every redundant claim never changes what is suppressed.
    `test_deleting_every_redundant_pragma_leaves_the_suppressed_set_unchanged` pins that.
    """
    by_target: dict[tuple[_Owner, str, str], list[int]] = defaultdict(list)
    by_candidate: dict[int, list[int]] = defaultdict(list)
    for index, claim in enumerate(claims):
        by_target[(*claim.target, claim.rule_id)].append(index)
        for candidate in claim.candidates:
            by_candidate[candidate].append(index)
    redundant: set[int] = set()
    for indexes in (*by_target.values(), *by_candidate.values()):
        if len(indexes) < 2:
            continue
        survivor = min(indexes, key=lambda index: _survival_rank(claims[index].pragma))
        redundant.update(index for index in indexes if index != survivor)
    return redundant


def _survival_rank(pragma: _Pragma) -> tuple[int, tuple[int, int]]:
    """Order overlapping pragmas: broadest scope first, then earliest in the file."""
    return _SCOPE_RANK[pragma.action], pragma.token.start


def _unsuppressed_stale_reports(
    scan: PragmaScan, stale: tuple[CandidateFinding, ...]
) -> list[CandidateFinding]:
    """Drop each HSL901 report a pragma naming HSL901 claims, by the same `_owns` as any finding.

    An `ignore-file[HSL901]` claims every report. A statement pragma claims the reports carrying
    its owner: those on every pragma owning the same statement, including the reports on its own
    other IDs, as in `ignore[HSL002, HSL901]` with HSL002 unused.
    """
    owners = [
        (entry.pragma, _target(entry.pragma)[0])
        for entry in scan.accepted
        if entry.rule_id == STALE_SUPPRESSION_RULE_ID
    ]
    return [
        report
        for report in stale
        if not any(_owns(pragma, owner, report) for pragma, owner in owners)
    ]


def _broken_pragma_report(source: PythonSource, token: Token, message: str) -> CandidateFinding:
    """Build an HSL900 report. It has no owner, since no pragma can claim it."""
    return _pragma_finding(
        source, token, ALWAYS_ON_RULE_ID, message, SourceKind.NO_OWNER, owner=None
    )


def _stale_report(source: PythonSource, pragma: _Pragma, message: str) -> CandidateFinding:
    """Build an HSL901 report on `pragma`, owned as the pragma is, so a pragma can claim it.

    An `ignore-file` pragma has no statement owner, so its reports are file-kind, which only an
    `ignore-file[HSL901]` claims.
    """
    kind = SourceKind.FILE if pragma.owner is None else SourceKind.STATEMENT
    return _pragma_finding(
        source, pragma.token, STALE_SUPPRESSION_RULE_ID, message, kind, owner=pragma.owner
    )


def _pragma_finding(
    source: PythonSource,
    token: Token,
    rule_id: str,
    message: str,
    kind: SourceKind,
    *,
    owner: StatementKey | None,
) -> CandidateFinding:
    (line, column), (end_line, end_column) = token.start, token.end
    return CandidateFinding(
        rule_id,
        source.relative_path,
        message,
        line,
        source.byte_column(line, column),
        end_line,
        source.byte_column(end_line, end_column),
        kind,
        owner,
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


__all__ = ["SuppressionResult", "apply_suppressions", "claims_for"]
