"""Detect mypy-style `type: ignore[...]` comments, whose error codes Pyright disregards."""

import re

from house_lint.analysis import (
    CandidateFinding,
    append_candidate,
    candidate_for_line,
    comment_owner_for_line,
)
from house_lint.python_source import PythonSource

# Matches where Pyright looks: right after any hash in the comment text, the opening one or a
# later one (`# noqa: E501` followed by a second hash). Pyright honors both, so both are flagged.
BRACKETED_TYPE_IGNORE = re.compile(r"#\s*type:\s*ignore\s*\[")
MESSAGE = "bracketed type: ignore - Pyright ignores the codes and silences every error on the line"


def detect(
    source: PythonSource, options: object, *, limit: int | None = None
) -> list[CandidateFinding]:
    """Return HSL105 candidates for comments holding a bracketed `type: ignore`."""
    findings: list[CandidateFinding] = []
    for line, comment in source.comments.items():
        if BRACKETED_TYPE_IGNORE.search(comment):
            append_candidate(
                findings,
                candidate_for_line(
                    source, "HSL105", MESSAGE, line, comment_owner_for_line(source, line, comment)
                ),
                source,
                limit,
            )
    return findings


__all__ = ["detect"]
