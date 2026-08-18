"""Detect exception bindings that do not follow the configured name policy."""

import ast

from house_lint.analysis import CandidateFinding, append_candidate, candidate_for_line
from house_lint.config import HSL103Options
from house_lint.source import SourceFile


def detect(
    source: SourceFile, options: HSL103Options, *, limit: int | None = None
) -> list[CandidateFinding]:
    """Return HSL103 candidates for disallowed bound exception names."""
    if source.error is not None:
        return []
    assert source.tree is not None
    findings: list[CandidateFinding] = []
    for try_statement in ast.walk(source.tree):
        if not isinstance(try_statement, (ast.Try, ast.TryStar)):
            continue
        for handler in try_statement.handlers:
            if handler.name is None or _is_allowed(handler.name, options.allowed):
                continue
            append_candidate(
                findings,
                candidate_for_line(
                    source,
                    "HSL103",
                    f"exception handler bound to '{handler.name}'",
                    handler.lineno,
                    try_statement,
                ),
                source,
                limit,
            )
    return findings


def _is_allowed(name: str, allowed: tuple[str, ...]) -> bool:
    return any(
        name == pattern if not pattern.startswith("*") else name.endswith(pattern[1:])
        for pattern in allowed
    )


__all__ = ["detect"]
