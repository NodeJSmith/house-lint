"""Detect AI-writing tells in comments and docstrings."""

import re

from house_lint.analysis import (
    CandidateFinding,
    append_candidate,
    candidate_for_line,
    comment_owner_for_line,
    docstring_owner_for_line,
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
    (
        re.compile(r"\bas mentioned (?:above|previously|earlier)\b", re.IGNORECASE),
        "name the thing directly",
    ),
    (re.compile(r"\b(?:leverage|leverages|leveraging|leveraged)\b", re.IGNORECASE), "use 'use'"),
    (re.compile(r"\b(?:utilize|utilizes|utilizing|utilized)\b", re.IGNORECASE), "use 'use'"),
    (
        re.compile(r"\b(?:facilitate|facilitates|facilitating|facilitated)\b", re.IGNORECASE),
        "use 'help' or be specific",
    ),
)


def detect(
    source: SourceFile, options: object, *, limit: int | None = None
) -> list[CandidateFinding]:
    """Return HSL001 candidates using only cached source representations."""
    if source.error is not None:
        return []

    findings: list[CandidateFinding] = []

    def add(finding: CandidateFinding) -> None:
        append_candidate(findings, finding, source, limit)

    for line, comment in source.comments.items():
        body = comment.lstrip("#").strip()
        if DIVIDER_RULE.fullmatch(body) or DIVIDER_WRAPPED.fullmatch(body):
            add(_comment_candidate(source, line, "section-divider comment"))
        for pattern, suggestion in FILLER_PATTERNS:
            if pattern.search(comment):
                add(_comment_candidate(source, line, f"filler - {suggestion}"))

    for start, end in source.docstring_spans:
        for line in range(start, end + 1):
            text = source.lines[line - 1]
            for pattern, suggestion in FILLER_PATTERNS:
                if pattern.search(text):
                    add(
                        candidate_for_line(
                            source,
                            "HSL001",
                            f"filler - {suggestion}",
                            line,
                            docstring_owner_for_line(source, line),
                        )
                    )
    return sorted(findings, key=lambda finding: (finding.line or 0, finding.message))


def _comment_candidate(source: SourceFile, line: int, message: str) -> CandidateFinding:
    comment = source.comments[line]
    return candidate_for_line(
        source,
        "HSL001",
        message,
        line,
        comment_owner_for_line(source, line, comment),
    )


__all__ = ["detect"]
