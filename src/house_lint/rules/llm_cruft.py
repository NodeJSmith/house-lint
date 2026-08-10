"""Detect AI-writing tells in comments and docstrings."""

import ast
import re

from house_lint.analysis import (
    CandidateFinding,
    candidate_for_line,
    candidate_for_statement,
    statement_owner_for_line,
)
from house_lint.source import SourceFile

DIVIDER_RULE = re.compile(r"^[-=#*~_]{4,}$")
DIVIDER_WRAPPED = re.compile(r"^[-=#*~_]{3,}\s+\S.*\S\s+[-=#*~_]{3,}$")
FILLER_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"\bit is important to note\b", re.IGNORECASE), "drop it; state the fact directly"),
    (re.compile(r"\bit should be noted\b", re.IGNORECASE), "drop it; state the fact directly"),
    (re.compile(r"\bit is worth noting\b", re.IGNORECASE), "drop it; state the fact directly"),
    (re.compile(r"\bplease note that\b", re.IGNORECASE), "drop 'please note that'"),
    (re.compile(r"\bneedless to say\b", re.IGNORECASE), "drop it"),
    (re.compile(r"\bdue to the fact that\b", re.IGNORECASE), "use 'because'"),
    (re.compile(r"\bin order to\b", re.IGNORECASE), "use 'to'"),
    (re.compile(r"\bas mentioned (?:above|previously|earlier)\b", re.IGNORECASE), "name the thing directly"),
    (re.compile(r"\b(?:leverage|leverages|leveraging)\b", re.IGNORECASE), "use 'use'"),
    (re.compile(r"\b(?:utilize|utilizes|utilizing)\b", re.IGNORECASE), "use 'use'"),
    (re.compile(r"\b(?:facilitate|facilitates|facilitating)\b", re.IGNORECASE), "use 'help' or be specific"),
)


def detect(source: SourceFile) -> list[CandidateFinding]:
    """Return HSL001 candidates using only cached source representations."""
    if source.error is not None:
        return []

    findings: list[CandidateFinding] = []
    for line, comment in source.comments.items():
        body = comment.lstrip("#").strip()
        if DIVIDER_RULE.fullmatch(body) or DIVIDER_WRAPPED.fullmatch(body):
            findings.append(_comment_candidate(source, line, "section-divider comment"))
        findings.extend(
            _comment_candidate(source, line, f"filler - {suggestion}")
            for pattern, suggestion in FILLER_PATTERNS
            if pattern.search(comment)
        )

    for start, end in source.docstring_spans:
        for line in range(start, end + 1):
            text = source.lines[line - 1]
            findings.extend(
                candidate_for_statement(
                    source,
                    "HSL001",
                    f"filler - {suggestion}",
                    _docstring_owner(source, line),
                )
                for pattern, suggestion in FILLER_PATTERNS
                if pattern.search(text)
            )
    return sorted(findings, key=lambda finding: (finding.line or 0, finding.message))


def _comment_candidate(source: SourceFile, line: int, message: str) -> CandidateFinding:
    return candidate_for_line(
        source, "HSL001", message, line, statement_owner_for_line(source, line)
    )


def _docstring_owner(source: SourceFile, line: int) -> ast.stmt:
    for statement in source.statements:
        if (
            isinstance(statement, ast.Expr)
            and isinstance(statement.value, ast.Constant)
            and isinstance(statement.value.value, str)
            and statement.lineno <= line <= (statement.end_lineno or statement.lineno)
        ):
            return statement
    raise RuntimeError("docstring line has no string-expression statement")
